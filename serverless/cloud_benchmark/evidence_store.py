"""S3-backed attempt evidence and allowlisted, incremental public room geometry.

Exact worker artifacts contain private execution logs and paths. Preserve those
privately; publish only geometric fields, never the original artifact wholesale.
Local scene payloads are evicted only after immutable uploads and a durable
recovery sidecar, so restarting a controller cannot lose its scientific evidence.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time

from serverless.benchmark.archive import merge_index, packed
from serverless.benchmark.stream_archive import upload
from serverless.benchmark.stimuli import diagram
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.common.object_counts import (
    OBJECT_COUNT_DEFINITION, OBJECT_COUNT_POLICY, validate_selection, window_extra_count,
)


BUCKET = 'soilie3d-data'


def archive_configuration(day):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('Output directory must be an ISO date')
    return {'bucket': BUCKET, 'publicPrefix': f'files/outputs/{day}/',
            'privatePrefix': 'private/new-benchmark-' + day.replace('-', '') + '/soilie/'}


def _configuration(plan):
    config = plan['configuration'].get('outputArchive')
    if config is None:
        return None
    prefix = config.get('publicPrefix', '')
    match = re.fullmatch(r'files/outputs/(\d{4}-\d{2}-\d{2})/', prefix)
    if not match or config != archive_configuration(match[1]):
        raise ValueError('Unexpected output archive configuration')
    return config


def public_prefix(plan, scene_id):
    """Keep distinct same-date authoritative plans in distinct public paths."""
    config = _configuration(plan)
    if config is None:
        raise ValueError('No frozen public output destination')
    _identifier(scene_id)
    prefix = config['publicPrefix'] + 'soilie/'
    policy = plan['configuration'].get('publicRunNamespace')
    if policy is not None:
        if policy != 'plan-sha256-v1' or not re.fullmatch(r'[a-f0-9]{64}', plan['planSha256']):
            raise ValueError('Unexpected public run namespace policy or plan identity')
        prefix += plan['planSha256'] + '/'
    return prefix + scene_id + '/'


def _client(plan):
    import boto3
    config = plan['configuration']
    return boto3.Session(profile_name=config.get('profile')).client(
        's3', region_name=config.get('region', 'ca-central-1'))


def _path(root, attempt):
    root = Path(root).resolve()
    path = (root / attempt['artifact']).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Attempt artifact escaped its campaign')
    return path


def _private_key(plan, attempt):
    config = _configuration(plan)
    if config is None:
        raise ValueError('No frozen S3 evidence destination')
    for value, pattern in ((plan['planSha256'], r'[a-f0-9]{64}'),
                           (attempt['requestId'], r'[a-z0-9_-]+'),
                           (attempt['sha256'], r'[a-f0-9]{64}')):
        if not re.fullmatch(pattern, value):
            raise ValueError('Unsafe evidence identity')
    number = attempt['attempt']
    if type(number) is not int or number < 1:
        raise ValueError('Invalid attempt number')
    return (config['privatePrefix'] + plan['planSha256'] + '/' + attempt['requestId']
            + f'/attempt-{number:05d}/' + attempt['sha256'] + '.json')


def load_artifact(root, attempt, plan, *, client=None):
    """Read exact original bytes locally or from their verified private receipt."""
    path = _path(root, attempt)
    if path.exists():
        body = path.read_bytes()
    else:
        sidecar = path.with_name(path.name + '.remote.json')
        document = json.loads(sidecar.read_bytes())
        receipt = document['privateArtifact']
        config = _configuration(plan)
        if (config is None or receipt['bucket'] != config['bucket']
                or receipt['key'] != _private_key(plan, attempt)
                or receipt['sha256'] != attempt['sha256']):
            raise ValueError('Remote evidence receipt differs from the frozen attempt')
        response = (client or _client(plan)).get_object(Bucket=receipt['bucket'], Key=receipt['key'])
        stream = response['Body']
        try:
            body = stream.read()
        finally:
            stream.close()
        if len(body) != receipt['bytes']:
            raise ValueError('Archived attempt evidence size changed')
    if hashlib.sha256(body).hexdigest() != attempt['sha256']:
        raise ValueError('Retained attempt evidence changed: checksum mismatch')
    return json.loads(body)


def recover_artifact_identity(root, attempt, plan, path):
    """Recover the eviction/ledger crash boundary from its durable sidecar.

    A controller may die after upload/eviction but before its ledger replace.
    Bind recovery to the already-dispatched request, attempt number, exact
    expected artifact filename and frozen private namespace; never invoke again.
    The caller must still validate the recovered original worker artifact.
    """
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Recovered attempt escaped its campaign')
    sidecar = path.with_name(path.name + '.remote.json')
    config = _configuration(plan)
    if config is None or not sidecar.exists():
        return False
    receipt = json.loads(sidecar.read_bytes())['privateArtifact']
    candidate = {**attempt, 'artifact': path.relative_to(root).as_posix(), 'sha256': receipt['sha256']}
    if receipt['bucket'] != config['bucket'] or receipt['key'] != _private_key(plan, candidate):
        raise ValueError('Recovery sidecar belongs to a different frozen attempt')
    attempt.update(artifact=candidate['artifact'], sha256=candidate['sha256'])
    return True


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value):
        raise ValueError('Public geometry identifiers must be simple names, never paths')
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Public geometry requires finite numeric observations')
    return value


def _vector(value, count):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError('Unexpected geometry vector shape')
    return [_number(number) for number in value]


def public_room(row, attempt, plan):
    """Use a positive field allowlist, rather than trying to redact raw logs."""
    if (plan['configuration'].get('objectCountDefinition') != OBJECT_COUNT_DEFINITION
            or plan['configuration'].get('objectCountPolicy') != OBJECT_COUNT_POLICY):
        raise ValueError('Do not republish a legacy cohort under the non-window count definition')
    stage = row['stages']['final']
    objects = []
    for item in stage['objects']:
        if item['kind'] not in ('furniture', 'architecture') or len(item['corners']) != 8:
            raise ValueError('Unexpected public geometry object')
        obj = {key: _identifier(item[key]) for key in ('id', 'label')}
        obj.update(kind=item['kind'], corners=[_vector(point, 3) for point in item['corners']])
        for key in ('assemblyId', 'asset'):
            if item.get(key) is not None:
                obj[key] = _identifier(item[key])
        if 'transform' in item:
            if not isinstance(item['transform'], list) or len(item['transform']) != 4:
                raise ValueError('Unexpected geometry transform shape')
            obj['transform'] = [_vector(vector, 4) for vector in item['transform']]
        if item.get('frontConvention') != 'SOILIE-3D asset-corrected local +X':
            raise ValueError('Unexpected public front convention')
        obj.update(frontDirection=_vector(item['frontDirection'], 2),
                   frontConvention='SOILIE-3D asset-corrected local +X')
        objects.append(obj)
    if len({obj['id'] for obj in objects}) != len(objects):
        raise ValueError('Duplicate public object identities')
    polygon = stage['room']['polygon']
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError('Room requires its actual boundary polygon')
    room = {'polygon': [_vector(point, 2) for point in polygon],
            'floorZ': _number(stage['room']['floorZ'])}
    identity = 'soilie-' + attempt['requestId']
    task = next((task for task in plan['requests'] if task['id'] == attempt['requestId']), None)
    if task is None:
        raise ValueError('Public artifact does not belong to the frozen request manifest')
    if task['platform'] not in ('local', 'lambda') or attempt['request']['roomType'] not in ('bedroom', 'living_room'):
        raise ValueError('Unexpected public generation condition')
    selection = row['selection']
    counts = validate_selection(attempt['request'], selection)
    if any(type(row.get(key)) is not type(value) or row.get(key) != value for key, value in counts.items()):
        raise ValueError('Published count metadata differs from the complete selected inventory')
    selection = [_identifier(label) for label in selection]
    rendered_windows = window_extra_count(obj['label'].lower().split('.', 1)[0] for obj in objects)
    companion_windows = rendered_windows - counts['selectedWindowCount']
    if (companion_windows < 0 or type(row.get('companionWindowCount')) is not int
            or type(row.get('renderedWindowCount')) is not int
            or row.get('companionWindowCount') != companion_windows
            or row.get('renderedWindowCount') != rendered_windows):
        raise ValueError('Published selected/companion window counts differ from final geometry')
    started = datetime.fromisoformat(row['startedAt'].replace('Z', '+00:00'))
    if started.tzinfo is None:
        raise ValueError('Generation timestamp must include its original timezone')
    version = row['implementation']['modelVersion']
    commit = row['implementation']['sourceCommit']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Unexpected public model identity')
    scene = {'schemaVersion': 2, 'id': identity, 'model': 'soilie',
             'roomType': attempt['request']['roomType'], 'units': 'm',
             'stage': 'final', 'room': room, 'objects': objects}
    public = {'schemaVersion': 1, 'status': 'comparisons-pending', 'scene': scene,
              'generation': {'platform': task['platform'], 'seed': attempt['request']['seed'],
                             **counts,
                             'companionWindowCount': companion_windows,
                             'renderedWindowCount': rendered_windows,
                             'selectedObjects': selection, 'startedAt': started.isoformat(),
                             'generationSeconds': _number(row['generationSeconds']),
                             'modelVersion': version, 'sourceCommit': commit,
                             'planSha256': plan['planSha256']},
              'description': 'Validated final room geometry and a measured-arrangement diagram; '
                             'not a photorealistic render, AI review, or completed comparison.'}
    packed(public)  # Validate nested values, including possible path-like asset fields.
    return public


def archive_attempt(root, attempt, plan, *, client=None):
    """Upload exact evidence and eligible public geometry, then evict that payload.

    Pilots remain private. A final-cohort room is public only after the existing
    terminal artifact validator has accepted it; final aggregate results still
    require full campaign acceptance. Failed attempts are always private.
    """
    config = _configuration(plan)
    if config is None or not attempt.get('artifact'):
        return None
    started = time.monotonic()
    client = client or _client(plan)
    path = _path(root, attempt)
    row = load_artifact(root, attempt, plan, client=client)
    # Resume uses the original remote receipt, never a reserialized substitute.
    if not path.exists():
        receipt = json.loads(path.with_name(path.name + '.remote.json').read_bytes())
        attempt['remoteEvidence'] = {'artifact': receipt['privateArtifact']}
        return receipt
    body = path.read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    if sha != attempt['sha256']:
        raise ValueError('Local attempt changed before archival')
    private = upload(client, config['bucket'], _private_key(plan, attempt), body,
                     'application/json', sha, len(body), cache_control='private,max-age=0,no-store')
    private['bucket'] = config['bucket']
    receipt = {'schemaVersion': 1, 'privateArtifact': private, 'publicArtifacts': []}
    if plan['phase'] == 'authoritative' and row['status'] == 'complete':
        public = public_room(row, attempt, plan)
        scene = public['scene']
        prefix = public_prefix(plan, scene['id'])
        for data, suffix, content_type in ((packed(public), 'json', 'application/json'),
                                           (diagram(scene).encode(), 'svg', 'image/svg+xml')):
            checksum = hashlib.sha256(data).hexdigest()
            key = prefix + checksum[:24] + '.' + suffix
            receipt['publicArtifacts'].append(upload(client, config['bucket'], key, data,
                                                       content_type, checksum, len(data)))
        merge_index(client, config['bucket'], [entry['key'] for entry in receipt['publicArtifacts']])
    sidecar = path.with_name(path.name + '.remote.json')
    write_json(sidecar, receipt)
    # The private upload was read back/verified by upload(), and the recovery
    # sidecar is now durable. Recheck exact local bytes after all network I/O.
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError('Local attempt changed before eviction')
    path.unlink()
    attempt['remoteEvidence'] = {'artifact': private}
    attempt['archiveSeconds'] = time.monotonic() - started
    return receipt
