"""Resumable full-rerun collector. No publication, retries or model fallback."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from serverless.cloud_benchmark.staged_pilot import digest, load_frozen, read, write_new
from serverless.cloud_benchmark.run_staged_pilot import preflight, run_one
from serverless.cloud_benchmark.full_review import results

DIMENSIONS = {
    'relationships': 'Object relationships',
    'orientation': 'Orientation',
    'access': 'Access and circulation',
    'proportions': 'Relative size',
    'room_function': 'Room function',
}
RUNNER_PATH = 'serverless/cloud_benchmark/run_full_review.py'


def progress(root, value):
    """A locked terminal heartbeat must never interrupt immutable answers."""
    pending = root / 'progress.pending'
    for attempt in range(4):
        try:
            pending.write_text(json.dumps(value, indent=2), encoding='utf-8')
            os.replace(pending, root / 'progress.json')
            return True
        except OSError as error:
            if attempt < 3:
                time.sleep(.05)
                continue
            failures = value.get('heartbeatWriteFailures', 0) + 1
            value['heartbeatWriteFailures'] = failures
            # Log at powers of two, avoiding an unbounded one-warning/sec log.
            if failures & (failures - 1) == 0:
                print(f'Heartbeat unavailable ({error}); saved judgments remain authoritative.',
                      file=sys.stderr, flush=True)
            return False


def validate_launch(root, launch, resume_note=None):
    """Keep the original receipt; explicitly record orchestration-only upgrades.

    Attribution rewrites change source commits without changing the protocol.
    Delivery, receipt validation, and aggregation code must still match exactly.
    An approval cannot authorize changing the model, evidence, or instructions.
    """
    receipt = root / 'collector-provenance.json'
    if not receipt.exists():
        write_new(receipt, launch)
        return
    original = read(receipt)
    if original == launch:
        return
    for field in ('protocolSha256', 'model', 'reasoningEffort'):
        if original[field] != launch[field]:
            raise ValueError(f'Frozen collection setting changed: {field}')
    old_code, new_code = original['codeSha256'], launch['codeSha256']
    if set(old_code) != set(new_code):
        raise ValueError('Collector provenance file inventory changed')
    changed = sorted(path for path in old_code if old_code[path] != new_code[path])
    if any(path != RUNNER_PATH for path in changed):
        raise ValueError('Judgment delivery, validation, or aggregation code changed')
    approval_path = root / 'collector-upgrades' / (digest(launch) + '.json')
    evidence = {'originalProvenanceSha256': digest(original), 'launch': launch,
                'changedCodeFiles': changed}
    if approval_path.exists():
        approval = read(approval_path)
        if any(approval.get(key) != value for key, value in evidence.items()):
            raise ValueError('Collector upgrade approval was altered')
    elif resume_note and resume_note.strip():
        write_new(approval_path, {**evidence, 'reason': resume_note,
                                 'approvedAt': datetime.now(timezone.utc).isoformat()})
    else:
        raise ValueError('Collector changed; an inspected orchestration-only update requires --resume-note')


def dimension_progress(protocol, remaining):
    groups = {key: {'key': key, 'label': label, 'completed': 0, 'expected': 0,
                    'active': 0, 'sessionCompleted': 0, 'etaSeconds': None}
              for key, label in DIMENSIONS.items()}
    for row in protocol['assignments']:
        group = groups[row['profile']]
        group['expected'] += 1
        group['completed'] += row['assignmentId'] not in remaining
    return list(groups.values())


def refresh_progress(state, active_rows, elapsed):
    """Use this session's collection time, excluding preflight and pauses."""
    state.update(active=len(active_rows), elapsedSeconds=round(elapsed),
                 updatedAt=datetime.now(timezone.utc).isoformat())
    completed_session = sum(group['sessionCompleted'] for group in state['dimensions'])
    left = state['expected'] - state['completed']
    state['etaSeconds'] = (0 if left == 0 else round(left * elapsed / completed_session)
                           if completed_session >= 3 else None)
    for group in state['dimensions']:
        group['active'] = sum(row['profile'] == group['key'] for row in active_rows)
        left = group['expected'] - group['completed']
        group['etaSeconds'] = (0 if left == 0 else round(left * elapsed / group['sessionCompleted'])
                               if group['sessionCompleted'] >= 3 else None)


def run(root, executable, workers=3, resume_note=None):
    if workers not in range(1, 6):
        raise ValueError('Use one to five independent workers')
    root = Path(root).resolve()
    lock = root / 'collection.lock'
    with lock.open('x') as stream:
        stream.write(str(os.getpid()))
    stop = threading.Event()
    state = {'state':'preflight', 'pid':os.getpid(), 'workers':workers, 'completed':0, 'expected':4800,
             'startedAt':datetime.now(timezone.utc).isoformat(), 'etaSeconds':None,
             'dimensions': []}
    try:
        progress(root, state)
        protocol = load_frozen(root)
        report = results(root)
        remaining = set(report['remainingAssignmentIds'])
        state.update(completed=report['responses'], expected=report['expected'],
                     dimensions=dimension_progress(protocol, remaining))
        progress(root, state)
        diagram_audit = read(root / 'diagram-audit.json')
        if not diagram_audit.get('passed') or diagram_audit.get('protocolSha256') != digest(protocol):
            raise ValueError('A successful all-diagram audit is required before collection')
        preflight(root)
        if protocol.get('stage') != 'full_counterbalanced' or not protocol['fullCampaignAuthorized']:
            raise ValueError('Not an authorized full campaign')
        code_root = Path(__file__).parents[2]
        launch = {'protocolSha256':digest(protocol), 'model':protocol['model'],
                  'reasoningEffort':protocol['reasoningEffort'],
                  'sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=code_root,text=True).strip(),
                  'codeSha256': {str(p.relative_to(code_root)).replace('\\','/'):digest(p.read_bytes()) for p in
                      [Path(__file__), Path(__file__).with_name('run_staged_pilot.py'), Path(__file__).with_name('staged_pilot.py'),
                       Path(__file__).with_name('full_review.py')]}}
        validate_launch(root, launch, resume_note)
        write_new(root / 'collector-sessions' / (str(os.getpid()) + '-' + str(time.time_ns()) + '.json'),
                  {'launch': launch, 'workers': workers, 'startedAt': state['startedAt'],
                   'completedAtStart': report['responses'],
                   'codexBinarySha256': digest(Path(executable).read_bytes())})
        rows = iter(r for r in protocol['assignments'] if r['assignmentId'] in remaining)
        by_dimension = {group['key']: group for group in state['dimensions']}
        started = time.monotonic()
        state.update(state='running', collectionStartedAt=datetime.now(timezone.utc).isoformat())
        progress(root, state)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            def fill():
                while len(futures) < workers and not stop.is_set():
                    row = next(rows, None)
                    if row is None:
                        break
                    futures[pool.submit(run_one, root, protocol, row, executable, stop)] = row
            fill()
            while futures:
                done, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    row = futures.pop(future)
                    try:
                        if future.result():
                            state['completed'] += 1
                            by_dimension[row['profile']]['completed'] += 1
                            by_dimension[row['profile']]['sessionCompleted'] += 1
                    except Exception as error:
                        stop.set()
                        state.update(state='needs_attention', failedAssignment=row['assignmentId'], error=str(error))
                fill()
                refresh_progress(state, list(futures.values()), time.monotonic()-started)
                progress(root, state)
        if stop.is_set():
            raise RuntimeError('Collection stopped on an invalid/interrupted attempt; prior answers are preserved')
        state.update(state='validating', active=0, etaSeconds=None)
        progress(root, state)
        report = results(root)
        if not report['complete']:
            raise ValueError('Missing final answers')
        final_path = root / 'full-results.json'
        if final_path.exists():
            if read(final_path) != report:
                raise ValueError('Previously saved final results changed')
        else:
            write_new(final_path, report)
        state.update(state='complete', completed=report['responses'], etaSeconds=0)
        progress(root, state)
    except BaseException as error:
        state.update(state='needs_attention', error=str(error), active=0)
        progress(root, state)
        raise
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--codex', default=shutil.which('codex'))
    parser.add_argument('--authorize-review', action='store_true')
    parser.add_argument('--resume-note', help='Explicit justification for an inspected orchestration-only update')
    args = parser.parse_args()
    if not args.authorize_review or not args.codex:
        parser.error('Explicit authorization and existing Codex executable required')
    run(args.root, args.codex, args.workers, args.resume_note)
