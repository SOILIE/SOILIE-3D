"""Journaled maintenance-window promotion of audited model data, not an S3 transaction.

The fixed S3 keys remain compatible with the existing hydrator. During a partial
promotion its old checksum manifest must fail closed. The tracked manifest is
installed last; deployed images already contain their own immutable model data.
Prepare reads S3 only. Apply/rollback require the exact frozen plan checksum.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from serverless.benchmark.run_batch import run_lock
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.compiler.build_runtime import MODEL_DATA_FILES
from serverless.compiler.rebuild_sources import core_fingerprint


ROOT = Path(__file__).resolve().parents[2]
BUCKET = 'soilie3d-data'
PRIVATE = 'private/new-benchmark-20261023/'
MANIFEST = 'serverless/runtime-assets.json'
MANUAL = 'data/object_sizes_manual.csv'
CHECKS = {'nativeInputSeals', 'worldFrameRelations', 'calibrationAndDepthScale',
          'canonicalTriplets', 'workingCatalogs3To8', 'colorContributionChain'}
HEADERS = ('ContentType', 'CacheControl', 'ContentDisposition', 'ContentEncoding', 'ContentLanguage', 'Metadata')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def snapshot(path):
    path = Path(path)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError('Runtime targets must be ordinary files, never symlinks')
    return {'bytes': path.stat().st_size, 'sha256': checksum(path)}


def same(left, right):
    return left is None and right is None or bool(left and right and
        left['bytes'] == right['bytes'] and left['sha256'] == right['sha256'])


def owned(path):
    path = Path(path).resolve()
    base = (ROOT / '.codex/fresh-campaign-20261022/promotion').resolve()
    if path == base or not path.is_relative_to(base):
        raise ValueError('Promotion work must be a dedicated child of the project promotion directory')
    return path


def target(relative):
    allowed = {MANIFEST, *('data/' + name for name in MODEL_DATA_FILES)}
    if relative not in allowed:
        raise ValueError('Target is outside the exact runtime-data/manifest allowlist')
    result = ROOT / relative
    if not result.resolve().is_relative_to(ROOT.resolve()) or result.is_symlink():
        raise ValueError('Runtime target escaped the repository')
    return result


def missing(error):
    return getattr(error, 'response', {}).get('Error', {}).get('Code') in ('NoSuchKey', '404', 'NotFound')


def conflict(error):
    return getattr(error, 'response', {}).get('Error', {}).get('Code') in ('PreconditionFailed', '412')


def remote(client, key, *, expected_etag=None, destination=None):
    """Read full original bytes, not an ETag/self-reported metadata digest."""
    options = {'IfMatch': expected_etag} if expected_etag else {}
    try:
        response = client.get_object(Bucket=BUCKET, Key=key, **options)
    except Exception as error:
        if missing(error):
            return None
        raise
    digest, length = hashlib.sha256(), 0
    body = response['Body']
    handle = None
    try:
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            handle = destination.open('wb')
        for block in iter(lambda: body.read(1024 * 1024), b''):
            digest.update(block)
            length += len(block)
            if handle:
                handle.write(block)
        if handle:
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        body.close()
        if handle:
            handle.close()
    if response.get('ContentLength', length) != length:
        raise ValueError('S3 response body was incomplete')
    return {'bytes': length, 'sha256': digest.hexdigest(), 'etag': response['ETag'],
            'headers': {key: response[key] for key in HEADERS if key in response}}


def immutable(client, key, path, expected):
    if not same(snapshot(path), expected):
        raise ValueError('Private staging/backup bytes changed')
    try:
        with path.open('rb') as body:
            client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentLength=expected['bytes'],
                IfNoneMatch='*', ContentType='application/octet-stream', ServerSideEncryption='AES256',
                CacheControl='private,max-age=0,no-store', Metadata={'sha256': expected['sha256']},
                ChecksumSHA256=base64.b64encode(bytes.fromhex(expected['sha256'])).decode())
    except Exception as error:
        if not conflict(error):
            raise
    result = remote(client, key)
    if not same(result, expected):
        raise ValueError('Private immutable upload did not survive full SHA-256 readback')
    return {'bucket': BUCKET, 'key': key, **{name: result[name] for name in ('bytes', 'sha256', 'etag')}}


def validate_audit(candidate, audit_path):
    audit = json.loads(audit_path.read_bytes())
    if (audit.get('schemaVersion') != 1 or audit.get('kind') != 'audited-runtime-data-candidate'
            or audit.get('passed') is not True or audit.get('coreFingerprint') != core_fingerprint()
            or any(audit.get('checks', {}).get(name) is not True for name in CHECKS)):
        raise ValueError('An actual byte-bound numerical/source candidate audit is required')
    wanted = {'data/' + name for name in MODEL_DATA_FILES}
    if set(audit.get('files', {})) != wanted or not audit.get('evidence'):
        raise ValueError('Candidate audit must cover all eight runtime data files and actual evidence')
    for relative in wanted:
        path = (candidate / relative).resolve()
        if not path.is_relative_to(candidate.resolve()) or not same(snapshot(path), audit['files'][relative]):
            raise ValueError('Candidate bytes differ from their audited file hashes: ' + relative)
    for evidence in audit['evidence']:
        path = (ROOT / evidence['path']).resolve()
        if (not path.is_relative_to((ROOT / '.codex').resolve()) or not path.is_file()
                or checksum(path) != evidence['sha256']):
            raise ValueError('Candidate audit evidence changed or escaped the ignored workspace')
    return audit


def manifest_rows(document):
    if document.get('schemaVersion') != 1 or len(document.get('files', [])) != 200:
        raise ValueError('Expected the current 191-mesh runtime manifest')
    rows = {row['path']: row for row in document['files']}
    data = {'data/' + name for name in MODEL_DATA_FILES}
    assets = {name: row for name, row in rows.items() if name.startswith('assets/')}
    if (len(rows) != 200 or len(assets) != 192 or
            len([name for name in assets if name.endswith('.obj')]) != 191 or
            'assets/asset_rotations.csv' not in assets or set(rows) != data | set(assets)):
        raise ValueError('Runtime manifest file set changed')
    for name, row in rows.items():
        if (row.get('sourceKey') != 'files/' + name or type(row.get('bytes')) is not int
                or row['bytes'] < 0 or not re.fullmatch(r'[a-f0-9]{64}', row.get('sha256', ''))):
            raise ValueError('Malformed runtime manifest entry')
    return rows, assets


def verify_static(plan, work=None):
    manifest = target(MANIFEST)
    if not manifest.exists() and work is not None:
        # A crash can leave the last-installed manifest in its owned hold,
        # between its guarded move and the exclusive candidate link.
        held = work / 'holds' / 'apply' / MANIFEST
        if same(snapshot(held), plan['oldManifest']):
            manifest = held
    document = json.loads(manifest.read_bytes())
    _rows, assets = manifest_rows(document)
    if assets != plan['assets']:
        raise ValueError('Static asset manifest entries changed during promotion')
    for relative, expected in assets.items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to((ROOT / 'assets').resolve()) or not same(snapshot(path), expected):
            raise ValueError('Static asset changed: ' + relative)


def prepare(candidate, audit_path, work, client, *, profile='darkest', region='ca-central-1'):
    work = owned(work)
    candidate, audit_path = Path(candidate).resolve(), Path(audit_path).resolve()
    if not candidate.is_relative_to((ROOT / '.codex').resolve()):
        raise ValueError('Candidate must stay in the ignored project workspace')
    audit = validate_audit(candidate, audit_path)
    old = json.loads(target(MANIFEST).read_bytes())
    rows, assets = manifest_rows(old)
    targets = []
    for name in MODEL_DATA_FILES:
        relative = 'data/' + name
        before = remote(client, 'files/' + relative)
        current = snapshot(target(relative))
        after = snapshot(candidate / relative)
        if relative == MANUAL and not all(same(after, value) for value in (current, before, rows[relative])):
            raise ValueError('The manual size table must remain byte-identical locally, remotely, and in the manifest')
        targets.append({'path': relative, 'key': 'files/' + relative,
                        'oldLocal': current, 'oldRemote': before, 'new': after})
    updated = json.loads(json.dumps(old))
    lookup = {row['path']: row for row in updated['files']}
    for row in targets:
        lookup[row['path']].update(row['new'])
    candidate_manifest = (json.dumps(updated, indent=2) + '\n').encode()
    plan = {'schemaVersion': 1, 'createdAt': datetime.now(UTC).isoformat(), 'bucket': BUCKET,
            'candidate': candidate.relative_to(ROOT).as_posix(), 'audit': audit_path.relative_to(ROOT).as_posix(),
            'auditSha256': checksum(audit_path), 'coreFingerprint': audit['coreFingerprint'],
            'profile': profile, 'region': region, 'assets': assets, 'targets': targets,
            'oldManifest': snapshot(target(MANIFEST)), 'newManifest': updated,
            'newManifestBytes': len(candidate_manifest), 'newManifestSha256': hashlib.sha256(candidate_manifest).hexdigest(),
            'consistency': 'Coordinated fixed-key maintenance; S3 updates are not atomic; hydrators must fail closed until manifest installation'}
    plan['planSha256'] = hashlib.sha256(encoded(plan)).hexdigest()
    verify_static(plan)
    with run_lock(work):
        if (work / 'plan.json').exists():
            raise ValueError('A frozen promotion plan already exists; never replace it')
        write_json(work / 'plan.json', plan)
        write_json(work / 'journal.json', {'schemaVersion': 1, 'planSha256': plan['planSha256'],
                   'phase': 'prepared', 'backups': {}, 'staged': {}, 'targets': {}})
    return {'planSha256': plan['planSha256'], 'files': len(targets), 'awsWrites': 0}


def load(work):
    work = owned(work)
    plan = json.loads((work / 'plan.json').read_bytes())
    body = {key: value for key, value in plan.items() if key != 'planSha256'}
    if plan.get('schemaVersion') != 1 or plan.get('bucket') != BUCKET or hashlib.sha256(encoded(body)).hexdigest() != plan.get('planSha256'):
        raise ValueError('Promotion plan checksum/contract changed')
    if {row['path'] for row in plan['targets']} != {'data/' + name for name in MODEL_DATA_FILES}:
        raise ValueError('Promotion file set changed')
    for row in plan['targets']:
        target(row['path'])
        if row['key'] != 'files/' + row['path']:
            raise ValueError('Promotion destination changed')
    journal = json.loads((work / 'journal.json').read_bytes())
    if journal.get('planSha256') != plan['planSha256']:
        raise ValueError('Promotion journal belongs to a different plan')
    return plan, journal


def save(work, journal):
    write_json(work / 'journal.json', journal)


def copy_checked(source, destination, expected):
    if destination.exists():
        if not same(snapshot(destination), expected):
            raise ValueError('Existing promotion staging file changed')
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.writing')
    with source.open('rb') as incoming, temporary.open('wb') as output:
        shutil.copyfileobj(incoming, output, 1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    if not same(snapshot(temporary), expected):
        raise ValueError('Promotion snapshot differs from its frozen source')
    temporary.replace(destination)
    return destination


def local_install(work, relative, expected, desired, payload, token):
    """Preserve displaced bytes before a no-clobber, atomic hard-link install.

    Moving and checking the displaced file avoids the usual hash-check/replace
    race: an intervening edit is retained, never silently overwritten/deleted.
    A missing destination is accepted only if this transaction owns its old hold.
    """
    path = target(relative)
    current = snapshot(path)
    if same(current, desired):
        return
    hold = work / 'holds' / token / relative
    hold.parent.mkdir(parents=True, exist_ok=True)
    if current is not None:
        if not same(current, expected) or hold.exists():
            raise ValueError('Intervening local edit; promotion stopped: ' + relative)
        os.rename(path, hold)
        if not same(snapshot(hold), expected):
            if not path.exists():
                os.link(hold, path)
            raise ValueError('Local target changed during its guarded move; displaced bytes retained')
    elif expected is not None and not same(snapshot(hold), expected):
        raise ValueError('Local target disappeared outside the recorded transaction: ' + relative)
    if desired is None:
        return
    if not same(snapshot(payload), desired):
        raise ValueError('Local replacement payload changed')
    try:
        os.link(payload, path)
    except FileExistsError:
        if not same(snapshot(path), desired):
            raise ValueError('A new local edit appeared during install; it was not overwritten')
    if not same(snapshot(path), desired):
        raise ValueError('Installed local bytes differ from the frozen candidate')


def backup_and_stage(work, plan, journal, client):
    """Finish every verified backup/stage before changing any fixed target."""
    prefix = PRIVATE + 'runtime-backup/' + plan['planSha256'] + '/'
    stage_prefix = PRIVATE + 'runtime-stage/' + plan['planSha256'] + '/'
    for row in [*plan['targets'], {'path': MANIFEST, 'oldLocal': plan['oldManifest']}]:
        relative = row['path']
        for origin, expected in [('local', row.get('oldLocal')), ('remote', row.get('oldRemote'))]:
            identity = origin + '/' + relative
            if expected is None or identity in journal['backups']:
                continue
            cache = work / 'backup-cache' / identity
            if origin == 'local':
                copy_checked(target(relative), cache, expected)
            else:
                observed = remote(client, row['key'], expected_etag=expected['etag'], destination=cache)
                if not same(observed, expected):
                    raise ValueError('Remote target changed before its backup')
            journal['backups'][identity] = immutable(client, prefix + identity + '/' + expected['sha256'], cache, expected)
            save(work, journal)
    for row in plan['targets']:
        relative = row['path']
        stage = copy_checked(ROOT / plan['candidate'] / relative, work / 'staged' / relative, row['new'])
        if relative not in journal['staged']:
            journal['staged'][relative] = immutable(client, stage_prefix + relative + '/' + row['new']['sha256'], stage, row['new'])
            save(work, journal)
    manifest = work / 'staged' / MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(plan['newManifest'], indent=2) + '\n').encode()
    if hashlib.sha256(data).hexdigest() != plan['newManifestSha256']:
        raise ValueError('Typed candidate manifest changed')
    if not manifest.exists():
        manifest.write_bytes(data)
    expected = {'bytes': plan['newManifestBytes'], 'sha256': plan['newManifestSha256']}
    journal['staged'][MANIFEST] = immutable(client, stage_prefix + MANIFEST + '/' + expected['sha256'], manifest, expected)
    for receipt in [*journal['backups'].values(), *journal['staged'].values()]:
        if not same(remote(client, receipt['key']), receipt):
            raise ValueError('Verified backup/staging evidence changed before fixed-key updates')
    journal['phase'] = 'backed-up-and-staged'
    save(work, journal)


def put_fixed(client, key, path, desired, lease, headers):
    if not same(snapshot(path), desired):
        raise ValueError('Replacement payload differs from the frozen target bytes')
    options = {'IfMatch': lease['etag']} if lease else {'IfNoneMatch': '*'}
    with path.open('rb') as body:
        client.put_object(Bucket=BUCKET, Key=key, Body=body, ContentLength=desired['bytes'],
            ServerSideEncryption='AES256', ChecksumSHA256=base64.b64encode(bytes.fromhex(desired['sha256'])).decode(),
            **headers, **options)
    result = remote(client, key)
    if not same(result, desired):
        raise ValueError('Fixed S3 target failed full SHA-256 readback')
    return result


def apply(work, confirmed, client):
    work = owned(work)
    with run_lock(work):
        plan, journal = load(work)
        if confirmed != plan['planSha256'] or journal['phase'] in ('rolling-back', 'rolled-back'):
            raise ValueError('Confirm this exact plan; a rolled-back transaction cannot be reapplied')
        audit_path = ROOT / plan['audit']
        if checksum(audit_path) != plan['auditSha256']:
            raise ValueError('Candidate audit changed')
        validate_audit(ROOT / plan['candidate'], audit_path)
        verify_static(plan, work)
        backup_and_stage(work, plan, journal, client)
        for row in plan['targets']:
            relative = row['path']
            state = journal['targets'].setdefault(relative, {})
            observed = remote(client, row['key'])
            if same(row['oldRemote'], row['new']):
                if not same(observed, row['new']):
                    raise ValueError('An unchanged remote runtime target was edited')
            elif state.get('remoteIntent') and same(observed, row['new']):
                state['remoteNew'] = observed  # Resume a successful PUT/ledger crash, never replay it.
            else:
                old = row['oldRemote']
                if not same(observed, old) or (old and observed['etag'] != old['etag']):
                    raise ValueError('Remote lease changed; stop without overwriting intervening work: ' + relative)
                state['remoteIntent'] = True
                save(work, journal)
                headers = {**(old or {}).get('headers', {}), 'Metadata': {'sha256': row['new']['sha256']}}
                headers.setdefault('ContentType', 'text/csv')
                state['remoteNew'] = put_fixed(client, row['key'], work / 'staged' / relative,
                                                row['new'], observed, headers)
            save(work, journal)
            state['localIntent'] = True
            save(work, journal)
            local_install(work, relative, row['oldLocal'], row['new'], work / 'staged' / relative, 'apply')
            state['localNew'] = True
            save(work, journal)
        for row in plan['targets']:
            if not same(snapshot(target(row['path'])), row['new']) or not same(remote(client, row['key']), row['new']):
                raise ValueError('Local/remote runtime set changed before manifest installation')
        verify_static(plan, work)
        local_install(work, MANIFEST, plan['oldManifest'],
                      {'bytes': plan['newManifestBytes'], 'sha256': plan['newManifestSha256']},
                      work / 'staged' / MANIFEST, 'apply')
        journal['phase'] = 'complete'
        save(work, journal)
        return {'phase': 'complete', 'planSha256': plan['planSha256'], 'manifestInstalledLast': True}


def rollback(work, confirmed, client):
    work = owned(work)
    with run_lock(work):
        plan, journal = load(work)
        if confirmed != plan['planSha256'] or not journal['backups']:
            raise ValueError('Confirm the exact backed-up transaction before rollback')
        journal['phase'] = 'rolling-back'
        save(work, journal)
        # Working caches may be evicted after apply. Recovery is from exact
        # immutable private receipts, never whatever currently occupies a key.
        for row in [*plan['targets'], {'path': MANIFEST, 'oldLocal': plan['oldManifest']}]:
            for origin, expected in [('local', row.get('oldLocal')), ('remote', row.get('oldRemote'))]:
                if expected is None:
                    continue
                identity = origin + '/' + row['path']
                receipt = journal['backups'].get(identity)
                if receipt is None:
                    # A backup failure cannot have authorized target writes.
                    if journal['targets']:
                        raise ValueError('Partial apply has no required private backup receipt')
                    continue
                key = PRIVATE + 'runtime-backup/' + plan['planSha256'] + '/' + identity + '/' + expected['sha256']
                if receipt['bucket'] != BUCKET or receipt['key'] != key or not same(receipt, expected):
                    raise ValueError('Private backup receipt changed')
                cache = work / 'backup-cache' / identity
                if not same(snapshot(cache), expected):
                    observed = remote(client, key, expected_etag=receipt['etag'], destination=cache)
                    if not same(observed, expected):
                        raise ValueError('Private recovery bytes differ from the frozen backup')
        manifest_new = {'bytes': plan['newManifestBytes'], 'sha256': plan['newManifestSha256']}
        local_install(work, MANIFEST, manifest_new, plan['oldManifest'],
                      work / 'backup-cache' / 'local' / MANIFEST, 'rollback')
        for row in reversed(plan['targets']):
            relative = row['path']
            state = journal['targets'].get(relative, {})
            if state.get('remoteIntent'):
                observed = remote(client, row['key'])
                old = row['oldRemote']
                if not same(observed, old):
                    expected = state.get('remoteNew')
                    if not expected and same(observed, row['new']):
                        expected = observed  # Recover PUT-before-ledger using the recorded intent.
                    if not expected or not same(observed, expected) or observed['etag'] != expected['etag']:
                        raise ValueError('Rollback lease changed; intervening remote edit was preserved: ' + relative)
                    if old is None:
                        client.delete_object(Bucket=BUCKET, Key=row['key'], IfMatch=observed['etag'])
                        if remote(client, row['key']) is not None:
                            raise ValueError('Rollback-created target deletion did not settle')
                    else:
                        restored = put_fixed(client, row['key'], work / 'backup-cache' / 'remote' / relative,
                                             old, observed, old.get('headers', {}))
                        if not same(restored, old):
                            raise ValueError('Restored remote target differs from its backup')
                state['remoteRolledBack'] = True
                save(work, journal)
            if state.get('localIntent'):
                local_install(work, relative, row['new'], row['oldLocal'],
                              work / 'backup-cache' / 'local' / relative, 'rollback')
                state['localRolledBack'] = True
                save(work, journal)
        journal['phase'] = 'rolled-back'
        save(work, journal)
        return {'phase': 'rolled-back', 'planSha256': plan['planSha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'apply', 'rollback', 'status'):
        item = commands.add_parser(name)
        item.add_argument('--work', type=Path, required=True)
        if name == 'prepare':
            item.add_argument('--candidate', type=Path, required=True)
            item.add_argument('--audit', type=Path, required=True)
            item.add_argument('--profile', default='darkest')
            item.add_argument('--region', default='ca-central-1')
        elif name != 'status':
            item.add_argument('--confirm-plan', required=True)
    args = parser.parse_args()
    if args.command == 'status':
        plan, journal = load(args.work)
        print(json.dumps({'planSha256': plan['planSha256'], 'phase': journal['phase'], 'targets': journal['targets']}, indent=2))
        return
    import boto3
    if args.command == 'prepare':
        client = boto3.Session(profile_name=args.profile, region_name=args.region).client('s3')
        result = prepare(args.candidate, args.audit, args.work, client, profile=args.profile, region=args.region)
    else:
        plan, _journal = load(args.work)
        client = boto3.Session(profile_name=plan['profile'], region_name=plan['region']).client('s3')
        result = (apply if args.command == 'apply' else rollback)(args.work, args.confirm_plan, client)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
