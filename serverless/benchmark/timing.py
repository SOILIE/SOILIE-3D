"""Timing/provenance of local measurement sessions, separate from model time."""
from contextlib import contextmanager
from datetime import datetime, UTC, timedelta
import json
import os
from pathlib import Path
import platform
import time
import uuid


def hardware():
    # Deliberately omit hostname, username, paths and cloud-account metadata.
    cpu = platform.processor()
    memory = None
    if Path('/proc/cpuinfo').exists():
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                cpu = line.partition(':')[2].strip()
                break
    if Path('/proc/meminfo').exists():
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
                memory = int(line.split()[1])*1024
                break
    return {'os':platform.platform(), 'cpu':cpu, 'logicalCpusVisible':os.cpu_count(),
            'memoryBytesVisible':memory, 'python':platform.python_version()}


@contextmanager
def record_session(directory, workload):
    """Record pauses as gaps between sessions, not as generation compute.

    A hard-killed session remains explicitly unfinished. Existing checkpoints
    without a session ledger stay usable but cannot acquire invented wall time.
    This instrumentation never alters generator configuration or RNG state.
    """
    from serverless.benchmark.run_batch import write_json
    before = {path.name for path in directory.glob('attempt-*.json')}
    destination = directory / ('session-'+uuid.uuid4().hex+'.json')
    row = {'schemaVersion':1, 'workload':workload, 'hardware':hardware(),
           'startedAt':datetime.now(UTC).isoformat(), 'status':'running', 'attemptFiles':[]}
    started = time.perf_counter()
    write_json(destination,row)
    try:
        yield
        row['status'] = 'finished'
    except BaseException:
        row['status'] = 'interrupted'
        raise
    finally:
        row['activeWallSeconds'] = time.perf_counter()-started
        row['endedAt'] = datetime.now(UTC).isoformat()
        row['attemptFiles'] = sorted({path.name for path in directory.glob('attempt-*.json')}-before)
        write_json(destination,row)


def session_summary(directory, attempts):
    files = {path.name for path in directory.glob('attempt-*.json')}
    sessions = [json.loads(path.read_text()) for path in sorted(directory.glob('session-*.json'))]
    finished = [row for row in sessions if row['status'] == 'finished']
    covered = [name for row in finished for name in row['attemptFiles']]
    if len(covered) != len(set(covered)) or not set(covered) <= files:
        raise ValueError('Session timing references overlapping or absent attempt checkpoints')
    complete = bool(files) and set(covered) == files and len(finished) == len(sessions)
    span = None
    if attempts:
        starts = [datetime.fromisoformat(row['startedAt']) for row in attempts]
        ends = [start+timedelta(seconds=row.get('wallSeconds',row['generationSeconds']))
                for start,row in zip(starts,attempts)]
        span = (max(ends)-min(starts)).total_seconds()
    return {'completeSessionCoverage':complete, 'attemptsWithSessionTiming':len(covered),
            'attemptsWithoutSessionTiming':len(files-set(covered)),
            'activeSessionWallSeconds':sum(row['activeWallSeconds'] for row in finished) if complete else None,
            'calendarAttemptSpanSeconds':span,
            'hardwareSnapshots':[row['hardware'] for row in sessions],
            'interpretation':'Generation seconds sum measured attempt timers and exclude observation work. Calendar span includes pauses. Full active-session wall time is unavailable when any session was not recorded or did not finish.'}


def generation_breakdown(attempts):
    """Summarize successful compute separately from failure reliability.

    A watchdog cutoff is an operational choice, not the duration an infinite
    layout would have taken. Failed attempts are therefore counted by error
    code but never added to successful-layout timing.
    """
    if any(row['status'] not in {'complete','failed'} for row in attempts):
        raise ValueError('Only finished attempt checkpoints enter generation timing')
    completed = sum(row['generationSeconds'] for row in attempts if row['status'] == 'complete')
    completed_count = sum(row['status'] == 'complete' for row in attempts)
    failures = {}
    for row in attempts:
        if row['status'] == 'failed':
            code = row.get('errorCode','UNCLASSIFIED_FAILURE')
            failures[code] = failures.get(code,0)+1
    return {'completedSeconds':completed,'totalSeconds':completed,
            'successfulLayouts':completed_count,
            'successfulLayoutsPerMinute':60*completed_count/completed if completed else None,
            'failedAttempts':sum(failures.values()),'failureCounts':failures,
            'collisionCyclesDetected':sum(bool(row.get('collisionRecoveryActivated')) for row in attempts),
            'collisionRecoveryMoves':sum(row.get('collisionRecoveryMoves',0) for row in attempts),
            'boundaryContainmentRepairs':sum(bool(row.get('boundaryContainmentActivated')) for row in attempts),
            'excludedObservationSeconds':sum(row.get('observationSeconds',0) for row in attempts),
            'meaning':'Successful generation time includes initialization and placement but excludes geometry observation, queue gaps, pauses, other cohorts, publishing, image rendering, and every failed attempt. Failures and watchdog terminations are reported as counts, because a watchdog cutoff is not model latency.'}
