"""Publish measured outputs incrementally and evict only verified local binaries.

Public geometry, diagrams and generated Blender files go to the Data archive.
Private execution logs, API credentials and reviewer sessions are never inputs.
This single-owner uploader watches completed checkpoints, not in-progress files.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import time

import boto3
from botocore.exceptions import ClientError

from serverless.benchmark.archive import merge_index, packed, public_only
from serverless.benchmark.expand_infinigen import sha_file
from serverless.benchmark.geometry import measure
from serverless.benchmark.run_batch import run_lock, write_json
from serverless.benchmark.stimuli import diagram

ROOT = Path(__file__).resolve().parents[2]


def verify_remote(client, bucket, key, checksum, size):
    remote = client.head_object(Bucket=bucket, Key=key, ChecksumMode='ENABLED')
    if remote['ContentLength'] != size:
        raise ValueError('Remote artifact size differs')
    expected = base64.b64encode(bytes.fromhex(checksum)).decode()
    if remote.get('ChecksumSHA256') != expected:
        # Old S3 objects may not have a full-object SHA256 field. An ETag or
        # self-reported metadata hash is not sufficient permission to delete.
        actual = hashlib.sha256()
        body = client.get_object(Bucket=bucket, Key=key)['Body']
        try:
            for chunk in iter(lambda: body.read(1024 * 1024), b''): actual.update(chunk)
        finally: body.close()
        if actual.hexdigest() != checksum: raise ValueError('Remote artifact checksum differs')
    return {'key': key, 'sha256': checksum, 'bytes': size}


def upload(client, bucket, key, body, content_type, checksum, size):
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentLength=size,
            ContentType=content_type, CacheControl='public,max-age=31536000,immutable',
            ServerSideEncryption='AES256', IfNoneMatch='*', Metadata={'sha256': checksum},
            ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode())
    except ClientError as error:
        if error.response['Error']['Code'] not in ('PreconditionFailed', '412'): raise
    return verify_remote(client, bucket, key, checksum, size)


def publish_row(client, bucket, prefix, row):
    public_only(row)
    scene = row['scene']
    if not all(re.fullmatch('[a-z0-9_-]+', scene[key]) for key in ('model', 'id')):
        raise ValueError('Unsafe generated scene identity')
    folder = prefix + 'scenes/' + scene['model'] + '/' + scene['id'] + '/'
    # No synthetic still or imagined animation is generated here: this is a
    # measured-arrangement diagram, labelled as such in the archive manifest.
    payloads = [('geometry', packed(row), 'json', 'application/json'),
                ('diagram', diagram(scene).encode(), 'svg', 'image/svg+xml')]
    record = {'id': scene['id'], 'model': scene['model'], 'roomType': scene['roomType'],
              'objects': [item['label'] for item in scene['objects']], 'artifacts': {}}
    for label, body, extension, content_type in payloads:
        checksum = hashlib.sha256(body).hexdigest()
        key = folder + checksum[:24] + '.' + extension
        record['artifacts'][label] = upload(client, bucket, key, body, content_type, checksum, len(body))
    return record


def archive_binary(client, bucket, prefix, campaign, checkpoint, record):
    if 'archive' not in checkpoint: return 0
    exported = (campaign / checkpoint['export']).resolve()
    if not exported.is_relative_to(campaign.resolve()): raise ValueError('Checkpoint escaped campaign')
    path = exported.parent / ('scene-' + record['roomType'] + '-000') / 'scene.blend.gz'
    receipt_path = exported.parent / 's3-binary-receipt.json'
    metadata = checkpoint['archive']
    checksum, size = metadata['archiveSha256'], metadata['archiveBytes']
    key = prefix + 'scenes/' + record['model'] + '/' + record['id'] + '/' + checksum[:24] + '.blend.gz'
    if path.exists():
        if sha_file(path) != checksum or path.stat().st_size != size: raise ValueError('Changed local Blender archive')
        with path.open('rb') as body:
            receipt = upload(client, bucket, key, body, 'application/gzip', checksum, size)
        # Re-check exact target/hash after the network operation. Only this
        # completed, recoverable binary is evicted, never its checkpoint.
        if not path.resolve().is_relative_to(campaign.resolve()) or sha_file(path) != checksum:
            raise ValueError('Local archive changed before eviction')
        receipt['originalBlendSha256'] = metadata['originalSha256']
        write_json(receipt_path, receipt)
        path.unlink()
        freed = size
    else:
        if not receipt_path.exists(): raise ValueError('Missing local binary and upload receipt')
        receipt = json.loads(receipt_path.read_bytes())
        if receipt['key'] != key or receipt['sha256'] != checksum: raise ValueError('Changed S3 receipt')
        verify_remote(client, bucket, key, checksum, size)
        freed = 0
    record['artifacts']['blenderScene'] = receipt
    return freed


def load_inputs(measurements, extra_exports):
    rows = json.loads(measurements.read_bytes())['rows']
    for path in extra_exports:
        document = json.loads(path.read_bytes())
        if 'rows' in document: rows.extend(document['rows'])
        else: rows.extend({'scene': scene, 'metrics': measure(scene)} for scene in document['scenes'])
    if len({row['scene']['id'] for row in rows}) != len(rows): raise ValueError('Duplicate input scene')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--measurements', type=Path, required=True)
    parser.add_argument('--extra-export', type=Path, action='append', default=[])
    parser.add_argument('--date', required=True)
    parser.add_argument('--bucket', default='soilie3d-data')
    parser.add_argument('--profile', default='darkest')
    args = parser.parse_args()
    args.campaign, args.state = args.campaign.resolve(), args.state.resolve()
    if args.bucket != 'soilie3d-data' or date.fromisoformat(args.date).isoformat() != args.date:
        raise ValueError('Expected the authorized research bucket and ISO archive date')
    if any(not path.is_relative_to(ROOT / '.codex/benchmark') for path in (args.campaign, args.state)):
        raise ValueError('Local state and cleanup must remain in project benchmark scratch')
    prefix = 'files/outputs/benchmark-' + args.date + '/'
    client = boto3.Session(profile_name=args.profile).client('s3', region_name='ca-central-1')
    inputs = load_inputs(args.measurements, args.extra_export)
    args.state.mkdir(parents=True, exist_ok=True)
    with run_lock(args.state):
        state_path = args.state / 'uploads.json'
        state = json.loads(state_path.read_bytes()) if state_path.exists() else {
            'prefix': prefix, 'scenes': {}, 'evictedBytes': 0}
        if state['prefix'] != prefix: raise ValueError('Archive prefix changed')
        pending = [row for row in inputs if row['scene']['id'] not in state['scenes']]
        while True:
            changed = False
            # Stream fresh binaries first so a long historical upload cannot
            # fill the disk while native generation continues.
            for room in ('bedroom', 'living_room'):
                checkpoint = args.campaign / room / 'checkpoint.json'
                if not checkpoint.exists(): continue
                for attempt in json.loads(checkpoint.read_bytes())['attempts']:
                    if 'measured' not in attempt: continue
                    identity = attempt['sceneId']
                    if identity in state['scenes'] and 'blenderScene' in state['scenes'][identity]['artifacts']: continue
                    measured = args.campaign / attempt['measured']
                    if sha_file(measured) != attempt['measuredSha256']: raise ValueError('Changed measured checkpoint')
                    record = publish_row(client, args.bucket, prefix, json.loads(measured.read_bytes()))
                    state['evictedBytes'] += archive_binary(client, args.bucket, prefix, args.campaign, attempt, record)
                    state['scenes'][identity] = record
                    changed = True
            batch, pending = pending[:50], pending[50:]
            if batch:
                with ThreadPoolExecutor(max_workers=4) as executor:
                    for record in executor.map(lambda row: publish_row(client, args.bucket, prefix, row), batch):
                        state['scenes'][record['id']] = record
                        changed = True
            if changed:
                document = {'schemaVersion': 1, 'description': 'Measured final room arrangements and plan, oblique and bird\u2019s-eye diagrams. These are geometry observations, not final AI review scores or imagined-sequence renders.',
                            'scenes': list(state['scenes'].values())}
                body = packed(document)
                key = prefix + 'observations.json'
                client.put_object(Bucket=args.bucket, Key=key, Body=body, ContentType='application/json',
                                  CacheControl='no-cache', ServerSideEncryption='AES256')
                keys = [artifact['key'] for row in document['scenes'] for artifact in row['artifacts'].values()]
                merge_index(client, args.bucket, keys + [key])
                write_json(state_path, state)
                print(json.dumps({'publishedScenes': len(state['scenes']), 'initialScenesRemaining': len(pending),
                                  'localBytesEvicted': state['evictedBytes']}), flush=True)
            if not pending and (args.campaign / 'complete.json').exists(): break
            if not changed: time.sleep(20)


if __name__ == '__main__': main()
