"""Back up explicit inactive scratch paths, then evict verified files one by one.

Unlike the published comparison archive, this bucket blocks ALL public access.
Do not pass active campaigns or credentials. Receipts retain every member hash
and the versioned S3 recovery pointer. Existing research outputs stay public.
"""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile

import boto3
from botocore.exceptions import ClientError

from serverless.arbutus.archive import HashReader
from serverless.arbutus.worker import save, sha
from serverless.benchmark.stream_archive import verify_remote

ROOT = Path(__file__).resolve().parents[2]
SENSITIVE = re.compile(r'(^|[._-])(secrets?|credentials?|passwords?|tokens?|auth)([._-]|$)', re.I)


def contained(path, scratch):
    scratch = scratch.resolve()
    path = Path(path).absolute()
    # Reject symlinks/junctions in every path component, not only the leaf.
    if not path.is_relative_to(scratch) or path == scratch:
        raise ValueError('Archive targets must be strictly inside project .codex')
    for part in (path, *path.parents):
        if part == scratch:
            break
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Archive target traverses a link')
    if not path.resolve().is_relative_to(scratch):
        raise ValueError('Archive target escaped project scratch')
    return path


def inventory(source, scratch):
    source = contained(source, scratch)
    if not source.exists():
        raise ValueError('Archive source is missing')
    candidates = sorted(source.rglob('*')) if source.is_dir() else [source]
    rows = []
    for path in candidates:
        contained(path, scratch)
        if path.is_dir():
            continue
        name = path.name.lower()
        if (any(SENSITIVE.search(part) or part in ('.ssh', '.aws')
                for part in path.relative_to(scratch).parts)
                or name == '.env' or name.startswith('.env.')
                or name.startswith('id_') or path.suffix.lower() in ('.pem', '.key', '.pfx')):
            raise ValueError(f'Credential-like path is not an archive input: {path.name}')
        if name == 'collection.lock':
            raise ValueError('A collector lock requires inspection; active roots cannot be archived')
        rows.append({'path': path.relative_to(scratch).as_posix(),
                     'bytes': path.stat().st_size, 'sha256': sha(path)})
    if not rows:
        raise ValueError('No source files to archive')
    return rows


def require_private(client, bucket):
    block = client.get_public_access_block(Bucket=bucket)['PublicAccessBlockConfiguration']
    if not all(block.get(k) is True for k in
               ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')):
        raise ValueError('Archive bucket does not block all public access')
    if client.get_bucket_versioning(Bucket=bucket).get('Status') != 'Enabled':
        raise ValueError('Archive bucket must retain versioned recovery copies')


def pack(staged, scratch, rows):
    # Store project-relative paths; no absolute machine paths inside the tar.
    with staged.open('wb') as output, gzip.GzipFile(fileobj=output, mode='wb',
            filename='', mtime=0, compresslevel=1) as compressed, \
            tarfile.open(fileobj=compressed, mode='w|') as archive:
        for row in rows:
            source = contained(scratch / row['path'], scratch)
            header = tarfile.TarInfo(row['path'])
            header.size = row['bytes']
            header.mode = 0o600
            with source.open('rb') as stream:
                reader = HashReader(stream)
                archive.addfile(header, reader)
                if reader.digest.hexdigest() != row['sha256']:
                    raise ValueError('Source changed during archive creation')
    # Check the actual compressed output, not only hashes from input reads.
    expected = {row['path']: row for row in rows}
    with tarfile.open(staged, 'r|gz') as archive:
        for member in archive:
            row = expected.pop(member.name)
            reader = HashReader(archive.extractfile(member))
            while reader.read(1024 ** 2):
                pass
            if member.size != row['bytes'] or reader.digest.hexdigest() != row['sha256']:
                raise ValueError('Staged recovery archive differs from the source')
    if expected:
        raise ValueError('Staged archive omitted files')


def evict(scratch, rows):
    # First validate the entire snapshot. New or modified files are not removed.
    for row in rows:
        path = contained(scratch / row['path'], scratch)
        if path.exists() and (path.stat().st_size != row['bytes'] or sha(path) != row['sha256']):
            raise ValueError('Local source changed; refusing eviction')
    freed = 0
    for row in rows:
        path = contained(scratch / row['path'], scratch)
        if path.exists():
            # Recheck immediately before unlinking, after possible network/disk delays.
            if sha(path) != row['sha256']:
                raise ValueError('Local source changed immediately before eviction')
            path.unlink()
            freed += row['bytes']
    return freed


def archive_path(client, bucket, source, state, scratch, remove=False):
    source, state = contained(source, scratch), contained(state, scratch)
    if state == source or state.is_relative_to(source):
        raise ValueError('Recovery state must not be inside its source')
    require_private(client, bucket)
    state.mkdir(parents=True, exist_ok=True)
    relative = source.relative_to(scratch).as_posix()
    identity = hashlib.sha256(relative.encode()).hexdigest()[:24]
    receipt_path = state / (identity + '.json')
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_bytes())
        if receipt['source'] != relative or receipt['bucket'] != bucket:
            raise ValueError('Recovery receipt belongs to another source')
        rows = receipt['members']
        verify_remote(client, bucket, receipt['key'], receipt['sha256'], receipt['bytes'])
    else:
        rows = inventory(source, scratch)
        staged = state / (identity + '.pending.tar.gz')
        pack(staged, scratch, rows)
        checksum, size = sha(staged), staged.stat().st_size
        key = f'research-checkpoints/{identity}/{checksum}.tar.gz'
        with staged.open('rb') as body:
            try:
                client.put_object(Bucket=bucket, Key=key, Body=body, ContentLength=size,
                    ContentType='application/gzip', CacheControl='private,no-store',
                    ServerSideEncryption='AES256', IfNoneMatch='*',
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode())
            except ClientError as error:
                if error.response['Error']['Code'] not in ('PreconditionFailed', '412'):
                    raise
        verify_remote(client, bucket, key, checksum, size)
        version = client.head_object(Bucket=bucket, Key=key)['VersionId']
        receipt = {'source': relative, 'bucket': bucket, 'key': key, 'versionId': version,
                   'sha256': checksum, 'bytes': size, 'members': rows,
                   'evictedBytes': 0, 'visibility': 'private'}
        # Both the local and remote recovery pointers exist before any eviction.
        receipt_key = key + '.receipt.json'
        body = json.dumps(receipt, indent=2).encode()
        client.put_object(Bucket=bucket, Key=receipt_key, Body=body,
                          ServerSideEncryption='AES256', CacheControl='private,no-store',
                          ContentType='application/json')
        save(receipt_path, receipt)
        staged.unlink()
    if remove:
        receipt['evictedBytes'] += evict(scratch, rows)
        save(receipt_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--state', type=Path, default=ROOT / '.codex/private-archive')
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--profile', default='darkest')
    parser.add_argument('--evict', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'soilie3d-research-private-\d{12}', args.bucket):
        raise ValueError('Use the dedicated private research archive bucket')
    client = boto3.Session(profile_name=args.profile).client('s3', region_name='ca-central-1')
    receipt = archive_path(client, args.bucket, args.source, args.state, ROOT / '.codex', args.evict)
    print(json.dumps({key: receipt[key] for key in
                      ('source', 'key', 'sha256', 'bytes', 'evictedBytes', 'visibility')}), flush=True)


if __name__ == '__main__':
    main()
