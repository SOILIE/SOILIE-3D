"""Resumable full-rerun collector. No publication, retries or model fallback."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from serverless.cloud_benchmark.staged_pilot import digest, load_frozen, read, write_new
from serverless.cloud_benchmark.run_staged_pilot import preflight, run_one
from serverless.cloud_benchmark.full_review import results


def progress(root, value):
    # This is a mutable operational heartbeat, not immutable research evidence.
    pending = root / 'progress.pending'
    pending.write_text(json.dumps(value, indent=2), encoding='utf-8')
    for attempt in range(10):
        try:
            os.replace(pending, root / 'progress.json')
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.02)  # A Windows terminal may briefly hold the old file.


def run(root, executable, workers=3):
    if workers not in (1,2,3):
        raise ValueError('Use at most three independent workers')
    root = Path(root).resolve()
    lock = root / 'collection.lock'
    with lock.open('x') as stream:
        stream.write(str(os.getpid()))
    started, completed_this_session = time.monotonic(), 0
    stop = threading.Event()
    state = {'state':'preflight', 'pid':os.getpid(), 'workers':workers, 'completed':0, 'expected':4800,
             'startedAt':datetime.now(timezone.utc).isoformat(), 'etaSeconds':None}
    try:
        progress(root, state)
        diagram_audit = read(root / 'diagram-audit.json')
        if not diagram_audit.get('passed') or diagram_audit.get('protocolSha256') != digest(load_frozen(root)):
            raise ValueError('A successful all-diagram audit is required before collection')
        preflight(root)
        protocol, report = load_frozen(root), results(root)
        if protocol.get('stage') != 'full_counterbalanced' or not protocol['fullCampaignAuthorized']:
            raise ValueError('Not an authorized full campaign')
        code_root = Path(__file__).parents[2]
        launch = {'protocolSha256':digest(protocol), 'model':protocol['model'],
                  'reasoningEffort':protocol['reasoningEffort'],
                  'sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=code_root,text=True).strip(),
                  'codeSha256': {str(p.relative_to(code_root)).replace('\\','/'):digest(p.read_bytes()) for p in
                      [Path(__file__), Path(__file__).with_name('run_staged_pilot.py'), Path(__file__).with_name('staged_pilot.py'),
                       Path(__file__).with_name('full_review.py')]}}
        receipt = root / 'collector-provenance.json'
        if receipt.exists():
            if read(receipt) != launch:
                raise ValueError('Collector changed since launch; inspect before resuming')
        else:
            write_new(receipt, launch)
        remaining = set(report['remainingAssignmentIds'])
        rows = iter(r for r in protocol['assignments'] if r['assignmentId'] in remaining)
        state.update(state='running', completed=report['responses'])
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
                            completed_this_session += 1
                    except Exception as error:
                        stop.set()
                        state.update(state='needs_attention', failedAssignment=row['assignmentId'], error=str(error))
                elapsed = time.monotonic()-started
                state.update(active=len(futures), elapsedSeconds=round(elapsed), updatedAt=datetime.now(timezone.utc).isoformat(),
                    etaSeconds=round((state['expected']-state['completed'])*elapsed/completed_this_session)
                    if completed_this_session >= 3 else None)
                progress(root, state)
                fill()
        if stop.is_set():
            raise RuntimeError('Collection stopped on an invalid/interrupted attempt; prior answers are preserved')
        state.update(state='validating', active=0, etaSeconds=None)
        progress(root, state)
        report = results(root)
        if not report['complete']:
            raise ValueError('Missing final answers')
        write_new(root / 'full-results.json', report)
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
    args = parser.parse_args()
    if not args.authorize_review or not args.codex:
        parser.error('Explicit authorization and existing Codex executable required')
    run(args.root, args.codex, args.workers)
