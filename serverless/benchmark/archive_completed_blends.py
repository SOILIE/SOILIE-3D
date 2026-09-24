"""Recover disk space from explicitly named, completed Infinigen runs.

Keep the original Blender file until a lossless archive is verified on S3.
Receipts retain exact restoration hashes; logs and study state remain local.
This backfills binaries which predate the incremental expansion uploader.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re

import boto3

from serverless.benchmark.archive import merge_index, packed, prefix
from serverless.benchmark.expand_infinigen import sha_file
from serverless.benchmark.run_batch import run_lock, write_json
from serverless.benchmark.stream_archive import upload, verify_remote

ROOT = Path(__file__).resolve().parents[2]


def archive_one(client, bucket, folder, destination):
    folder = folder.resolve()
    source = folder / 'scene.blend'
    receipt_file = folder / 's3-scene-archive.json'
    if not source.exists():
        if not receipt_file.exists(): return None, 0
        receipt = json.loads(receipt_file.read_bytes())
        verify_remote(client, bucket, receipt['key'], receipt['sha256'], receipt['bytes'])
        return receipt, 0
    if source.is_symlink() or source.resolve().parent != folder:
        raise ValueError('Source must be a regular, owned Blender file')
    temporary = folder / 'scene.blend.archive-upload.tmp'
    if temporary.is_symlink(): raise ValueError('Unsafe archive staging path')
    before = source.stat()
    raw_hash = hashlib.sha256()
    try:
        with source.open('rb') as original, temporary.open('wb') as compressed:
            # Stable gzip header enables deduplication of identical old probes.
            with gzip.GzipFile(filename='', fileobj=compressed, mode='wb', compresslevel=1, mtime=0) as output:
                for chunk in iter(lambda: original.read(1024 * 1024), b''):
                    raw_hash.update(chunk)
                    output.write(chunk)
        restored = hashlib.sha256()
        with gzip.open(temporary, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): restored.update(chunk)
        if raw_hash.digest() != restored.digest(): raise ValueError('Archive is not lossless')
        checksum, size = sha_file(temporary), temporary.stat().st_size
        key = destination + 'blender-scenes/' + checksum + '.blend.gz'
        with temporary.open('rb') as body:
            receipt = upload(client, bucket, key, body, 'application/gzip', checksum, size)
        receipt.update(originalBlendSha256=raw_hash.hexdigest(), originalBytes=before.st_size,
                       restore='Download and decompress to scene.blend; verify originalBlendSha256.')
        # A changed file is never evicted, even if a valid older snapshot exists.
        after = source.stat()
        if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or sha_file(source) != raw_hash.hexdigest()):
            raise ValueError('Local scene changed while archiving')
        write_json(receipt_file, receipt)
        source.unlink()
        return receipt, before.st_size
    finally:
        # Only this invocation's disposable compressed staging copy is removed.
        # On any error the original is retained; on success S3 is verified.
        if temporary.exists(): temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='append', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--profile', default='darkest')
    args = parser.parse_args()
    owner = (ROOT / '.codex/benchmark').resolve()
    runs = [run.resolve() for run in args.run]
    state = args.state.resolve()
    if (any(run.parent != owner or not re.fullmatch('infinigen-[a-z0-9-]+', run.name) for run in runs)
            or not state.is_relative_to(owner) or state == owner):
        raise ValueError('Explicit Infinigen run folders and project-local state required')
    bucket, destination = 'soilie3d-data', prefix(args.date)
    client = boto3.Session(profile_name=args.profile).client('s3', region_name='ca-central-1')
    state.mkdir(parents=True, exist_ok=True)
    manifest_file = state / 'manifest.json'
    manifest = json.loads(manifest_file.read_bytes()) if manifest_file.exists() else {
        'schemaVersion': 1, 'description': 'Lossless Blender outputs from completed procedural generations; the comparison measurements define which scenes enter each analysis.',
        'scenes': {}, 'evictedBytes': 0}
    with run_lock(state):
        for run in runs:
            with run_lock(run):
                for attempt_file in sorted(run.glob('attempt-*.json')):
                    attempt = json.loads(attempt_file.read_bytes())
                    if attempt['status'] != 'complete': continue
                    name = attempt_file.stem.removeprefix('attempt-')
                    if not re.fullmatch('(bedroom|living_room)-[0-9]+', name):
                        raise ValueError('Unexpected completed attempt filename')
                    folder = run / ('scene-' + name)
                    if folder.resolve().parent != run: raise ValueError('Scene directory escaped run')
                    receipt, freed = archive_one(client, bucket, folder, destination)
                    if not receipt: continue
                    identity = run.name + '/' + name
                    manifest['scenes'][identity] = {'run': run.name, 'sceneId': attempt['id'],
                        'roomType': attempt['roomType'], 'seed': attempt['seed'], 'artifact': receipt}
                    manifest['evictedBytes'] += freed
                    write_json(manifest_file, manifest)
                    manifest_key = destination + 'blender-scenes.json'
                    client.put_object(Bucket=bucket, Key=manifest_key, Body=packed(manifest),
                        ContentType='application/json', CacheControl='no-cache', ServerSideEncryption='AES256')
                    merge_index(client, bucket, [manifest_key] + [row['artifact']['key'] for row in manifest['scenes'].values()])
                    print(json.dumps({'scene': identity, 'archived': len(manifest['scenes']),
                                      'freedGiB': round(manifest['evictedBytes'] / 1024**3, 3)}), flush=True)


if __name__ == '__main__': main()
