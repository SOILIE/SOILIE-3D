"""Retire an explicit, verified comparison snapshot into a private S3 prefix.

The public bucket remains public for the Data browser. Explicit private-prefix
denies are installed first. Copies are content verified and checkpointed remotely
before deletion is possible; multipart ETags are never used as content hashes.
Run each phase separately so the website can enter its pending-results state
between the recovery snapshot and removal of the old public objects.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_ROOT = 'files/outputs/'
PRESERVED = PUBLIC_ROOT + '2024-06-27/'
PRIVATE_ROOT = 'private/retired-comparisons/'
CATALOG = 'catalog/files-index.json'


def packed(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':')).encode()


def save(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_bytes(packed(document))
    temporary.replace(path)


def digest_stream(body):
    digest = hashlib.sha256()
    size = 0
    try:
        for chunk in iter(lambda: body.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
    finally:
        body.close()
    return digest.hexdigest(), size


def private_policy(previous, bucket, account):
    """Preserve unrelated policy statements, replacing our two named guards."""
    if not re.fullmatch(r'\d{12}', account):
        raise ValueError('Expected the authenticated owner account')
    document = json.loads(json.dumps(previous))
    identifiers = {'RetiredComparisonOwnerOnly', 'RetiredComparisonNoCloudFront'}
    statements = document.get('Statement', [])
    if isinstance(statements, dict):
        statements = [statements]
    resource = f'arn:aws:s3:::{bucket}/private/*'
    actions = ['s3:GetObject', 's3:GetObjectVersion']
    document['Statement'] = [row for row in statements if row.get('Sid') not in identifiers] + [
        {'Sid': 'RetiredComparisonOwnerOnly', 'Effect': 'Deny', 'Principal': '*',
         'Action': actions, 'Resource': resource,
         'Condition': {'StringNotEquals': {'aws:PrincipalAccount': account}}},
        {'Sid': 'RetiredComparisonNoCloudFront', 'Effect': 'Deny',
         'Principal': {'Service': 'cloudfront.amazonaws.com'},
         'Action': actions, 'Resource': resource}]
    return document


def require_private(client, bucket, account):
    policy = json.loads(client.get_bucket_policy(Bucket=bucket)['Policy'])
    expected = private_policy(policy, bucket, account)
    if packed(policy) != packed(expected):
        raise ValueError('Explicit owner-only and CloudFront private-prefix denies are missing')


def verify(client, bucket, key, checksum, size):
    head = client.head_object(Bucket=bucket, Key=key, ChecksumMode='ENABLED')
    if head['ContentLength'] != size:
        raise ValueError(f'Recovery size mismatch: {key}')
    encoded = base64.b64encode(bytes.fromhex(checksum)).decode()
    # Only a FULL_OBJECT checksum is a content hash. Composite checksums can
    # encode multipart structure and must be verified by reading actual bytes.
    if head.get('ChecksumSHA256') != encoded or head.get('ChecksumType') == 'COMPOSITE':
        actual, actual_size = digest_stream(client.get_object(
            Bucket=bucket, Key=key, IfMatch=head['ETag'])['Body'])
        if (actual, actual_size) != (checksum, size):
            raise ValueError(f'Recovery SHA-256 mismatch: {key}')
    return {'key': key, 'bytes': size, 'sha256': checksum, 'etag': head['ETag']}


def put_verified(client, bucket, key, body, content_type='application/json'):
    checksum = hashlib.sha256(body).hexdigest()
    client.put_object(Bucket=bucket, Key=key, Body=body,
                      ContentType=content_type, CacheControl='private,no-store',
                      ServerSideEncryption='AES256',
                      ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode())
    return verify(client, bucket, key, checksum, len(body))


def prepare(client, sts, bucket, state, archive_prefix):
    if not archive_prefix.startswith(PRIVATE_ROOT) or not re.fullmatch(
            re.escape(PRIVATE_ROOT) + r'[A-Za-z0-9_-]+/', archive_prefix):
        raise ValueError('Use one timestamped retirement prefix')
    state.mkdir(parents=True, exist_ok=True)
    path = state / 'context.json'
    if path.exists():
        context = json.loads(path.read_bytes())
        if (context['bucket'], context['prefix']) != (bucket, archive_prefix):
            raise ValueError('State already belongs to a different retirement')
        require_private(client, bucket, context['account'])
        return context
    account = sts.get_caller_identity()['Account']
    owner = client.get_bucket_acl(Bucket=bucket)['Owner']['ID']
    previous = json.loads(client.get_bucket_policy(Bucket=bucket)['Policy'])
    # Save the exact pre-change policy locally before the first mutation.
    save(state / 'bucket-policy-before.json', previous)
    desired = private_policy(previous, bucket, account)
    client.put_bucket_policy(Bucket=bucket, Policy=json.dumps(desired))
    require_private(client, bucket, account)
    context = {'schemaVersion': 1, 'bucket': bucket, 'prefix': archive_prefix,
               'account': account, 'canonicalBucketOwner': owner,
               'createdAt': datetime.now(UTC).isoformat()}
    put_verified(client, bucket, archive_prefix + 'control/bucket-policy-before.json', packed(previous))
    put_verified(client, bucket, archive_prefix + 'control/context.json', packed(context))
    save(path, context)
    return context


def upload_local(client, context, source, label, state):
    """Snapshot an explicit directory; never follow symlinks or evict here."""
    if not re.fullmatch(r'[a-z0-9_-]+', label):
        raise ValueError('Unsafe snapshot label')
    receipt_path = state / ('local-' + label + '.json')
    manifest_key = context['prefix'] + 'manifests/local-' + label + '.json'
    # Completed labels are immutable. In particular, the website can now have
    # pending-only content without risking replacement of its old recovery copy.
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_bytes())
        for row in receipt['files']:
            verify(client, context['bucket'], row['key'], row['sha256'], row['bytes'])
        remote = receipt['remoteReceipt']
        verify(client, context['bucket'], remote['key'], remote['sha256'], remote['bytes'])
        return {'files': len(receipt['files']), 'bytes': receipt['bytes'], 'receipt': remote}
    try:
        existing = client.get_object(Bucket=context['bucket'], Key=manifest_key)
    except ClientError as error:
        if error.response['Error']['Code'] not in ('NoSuchKey', '404'):
            raise
    else:
        body = existing['Body'].read()
        receipt = json.loads(body)
        for row in receipt['files']:
            verify(client, context['bucket'], row['key'], row['sha256'], row['bytes'])
        remote = verify(client, context['bucket'], manifest_key, hashlib.sha256(body).hexdigest(), len(body))
        save(receipt_path, dict(receipt, remoteReceipt=remote))
        return {'files': len(receipt['files']), 'bytes': receipt['bytes'], 'receipt': remote}
    source = source.absolute()
    if (source.is_symlink() or getattr(source, 'is_junction', lambda: False)() or
            getattr(source.lstat(), 'st_file_attributes', 0) & 1024 or not source.is_dir()):
        raise ValueError('Snapshot source must be a regular directory')
    source = source.resolve(strict=True)
    files = sorted(source.rglob('*'))
    rows = []
    for path in files:
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('Snapshot input contains a link')
        if path.is_dir():
            continue
        relative = path.relative_to(source).as_posix()
        if any(re.search(r'(^|[._-])(credentials?|secrets?|passwords?|tokens?|auth)([._-]|$)',
                         part, re.I) or part in ('.aws', '.ssh', '.env')
               for part in Path(relative).parts):
            raise ValueError(f'Credential-like snapshot path: {relative}')
        with path.open('rb') as body:
            checksum, size = digest_stream(body)
        key = context['prefix'] + 'local/' + label + '/' + relative
        with path.open('rb') as body:
            client.upload_fileobj(body, context['bucket'], key, ExtraArgs={
                'ServerSideEncryption': 'AES256', 'CacheControl': 'private,no-store',
                'ChecksumAlgorithm': 'SHA256'})
        row = verify(client, context['bucket'], key, checksum, size)
        with path.open('rb') as body:
            if digest_stream(body) != (checksum, size):
                raise ValueError('Local input changed during snapshot')
        rows.append(dict(row, path=relative))
    receipt = {'source': str(source), 'label': label, 'files': rows,
               'bytes': sum(row['bytes'] for row in rows)}
    remote = put_verified(client, context['bucket'], manifest_key, packed(receipt))
    save(receipt_path, dict(receipt, remoteReceipt=remote))
    return {'files': len(rows), 'bytes': receipt['bytes'], 'receipt': remote}


def local_inventory(scratch, roots):
    """Select scientific evidence without traversing old runtimes or link caches."""
    from serverless.benchmark.private_archive import contained
    extensions = ('.json', '.jsonl', '.ndjson', '.csv', '.yaml', '.yml', '.txt',
                  '.md', '.log', '.out', '.err', '.blend', '.blend1', '.blend.gz',
                  '.tar.gz', '.zip', '.svg', '.png', '.jpg', '.jpeg', '.webp',
                  '.gif', '.mp4', '.npy', '.npz', '.xlsx', '.xls', '.pdf', '.html',
                  '.obj', '.ply', '.stl', '.mtl', '.exr', '.pkl', '.pickle', '.7z', '.tar')
    cache_names = {'assets', '__pycache__', 'runtime', 'node_modules', '.git',
                   '.codex', '.aws', '.ssh', 'site-packages', 'dist-packages',
                   'venv', '.venv', 'env', 'envs', 'environments', 'bin', 'lib',
                   'lib64', 'scripts', 'include', 'share', 'src', 'source',
                   'source-checkout', 'source_checkout', 'layoutgpt-source', 'infinigen-source',
                   'local storage', 'session storage', 'service worker', 'sync data',
                   'indexeddb', 'extension state', 'sessions', 'localstorage',
                   'sessionstorage', 'network', 'code cache', 'gpucache', 'dawncache',
                   'grshadercache', 'shadercache', 'crashpad', 'cache', 'caches', '.cache',
                   'user-data-dir', 'user_data_dir', 'userdata', 'user-data', 'user_data',
                   'browsercontext', 'browser-context', 'browser_context', 'browsercontexts',
                   'browserprofiles', 'browser-profiles', 'browser-profile', 'browser_profile'}
    sensitive = re.compile(r'(^|[._-])(credentials?|secrets?|passwords?|tokens?|auth)([._-]|$)', re.I)
    rows, candidates, excluded, seen = [], [], [], set()

    def is_link(path):
        stat = path.lstat()
        # On Windows all links/junctions are reparse points. One lstat avoids
        # repeated network/filesystem metadata walks for every ancestor/file.
        return bool(getattr(stat, 'st_file_attributes', 0) & 1024) if os.name == 'nt' else path.is_symlink()

    def is_checkout(path):
        return any((path / marker).exists() for marker in ('.git', 'pyproject.toml', 'setup.py', 'package.json'))

    def is_browser_profile(path):
        # Browser profile directory names are arbitrary. Recognize their own
        # markers instead of relying only on Chrome/Playwright naming patterns.
        return ((path / 'Local State').is_file() or
                ((path / 'Preferences').is_file() and any(
                    (path / marker).exists() for marker in
                    ('History', 'Login Data', 'Cookies', 'Network'))))

    for root in roots:
        candidate = scratch / root
        if is_link(candidate):
            raise ValueError('Local evidence root must not be a link')
        source = contained(candidate, scratch)
        if not source.is_dir():
            raise ValueError('Local evidence roots must be explicit existing directories')
        if is_checkout(source) or is_browser_profile(source):
            excluded.append({'path': source.relative_to(scratch).as_posix(), 'reason': 'source checkout/browser root'})
            continue
        for folder, dirs, files in os.walk(source, followlinks=False):
            keep = []
            for name in sorted(dirs):
                path = Path(folder) / name
                lower = name.lower()
                if (lower in cache_names or lower.startswith('work-') or
                        re.search(r'^(?:chrome|chromium|edge|firefox)[-_ ]?profile', lower) or
                        re.search(r'playwright.*(?:profile|user.?data|context|cache)', lower) or
                        sensitive.search(name) or is_link(path) or is_checkout(path) or
                        is_browser_profile(path)):
                    excluded.append({'path': path.relative_to(scratch).as_posix(), 'reason': 'link/cache/credential directory'})
                else:
                    keep.append(name)
            dirs[:] = keep
            for name in sorted(files):
                path = Path(folder) / name
                relative = path.relative_to(scratch).as_posix()
                if relative in seen:
                    continue
                seen.add(relative)
                if (is_link(path) or sensitive.search(name) or name.startswith('id_')
                        or name.startswith('.env') or not name.lower().endswith(extensions)):
                    excluded.append({'path': relative, 'reason': 'link/credential/non-evidence file'})
                    continue
                size = path.stat().st_size
                if size > 4 * 1024 ** 3:
                    raise ValueError('A local evidence file exceeds the safe single-object archive limit')
                # Directory links are excluded above; archive packing and
                # eviction separately revalidate every path's full ancestry.
                candidates.append({'path': relative, 'bytes': size})
                if len(candidates) % 5000 == 0:
                    print(json.dumps({'phase': 'local-select', 'files': len(candidates),
                                      'excluded': len(excluded), 'root': root}), flush=True)

    def hash_one(row):
        with (scratch / row['path']).open('rb') as body:
            checksum, size = digest_stream(body)
        if size != row['bytes']:
            raise ValueError('Local evidence changed during inventory')
        return dict(row, sha256=checksum)

    with ThreadPoolExecutor(max_workers=16) as executor:
        for offset in range(0, len(candidates), 1000):
            rows.extend(executor.map(hash_one, candidates[offset:offset + 1000]))
            print(json.dumps({'phase': 'local-hash', 'files': len(rows), 'total': len(candidates),
                              'bytes': sum(row['bytes'] for row in rows)}), flush=True)
    return {'roots': roots, 'members': sorted(rows, key=lambda row: row['path']), 'excluded': excluded}


def archive_local(client, context, roots, state, evict=False, shard_bytes=1024 ** 3, inventory_only=False):
    """Pack bounded local shards, verify recovery, then evict exact unchanged files."""
    from serverless.benchmark.private_archive import evict as evict_files, pack, sha
    scratch = ROOT / '.codex'
    if any(state.resolve().is_relative_to((scratch / root).resolve()) for root in roots):
        raise ValueError('Recovery state must not be inside any local evidence source')
    manifest_path = state / 'local-evidence-inventory.json'
    manifest_key = context['prefix'] + 'manifests/local-evidence-inventory.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_bytes())
    else:
        # A lost local state file must not turn an already evicted source tree
        # into a new empty inventory that overwrites the recovery manifest.
        try:
            response = client.get_object(Bucket=context['bucket'], Key=manifest_key)
        except ClientError as error:
            if error.response['Error']['Code'] not in ('NoSuchKey', '404'):
                raise
            manifest = local_inventory(scratch, roots)
        else:
            manifest = json.loads(response['Body'].read())
        save(manifest_path, manifest)
    if manifest['roots'] != roots:
        raise ValueError('Local evidence roots are frozen; do not change a resumed inventory')
    if inventory_only:
        totals = {root: {'files': 0, 'bytes': 0} for root in roots}
        for row in manifest['members']:
            for root in roots:
                if row['path'].startswith(root.rstrip('/') + '/'):
                    totals[root]['files'] += 1
                    totals[root]['bytes'] += row['bytes']
                    break
        return {'roots': totals, 'excluded': len(manifest['excluded']),
                'files': len(manifest['members']), 'bytes': sum(row['bytes'] for row in manifest['members'])}
    put_verified(client, context['bucket'], manifest_key, packed(manifest))
    # A compact baseline index points to the exact frozen originals without
    # mixing their geometry or timing identities with withdrawn SOILIE results.
    baselines = [row for row in manifest['members'] if re.search(r'(layoutgpt|infinigen)', row['path'], re.I)]
    put_verified(client, context['bucket'], context['prefix'] + 'manifests/local-baseline-index.json', packed({'members': baselines}))
    shards, current, size = [], [], 0
    for row in manifest['members']:
        if current and size + row['bytes'] > shard_bytes:
            shards.append(current)
            current, size = [], 0
        current.append(row)
        size += row['bytes']
    if current:
        shards.append(current)
    receipts = state / 'local-receipts'
    receipts.mkdir(exist_ok=True)
    completed, removed = 0, 0
    for index, rows in enumerate(shards):
        receipt_path = receipts / f'{index:05d}.json'
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            if receipt['members'] != rows:
                raise ValueError('Local shard inventory changed')
            recovery = receipt['recovery']
            verify(client, context['bucket'], recovery['key'], recovery['sha256'], recovery['bytes'])
        else:
            staged = state / f'local-{index:05d}.pending.tar.gz'
            pack(staged, scratch, rows)
            checksum, archive_size = sha(staged), staged.stat().st_size
            key = context['prefix'] + f'local-evidence/shard-{index:05d}-{checksum[:24]}.tar.gz'
            with staged.open('rb') as body:
                client.put_object(Bucket=context['bucket'], Key=key, Body=body,
                    ContentLength=archive_size, ContentType='application/gzip',
                    CacheControl='private,no-store', ServerSideEncryption='AES256',
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode())
            recovery = verify(client, context['bucket'], key, checksum, archive_size)
            receipt = {'members': rows, 'recovery': recovery, 'evictedBytes': 0}
            put_verified(client, context['bucket'], context['prefix'] + f'manifests/local-shard-{index:05d}.json', packed(receipt))
            save(receipt_path, receipt)
            staged.unlink()
        # Entire source-shard validation happens before its first unlink, and
        # each file is checked again immediately before removal. New files stay.
        if evict:
            freed = evict_files(scratch, rows)
            receipt['evictedBytes'] += freed
            removed += freed
            save(receipt_path, receipt)
            put_verified(client, context['bucket'], context['prefix'] + f'manifests/local-shard-{index:05d}.json', packed(receipt))
        completed += len(rows)
        print(json.dumps({'phase': 'local-evidence', 'shard': index + 1, 'shards': len(shards),
                          'files': completed, 'totalFiles': len(manifest['members']),
                          'evictedBytes': removed}), flush=True)
    return {'files': completed, 'sourceBytes': sum(row['bytes'] for row in manifest['members']),
            'evictedBytes': removed, 'baselineFiles': len(baselines)}


def inventory(client, context, state):
    path = state / 'inventory.json'
    if path.exists():
        result = json.loads(path.read_bytes())
        if result['context'] != context:
            raise ValueError('Frozen inventory belongs to another retirement context')
        rows, preserved = result['objects'], result['preserved']
    else:
        rows, preserved = [], []
        for page in client.get_paginator('list_objects_v2').paginate(
                Bucket=context['bucket'], Prefix=PUBLIC_ROOT):
            for obj in page.get('Contents', []):
                row = {'key': obj['Key'], 'bytes': obj['Size'], 'etag': obj['ETag'],
                       'lastModified': obj['LastModified'].isoformat()}
                # Preserve the folder marker as well as the historic demonstrations.
                (preserved if obj['Key'].startswith(PRESERVED) or obj['Key'] == PUBLIC_ROOT
                 else rows).append(row)
        result = {'context': context, 'objects': rows, 'preserved': preserved}
        save(path, result)
    # Repeating this phase finishes a partially uploaded snapshot rather than
    # relisting newer source objects or discarding the original frozen catalog.
    put_verified(client, context['bucket'], context['prefix'] + 'manifests/inventory.json', packed(result))
    catalog_path = state / 'catalog-before.json'
    remote_key = context['prefix'] + 'control/files-index-before'
    if catalog_path.exists():
        catalog_receipt = json.loads(catalog_path.read_bytes())
        try:
            remote = client.get_object(Bucket=context['bucket'], Key=remote_key)
        except ClientError as error:
            if error.response['Error']['Code'] not in ('NoSuchKey', '404'):
                raise
            catalog = client.get_object(Bucket=context['bucket'], Key=CATALOG,
                                        IfMatch=catalog_receipt['etag'])
            body = catalog['Body'].read()
            if hashlib.sha256(body).hexdigest() != catalog_receipt['sha256']:
                raise ValueError('Frozen catalog source differs during snapshot recovery')
            put_verified(client, context['bucket'], remote_key, body, 'application/octet-stream')
        else:
            body = remote['Body'].read()
            verify(client, context['bucket'], remote_key, catalog_receipt['sha256'], len(body))
    else:
        catalog = client.get_object(Bucket=context['bucket'], Key=CATALOG)
        body = catalog['Body'].read()
        save(catalog_path, {'etag': catalog['ETag'], 'encoding': catalog.get('ContentEncoding'),
                            'sha256': hashlib.sha256(body).hexdigest()})
        put_verified(client, context['bucket'], remote_key, body, 'application/octet-stream')
    return {'objects': len(rows), 'bytes': sum(row['bytes'] for row in rows),
            'preservedObjects': len(preserved), 'prefix': context['prefix']}


def copy_one(client, context, row):
    bucket, key = context['bucket'], row['key']
    if not key.startswith(PUBLIC_ROOT) or key.startswith(PRESERVED) or key == PUBLIC_ROOT:
        raise ValueError('Inventory includes a protected or out-of-scope key')
    # An IfMatch read pins the content identity, even in a non-versioned bucket.
    checksum, size = digest_stream(client.get_object(Bucket=bucket, Key=key,
                                                    IfMatch=row['etag'])['Body'])
    if size != row['bytes']:
        raise ValueError('Source size changed since inventory')
    destination = context['prefix'] + 's3/' + key
    client.copy_object(Bucket=bucket, Key=destination,
                       CopySource={'Bucket': bucket, 'Key': key},
                       CopySourceIfMatch=row['etag'], ChecksumAlgorithm='SHA256',
                       ServerSideEncryption='AES256', MetadataDirective='REPLACE',
                       Metadata={'source-sha256': checksum}, CacheControl='private,no-store')
    recovery = verify(client, bucket, destination, checksum, size)
    return dict(row, recovery=recovery)


def copy_all(client, context, state, workers=24):
    document = json.loads((state / 'inventory.json').read_bytes())
    receipt_dir = state / 'copy-receipts'
    receipt_dir.mkdir(exist_ok=True)
    completed = {}
    for path in sorted(receipt_dir.glob('*.json')):
        receipt = json.loads(path.read_bytes())
        # Recovery manifests are immutable after a completed checkpoint.
        verify(client, context['bucket'], receipt['remote']['key'],
               receipt['remote']['sha256'], receipt['remote']['bytes'])
        completed.update((row['key'], row) for row in receipt['rows'])
    pending = [row for row in document['objects'] if row['key'] not in completed]
    batch, started = [], time.monotonic()
    total_bytes = sum(row['bytes'] for row in completed.values())

    def checkpoint():
        nonlocal batch
        if not batch:
            return
        index = len(list(receipt_dir.glob('*.json')))
        remote = put_verified(client, context['bucket'], context['prefix'] +
                              f'manifests/copy-{index:05d}.json', packed({'rows': batch}))
        save(receipt_dir / f'{index:05d}.json', {'rows': batch, 'remote': remote})
        batch = []

    # Keep only a bounded number of pending futures, avoiding 78k Python tasks.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(pending), workers * 8):
            futures = [executor.submit(copy_one, client, context, row)
                       for row in pending[offset:offset + workers * 8]]
            try:
                for future in as_completed(futures):
                    row = future.result()
                    completed[row['key']] = row
                    total_bytes += row['bytes']
                    batch.append(row)
                    if len(batch) >= 250:
                        checkpoint()
                        print(json.dumps({'phase': 'copy', 'completed': len(completed),
                              'total': len(document['objects']), 'verifiedBytes': total_bytes,
                              'elapsedSeconds': round(time.monotonic() - started, 1)}), flush=True)
            finally:
                checkpoint()
            # With 24 workers a bounded window has only 192 objects, below the
            # 250-object checkpoint threshold. Report those completed windows
            # too, rather than leaving a resumed large copy apparently idle.
            print(json.dumps({'phase': 'copy', 'completed': len(completed),
                  'total': len(document['objects']), 'verifiedBytes': total_bytes,
                  'elapsedSeconds': round(time.monotonic() - started, 1)}), flush=True)
    checkpoint()
    return {'completed': len(completed), 'bytes': total_bytes}


def baseline_recovery(client, context, state):
    """Index retained baseline geometry/native sources without adopting old scores."""
    recovery = {}
    for path in sorted((state / 'copy-receipts').glob('*.json')):
        checkpoint = json.loads(path.read_bytes())
        verify(client, context['bucket'], checkpoint['remote']['key'],
               checkpoint['remote']['sha256'], checkpoint['remote']['bytes'])
        recovery.update((row['key'], row['recovery']) for row in checkpoint['rows'])
    manifest_key = PUBLIC_ROOT + 'website-comparisons/manifest.json'
    source = recovery[manifest_key]
    verify(client, context['bucket'], source['key'], source['sha256'], source['bytes'])
    response = client.get_object(Bucket=context['bucket'], Key=source['key'], IfMatch=source['etag'])
    manifest = json.loads(response['Body'].read())

    def attach(value):
        if isinstance(value, dict):
            result = {key: attach(item) for key, item in value.items()}
            if {'key', 'sha256', 'bytes'} <= value.keys():
                stored = recovery[value['key']]
                if (stored['sha256'], stored['bytes']) != (value['sha256'], value['bytes']):
                    raise ValueError('Baseline source manifest differs from the verified recovery content')
                result['privateRecovery'] = stored
            return result
        if isinstance(value, list):
            return [attach(item) for item in value]
        return value

    document = {'schemaVersion': 1, 'sourceManifest': source,
                'purpose': 'Reusable baseline inputs and provenance; old scores and AI judgments are not replacement results.',
                'collections': {}}
    for field in ('quantitativeRooms', 'inventoryRooms', 'supplementalLayoutgpt', 'nativeSourceScenes'):
        rows = manifest.get(field, [])
        if field == 'quantitativeRooms':
            rows = [row for row in rows if row.get('model') in ('layoutgpt', 'infinigen', 'infinigen_controlled')]
        elif field == 'inventoryRooms':
            rows = [row for row in rows if row.get('baseline') in ('layoutgpt', 'infinigen')]
        elif field == 'nativeSourceScenes':
            rows = [row for row in rows if str(row.get('run', '')).startswith(('layoutgpt', 'infinigen'))]
        document['collections'][field] = attach(rows)
    # These frozen packets retain historical execution measurements as inputs;
    # their cost/score summaries cannot be relabeled as the new campaign.
    document['historicalProvenance'] = [context['prefix'] + 'local/website-benchmarks/' + name
        for name in ('cost-measurements.json', 'timing-measurements.json',
                     'publication-inputs.json', 'layoutgpt-scale.json')]
    remote = put_verified(client, context['bucket'], context['prefix'] +
                          'manifests/baseline-recovery.json', packed(document))
    result = {'collections': {field: len(rows) for field, rows in document['collections'].items()},
              'receipt': remote}
    save(state / 'baseline-recovery.json', result)
    return result


def retire(client, context, state, workers=24):
    """Delete exact verified objects only; changed or new keys are never removed."""
    require_private(client, context['bucket'], context['account'])
    inventory_document = json.loads((state / 'inventory.json').read_bytes())
    expected = {row['key']: row for row in inventory_document['objects']}
    verified = {}
    for path in sorted((state / 'copy-receipts').glob('*.json')):
        checkpoint = json.loads(path.read_bytes())
        verify(client, context['bucket'], checkpoint['remote']['key'],
               checkpoint['remote']['sha256'], checkpoint['remote']['bytes'])
        verified.update((row['key'], row) for row in checkpoint['rows'])
    if set(verified) != set(expected):
        raise ValueError('The entire frozen inventory must have verified remote recovery receipts')
    for key, row in verified.items():
        if key == PUBLIC_ROOT or key.startswith(PRESERVED) or not key.startswith(PUBLIC_ROOT):
            raise ValueError('Protected or out-of-scope deletion')
        if (row['etag'], row['bytes']) != (expected[key]['etag'], expected[key]['bytes']):
            raise ValueError('Recovery receipt belongs to a different source snapshot')
        if row['recovery']['key'] != context['prefix'] + 's3/' + key:
            raise ValueError('Recovery receipt points outside the expected private snapshot')
    deleted = set()
    journal = state / 'deleted.jsonl'
    if journal.exists():
        deleted = {json.loads(line)['key'] for line in journal.read_text().splitlines()}
    def delete_one(row):
        key = row['key']
        if key == PUBLIC_ROOT or key.startswith(PRESERVED) or not key.startswith(PUBLIC_ROOT):
            raise ValueError('Protected or out-of-scope deletion')
        recovery = row['recovery']
        # Remote SHA-256 is rechecked immediately before the conditional delete.
        verify(client, context['bucket'], recovery['key'], recovery['sha256'], recovery['bytes'])
        client.delete_object(Bucket=context['bucket'], Key=key, IfMatch=row['etag'])
        return {'key': key, 'sha256': recovery['sha256']}

    pending = [row for key, row in sorted(verified.items()) if key not in deleted]
    with journal.open('a', encoding='utf-8') as output, ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(pending), workers * 8):
            futures = [executor.submit(delete_one, row)
                       for row in pending[offset:offset + workers * 8]]
            for future in as_completed(futures):
                result = future.result()
                output.write(json.dumps(result) + '\n')
                output.flush()
                deleted.add(result['key'])
            # Recovery receipts already exist remotely for every deletion. The
            # separate deletion journal also advances remotely after each batch.
            put_verified(client, context['bucket'], context['prefix'] + 'manifests/deleted.jsonl',
                         journal.read_bytes(), 'application/x-ndjson')
            print(json.dumps({'phase': 'delete', 'completed': len(deleted),
                              'total': len(expected)}), flush=True)
    put_verified(client, context['bucket'], context['prefix'] + 'manifests/deleted.jsonl',
                 journal.read_bytes(), 'application/x-ndjson')
    return {'deleted': len(deleted)}


def rebuild_index(client, context, state):
    """Rebuild from actual remaining keys, using a compare-and-swap catalog write."""
    previous = client.get_object(Bucket=context['bucket'], Key=CATALOG)
    before = json.loads((state / 'catalog-before.json').read_bytes())
    body = previous['Body'].read()
    intent_path = state / 'catalog-index-intent.json'
    intent = json.loads(intent_path.read_bytes()) if intent_path.exists() else None
    if intent and hashlib.sha256(body).hexdigest() == intent['sha256']:
        save(state / 'catalog-after.json', {key: intent[key] for key in ('keys', 'sha256')})
        return {'keys': intent['keys'], 'preservedOutputs': intent['preservedOutputs']}
    if previous['ETag'] != before['etag']:
        raise ValueError('Public catalog changed since retirement began; reconcile before overwriting')
    document = json.loads(gzip.decompress(body) if previous.get('ContentEncoding') == 'gzip' else body)
    if document.get('schemaVersion') != 1 or not isinstance(document.get('keys'), list):
        raise ValueError('Unexpected public catalog schema')
    remaining = [obj['Key'] for page in client.get_paginator('list_objects_v2').paginate(
        Bucket=context['bucket'], Prefix=PUBLIC_ROOT) for obj in page.get('Contents', [])]
    unexpected = [key for key in remaining if key != PUBLIC_ROOT and not key.startswith(PRESERVED)]
    if unexpected:
        raise ValueError('Public retirement has not completed or new results appeared')
    document['keys'] = sorted({key for key in document['keys'] if not key.startswith(PUBLIC_ROOT)} |
                              {key for key in remaining if key != PUBLIC_ROOT})
    encoding = {'ContentEncoding': 'gzip'} if previous.get('ContentEncoding') == 'gzip' else {}
    if intent:
        encoded = base64.b64decode(intent['bodyBase64'])
        if hashlib.sha256(encoded).hexdigest() != intent['sha256']:
            raise ValueError('Stored catalog update intent is corrupt')
    else:
        document['updatedAt'] = datetime.now(UTC).isoformat()
        encoded = gzip.compress(packed(document), mtime=0) if encoding else packed(document)
        intent = {'sha256': hashlib.sha256(encoded).hexdigest(),
                  'bodyBase64': base64.b64encode(encoded).decode(),
                  'keys': len(document['keys']), 'preservedOutputs': len(remaining)}
        save(intent_path, intent)
    client.put_object(Bucket=context['bucket'], Key=CATALOG, Body=encoded,
                      ContentType='application/json', CacheControl='no-cache',
                      ServerSideEncryption='AES256', IfMatch=previous['ETag'], **encoding)
    save(state / 'catalog-after.json', {'keys': len(document['keys']),
                                      'sha256': hashlib.sha256(encoded).hexdigest()})
    return {'keys': len(document['keys']), 'preservedOutputs': len(remaining)}


def audit(client, context, state, origins=()):
    """Prove the public historic snapshot is unchanged and recovery stays private."""
    require_private(client, context['bucket'], context['account'])
    frozen = json.loads((state / 'inventory.json').read_bytes())
    remaining = []
    for page in client.get_paginator('list_objects_v2').paginate(
            Bucket=context['bucket'], Prefix=PUBLIC_ROOT):
        for obj in page.get('Contents', []):
            remaining.append({'key': obj['Key'], 'bytes': obj['Size'], 'etag': obj['ETag'],
                              'lastModified': obj['LastModified'].isoformat()})
    if sorted(remaining, key=lambda row: row['key']) != sorted(frozen['preserved'], key=lambda row: row['key']):
        raise ValueError('Historic outputs changed or unexpected public output keys remain')
    response = client.get_object(Bucket=context['bucket'], Key=CATALOG)
    body = response['Body'].read()
    document = json.loads(gzip.decompress(body) if response.get('ContentEncoding') == 'gzip' else body)
    published_outputs = [key for key in document['keys'] if key.startswith(PUBLIC_ROOT)]
    if (any(not key.startswith(PRESERVED) for key in published_outputs) or
            any(key.startswith('private/') for key in document['keys'])):
        raise ValueError('Catalog still advertises withdrawn or private evidence')
    if set(published_outputs) != {row['key'] for row in remaining if row['key'] != PUBLIC_ROOT}:
        raise ValueError('Public catalog does not match actual historic output objects')
    key = context['prefix'] + 'control/context.json'
    signed = client.get_object(Bucket=context['bucket'], Key=key)['Body'].read()
    if json.loads(signed) != context:
        raise ValueError('Authenticated private recovery context differs')
    unsigned = boto3.client('s3', region_name='ca-central-1', config=Config(signature_version=UNSIGNED))
    probes = {}
    for name, operation in (
            ('anonymousObject', lambda: unsigned.get_object(Bucket=context['bucket'], Key=key)),
            ('anonymousListing', lambda: unsigned.list_objects_v2(Bucket=context['bucket'], Prefix=context['prefix']))):
        try:
            operation()
        except ClientError as error:
            status = error.response['ResponseMetadata']['HTTPStatusCode']
            if status != 403:
                raise ValueError('Private access probe returned an unexpected response') from error
            probes[name] = status
        else:
            raise ValueError('Private evidence is publicly readable/listable')
    for origin in origins:
        if not origin.startswith('https://') or origin.rstrip('/').count('/') != 2:
            raise ValueError('Probe origin must be a plain HTTPS origin')
        try:
            with urlopen(origin.rstrip('/') + '/' + key, timeout=30) as response:
                response.read()
        except HTTPError as error:
            if error.code not in (403, 404):
                raise
            probes[origin] = error.code
        else:
            raise ValueError('A public origin served the private recovery path')
    result = {'preservedObjects': len(remaining),
              'preservedBytes': sum(row['bytes'] for row in remaining),
              'historicListing': 'identical-keys-bytes-etags-last-modified',
              'retiredPublicObjectsRemaining': 0, 'publishedOutputKeys': len(published_outputs),
              'signedRecovery': 'verified', 'privacyProbes': probes}
    put_verified(client, context['bucket'], context['prefix'] + 'manifests/final-audit.json', packed(result))
    save(state / 'final-audit.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'inventory', 'upload-local', 'archive-local', 'copy', 'baselines', 'retire', 'index', 'audit'))
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--bucket', default='soilie3d-data')
    parser.add_argument('--profile', default='darkest')
    parser.add_argument('--prefix')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--label')
    parser.add_argument('--workers', type=int, default=24)
    parser.add_argument('--local-root', action='append', default=[])
    parser.add_argument('--evict', action='store_true')
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--probe-origin', action='append', default=[])
    args = parser.parse_args()
    state = args.state.resolve()
    if not state.is_relative_to(ROOT / '.codex') or state == ROOT / '.codex':
        raise ValueError('Task state must be strictly inside this project .codex directory')
    session = boto3.Session(profile_name=args.profile, region_name='ca-central-1')
    client = session.client('s3', config=Config(max_pool_connections=max(32, args.workers),
                                                retries={'mode': 'adaptive', 'max_attempts': 8}))
    if args.phase == 'prepare':
        if not args.prefix:
            parser.error('prepare requires --prefix')
        result = prepare(client, session.client('sts'), args.bucket, state, args.prefix)
    else:
        context = json.loads((state / 'context.json').read_bytes())
        require_private(client, context['bucket'], context['account'])
        if args.phase == 'upload-local':
            if not args.source or not args.label:
                parser.error('upload-local requires --source and --label')
            result = upload_local(client, context, args.source, args.label, state)
        elif args.phase == 'inventory':
            result = inventory(client, context, state)
        elif args.phase == 'archive-local':
            if not args.local_root:
                parser.error('archive-local requires explicit --local-root arguments')
            result = archive_local(client, context, args.local_root, state, args.evict,
                                   inventory_only=args.inventory_only)
        elif args.phase == 'copy':
            result = copy_all(client, context, state, args.workers)
        elif args.phase == 'baselines':
            result = baseline_recovery(client, context, state)
        elif args.phase == 'retire':
            result = retire(client, context, state, args.workers)
        elif args.phase == 'audit':
            result = audit(client, context, state, args.probe_origin)
        else:
            result = rebuild_index(client, context, state)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
