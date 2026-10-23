"""Retain each paid extension attempt privately before publishing typed geometry.

The inference runner invokes this synchronously between calls. A failed upload
stops new dispatch; resuming archives the saved terminal response without making
another paid request. Neither prompts nor raw provider bodies enter public keys.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stdout
import gzip
import hashlib
from importlib import metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time

from serverless.benchmark.archive import merge_index, packed
from serverless.benchmark.import_layoutgpt import normalize
from serverless.benchmark.import_layoutgpt_controlled import load_parser
from serverless.benchmark.layoutgpt_inventory_extension import (
    AUTHOR_COMMIT, VARIANT, canonical, scratch_path,
)
from serverless.benchmark.review_annotations import LAYOUTGPT_FRONT, presentation_label
from serverless.benchmark.run_batch import run_lock
from serverless.benchmark.stimuli import diagram
from serverless.benchmark.stream_archive import upload, verify_remote
from serverless.cloud_benchmark.checkpoint import write_json

ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / '.codex/layoutgpt-source/utils.py'
PARSER_SHA256 = '5e8ea10b8d0980da40fe311434a9475f6f605e51b2d5c3cab6e8f87c0f9b5e56'
BUCKET = 'soilie3d-data'
PUBLIC_PREFIX = 'files/outputs/2026-10-23/layoutgpt/'
PRIVATE_PREFIX = 'private/new-benchmark-20261023/layoutgpt/'
TERMINAL = {'complete', 'error', 'uncertain'}
IMPLEMENTATION_FILES = (
    'serverless/benchmark/layoutgpt_inventory_extension.py',
    'serverless/benchmark/layoutgpt_extension_store.py',
    'serverless/benchmark/run_layoutgpt_controlled.mjs',
    'serverless/benchmark/import_layoutgpt_controlled.py',
    'serverless/benchmark/import_layoutgpt.py',
    'serverless/benchmark/geometry.py',
    'serverless/benchmark/review_annotations.py',
    'serverless/benchmark/inventory_matching.py',
    'serverless/benchmark/stimuli.py',
    'serverless/benchmark/archive.py',
    'serverless/benchmark/stream_archive.py',
    'serverless/benchmark/run_batch.py',
    'serverless/cloud_benchmark/checkpoint.py',
    'serverless/cloud_benchmark/authoritative.py',
    'serverless/cloud_benchmark/evidence_store.py',
    '.codex/layoutgpt-source/run_layoutgpt_3d.py',
    '.codex/layoutgpt-source/utils.py',
)
DEPENDENCIES = ('numpy', 'scipy', 'shapely', 'Pillow', 'tiktoken', 'boto3', 'botocore')


def configuration():
    return {'schemaVersion': 1, 'bucket': BUCKET, 'publicPrefix': PUBLIC_PREFIX,
            'privatePrefix': PRIVATE_PREFIX, 'parserSha256': PARSER_SHA256}


def sha(body):
    return hashlib.sha256(body).hexdigest()


def runtime_snapshot(node_path=None):
    """Pin the actual inference/normalization host, not only prompt text."""
    node = Path(node_path or shutil.which('node') or '').resolve()
    if not node.is_file():
        raise ValueError('An explicit installed Node executable is required')
    node_info = json.loads(subprocess.run([str(node), '-p',
        'JSON.stringify({version:process.version,platform:process.platform,architecture:process.arch})'],
        check=True, capture_output=True, text=True).stdout)
    return {'schemaVersion': 1,
            'files': {name: sha((ROOT / name).read_bytes()) for name in IMPLEMENTATION_FILES},
            'python': {'version': platform.python_version(), 'executableSha256': sha(Path(sys.executable).read_bytes()),
                       'system': platform.system(), 'architecture': platform.machine()},
            'dependencies': {name: metadata.version(name) for name in DEPENDENCIES},
            'node': {**node_info, 'executable': str(node), 'executableSha256': sha(node.read_bytes())}}


def validate_runtime(root, node_path=None):
    plan = json.loads((scratch_path(root) / 'requests.json').read_bytes())
    frozen = plan.get('runtimeSnapshot')
    if (not isinstance(frozen, dict) or plan.get('runtimeSnapshotSha256') != sha(canonical(frozen).encode())
            or runtime_snapshot(node_path or frozen['node']['executable']) != frozen):
        raise ValueError('Frozen Python, Node, dependencies or normalization/storage implementation changed')
    if plan.get('inventoryPolicy') == 'fresh-soilie-first-scheduled-local-v1':
        from serverless.benchmark.layoutgpt_inventory_extension import validate_source_binding_receipt
        validate_source_binding_receipt(root, plan)
    return frozen


def _client(profile):
    import boto3
    return boto3.Session(profile_name=profile).client('s3', region_name='ca-central-1')


def _inputs(root, task_id):
    root = scratch_path(root)
    plan_raw = (root / 'requests.json').read_bytes()
    plan = json.loads(plan_raw)
    if plan.get('variant') != VARIANT or plan.get('outputArchive') != configuration():
        raise ValueError('Only the frozen, storage-enabled inventory extension is accepted')
    ledger = json.loads((root / 'inference-ledger.json').read_bytes())
    if ledger['planSha256'] != sha(plan_raw) or ledger['budgetUsd'] != plan['budgetUsd']:
        raise ValueError('Inference plan or single spending ledger changed')
    matches = [row for row in plan['requests'] if row['id'] == task_id]
    if len(matches) != 1 or not re.fullmatch(r'layoutgpt-[a-z0-9-]+-(bedroom|living_room)-[78]-\d{3}', task_id):
        raise ValueError('Unknown immutable extension task')
    entry = ledger['entries'][task_id]
    if entry.get('status') not in TERMINAL or entry.get('previousAttempts'):
        raise ValueError('Archive terminal single-attempt tasks only')
    return root, plan_raw, plan, matches[0], entry


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Public geometry requires finite numbers')
    return value


def _vector(value, width):
    if not isinstance(value, list) or len(value) != width:
        raise ValueError('Invalid public coordinate shape')
    return [_number(item) for item in value]


def public_scene(raw, request, plan, response_sha):
    """Rebuild all public leaves; never copy arbitrary nested provenance."""
    if (raw.get('model') != 'layoutgpt' or raw.get('roomType') != request['roomType']
            or raw.get('units') != 'px' or request['roomType'] not in ('bedroom', 'living_room')
            or plan['sourceCommit'] != AUTHOR_COMMIT or not re.fullmatch('[a-f0-9]{64}', response_sha)):
        raise ValueError('Unexpected native layout identity')
    # Labels are checked against this task's frozen vocabulary, rather than a
    # permissive string regex that could carry a path/provider field publicly.
    inventory = request['nativeInventory']
    if (any(not re.fullmatch('[a-z][a-z_ ]{0,63}', name) for name in inventory)
            or Counter(item['label'] for item in raw['objects']) != Counter(inventory)):
        raise ValueError('Native response differs from the requested inventory')
    polygon = raw['room']['polygon']
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise ValueError('Invalid native room boundary')
    scene = {'schemaVersion': 1, 'id': request['id'], 'model': 'layoutgpt',
             'roomType': request['roomType'], 'units': 'px', 'stage': 'api-final-layout',
             'room': {'polygon': [_vector(point, 2) for point in polygon],
                      'floorZ': _number(raw['room']['floorZ'])}, 'objects': [],
             'provenance': {'commit': AUTHOR_COMMIT, 'inferenceRerun': True,
                            'responseSha256': response_sha, 'requestSha256': request['requestSha256'],
                            'model': 'gpt-4-0613', 'requestedObjects': request['requestedObjects'],
                            'benchmarkVariant': VARIANT}}
    if not re.fullmatch('[a-f0-9]{64}', request['requestSha256']):
        raise ValueError('Invalid request content hash')
    for index, item in enumerate(raw['objects']):
        if item.get('kind') != 'furniture' or item.get('frontConvention') != LAYOUTGPT_FRONT:
            raise ValueError('Unknown furniture/front convention')
        corners = item['corners']
        if not isinstance(corners, list) or len(corners) != 8:
            raise ValueError('Expected eight world-space envelope corners')
        scene['objects'].append({'id': f'object-{index:03d}', 'label': item['label'],
                                 'kind': 'furniture', 'corners': [_vector(point, 3) for point in corners],
                                 'frontDirection': _vector(item['frontDirection'], 2),
                                 'frontConvention': LAYOUTGPT_FRONT})
    from serverless.benchmark.geometry import measure
    measure(scene)  # Structural validity only; comparison metrics are not public yet.
    return scene


def _parse(request, entry, response_raw, plan):
    """Use the pinned native parser without repairing or replacing output."""
    outcome = {'inferenceStatus': entry['status'], 'geometryStatus': 'not-produced',
               'countSatisfied': False, 'inventorySatisfied': False}
    if entry['status'] != 'complete':
        return outcome, None
    if response_raw is None or sha(response_raw) != entry['responseSha256']:
        raise ValueError('Completed response bytes are missing or changed')
    if sha(PARSER.read_bytes()) != PARSER_SHA256:
        raise ValueError('Native parser differs from the pinned official source')
    parser = load_parser(PARSER)
    response = json.loads(response_raw)
    if response.get('model') != 'gpt-4-0613':
        raise ValueError('Response is not the frozen GPT-4 model')
    objects, unparsed, parser_output = [], [], io.StringIO()
    try:
        content = response['choices'][0]['message']['content']
        if not isinstance(content, str):
            raise ValueError('Missing native CSS response')
        for line in content.splitlines():
            if not line.strip():
                continue
            try:
                with redirect_stdout(parser_output):
                    label, box = parser(line, 'px')
                if label is None:
                    unparsed.append(line)
                else:
                    objects.append((label, box))
            except (ValueError, KeyError, TypeError, AssertionError):
                unparsed.append(line)
        outcome.update(parsedObjects=len(objects), unparsedLines=unparsed,
                       parserOutput=parser_output.getvalue(),
                       countSatisfied=len(objects) == request['requestedObjects'])
        counts = Counter(presentation_label(label) for label, _ in objects)
        outcome['inventorySatisfied'] = counts == Counter(request['requestedInventory'])
        raw = normalize({'prompt': request['request']['messages'][-1]['content'], 'object_list': objects},
                        request['roomType'], 0, entry['responseSha256'])
        # The authors' parser may ignore fences/comment lines. Preserve that
        # extraction behavior; unmatched source text is private diagnostics, not
        # a reason to silently repair geometry or reject otherwise exact output.
        if not outcome['countSatisfied'] or not outcome['inventorySatisfied']:
            outcome['geometryStatus'] = 'task-not-satisfied'
            return outcome, None
        scene = public_scene(raw, request, plan, entry['responseSha256'])
        outcome['geometryStatus'] = 'complete'
        return outcome, scene
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        # Raw CSS, native diagnostics and error text remain private evidence.
        outcome.update(geometryStatus='invalid', parseError=type(error).__name__)
        return outcome, None


def _archive_bytes(files):
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for name, body in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
    body = stream.getvalue()
    # Read every member back before claiming exact recovery or evicting bytes.
    with tarfile.open(fileobj=io.BytesIO(body), mode='r:gz') as archive:
        actual = {member.name: archive.extractfile(member).read() for member in archive}
    if actual != files:
        raise ValueError('Private inference evidence did not survive packing')
    return body


def archive_attempt(root, task_id, *, client=None, profile='darkest', node_path=None):
    root = scratch_path(root)
    if not re.fullmatch(r'layoutgpt-[a-z0-9-]+-(bedroom|living_room)-[78]-\d{3}', task_id):
        raise ValueError('Unsafe immutable extension task identity')
    directory = root / 'archive' / task_id
    with run_lock(directory):
        root, plan_raw, plan, request, entry = _inputs(root, task_id)
        validate_runtime(root, node_path)
        client = client or _client(profile)
        plan_sha, entry_sha = sha(plan_raw), sha(canonical(entry).encode())
        receipt_path = directory / 'remote.json'
        response_path = root / 'responses' / (task_id + '.json')
        wire_path = root / 'wire-requests' / (task_id + '.json')
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            expected_key = PRIVATE_PREFIX + plan_sha + '/' + task_id + '/' + receipt['privateArtifact']['sha256'] + '.tar.gz'
            if (receipt['planSha256'] != plan_sha or receipt['entrySha256'] != entry_sha
                    or receipt['privateArtifact']['key'] != expected_key):
                raise ValueError('Recovery receipt differs from the immutable paid attempt')
            for artifact in [receipt['privateArtifact'], *receipt['publicArtifacts']]:
                verify_remote(client, BUCKET, artifact['key'], artifact['sha256'], artifact['bytes'])
            return _evict(receipt, response_path, wire_path)
        response_raw = response_path.read_bytes() if response_path.exists() else None
        if response_raw is not None and sha(response_raw) != entry.get('responseSha256'):
            raise ValueError('Saved provider response changed')
        wire = wire_path.read_bytes()
        if sha(wire) != entry.get('wireRequestSha256') or json.loads(wire) != request['request']:
            raise ValueError('Exact dispatched request bytes differ')
        outcome, scene = _parse(request, entry, response_raw, plan)
        files = {'request-wire.json': wire, 'task.json': canonical(request).encode(),
                 'inference-entry.json': canonical(entry).encode(),
                 'outcome.json': canonical(outcome).encode()}
        if response_raw is not None:
            files['provider-body.json'] = response_raw
        evidence = {'schemaVersion': 1, 'planSha256': plan_sha, 'entrySha256': entry_sha,
                    'files': {name: {'sha256': sha(body), 'bytes': len(body)} for name, body in files.items()},
                    'budgetUsd': plan['budgetUsd'], 'parserSha256': PARSER_SHA256,
                    'runtimeSnapshotSha256': plan['runtimeSnapshotSha256']}
        files['evidence-index.json'] = canonical(evidence).encode()
        private = _archive_bytes(files)
        private_sha = sha(private)
        key = PRIVATE_PREFIX + plan_sha + '/' + task_id + '/' + private_sha + '.tar.gz'
        artifact = upload(client, BUCKET, key, private, 'application/gzip', private_sha, len(private),
                          cache_control='private,max-age=0,no-store')
        receipt = {'schemaVersion': 1, 'planSha256': plan_sha, 'entrySha256': entry_sha,
                   'privateArtifact': artifact, 'publicArtifacts': [],
                   'responseSha256': entry.get('responseSha256'), 'wireRequestSha256': sha(wire),
                   'geometryStatus': outcome['geometryStatus']}
        if scene is not None:
            packet = {'schemaVersion': 1, 'status': 'comparisons-pending', 'scene': scene,
                      'description': 'Native generated layout geometry and neutral diagrams; not mesh realization, a final comparison or an AI review.'}
            for body, extension, content_type in ((packed(packet), 'json', 'application/json'),
                                                   (diagram(scene).encode(), 'svg', 'image/svg+xml')):
                checksum = sha(body)
                receipt['publicArtifacts'].append(upload(client, BUCKET,
                    PUBLIC_PREFIX + task_id + '/' + checksum[:24] + '.' + extension,
                    body, content_type, checksum, len(body)))
            merge_index(client, BUCKET, [row['key'] for row in receipt['publicArtifacts']])
        # Durable exact-byte recovery comes before eviction, including failed
        # requests. A crash here repeats storage verification, never inference.
        write_json(receipt_path, receipt)
        return _evict(receipt, response_path, wire_path)


def _evict(receipt, response_path, wire_path):
    freed = 0
    for path, checksum in ((response_path, receipt['responseSha256']),
                           (wire_path, receipt['wireRequestSha256'])):
        if path.exists():
            if checksum is None or sha(path.read_bytes()) != checksum:
                raise ValueError('Local evidence changed before eviction')
            freed += path.stat().st_size
            path.unlink()
    return {'id': response_path.stem, 'geometryStatus': receipt['geometryStatus'],
            'publicArtifacts': len(receipt['publicArtifacts']), 'localBytesEvicted': freed}


def load_response(root, task_id, *, client=None, profile='darkest'):
    """Read exact evicted provider bytes without restoring local scene files."""
    root, plan_raw, plan, request, entry = _inputs(root, task_id)
    local = root / 'responses' / (task_id + '.json')
    if local.exists():
        raw = local.read_bytes()
    else:
        receipt = json.loads((root / 'archive' / task_id / 'remote.json').read_bytes())
        artifact = receipt['privateArtifact']
        expected_key = PRIVATE_PREFIX + sha(plan_raw) + '/' + task_id + '/' + artifact['sha256'] + '.tar.gz'
        if (receipt['planSha256'] != sha(plan_raw) or receipt['entrySha256'] != sha(canonical(entry).encode())
                or artifact['key'] != expected_key):
            raise ValueError('Private recovery receipt differs from the paid attempt')
        client = client or _client(profile)
        body = client.get_object(Bucket=BUCKET, Key=artifact['key'])['Body']
        try:
            archive_raw = body.read()
        finally:
            body.close()
        if sha(archive_raw) != artifact['sha256'] or len(archive_raw) != artifact['bytes']:
            raise ValueError('Private inference archive changed')
        with tarfile.open(fileobj=io.BytesIO(archive_raw), mode='r:gz') as archive:
            files = {}
            for member in archive:
                if not member.isfile() or member.name in files:
                    raise ValueError('Unexpected private evidence member')
                files[member.name] = archive.extractfile(member).read()
        index = json.loads(files.pop('evidence-index.json'))
        if (index['planSha256'] != sha(plan_raw) or index['entrySha256'] != receipt['entrySha256']
                or index['files'] != {name: {'sha256': sha(data), 'bytes': len(data)} for name, data in files.items()}):
            raise ValueError('Private recovery index or exact member bytes changed')
        if files['task.json'] != canonical(request).encode() or files['inference-entry.json'] != canonical(entry).encode():
            raise ValueError('Private archive belongs to a different request')
        raw = files['provider-body.json']
    if sha(raw) != entry.get('responseSha256'):
        raise ValueError('Exact recovered response differs from the inference ledger')
    return raw


def _owner_alive(pid):
    if os.name == 'nt':
        # os.kill(pid, 0) is not a portable Windows liveness probe. Inspect a
        # waitable process handle without sending any signal or terminating it.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: no PID
                return False
            raise ValueError('Cannot safely inspect the previous Windows owner')
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 258):
                raise ValueError('Cannot determine previous Windows owner state')
            return result == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def run(root, credential_file, authorized_budget_usd, workers=3, profile='darkest', node_path=None):
    """Single OS-locked owner; recover only a provably dead Node lock owner."""
    root = scratch_path(root)
    with run_lock(root / 'controller'):
        snapshot = validate_runtime(root, node_path)
        lock_path = root / 'inference.lock'
        if lock_path.exists():
            previous = lock_path.read_bytes()
            pid = json.loads(previous).get('pid')
            if type(pid) is not int or pid < 1:
                raise ValueError('Unverifiable inference lock; inspect it before resuming')
            if not _owner_alive(pid):
                if lock_path.read_bytes() != previous:
                    raise ValueError('Inference owner changed during recovery')
                lock_path.unlink()
            else:
                raise ValueError('Prior Node PID is alive or reused; do not race it')
        command = [snapshot['node']['executable'], str(ROOT / 'serverless/benchmark/run_layoutgpt_controlled.mjs'),
                   str(root), str(Path(credential_file).resolve()),
                   '--authorized-budget-usd=' + str(authorized_budget_usd), '--workers=' + str(workers),
                   '--archive-python=' + sys.executable, '--archive-profile=' + profile]
        # Inherit the terminal for compact progress rows, never pass a key in
        # argv or environment files. The existing credential file stays put.
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            raise RuntimeError('Inference controller stopped; no automatic paid retry was made')


def status(root):
    root = scratch_path(root)
    plan_raw = (root / 'requests.json').read_bytes()
    plan = json.loads(plan_raw)
    if plan.get('variant') != VARIANT:
        raise ValueError('Not a seven/eight extension plan')
    path = root / 'inference-ledger.json'
    ledger = json.loads(path.read_bytes()) if path.exists() else {'entries': {}, 'budgetUsd': plan['budgetUsd']}
    if path.exists() and ledger['planSha256'] != sha(plan_raw):
        raise ValueError('Inference ledger does not bind this plan')
    entries = ledger['entries']
    cells = []
    for room in ('bedroom', 'living_room'):
        for count in (7, 8):
            tasks = [row for row in plan['requests'] if row['roomType'] == room and row['requestedObjects'] == count]
            rows = [entries[row['id']] for row in tasks if row['id'] in entries]
            archived = sum((root / 'archive' / row['id'] / 'remote.json').exists() for row in tasks)
            cells.append({'roomType': room, 'objectCount': count, 'total': len(tasks),
                          'terminal': sum(row['status'] in TERMINAL for row in rows),
                          'complete': sum(row['status'] == 'complete' for row in rows),
                          'failedOrUncertain': sum(row['status'] in ('error', 'uncertain') for row in rows),
                          'reserved': sum(row['status'] == 'reserved' for row in rows), 'archived': archived})
    return {'cells': cells, 'accountedUsd': sum(row.get('actualUsd', row['reservedUsd']) for row in entries.values()),
            'budgetUsd': ledger['budgetUsd']}


def render_progress(document, color):
    lines = []
    for index, cell in enumerate(document['cells']):
        done, total = cell['terminal'], cell['total']
        filled = 28 * done // max(total, 1)
        bar = '█' * filled + '░' * (28 - filled)
        if color:
            bar = f'\x1b[{36 if index < 2 else 35}m{bar}\x1b[0m'
        lines.append(f"{cell['roomType']:11s} {cell['objectCount']} items {bar} {done:3}/{total:<3} "
                     f"ok {cell['complete']:3} fail/uncertain {cell['failedOrUncertain']:3} S3 {cell['archived']:3}")
    lines.append(f"GPT-4 accounted/reserved: US${document['accountedUsd']:.4f} / US${document['budgetUsd']:.2f}")
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('archive', 'status', 'watch', 'run', 'validate-runtime'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--task-id')
    parser.add_argument('--profile', default='darkest')
    parser.add_argument('--color', choices=('auto', 'always', 'never'), default='auto')
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--once', action='store_true', help='Render one watch frame and exit')
    parser.add_argument('--credential-file', type=Path)
    parser.add_argument('--authorized-budget-usd', type=float)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--node-path', type=Path)
    args = parser.parse_args()
    if args.command == 'run':
        if args.credential_file is None or args.authorized_budget_usd is None or not 1 <= args.workers <= 3:
            parser.error('run requires credential-file, authorized-budget-usd and 1..3 workers')
        run(args.root, args.credential_file, args.authorized_budget_usd, args.workers, args.profile, args.node_path)
    elif args.command == 'validate-runtime':
        validate_runtime(args.root, args.node_path)
        print(json.dumps({'runtimeVerified': True}))
    elif args.command == 'archive':
        if not args.task_id:
            parser.error('archive requires --task-id')
        print(json.dumps(archive_attempt(args.root, args.task_id, profile=args.profile, node_path=args.node_path)))
    elif args.command == 'status':
        print(json.dumps(status(args.root)))
    else:
        if args.interval < 1 or not math.isfinite(args.interval):
            parser.error('interval must be finite and at least one second')
        color = args.color == 'always' or (args.color == 'auto' and sys.stdout.isatty() and 'NO_COLOR' not in os.environ)
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        try:
            while True:
                if sys.stdout.isatty():
                    print('\x1b[2J\x1b[H', end='')
                print(render_progress(status(args.root), color), flush=True)
                if args.once:
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
