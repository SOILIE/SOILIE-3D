"""Freeze and resume fresh SOILIE cohorts without replacing failed requests.

Each requested scene keeps every attempt. The budget ledger is shared by the
development pilots and final campaign, so starting a new directory cannot reset
paid usage. This module never starts AI review or publishes evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

from serverless.benchmark.run_batch import run_lock, terminate_tree, verify_execution_log
from serverless.benchmark.supervise import command as supervised
from serverless.cloud_benchmark.checkpoint import write_json


ROOT = Path(__file__).resolve().parents[2]
ROOMS = ('bedroom', 'living_room')
PLATFORMS = ('local', 'lambda')
UNCERTAIN = {'dispatched', 'unknown'}
SOURCE_CHECKS = {'coordinateFrames', 'cameraDepthPairing', 'angleSemantics', 'anchorRotation',
                 'workingCombinations', 'exactRequestedCounts', 'gifVisibility', 'observerParity'}
FIXTURE_CHECKS = {'geometryFixtures', 'animationFixtures', 'observerParity'}


def digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def file_digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def allocation(max_count=8, phase='authoritative'):
    """Allocate before generation; all four cells have the same histogram."""
    if max_count not in (7, 8) or phase not in ('pilot', 'authoritative'):
        raise ValueError('Use a pilot/authoritative phase and maximum count seven or eight')
    counts = list(range(3, max_count + 1))
    histogram = ({count: 10 for count in counts} if phase == 'pilot' else
                 {count: 2500 // len(counts) + (index < 2500 % len(counts))
                  for index, count in enumerate(counts)})
    # Round-robin counts and cells prevent a stopped pilot from preferentially
    # finishing one room type. Pilot and final schedules never share a seed.
    schedule = [count for index in range(max(histogram.values()))
                for count in counts if index < histogram[count]]
    seed_base = 1_000_000_000 if phase == 'pilot' else 2_000_000_000
    result = []
    for index, count in enumerate(schedule):
        for platform_name in PLATFORMS:
            for room in ROOMS:
                ordinal = len(result)
                result.append({'id': f'{platform_name}-{room}-{index:04d}',
                               'platform': platform_name,
                               'request': {'mode': 'room_type', 'roomType': room,
                                           'objectCount': count, 'seed': seed_base + 997 * ordinal,
                                           'allowDuplicates': True, 'sameObjectsAcrossScenes': True}})
    return result


def validate_rates(rates, region):
    if (rates.get('schemaVersion') != 1 or rates.get('currency') != 'USD'
            or rates.get('region') != region or rates.get('architecture') != 'x86_64'
            or not str(rates.get('sourceUrl', '')).startswith('https://')
            or not rates.get('retrievedAt')):
        raise ValueError('A dated, sourced USD x86-64 rate card for the selected region is required')
    for field in ('computeGBSecondUSD', 'requestUSD', 'ephemeralGBSecondUSD'):
        value = rates.get(field)
        if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid regional rate: ' + field)
    return rates


def invocation_cost(seconds, rates, memory_mb=4096, ephemeral_mb=1024):
    if type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Finite nonnegative billed duration required')
    return (seconds * (memory_mb / 1024 * rates['computeGBSecondUSD']
                       + max(0, ephemeral_mb - 512) / 1024 * rates['ephemeralGBSecondUSD'])
            + rates['requestUSD'])


def hardware_snapshot():
    cpu = platform.processor()
    memory = None
    if os.name == 'posix':
        cpuinfo = Path('/proc/cpuinfo')
        if cpuinfo.exists():
            cpu = next((line.split(':', 1)[1].strip() for line in cpuinfo.read_text().splitlines()
                        if line.startswith('model name')), cpu)
        meminfo = Path('/proc/meminfo')
        if meminfo.exists():
            memory = next((int(line.split()[1]) * 1024 for line in meminfo.read_text().splitlines()
                           if line.startswith('MemTotal:')), None)
    elif os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ('totalPhysical', 'availablePhysical', 'totalPage', 'availablePage',
                 'totalVirtual', 'availableVirtual', 'availableExtended')]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError('Cannot record local RAM allocation')
        memory = status.totalPhysical
    return {'system': platform.system(), 'release': platform.release(), 'architecture': platform.machine(),
            'cpu': cpu, 'logicalCPUs': os.cpu_count(), 'memoryBytes': memory,
            'availableCPUs': len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count()}


def runtime_snapshot(runtime, provenance_path, blender):
    provenance = read(provenance_path)
    package = read(runtime / 'package.json')
    source_commit = subprocess.run(['git', '-C', str(runtime), 'rev-parse', 'HEAD'],
                                   check=True, capture_output=True, text=True).stdout.strip()
    if source_commit != provenance['baselineCommit'] or package['version'] != provenance['version']:
        raise ValueError('Current repository commit/package version differs from compiled provenance')
    loaded = runtime / 'model-provenance.json'
    if not loaded.exists():
        loaded = runtime / '.codex/runtime/model-provenance.json'
    if file_digest(loaded) != file_digest(provenance_path):
        raise ValueError('The benchmark worker would load different model provenance')
    files = {}
    for relative, entry in provenance['files'].items():
        source = runtime / relative
        if not source.resolve().is_relative_to(runtime.resolve()) or file_digest(source) != entry['sha256']:
            raise ValueError('Runtime differs from compiled provenance: ' + relative)
        files[relative] = entry['sha256']
    # These scripts are outside the model manifest but can change measurements,
    # seed handling, dispatch or timing. A repair requires a new campaign.
    execution = {}
    for folder in ('benchmark', 'common', 'cloud_benchmark'):
        for source in sorted((ROOT / 'serverless' / folder).glob('*.py')):
            execution[source.relative_to(ROOT).as_posix()] = file_digest(source)
    dependencies = {}
    for name in ('numpy', 'pandas', 'scipy', 'shapely', 'Pillow', 'boto3', 'botocore'):
        try:
            dependencies[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            dependencies[name] = None
    return {'provenanceSha256': file_digest(provenance_path), 'version': provenance['version'],
            'sourceCommit': provenance['baselineCommit'], 'files': files,
            'executionFiles': execution, 'blenderSha256': file_digest(blender),
            'packageSha256': file_digest(runtime / 'package.json'), 'pythonVersion': platform.python_version(),
            'dependencies': dependencies, 'localHardware': hardware_snapshot()}


def passed_gate(path, runtime_sha, label, required_checks=()):
    document = read(path)
    if document.get('passed') is not True or document.get('runtimeProvenanceSha256') != runtime_sha:
        raise ValueError(label + ' must pass for the exact compiled model provenance')
    checks = document.get('checks', {})
    if (not isinstance(checks, dict) or not set(required_checks).issubset(checks)
            or any(checks[name] is not True for name in required_checks)):
        raise ValueError(label + ' must explicitly pass every required check: ' + ', '.join(sorted(required_checks)))
    return file_digest(path)


def versioned_function(function):
    base, separator, version = function.rpartition(':')
    if not separator or not base or not re.fullmatch(r'[1-9][0-9]*', version):
        raise ValueError('Use a published numeric Lambda version, such as function-name:42; aliases/$LATEST are mutable')
    return base, version


def process_alive(pid):
    """Probe without sending Windows' destructive emulation of POSIX signals."""
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.windll.kernel32.GetLastError() == 5
    try:
        code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError('Cannot establish worker exit')
        return code.value == 259  # STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def load_plan(root, verify_runtime=True):
    plan = read(root / 'plan.json')
    if plan.get('schemaVersion') != 1 or plan.get('planSha256') != digest({
            key: value for key, value in plan.items() if key != 'planSha256'}):
        raise ValueError('Frozen plan checksum differs')
    if plan['requests'] != allocation(plan['maxObjectCount'], plan['phase']):
        raise ValueError('Frozen requests differ from the fixed allocation')
    config = plan['configuration']
    if verify_runtime and runtime_snapshot(Path(config['runtime']), Path(config['provenance']),
                                          Path(config['blender'])) != config['runtimeSnapshot']:
        raise ValueError('Execution code, data, assets, Python or Blender changed; start a new campaign')
    return plan


def prepare(args):
    versioned_function(args.function)
    args.root.mkdir(parents=True, exist_ok=True)
    rates = validate_rates(read(args.rates), args.region)
    snapshot = runtime_snapshot(args.runtime, args.provenance, args.blender)
    source_audit = passed_gate(args.source_audit, snapshot['provenanceSha256'], 'Source audit', SOURCE_CHECKS)
    configuration = {'runtime': str(args.runtime.resolve()), 'provenance': str(args.provenance.resolve()),
                     'blender': str(args.blender.resolve()), 'runtimeSnapshot': snapshot,
                     'budgetRoot': str(args.budget_root.resolve()), 'rateCard': rates,
                     'function': args.function, 'image': args.image, 'bucket': args.bucket,
                     'region': args.region, 'profile': args.profile,
                     'localWorkers': 6, 'blenderThreads': 1, 'lambdaConcurrency': 16,
                     'memoryMB': 4096, 'timeoutSeconds': 900, 'ephemeralMB': 1024,
                     'budgetUSD': 25.0, 'sourceAuditSha256': source_audit}
    gates = {}
    if args.phase == 'authoritative':
        if not all((args.pilot, args.parity, args.fixtures)):
            raise ValueError('Final freeze requires the completed pilot, parity and geometry/animation fixtures')
        pilot = load_plan(args.pilot)
        if pilot['phase'] != 'pilot' or pilot['maxObjectCount'] != args.max_count:
            raise ValueError('Pilot phase or supported range differs')
        if pilot['configuration'] != configuration:
            raise ValueError('Final runtime, hardware, rates or Lambda differ from the validated pilot')
        summary = campaign_status(args.pilot, pilot)
        if not summary['complete'] or summary['failedAttempts'] or summary['unknownAttempts']:
            raise ValueError('Repair and rerun the pilot before freezing the authoritative campaign')
        for platform_name in PLATFORMS:
            verify_completed(args.pilot, pilot, platform_name, attempt_rows(args.pilot, pilot, platform_name))
        parity = read(args.parity)
        expected = {(room, count) for room in ROOMS for count in range(3, args.max_count + 1)}
        if (parity.get('passed') is not True or parity.get('function') != args.function
                or parity.get('image') != args.image or parity.get('memoryMB') != 4096
                or parity.get('pilotPlanSha256') != pilot['planSha256']
                or {(row['roomType'], row['objectCount']) for row in parity.get('scenes', [])} != expected):
            raise ValueError('Local/Lambda parity must cover each supported count and room type')
        gates = {'pilotPlanSha256': pilot['planSha256'], 'paritySha256': file_digest(args.parity),
                 'fixturesSha256': passed_gate(args.fixtures, snapshot['provenanceSha256'], 'Fixtures', FIXTURE_CHECKS)}
    body = {'schemaVersion': 1, 'phase': args.phase, 'maxObjectCount': args.max_count,
            'configuration': configuration, 'gates': gates, 'requests': allocation(args.max_count, args.phase),
            'timingPolicy': 'Per-scene generation through support settlement; observation excluded. '
                            'Retain billed invocation, active parallel session and calendar times separately.',
            'reviewPolicy': 'No previous AI judgments. Approve and freeze the evaluation protocol before collection.'}
    body['planSha256'] = digest(body)
    path = args.root / 'plan.json'
    if path.exists():
        if read(path) != body:
            raise ValueError('A different plan already exists; never rewrite a frozen campaign')
    else:
        write_json(path, body)
    print(json.dumps({'planSha256': body['planSha256'], 'phase': args.phase,
                      'requests': len(body['requests']), 'maxObjectCount': args.max_count}), flush=True)


def budget_document(plan):
    config = plan['configuration']
    path = Path(config['budgetRoot']) / 'budget.json'
    if path.exists():
        result = read(path)
        if (result.get('rateCard') != config['rateCard'] or result.get('budgetUSD') != 25.0
                or result.get('region') != config['region']):
            raise ValueError('Cumulative budget configuration differs; never reset pilot spending')
        return result
    return {'schemaVersion': 1, 'budgetUSD': 25.0, 'region': config['region'],
            'rateCard': config['rateCard'], 'attempts': []}


def attempt_rows(root, plan, platform_name):
    if platform_name == 'lambda':
        return [row for row in budget_document(plan)['attempts']
                if row['planSha256'] == plan['planSha256'] and row.get('role') != 'parity']
    path = root / 'local-ledger.json'
    if not path.exists():
        return []
    document = read(path)
    if document['planSha256'] != plan['planSha256']:
        raise ValueError('Local ledger belongs to a different plan')
    return document['attempts']


def validate_artifact(row, task, plan):
    if any(row.get('request', {}).get(key) != value for key, value in task['request'].items()):
        raise ValueError('Artifact differs from its frozen request')
    if row.get('status') not in ('complete', 'failed'):
        raise ValueError('Artifact has no terminal status')
    verify_execution_log(row.get('executionLog'))
    expected = plan['configuration']['runtimeSnapshot']
    implementation = row.get('implementation', {})
    if (implementation.get('modelVersion') != expected['version']
            or implementation.get('sourceCommit') != expected['sourceCommit']
            or implementation.get('renderSha256') != expected['files']['modules/render.py']):
        raise ValueError('Artifact implementation differs from the frozen runtime')
    if row['status'] == 'complete':
        if len(row.get('selection', [])) != task['request']['objectCount']:
            raise ValueError('Selected instance count differs from the exact requested count')
        for key in ('generationSeconds', 'wallSeconds', 'observationSeconds'):
            value = row.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('Incomplete timing evidence: ' + key)
        if not math.isclose(row['wallSeconds'] - row['observationSeconds'], row['generationSeconds'], abs_tol=.001):
            raise ValueError('Generation and observation timers do not reconcile')
    return row


def verify_attempt_evidence(root, row):
    for name, sha_name in (('artifact', 'sha256'), ('receipt', 'receiptSha256'), ('log', 'logSha256'),
                          ('invocationIdentity', 'invocationIdentitySha256')):
        if row.get(name):
            if not row.get(sha_name) or file_digest(root / row[name]) != row[sha_name]:
                raise ValueError('Retained attempt evidence changed: ' + row['requestId'])


def verify_completed(root, plan, platform_name, rows):
    tasks = {task['id']: task for task in plan['requests'] if task['platform'] == platform_name}
    complete = set()
    for row in rows:
        if row['requestId'] not in tasks or row['request'] != tasks[row['requestId']]['request']:
            raise ValueError('Attempt ledger contains an unallocated request')
        verify_attempt_evidence(root, row)
        if row['status'] != 'complete':
            continue
        raw_path = root / row['artifact']
        if file_digest(raw_path) != row['sha256']:
            raise ValueError('Completed evidence changed: ' + row['requestId'])
        validate_artifact(read(raw_path), tasks[row['requestId']], plan)
        if row['requestId'] in complete:
            raise ValueError('More than one successful attempt for the same request')
        complete.add(row['requestId'])
    return complete


def pending_tasks(plan, platform_name, rows, retry_ids=()):
    if any(row['status'] in UNCERTAIN for row in rows):
        raise ValueError('Reconcile every uncertain invocation before dispatching another request')
    latest = {row['requestId']: row for row in rows}
    failed = {key for key, row in latest.items() if row['status'] == 'failed'}
    retry_ids = set(retry_ids)
    if retry_ids != failed:
        raise ValueError('Explicitly name every reconciled failed request with --retry-request; never replace its seed')
    return [task for task in plan['requests'] if task['platform'] == platform_name
            and (task['id'] not in latest or task['id'] in retry_ids)]


def spent(budget):
    return sum(row.get('costUSD', row['reservedUSD']) for row in budget['attempts'])


def campaign_status(root, plan=None):
    plan = plan or load_plan(root, verify_runtime=False)
    counts = Counter()
    failures = unknown = attempts = 0
    for platform_name in PLATFORMS:
        rows = attempt_rows(root, plan, platform_name)
        latest = {row['requestId']: row for row in rows}
        failures += sum(row['status'] == 'failed' for row in rows)
        unknown += sum(row['status'] in UNCERTAIN for row in rows)
        attempts += len(rows)
        for task in plan['requests']:
            if task['platform'] == platform_name and latest.get(task['id'], {}).get('status') == 'complete':
                counts[(platform_name, task['request']['roomType'])] += 1
    cells = [{'platform': platform_name, 'roomType': room,
              'complete': counts[(platform_name, room)],
              'target': sum(task['platform'] == platform_name and task['request']['roomType'] == room
                            for task in plan['requests'])} for platform_name in PLATFORMS for room in ROOMS]
    budget = budget_document(plan)
    return {'phase': plan['phase'], 'planSha256': plan['planSha256'], 'cells': cells,
            'complete': all(cell['complete'] == cell['target'] for cell in cells) and not unknown,
            'attempts': attempts, 'failedAttempts': failures, 'unknownAttempts': unknown,
            'costPlusReservedUSD': spent(budget), 'budgetUSD': budget['budgetUSD']}


def next_attempt(root, plan, task, rows):
    number = 1 + sum(row['requestId'] == task['id'] for row in rows)
    relative = Path('attempts') / task['id'] / f'{number:04d}'
    folder = root / relative
    if folder.exists():
        raise ValueError('Unledgered attempt directory exists; reconcile before continuing: ' + str(folder))
    folder.mkdir(parents=True)
    return {'planSha256': plan['planSha256'], 'requestId': task['id'], 'request': task['request'],
            'attempt': number, 'status': 'dispatched', 'startedAt': datetime.now(UTC).isoformat(),
            'directory': relative.as_posix()}


def finish_local(root, plan, attempt, returncode):
    folder = root / attempt['directory']
    log = folder / 'controller.log'
    if log.exists():
        attempt.update(log=log.relative_to(root).as_posix(), logSha256=file_digest(log))
    path = folder / 'attempt-00000.json'
    if not path.exists():
        attempt.update(status='failed', error=f'Worker exited {returncode} without terminal evidence')
        return
    task = {'request': attempt['request']}
    row = validate_artifact(read(path), task, plan)
    attempt.update(status=row['status'], artifact=path.relative_to(root).as_posix(), sha256=file_digest(path))
    if returncode and row['status'] == 'complete':
        raise ValueError('Worker exited unsuccessfully after writing completed evidence')


def local_run(args, plan):
    config = plan['configuration']
    rows = attempt_rows(args.root, plan, 'local')
    verify_completed(args.root, plan, 'local', rows)
    tasks = pending_tasks(plan, 'local', rows, args.retry_request)
    ledger = {'schemaVersion': 1, 'planSha256': plan['planSha256'], 'attempts': rows, 'sessions': []}
    path = args.root / 'local-ledger.json'
    if path.exists():
        ledger['sessions'] = read(path).get('sessions', [])
    session = {'startedAt': datetime.now(UTC).isoformat(), 'workers': 6, 'blenderThreads': 1}
    ledger['sessions'].append(session)
    active, dispatched = {}, 0
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    started = time.monotonic()
    try:
        while tasks or active:
            while (tasks and len(active) < 6 and not stopping and not (args.root / 'STOP').exists()
                   and (not args.max_new_calls or dispatched < args.max_new_calls)):
                task = tasks.pop(0)
                attempt = next_attempt(args.root, plan, task, rows)
                rows.append(attempt)
                folder = args.root / attempt['directory']
                request_plan = folder / 'request-plan.json'
                write_json(request_plan, {'schemaVersion': 1, 'cohort': 'authoritative',
                                          'requests': [task['request']]})
                write_json(path, ledger)  # Persist ownership before process creation.
                command = [sys.executable, '-m', 'serverless.benchmark.run_batch',
                           '--runtime', config['runtime'], '--blender', config['blender'],
                           '--output', str(folder), '--request-plan', str(request_plan),
                           '--target', '1', '--max-attempts', '1', '--timeout', '900',
                           '--blender-threads', '1', '--parallel-workers', '6',
                           '--support', '--solid-mesh-overlap']
                environment = {**os.environ, 'PYTHONHASHSEED': '0', 'OMP_NUM_THREADS': '1',
                               'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'NUMEXPR_NUM_THREADS': '1'}
                handle = (folder / 'controller.log').open('ab')
                child = subprocess.Popen(supervised(command), cwd=ROOT, env=environment,
                                         stdout=handle, stderr=subprocess.STDOUT,
                                         start_new_session=os.name == 'posix')
                attempt['pid'] = child.pid
                write_json(path, ledger)
                active[task['id']] = (child, handle, attempt)
                dispatched += 1
            for identity, (child, handle, attempt) in list(active.items()):
                if child.poll() is None:
                    continue
                handle.close()
                del active[identity]
                try:
                    finish_local(args.root, plan, attempt, child.returncode)
                except Exception as error:
                    attempt.update(status='unknown', error=str(error))
                attempt['finishedAt'] = datetime.now(UTC).isoformat()
                stopping |= attempt['status'] != 'complete'
                write_json(path, ledger)
            if not active:
                break
            time.sleep(.5)
    finally:
        for child, handle, attempt in active.values():
            if child.poll() is None:
                terminate_tree(child)
            handle.close()
            attempt.update(status='unknown', error='Controller interrupted before terminal reconciliation')
        session.update(finishedAt=datetime.now(UTC).isoformat(), activeElapsedSeconds=time.monotonic() - started,
                       dispatchedCalls=dispatched)
        write_json(path, ledger)
    print(json.dumps(campaign_status(args.root, plan)), flush=True)
    if stopping or (args.root / 'STOP').exists():
        raise RuntimeError('Local campaign paused; inspect all retained attempts before resuming')


def verify_lambda(config, session):
    base, version = versioned_function(config['function'])
    configured = session.client('lambda').get_function(FunctionName=config['function'])
    actual = configured['Configuration']
    concurrency = session.client('lambda').get_function_concurrency(FunctionName=base)
    if (configured['Code'].get('ResolvedImageUri') != config['image']
            or actual.get('Version') != version
            or actual.get('MemorySize') != 4096 or actual.get('Timeout') != 900
            or actual.get('EphemeralStorage', {}).get('Size', 512) != 1024
            or actual.get('Architectures') != ['x86_64']
            or concurrency.get('ReservedConcurrentExecutions') != 16):
        raise ValueError('Lambda image, memory, watchdog, storage, architecture or concurrency differs')


def finish_cloud(root, plan, attempt):
    folder = root / attempt['directory']
    seed = str(attempt['request']['seed'])
    identity = saved_invocation_identity(root, plan, attempt)
    receipt = read(folder / (seed + '-receipt.json'))
    attempt.update(receipt=(folder / (seed + '-receipt.json')).relative_to(root).as_posix(),
                   receiptSha256=file_digest(folder / (seed + '-receipt.json')))
    if receipt['request'] != {key: attempt['request'][key] for key in ('seed', 'objectCount', 'roomType')}:
        raise ValueError('Invocation receipt request differs')
    if identity and identity.get('awsRequestId'):
        reported_id = receipt.get('awsRequestId')
        if reported_id != identity['awsRequestId']:
            raise ValueError('Receipt differs from the durable invocation identity')
    billed = receipt.get('billedSeconds')
    cost = invocation_cost(billed, plan['configuration']['rateCard'])
    attempt.update(billedSeconds=billed, costUSD=cost)
    # A terminal service error still incurred a request and billed duration.
    if receipt.get('functionError'):
        attempt.update(status='failed', billedSeconds=billed, costUSD=cost,
                       error=receipt['functionError'], receipt=(folder / (seed + '-receipt.json')).relative_to(root).as_posix())
        return
    path = folder / (seed + '.json')
    raw_sha = file_digest(path)
    if raw_sha != receipt['response']['sha256']:
        raise ValueError('Downloaded scene hash differs from invocation receipt')
    if identity and identity.get('awsRequestId') and not receipt['response']['key'].endswith('/' + identity['awsRequestId'] + '.json'):
        raise ValueError('S3 scene belongs to another invocation')
    row = validate_artifact(read(path), {'request': attempt['request']}, plan)
    attempt.update(status=row['status'], billedSeconds=billed, costUSD=cost,
                   artifact=path.relative_to(root).as_posix(), sha256=raw_sha,
                   key=receipt['response']['key'], receipt=(folder / (seed + '-receipt.json')).relative_to(root).as_posix())


def cloud_run(args, plan):
    import boto3
    from botocore.config import Config
    from serverless.cloud_benchmark.pilot import invoke_and_download
    config = plan['configuration']
    budget = budget_document(plan)
    # The shared budget lock is held for dispatch, waiting and settlement. Two
    # pilot/final controllers cannot spend against the same remaining dollars.
    if any(row['status'] in UNCERTAIN for row in budget['attempts']):
        raise ValueError('Reconcile uncertain invocations in the cumulative budget before dispatch')
    rows = [row for row in budget['attempts']
            if row['planSha256'] == plan['planSha256'] and row.get('role') != 'parity']
    verify_completed(args.root, plan, 'lambda', rows)
    tasks = pending_tasks(plan, 'lambda', rows, args.retry_request)
    session = boto3.Session(profile_name=config['profile'], region_name=config['region'])
    verify_lambda(config, session)
    clients = {'lambda': session.client('lambda', config=Config(read_timeout=930, connect_timeout=15,
                         max_pool_connections=16, retries={'total_max_attempts': 1})),
               's3': session.client('s3', config=Config(max_pool_connections=16))}
    path = Path(config['budgetRoot']) / 'budget.json'
    reserve = invocation_cost(915, config['rateCard'])
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    active, dispatched = {}, 0
    session_receipt = {'planSha256': plan['planSha256'], 'startedAt': datetime.now(UTC).isoformat(),
                       'concurrency': 16}
    budget.setdefault('sessions', []).append(session_receipt)
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            while tasks or active:
                while (tasks and len(active) < 16 and not stopping and not (args.root / 'STOP').exists()
                       and spent(budget) + reserve <= 25
                       and (not args.max_new_calls or dispatched < args.max_new_calls)):
                    task = tasks.pop(0)
                    attempt = next_attempt(args.root, plan, task, rows)
                    attempt.update(reservedUSD=reserve, campaignRoot=str(args.root.resolve()))
                    budget['attempts'].append(attempt)
                    rows.append(attempt)
                    write_json(path, budget)
                    event = {key: task['request'][key] for key in ('seed', 'objectCount', 'roomType')}
                    active[pool.submit(invoke_and_download, None, config['function'], config['bucket'], event,
                                       args.root / attempt['directory'], False, clients)] = attempt
                    dispatched += 1
                if not active:
                    break
                done, _ = wait(active, timeout=2, return_when=FIRST_COMPLETED)
                for future in done:
                    attempt = active.pop(future)
                    try:
                        future.result()
                    except Exception as error:
                        attempt['dispatchError'] = str(error)
                    try:
                        finish_cloud(args.root, plan, attempt)
                    except Exception as error:
                        attempt.update(status='unknown', error=str(error))
                    attempt['finishedAt'] = datetime.now(UTC).isoformat()
                    stopping |= attempt['status'] != 'complete'
                    write_json(path, budget)
    finally:
        session_receipt.update(finishedAt=datetime.now(UTC).isoformat(),
                               activeElapsedSeconds=time.monotonic() - started, dispatchedCalls=dispatched)
        write_json(path, budget)
    status = campaign_status(args.root, plan)
    print(json.dumps(status), flush=True)
    if (stopping or (args.root / 'STOP').exists()
            or (tasks and (not args.max_new_calls or dispatched < args.max_new_calls))):
        raise RuntimeError('Cloud campaign paused at the cumulative budget or a failed/uncertain invocation')


def reconcile(args, plan):
    """Read retained evidence only; missing cloud evidence requires recovery first."""
    platform_name = args.platform
    rows = (attempt_rows(args.root, plan, platform_name) if platform_name == 'local' else
            [row for row in budget_document(plan)['attempts'] if row['planSha256'] == plan['planSha256']])
    for attempt in rows:
        if attempt['status'] not in UNCERTAIN:
            continue
        if platform_name == 'local':
            pid = attempt.get('pid')
            if pid and process_alive(pid):
                raise ValueError('Local worker PID still exists; establish process exit before reconciliation')
            finish_local(args.root, plan, attempt, 0)
        else:
            # recover_response can recover the original S3 body and billed log
            # tail. Never change unknown into a retryable failure by assumption.
            finish_cloud(args.root, plan, attempt)
        attempt['reconciledAt'] = datetime.now(UTC).isoformat()
    if platform_name == 'local':
        path = args.root / 'local-ledger.json'
        ledger = read(path)
        ledger['attempts'] = rows
    else:
        path = Path(plan['configuration']['budgetRoot']) / 'budget.json'
        ledger = budget_document(plan)
        index = {(row['planSha256'], row['requestId'], row['attempt']): row for row in rows}
        ledger['attempts'] = [index.get((row['planSha256'], row['requestId'], row['attempt']), row)
                              for row in ledger['attempts']]
    write_json(path, ledger)
    print(json.dumps(campaign_status(args.root, plan)), flush=True)


def parity_run(args, plan):
    """Paid parity probes use the same cumulative reservation boundary."""
    import boto3
    from botocore.config import Config
    from serverless.cloud_benchmark.pilot import compare_placements, invoke_and_download
    if plan['phase'] != 'pilot':
        raise ValueError('Parity is established on the development pilot before the final freeze')
    config = plan['configuration']
    local_rows = attempt_rows(args.root, plan, 'local')
    complete = verify_completed(args.root, plan, 'local', local_rows)
    local_tasks = [task for task in plan['requests'] if task['platform'] == 'local']
    if len(complete) != len(local_tasks):
        raise ValueError('Complete the local placement pilot before paid parity probes')
    selected = {}
    for task in local_tasks:
        key = (task['request']['roomType'], task['request']['objectCount'])
        selected.setdefault(key, task)
    locals_by_id = {row['requestId']: row for row in local_rows if row['status'] == 'complete'}
    budget = budget_document(plan)
    if any(row['status'] in UNCERTAIN for row in budget['attempts']):
        raise ValueError('Reconcile uncertain invocations before paid parity probes')
    rows = [row for row in budget['attempts']
            if row['planSha256'] == plan['planSha256'] and row.get('role') == 'parity']
    parity_tasks = [{**task, 'id': 'parity-' + task['id'], 'platform': 'lambda'} for task in selected.values()]
    tasks = pending_tasks({'requests': parity_tasks}, 'lambda', rows, args.retry_request)
    session = boto3.Session(profile_name=config['profile'], region_name=config['region'])
    verify_lambda(config, session)
    clients = {'lambda': session.client('lambda', config=Config(read_timeout=930, connect_timeout=15,
                         max_pool_connections=4, retries={'total_max_attempts': 1})),
               's3': session.client('s3', config=Config(max_pool_connections=4))}
    path = Path(config['budgetRoot']) / 'budget.json'
    reserve = invocation_cost(915, config['rateCard'])
    active = {}
    stopping = False
    with ThreadPoolExecutor(max_workers=4) as pool:
        while tasks or active:
            while (tasks and len(active) < 4 and not stopping and not (args.root / 'STOP').exists()
                   and spent(budget) + reserve <= 25):
                task = tasks.pop(0)
                attempt = next_attempt(args.root, plan, task, rows)
                attempt.update(reservedUSD=reserve, campaignRoot=str(args.root.resolve()), role='parity')
                budget['attempts'].append(attempt)
                rows.append(attempt)
                write_json(path, budget)
                event = {key: task['request'][key] for key in ('seed', 'objectCount', 'roomType')}
                active[pool.submit(invoke_and_download, None, config['function'], config['bucket'], event,
                                   args.root / attempt['directory'], False, clients)] = attempt
            if not active:
                break
            done, _ = wait(active, timeout=2, return_when=FIRST_COMPLETED)
            for future in done:
                attempt = active.pop(future)
                try:
                    future.result()
                except Exception as error:
                    attempt['dispatchError'] = str(error)
                try:
                    finish_cloud(args.root, plan, attempt)
                except Exception as error:
                    attempt.update(status='unknown', error=str(error))
                attempt['finishedAt'] = datetime.now(UTC).isoformat()
                stopping |= attempt['status'] != 'complete'
                write_json(path, budget)
    if stopping or tasks or (args.root / 'STOP').exists():
        raise RuntimeError('Parity paused at the shared budget or a failed/uncertain invocation')
    latest = {row['requestId']: row for row in rows}
    results = []
    for task in parity_tasks:
        attempt = latest[task['id']]
        local_entry = locals_by_id[task['id'][len('parity-'):]]
        remote_path = args.root / attempt['artifact']
        if file_digest(remote_path) != attempt['sha256']:
            raise ValueError('Parity artifact hash changed')
        error = compare_placements(read(args.root / local_entry['artifact']), read(remote_path))
        results.append({'roomType': task['request']['roomType'], 'objectCount': task['request']['objectCount'],
                        'seed': task['request']['seed'], 'maxGeometryErrorM': error,
                        'billedSeconds': attempt['billedSeconds'], 'sha256': attempt['sha256']})
    document = {'passed': True, 'pilotPlanSha256': plan['planSha256'], 'function': config['function'],
                'image': config['image'], 'memoryMB': 4096, 'scenes': results}
    output = args.root / 'parity.json'
    if output.exists() and read(output) != document:
        raise ValueError('Existing parity receipt differs; do not overwrite validation evidence')
    write_json(output, document)
    print(json.dumps({'passed': True, 'scenes': len(results), 'receipt': str(output)}), flush=True)


def saved_invocation_identity(root, plan, attempt):
    path = root / attempt['directory'] / (str(attempt['request']['seed']) + '-invocation.json')
    if not path.exists():
        if attempt.get('invocationIdentity'):
            raise ValueError('Durable invocation identity disappeared')
        return None
    sha = file_digest(path)
    if attempt.get('invocationIdentitySha256') not in (None, sha):
        raise ValueError('Durable invocation identity changed')
    identity = read(path)
    config = plan['configuration']
    event = {key: attempt['request'][key] for key in ('seed', 'roomType', 'objectCount')}
    if (identity.get('request') != event or identity.get('function') != config['function']
            or identity.get('bucket') != config['bucket']
            or identity.get('executedVersion') != versioned_function(config['function'])[1]):
        raise ValueError('Durable invocation identity belongs to another frozen request/version')
    attempt.update(invocationIdentity=path.relative_to(root).as_posix(), invocationIdentitySha256=sha)
    return identity


def recovered_evidence(plan, attempt, request_id, storage, logs, service_failure=False, invocation_identity=None):
    """Associate one uncertain call with its original service evidence, never replay it."""
    if str(uuid.UUID(request_id)) != request_id.lower():
        raise ValueError('Use the exact AWS invocation request UUID')
    config = plan['configuration']
    event = {key: attempt['request'][key] for key in ('seed', 'roomType', 'objectCount')}
    identity_bound = False
    if invocation_identity:
        if (invocation_identity.get('request') != event or invocation_identity.get('function') != config['function']
                or invocation_identity.get('bucket') != config['bucket']
                or invocation_identity.get('executedVersion') != versioned_function(config['function'])[1]):
            raise ValueError('Saved invocation identity belongs to another request/version')
        saved_id = invocation_identity.get('awsRequestId')
        if saved_id and saved_id != request_id:
            raise ValueError('Requested recovery UUID differs from the durable invocation identity')
        identity_bound = saved_id == request_id
    function_name = config['function'].split(':function:')[-1].split(':')[0]
    events = [item for page in logs.get_paginator('filter_log_events').paginate(
        logGroupName='/aws/lambda/' + function_name, filterPattern='"' + request_id + '"')
        for item in page.get('events', [])]
    # Only Lambda's service REPORT line can settle billing. A user exception
    # or diagnostic containing the same words is not an execution receipt.
    reports = [item for item in events if re.match(r'^REPORT RequestId:\s*' + re.escape(request_id) + r'\b', item['message'])]
    if len(reports) != 1:
        raise ValueError('Recovery requires one unique CloudWatch billing report for that invocation')
    report = reports[0]
    dispatched_at = datetime.fromisoformat(attempt['startedAt'].replace('Z', '+00:00')).timestamp()
    if report['timestamp'] / 1000 < dispatched_at - 5:
        raise ValueError('CloudWatch invocation predates this dispatch; never attach an older attempt')
    markers = []
    for item in events:
        try:
            marker = json.loads(item['message'])
        except (TypeError, ValueError):
            continue
        if isinstance(marker, dict) and marker.get('stage') == 'placing' and marker.get('awsRequestId') == request_id:
            markers.append((item, marker))
    if markers:
        if len(markers) != 1:
            raise ValueError('Recovery scene marker is ambiguous')
        item, marker = markers[0]
        if (marker.get('request') != event or marker.get('functionVersion') != versioned_function(config['function'])[1]
                or marker.get('scene') != f"soilie-{event['roomType']}-{event['seed']}"
                or not dispatched_at - 5 <= item['timestamp'] / 1000 <= report['timestamp'] / 1000 + 5):
            raise ValueError('CloudWatch invocation marker belongs to another scene/request/version')
        identity_bound = True
    if not identity_bound:
        raise ValueError('Recovery requires a durable execution identity or the same invocation scene marker; a REPORT alone is insufficient')
    billed = re.search(r'Billed Duration:\s*([\d.]+) ms', report['message'])
    memory = re.search(r'Max Memory Used:\s*(\d+) MB', report['message'])
    if not billed:
        raise ValueError('Recovery requires actual billed duration; retain the reservation')
    seconds = float(billed[1]) / 1000
    invocation_cost(seconds, config['rateCard'])  # Reject malformed/nonfinite billing.
    receipt = {'request': event, 'wallSeconds': None, 'billedSeconds': seconds,
               'maxMemoryMB': int(memory[1]) if memory else None, 'awsRequestId': request_id,
               'logTail': '\n'.join(item['message'] for item in events),
               'recoveredFrom': 'Exact AWS request ID, private S3 artifact and CloudWatch billing; no invocation replay'}
    if service_failure:
        failure = re.search(r'\bStatus:\s*(error|timeout)\b', report['message'], flags=re.IGNORECASE)
        if not failure:
            raise ValueError('No explicit terminal service failure in the billing report; do not assume a missing scene failed')
        receipt.update(functionError=failure[1].lower(), response={})
        return None, receipt
    key = f"attempts/soilie-{event['roomType']}-{event['seed']}/{request_id}.json"
    response = storage.get_object(Bucket=config['bucket'], Key=key)
    raw = response['Body'].read()
    sha = hashlib.sha256(raw).hexdigest()
    if response.get('Metadata', {}).get('sha256') != sha:
        raise ValueError('Recovered S3 metadata hash differs from its original bytes')
    row = validate_artifact(json.loads(raw), {'request': attempt['request']}, plan)
    started_at = datetime.fromisoformat(row['startedAt'].replace('Z', '+00:00')).timestamp()
    if not dispatched_at - 5 <= started_at <= report['timestamp'] / 1000 + 5:
        raise ValueError('Recovered scene timestamps do not belong to this dispatch')
    receipt.update(functionError=None, response={'key': key, 'sha256': sha, 'status': row['status'],
                                                'generationSeconds': row['generationSeconds'],
                                                'wallSeconds': row['wallSeconds']})
    return raw, receipt


def recover(args, plan):
    """Recover a particular paid attempt into its existing directory and ledger."""
    import boto3
    budget = budget_document(plan)
    matches = [row for row in budget['attempts'] if row['planSha256'] == plan['planSha256']
               and row['requestId'] == args.request and row['attempt'] == args.attempt]
    if len(matches) != 1 or matches[0]['status'] not in UNCERTAIN:
        raise ValueError('Name exactly one uncertain request/attempt in this frozen campaign')
    attempt = matches[0]
    folder = args.root / attempt['directory']
    seed = str(attempt['request']['seed'])
    path = folder / (seed + '-receipt.json')
    if path.exists():
        raise ValueError('A response receipt already exists; use reconcile without overwriting it')
    config = plan['configuration']
    session = boto3.Session(profile_name=config['profile'], region_name=config['region'])
    identity = saved_invocation_identity(args.root, plan, attempt)
    raw, receipt = recovered_evidence(plan, attempt, args.aws_request_id, session.client('s3'),
                                      session.client('logs'), args.service_failure, identity)
    artifact = folder / (seed + '.json')
    if raw is not None and artifact.exists() and artifact.read_bytes() != raw:
        raise ValueError('Existing artifact differs; preserve it for investigation')
    # Preserve existing evidence; recovery may only create missing files.
    if raw is not None and not artifact.exists():
        with artifact.open('xb') as handle:
            handle.write(raw)
    write_json(path, receipt)
    finish_cloud(args.root, plan, attempt)
    attempt.update(reconciledAt=datetime.now(UTC).isoformat(), recoveredAwsRequestId=args.aws_request_id)
    write_json(Path(config['budgetRoot']) / 'budget.json', budget)
    print(json.dumps({'requestId': args.request, 'attempt': args.attempt, 'status': attempt['status'],
                      'billedSeconds': attempt['billedSeconds'], 'costUSD': attempt['costUSD']}), flush=True)


def generation_date(attempts, sessions):
    values = [row['startedAt'] for row in attempts]
    values.extend(row['startedAt'] for group in sessions.values() for row in group if row.get('dispatchedCalls', 0))
    if not values:
        raise ValueError('No actual authoritative generation dispatch was recorded')
    timestamps = [datetime.fromisoformat(value.replace('Z', '+00:00')) for value in values]
    if any(value.tzinfo is None for value in timestamps):
        raise ValueError('Generation start receipts must include a timezone')
    return min(timestamps).astimezone(ZoneInfo('America/Toronto')).date().isoformat()


def export_evidence(root, plan, output):
    """Freeze complete coverage with attempt accounting; never rank/filter scenes."""
    if plan['phase'] != 'authoritative':
        raise ValueError('Development pilot evidence cannot be released as the authoritative cohort')
    status = campaign_status(root, plan)
    if not status['complete']:
        raise ValueError('Every frozen request must have a completed artifact before export')
    records = []
    accounting = []
    for platform_name in PLATFORMS:
        rows = attempt_rows(root, plan, platform_name)
        completed = verify_completed(root, plan, platform_name, rows)
        latest = {row['requestId']: row for row in rows if row['status'] == 'complete'}
        for task in plan['requests']:
            if task['platform'] != platform_name:
                continue
            if task['id'] not in completed:
                raise ValueError('Missing frozen request: ' + task['id'])
            attempt = latest[task['id']]
            row = read(root / attempt['artifact'])
            records.append({'requestId': task['id'], 'platform': platform_name,
                            'request': task['request'], 'sha256': attempt['sha256'],
                            'artifact': attempt['artifact'], 'scene': row})
        accounting.extend(rows)
    budget = budget_document(plan)
    sessions = {'local': read(root / 'local-ledger.json').get('sessions', []),
                'lambda': [row for row in budget.get('sessions', []) if row['planSha256'] == plan['planSha256']]}
    document = {'schemaVersion': 1, 'complete': True, 'planSha256': plan['planSha256'],
                'configuration': plan['configuration'], 'gates': plan['gates'],
                'coverage': status, 'scenes': records, 'attempts': accounting,
                'executionSessions': sessions, 'lambdaBudgetAccounting': budget,
                'authoritativeGenerationDate': generation_date(accounting, sessions),
                'generationDateTimezone': 'America/Toronto',
                'timingPolicy': plan['timingPolicy'], 'reviewApproved': False}
    document['evidenceSha256'] = digest(document)
    if output.exists() and read(output) != document:
        raise ValueError('Existing export differs; preserve the original evidence')
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, document)
    return {'complete': True, 'scenes': len(records), 'attempts': len(accounting),
            'evidenceSha256': document['evidenceSha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    freeze_parser = commands.add_parser('prepare', help='Freeze a gated pilot or final request manifest')
    freeze_parser.add_argument('--root', type=Path, required=True)
    freeze_parser.add_argument('--phase', choices=['pilot', 'authoritative'], required=True)
    freeze_parser.add_argument('--max-count', type=int, choices=[7, 8], default=8)
    for name in ('runtime', 'provenance', 'blender', 'rates', 'source-audit', 'budget-root'):
        freeze_parser.add_argument('--' + name, type=Path, required=True)
    for name in ('pilot', 'parity', 'fixtures'):
        freeze_parser.add_argument('--' + name, type=Path)
    for name in ('function', 'image', 'bucket'):
        freeze_parser.add_argument('--' + name, required=True)
    freeze_parser.add_argument('--region', default='ca-central-1')
    freeze_parser.add_argument('--profile', default='darkest')
    for command_name in ('run', 'reconcile', 'status', 'export', 'parity', 'recover'):
        sub = commands.add_parser(command_name)
        sub.add_argument('--root', type=Path, required=True)
        if command_name in ('run', 'reconcile'):
            sub.add_argument('--platform', choices=PLATFORMS, required=True)
        if command_name == 'export':
            sub.add_argument('--output', type=Path, required=True)
        if command_name in ('run', 'parity'):
            sub.add_argument('--retry-request', action='append', default=[])
        if command_name == 'run':
            sub.add_argument('--max-new-calls', type=int, default=0)
        if command_name == 'recover':
            sub.add_argument('--request', required=True, help='Frozen request ID, including parity- when applicable')
            sub.add_argument('--attempt', type=int, required=True)
            sub.add_argument('--aws-request-id', required=True, help='Original AWS invocation UUID from service evidence')
            sub.add_argument('--service-failure', action='store_true',
                             help='Require a terminal error/timeout REPORT when no scene was persisted')
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.command == 'prepare':
        with run_lock(args.root / 'prepare-controller'):
            prepare(args)
        return
    plan = load_plan(args.root, verify_runtime=args.command != 'status')
    if args.command == 'status':
        print(json.dumps(campaign_status(args.root, plan), indent=2))
        return
    if args.command == 'export':
        with run_lock(args.root / 'local-controller'), run_lock(Path(plan['configuration']['budgetRoot'])):
            print(json.dumps(export_evidence(args.root, plan, args.output)), flush=True)
        return
    if args.command == 'parity':
        with run_lock(Path(plan['configuration']['budgetRoot'])):
            parity_run(args, plan)
        return
    if args.command == 'recover':
        with run_lock(Path(plan['configuration']['budgetRoot'])):
            recover(args, plan)
        return
    lock = (Path(plan['configuration']['budgetRoot']) if args.platform == 'lambda'
            else args.root / 'local-controller')
    with run_lock(lock):
        if args.command == 'reconcile':
            reconcile(args, plan)
        elif args.platform == 'local':
            local_run(args, plan)
        else:
            cloud_run(args, plan)


if __name__ == '__main__':
    main()
