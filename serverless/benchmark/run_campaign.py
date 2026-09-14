"""Serial, restartable local campaign; never releases or deploys partial results.

Run under the same Linux environment as the measured batches. A prior running
Infinigen preflight is allowed to finish before another heavy worker starts.
Each generator owns its checkpoints; this controller only sequences commands
and journals their completion. All logs and paths remain in private .codex/.
"""
import argparse
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from serverless.benchmark.run_batch import ROOT, run_lock, terminate_tree, write_json
from serverless.benchmark.supervise import command as supervised


def stages(args):
    def python(module, *values):
        return [sys.executable, '-m', 'serverless.benchmark.'+module, *map(str, values)]
    common = ['--runtime', args.runtime, '--blender', args.blender]
    indoors = ['--repository', args.infinigen, '--blender', args.infinigen_blender,
               '--site-packages', args.infinigen_packages]
    run = args.output/'infinigen-original'
    imported = run/'layouts.json'
    return [
        # Predetermined first 40 attempts, not 40 cherry-picked successes. These
        # replays are excluded from the placement throughput ledger.
        ('support-replays', python('run_batch', *common, '--output', args.output/'soilie-support',
                                  '--target', 10000, '--seed', 20260913, '--max-attempts', 40, '--support')),
        ('support-parity', python('support_replays', '--baseline', args.output/'soilie-bedroom',
                                 '--replays',args.output/'soilie-support','--output',args.output/'soilie-support/parity.json')),
        ('infinigen-preflight', python('run_infinigen', *indoors, '--output', run, '--per-room', 20, '--max-attempts', 1)),
        ('infinigen-preflight-export', python('import_infinigen', *indoors, '--run', run, '--output', imported)),
        ('living-room', python('run_batch', *common, '--output', args.output/'soilie-living-room',
                              '--target', 200, '--seed', 30260913, '--room-type', 'living_room')),
        ('bedroom-10000', python('run_batch', *common, '--output', args.output/'soilie-bedroom',
                                '--target', 10000, '--seed', 20260913)),
        ('infinigen-40', python('run_infinigen', *indoors, '--output', run, '--per-room', 20)),
        ('infinigen-final-export', python('import_infinigen', *indoors, '--run', run, '--output', imported)),
    ]


def wait_for_existing_run(directory):
    # Probe an OS lock rather than inferring liveness from a stale PID file.
    announced = False
    while True:
        try:
            with run_lock(directory):
                return
        except BlockingIOError:
            if not announced:
                print(json.dumps({'stage':'waiting-for-existing-infinigen', 'status':'waiting'}), flush=True)
                announced = True
            time.sleep(10)


def run_stage(name, command, directory):
    journal = directory/(name+'.json')
    history = []
    if journal.exists():
        previous = json.loads(journal.read_text())
        if previous['command'] != command:
            raise ValueError('Campaign stage command changed; review its existing journal before resuming')
        if previous['status'] == 'complete':
            return
        history = previous.pop('previousSessions',[]) + [previous]
    row = {'stage':name, 'command':command, 'status':'running', 'startedAt':datetime.now(UTC).isoformat()}
    if history:
        row['previousSessions'] = history
    write_json(journal,row)
    print(json.dumps({'stage':name, 'status':'running'}),flush=True)
    # An interrupted stage resumes from generator-owned checkpoints. Append the
    # controller log so the earlier session's ending is not silently erased.
    with (directory/(name+'.log')).open('ab') as log:
        process = subprocess.Popen(supervised(command),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            code = process.wait()
            row.update(status='complete' if code == 0 else 'failed', exitCode=code)
        except BaseException:
            if process.poll() is None:
                # Let the batch runner stop its own separately grouped worker
                # first; killing just the controller can otherwise orphan it.
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    terminate_tree(process)
            row['status'] = 'interrupted'
            raise
        finally:
            row['endedAt'] = datetime.now(UTC).isoformat()
            write_json(journal,row)
    print(json.dumps({'stage':name, 'status':row['status'], 'exitCode':code}),flush=True)
    if code:
        raise RuntimeError('Campaign stage failed; inspect its private log before resuming')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--blender', type=Path, required=True)
    parser.add_argument('--infinigen', type=Path, required=True)
    parser.add_argument('--infinigen-blender', type=Path, required=True)
    parser.add_argument('--infinigen-packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'.codex/benchmark')
    args = parser.parse_args()
    if os.name != 'posix':
        raise RuntimeError('Run this local campaign from the benchmark Linux/WSL environment')
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Campaign interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    for name in vars(args):
        setattr(args,name,getattr(args,name).resolve())
    if not args.output.is_relative_to(ROOT/'.codex'):
        raise ValueError('Campaign output must stay under the backend project .codex directory')
    directory = args.output/'campaign'
    with run_lock(directory):
        wait_for_existing_run(args.output/'infinigen-original')
        for name, command in stages(args):
            run_stage(name,command,directory)
    print('Campaign commands finished. Validate geometry, support parity and coverage before publication.',flush=True)


if __name__ == '__main__':
    main()
