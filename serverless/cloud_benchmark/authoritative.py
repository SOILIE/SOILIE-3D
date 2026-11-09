"""Freeze and resume fresh SOILIE cohorts without replacing failed requests.

Each requested scene keeps every attempt. The budget ledger is shared by the
development pilots and final campaign, so starting a new directory cannot reset
paid usage. AI review remains separate. Optional frozen S3 output storage
publishes allowlisted final-cohort room geometry, never raw private logs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
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
from serverless.cloud_benchmark.evidence_store import (
    archive_attempt, archive_configuration, load_artifact, recover_artifact_identity,
)
from serverless.common.object_counts import (
    OBJECT_COUNT_DEFINITION, OBJECT_COUNT_POLICY, validate_selection, window_extra_count,
)


ROOT = Path(__file__).resolve().parents[2]
ROOMS = ('bedroom', 'living_room')
PLATFORMS = ('local', 'lambda')
UNCERTAIN = {'dispatched', 'unknown'}
SOURCE_CHECKS = {'coordinateFrames', 'cameraDepthPairing', 'angleSemantics', 'anchorRotation',
                 'workingCombinations', 'exactRequestedCounts', 'gifVisibility', 'observerParity'}
FIXTURE_CHECKS = {'geometryFixtures', 'animationFixtures', 'observerParity'}
LOCAL_EXECUTION_CHECKS = {'ordinaryObserverParity', 'productionWorkerParity', 'canonicalAssetOrder'}


def selected_platforms(values):
    """Only the ordered mixed condition or explicitly local-only scope exists."""
    if not isinstance(values, (list, tuple)) or tuple(values) not in (('local',), PLATFORMS):
        raise ValueError('Freeze either local-only or the ordered local/Lambda condition')
    return tuple(values)


def plan_platforms(plan):
    # Existing frozen mixed plans remain readable; every new freeze declares
    # its scope rather than silently changing a 10,000-room completion target.
    if plan.get('kind') == 'lambda-continuation-v1' and plan.get('platforms') == ['lambda']:
        return ('lambda',)
    return selected_platforms(plan.get('platforms', PLATFORMS))


def require_platform(plan, platform_name):
    if platform_name not in plan_platforms(plan):
        raise ValueError('Platform is not part of this frozen campaign: ' + platform_name)


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


def allocation(max_count=8, phase='authoritative', platforms=PLATFORMS):
    """Allocate before generation; all four cells have the same histogram."""
    if max_count not in (7, 8) or phase not in ('pilot', 'authoritative'):
        raise ValueError('Use a pilot/authoritative phase and maximum count seven or eight')
    platforms = selected_platforms(platforms)
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
        for platform_name in platforms:
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
    if plan.get('kind') == 'lambda-continuation-v1':
        from serverless.cloud_benchmark.continuation import validate
        return validate(plan, root, verify_runtime=verify_runtime, metadata_only=not verify_runtime)
    platforms = plan_platforms(plan)
    if plan['requests'] != allocation(plan['maxObjectCount'], plan['phase'], platforms):
        raise ValueError('Frozen requests differ from the fixed allocation')
    config = plan['configuration']
    if platforms == ('local',):
        if (plan.get('phase') != 'authoritative' or plan.get('maxObjectCount') != 8
                or plan.get('cloudParity', {}).get('status') != 'deferred'
                or config.get('publicRunNamespace') != 'plan-sha256-v1'
                or any(key in config for key in ('function', 'image', 'bucket', 'budgetRoot', 'budgetUSD', 'rateCard',
                                                'lambdaConcurrency', 'memoryMB', 'ephemeralMB'))):
            raise ValueError('A local-only plan must defer cloud execution without stale cloud resource/cost pins')
    if verify_runtime and (config.get('objectCountDefinition') != OBJECT_COUNT_DEFINITION
                           or config.get('objectCountPolicy') != OBJECT_COUNT_POLICY):
        raise ValueError('Object-count semantics changed; qualify and freeze a new campaign')
    if verify_runtime and runtime_snapshot(Path(config['runtime']), Path(config['provenance']),
                                          Path(config['blender'])) != config['runtimeSnapshot']:
        raise ValueError('Execution code, data, assets, Python or Blender changed; start a new campaign')
    return plan


def prepare(args):
    platforms = selected_platforms(getattr(args, 'platforms', PLATFORMS))
    local_only = platforms == ('local',)
    if local_only:
        if (args.phase != 'authoritative' or args.max_count != 8
                or any(getattr(args, name, None) is not None for name in
                       ('function', 'image', 'bucket', 'rates', 'budget_root', 'pilot', 'parity'))):
            raise ValueError('Local-only authoritative 3-8 scope must omit cloud/pilot/parity inputs')
        if not all(getattr(args, name, None) for name in ('fixtures', 'local_qualification', 'archive_date')):
            raise ValueError('Local-only freeze requires current fixtures, local qualification and an output date')
    else:
        if not all(getattr(args, name, None) for name in ('function', 'image', 'bucket', 'rates', 'budget_root')):
            raise ValueError('Mixed-platform freeze requires immutable cloud inputs and its cumulative budget')
        if getattr(args, 'local_qualification', None):
            raise ValueError('Local qualification cannot replace mixed-platform pilot/parity gates')
        versioned_function(args.function)
    args.root.mkdir(parents=True, exist_ok=True)
    snapshot = runtime_snapshot(args.runtime, args.provenance, args.blender)
    source_audit = passed_gate(args.source_audit, snapshot['provenanceSha256'], 'Source audit', SOURCE_CHECKS)
    configuration = {'runtime': str(args.runtime.resolve()), 'provenance': str(args.provenance.resolve()),
                     'blender': str(args.blender.resolve()), 'runtimeSnapshot': snapshot,
                     'region': args.region, 'profile': args.profile,
                     'localWorkers': 6, 'blenderThreads': 1, 'timeoutSeconds': 900,
                     'sourceAuditSha256': source_audit,
                     'objectCountDefinition': OBJECT_COUNT_DEFINITION,
                     'objectCountPolicy': OBJECT_COUNT_POLICY}
    if not local_only:
        rates = validate_rates(read(args.rates), args.region)
        configuration.update(budgetRoot=str(args.budget_root.resolve()), rateCard=rates,
            function=args.function, image=args.image, bucket=args.bucket, lambdaConcurrency=16,
            memoryMB=4096, ephemeralMB=1024, budgetUSD=25.0)
    if getattr(args, 'archive_date', None):
        configuration['outputArchive'] = archive_configuration(args.archive_date)
    gates = {}
    if local_only:
        configuration['publicRunNamespace'] = 'plan-sha256-v1'
        gates = {'fixturesSha256': passed_gate(args.fixtures, snapshot['provenanceSha256'], 'Fixtures', FIXTURE_CHECKS),
                 'localQualificationSha256': passed_gate(args.local_qualification, snapshot['provenanceSha256'],
                     'Local execution qualification', LOCAL_EXECUTION_CHECKS)}
        if read(args.local_qualification).get('sourceCommit') != snapshot['sourceCommit']:
            raise ValueError('Local execution qualification belongs to another source commit')
    elif args.phase == 'authoritative':
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
        for platform_name in platforms:
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
            'platforms': list(platforms), 'configuration': configuration, 'gates': gates,
            'requests': allocation(args.max_count, args.phase, platforms),
            'timingPolicy': 'Per-scene generation through support settlement; observation and archival excluded. '
                            'Controller session/calendar times include dispatch and archival. '
                            'Retain billed invocation, active parallel session and calendar times separately.',
            'reviewPolicy': 'No previous AI judgments. Approve and freeze the evaluation protocol before collection.'}
    if local_only:
        body['cloudParity'] = {'status': 'deferred',
            'reason': 'Lambda execution and hardware comparison deferred by user; no cloud parity established.'}
        body['timingPolicy'] = ('Per-scene generation through support settlement; observation and archival excluded. '
            'Controller session/calendar times include dispatch and archival. Retain local per-scene, active parallel '
            'session and calendar times separately; Lambda timing, cost and parity remain deferred.')
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
    require_platform(plan, 'lambda')
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
    require_platform(plan, platform_name)
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
    config = plan['configuration']
    if (config.get('objectCountDefinition') != OBJECT_COUNT_DEFINITION
            or config.get('objectCountPolicy') != OBJECT_COUNT_POLICY):
        raise ValueError('Artifact validation requires the frozen non-window count protocol')
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
        counts = validate_selection(task['request'], row.get('selection', []))
        if any(type(row.get(key)) is not type(value) or row.get(key) != value for key, value in counts.items()):
            raise ValueError('Declared requested, counted, or selected-window inventory differs')
        if row.get('selectedObjectCount') != counts['selectedTotalCount']:
            raise ValueError('Complete selected-instance inventory differs from its declared total')
        # Final geometry retains both selected windows and additive companions.
        # This is an inventory observation, not a furniture/quality threshold.
        final_windows = window_extra_count(
            item['label'].lower().replace(' ', '_').split('.', 1)[0]
            for item in row['stages']['final']['objects'])
        companions = final_windows - counts['selectedWindowCount']
        if (companions < 0 or type(row.get('companionWindowCount')) is not int
                or type(row.get('renderedWindowCount')) is not int
                or row.get('companionWindowCount') != companions
                or row.get('renderedWindowCount') != final_windows):
            raise ValueError('Selected and companion window observations do not reconcile')
        for key in ('generationSeconds', 'wallSeconds', 'observationSeconds'):
            value = row.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('Incomplete timing evidence: ' + key)
        if not math.isclose(row['wallSeconds'] - row['observationSeconds'], row['generationSeconds'], abs_tol=.001):
            raise ValueError('Generation and observation timers do not reconcile')
    return row


def verify_attempt_evidence(root, row, plan=None, *, client=None):
    artifact = None
    for name, sha_name in (('artifact', 'sha256'), ('receipt', 'receiptSha256'), ('log', 'logSha256'),
                          ('invocationIdentity', 'invocationIdentitySha256')):
        if row.get(name):
            if not row.get(sha_name):
                raise ValueError('Retained attempt evidence changed: ' + row['requestId'])
            if name == 'artifact' and plan is not None:
                artifact = (load_artifact(root, row, plan) if client is None
                            else load_artifact(root, row, plan, client=client))
            elif file_digest(root / row[name]) != row[sha_name]:
                raise ValueError('Retained attempt evidence changed: ' + row['requestId'])
    return artifact


def verify_completed(root, plan, platform_name, rows):
    require_platform(plan, platform_name)
    tasks = {task['id']: task for task in plan['requests'] if task['platform'] == platform_name}
    complete = set()
    for row in rows:
        if row['requestId'] not in tasks or row['request'] != tasks[row['requestId']]['request']:
            raise ValueError('Attempt ledger contains an unallocated request')
        artifact = verify_attempt_evidence(root, row, plan)
        if row['status'] != 'complete':
            continue
        validate_artifact(artifact, tasks[row['requestId']], plan)
        if row['requestId'] in complete:
            raise ValueError('More than one successful attempt for the same request')
        complete.add(row['requestId'])
    return complete


def pending_tasks(plan, platform_name, rows, retry_ids=()):
    require_platform(plan, platform_name)
    if (plan_platforms(plan) == ('local',) or plan.get('kind') == 'lambda-continuation-v1') and retry_ids:
        raise ValueError('The local-only or continuation cohort never retries or replaces an original attempt')
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
    platforms = plan_platforms(plan)
    counts = Counter()
    failures = unknown = attempts = 0
    for platform_name in platforms:
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
                            for task in plan['requests'])} for platform_name in platforms for room in ROOMS]
    budget = budget_document(plan) if 'lambda' in platforms else None
    status = {'phase': plan['phase'], 'planSha256': plan['planSha256'], 'cells': cells,
            'complete': all(cell['complete'] == cell['target'] for cell in cells) and not unknown,
            'attempts': attempts, 'failedAttempts': failures, 'unknownAttempts': unknown,
            'platforms': list(platforms), 'cloudParity': plan.get('cloudParity'),
            'costPlusReservedUSD': spent(budget) if budget is not None else None,
            'budgetUSD': budget['budgetUSD'] if budget is not None else None}
    if plan.get('kind') == 'lambda-continuation-v1':
        status['parentEvidenceReadback'] = 'not performed; status uses frozen ledger metadata'
    return status


def eligible_pending_tasks(plan, platform_name, rows, eligible_ids=(), retry_ids=()):
    """Limit dispatch to exact frozen IDs without altering the scientific plan."""
    tasks = pending_tasks(plan, platform_name, rows, retry_ids)
    if not eligible_ids:
        return tasks
    ids = list(eligible_ids)
    scheduled = [task['id'] for task in plan['requests'] if task['platform'] == platform_name]
    if (any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids)
            or [value for value in scheduled if value in set(ids)] != ids):
        raise ValueError('Eligible request IDs must be unique allocated IDs in their original frozen order')
    return [task for task in tasks if task['id'] in set(ids)]


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
    if not path.exists() and not recover_artifact_identity(root, attempt, plan, path):
        attempt.update(status='failed', error=f'Worker exited {returncode} without terminal evidence')
        return
    task = {'request': attempt['request']}
    if path.exists():
        attempt.update(artifact=path.relative_to(root).as_posix(), sha256=file_digest(path))
    row = validate_artifact(load_artifact(root, attempt, plan), task, plan)
    attempt.update(status=row['status'])
    if returncode and row['status'] == 'complete':
        raise ValueError('Worker exited unsuccessfully after writing completed evidence')
    archive_attempt(root, attempt, plan)


def local_run(args, plan):
    require_platform(plan, 'local')
    config = plan['configuration']
    rows = attempt_rows(args.root, plan, 'local')
    verify_completed(args.root, plan, 'local', rows)
    tasks = eligible_pending_tasks(plan, 'local', rows, getattr(args, 'eligible_request', ()), args.retry_request)
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
                # Archival can outlast every child in a wave. An empty pool is
                # not completion when undispatched frozen requests still exist.
                if (not tasks or stopping or (args.root / 'STOP').exists()
                        or (args.max_new_calls and dispatched >= args.max_new_calls)):
                    break
                continue
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
    if path.exists():
        raw_sha = file_digest(path)
    elif recover_artifact_identity(root, attempt, plan, path):
        raw_sha = attempt['sha256']
    else:
        raise ValueError('Missing Lambda scene and no verified archive recovery sidecar')
    if raw_sha != receipt['response']['sha256']:
        raise ValueError('Downloaded scene hash differs from invocation receipt')
    if identity and identity.get('awsRequestId') and not receipt['response']['key'].endswith('/' + identity['awsRequestId'] + '.json'):
        raise ValueError('S3 scene belongs to another invocation')
    attempt.update(artifact=path.relative_to(root).as_posix(), sha256=raw_sha)
    row = validate_artifact(load_artifact(root, attempt, plan), {'request': attempt['request']}, plan)
    attempt.update(status=row['status'], billedSeconds=billed, costUSD=cost,
                   artifact=path.relative_to(root).as_posix(), sha256=raw_sha,
                   key=receipt['response']['key'], receipt=(folder / (seed + '-receipt.json')).relative_to(root).as_posix())
    archive_attempt(root, attempt, plan)


def cloud_run(args, plan):
    require_platform(plan, 'lambda')
    if plan.get('kind') == 'lambda-continuation-v1':
        if load_plan(args.root) != plan:
            raise ValueError('Continuation plan differs from its fully verified frozen plan')
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
    require_platform(plan, platform_name)
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
    require_platform(plan, 'lambda')
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
        error = compare_placements(load_artifact(args.root, local_entry, plan),
                                   load_artifact(args.root, attempt, plan))
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
    require_platform(plan, 'lambda')
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
    if plan.get('kind') == 'lambda-continuation-v1':
        from serverless.cloud_benchmark.continuation import export_evidence as export_continuation
        return export_continuation(root, plan, output)
    if plan['phase'] != 'authoritative':
        raise ValueError('Development pilot evidence cannot be released as the authoritative cohort')
    status = campaign_status(root, plan)
    if not status['complete']:
        raise ValueError('Every frozen request must have a completed artifact before export')
    records = []
    accounting = []
    platforms = plan_platforms(plan)
    for platform_name in platforms:
        rows = attempt_rows(root, plan, platform_name)
        completed = verify_completed(root, plan, platform_name, rows)
        latest = {row['requestId']: row for row in rows if row['status'] == 'complete'}
        for task in plan['requests']:
            if task['platform'] != platform_name:
                continue
            if task['id'] not in completed:
                raise ValueError('Missing frozen request: ' + task['id'])
            attempt = latest[task['id']]
            row = load_artifact(root, attempt, plan)
            records.append({'requestId': task['id'], 'platform': platform_name,
                            'request': task['request'], 'sha256': attempt['sha256'],
                            'artifact': attempt['artifact'], 'scene': row})
        accounting.extend(rows)
    budget = budget_document(plan) if 'lambda' in platforms else None
    sessions = {'local': read(root / 'local-ledger.json').get('sessions', [])}
    if budget is not None:
        sessions['lambda'] = [row for row in budget.get('sessions', []) if row['planSha256'] == plan['planSha256']]
    document = {'schemaVersion': 1, 'complete': True, 'planSha256': plan['planSha256'],
                'configuration': plan['configuration'], 'gates': plan['gates'], 'platforms': list(platforms),
                'cloudParity': plan.get('cloudParity'),
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
    freeze_parser.add_argument('--platforms', nargs='+', choices=PLATFORMS, default=list(PLATFORMS),
                               help='Freeze local-only or the ordered local lambda condition')
    freeze_parser.add_argument('--archive-date',
                               help='Frozen public S3 output date; pilots and exact logs stay private')
    for name in ('runtime', 'provenance', 'blender', 'source-audit'):
        freeze_parser.add_argument('--' + name, type=Path, required=True)
    for name in ('rates', 'budget-root', 'pilot', 'parity', 'fixtures', 'local-qualification'):
        freeze_parser.add_argument('--' + name, type=Path)
    for name in ('function', 'image', 'bucket'):
        freeze_parser.add_argument('--' + name)
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
            sub.add_argument('--eligible-request', action='append', default=[],
                             help='Dispatch only these original local request IDs, in frozen schedule order')
        if command_name == 'recover':
            sub.add_argument('--request', required=True, help='Frozen request ID, including parity- when applicable')
            sub.add_argument('--attempt', type=int, required=True)
            sub.add_argument('--aws-request-id', required=True, help='Original AWS invocation UUID from service evidence')
            sub.add_argument('--service-failure', action='store_true',
                             help='Require a terminal error/timeout REPORT when no scene was persisted')
    args = parser.parse_args()
    if args.command == 'run' and (args.max_new_calls < 0 or (args.eligible_request and args.platform != 'local')):
        parser.error('Use nonnegative max-new-calls and restrict eligible-request to local dispatch')
    args.root = args.root.resolve()
    if args.command == 'prepare':
        with run_lock(args.root / 'prepare-controller'):
            prepare(args)
        return
    # A linked continuation is revalidated under the shared budget lock before
    # dispatch. The initial read only selects that lock; avoid downloading the
    # entire parent cohort twice for one command.
    continuation_dispatch = (args.command == 'run' and args.platform == 'lambda'
                             and read(args.root / 'plan.json').get('kind') == 'lambda-continuation-v1')
    plan = load_plan(args.root, verify_runtime=args.command != 'status' and not continuation_dispatch)
    if args.command == 'status':
        status = campaign_status(args.root, plan)
        print(json.dumps(status, indent=2))
        return
    if args.command == 'export':
        with ExitStack() as locks:
            locks.enter_context(run_lock(args.root / 'local-controller'))
            if 'lambda' in plan_platforms(plan):
                locks.enter_context(run_lock(Path(plan['configuration']['budgetRoot'])))
            print(json.dumps(export_evidence(args.root, plan, args.output)), flush=True)
        return
    if args.command == 'parity':
        require_platform(plan, 'lambda')
        with run_lock(Path(plan['configuration']['budgetRoot'])):
            parity_run(args, plan)
        return
    if args.command == 'recover':
        require_platform(plan, 'lambda')
        with run_lock(Path(plan['configuration']['budgetRoot'])):
            recover(args, plan)
        return
    require_platform(plan, args.platform)
    lock = (Path(plan['configuration']['budgetRoot']) if args.platform == 'lambda'
            else args.root / 'local-controller')
    with run_lock(lock):
        if args.command == 'reconcile':
            reconcile(args, plan)
        elif args.platform == 'local':
            # Baseline procedural solving must not compete with timed local
            # SOILIE workers. Both controllers take this same workstation lock.
            with run_lock(ROOT / '.codex/benchmark/local-timing-scheduler'):
                local_run(args, plan)
        else:
            cloud_run(args, plan)


if __name__ == '__main__':
    main()
