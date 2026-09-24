"""Finish only the local half of the frozen 2 x 2 design.

The previous controller drains its six already-owned living-room shards. This
controller owns only the other shards and repairs, filling released slots up
to the same six-process limit. No successful record is regenerated.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from serverless.benchmark.audit_support_corrections import audit_record
from serverless.benchmark.balanced_campaign import implementation_digest, jobs_for, task_complete
from serverless.benchmark.run_batch import run_lock, terminate_tree, write_json
from serverless.benchmark.supervise import command as supervised
from serverless.cloud_benchmark.design import freeze


def jobs(document, campaign, runtime, blender):
    tasks = jobs_for(document, campaign, runtime, blender, 6)
    selected = []
    for task in tasks:
        if task['kind'] == 'repair':
            if 1500 <= task['start'] < 2500:
                selected.append(task)
            continue
        index = int(task['id'].split('-')[1])
        if 6 <= index < 12:
            if index >= 8:
                task['target'] = 125
                command = task['command']
                command[command.index('--target')+1] = '125'
                command[command.index('--max-attempts')+1] = '125'
            selected.append(task)
    return selected


def old_workers(campaign):
    state = json.loads((campaign/'execution.json').read_text())
    count = 0
    for name, pid in state['activePids'].items():
        command = Path('/proc')/str(pid)/'cmdline'
        if command.exists():
            raw = command.read_bytes()
            if b'serverless.benchmark.run_batch' in raw and name.encode() in raw:
                count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--blender', type=Path, required=True)
    args = parser.parse_args()
    campaign, output, blender = args.campaign.resolve(), args.output.resolve(), args.blender.resolve()
    root = Path(__file__).resolve().parents[2]
    if not (campaign/'STOP').exists():
        raise ValueError('The superseded controller must stop scheduling before this controller starts')
    plan = freeze(campaign, output)
    document = json.loads((campaign/'campaign.json').read_text())
    expected = json.loads((campaign/'execution.json').read_text())['implementationSha256']
    if implementation_digest(root) != expected:
        raise ValueError('Model or benchmark implementation changed')
    tasks = jobs(document, campaign, root, blender)
    active, finished = {}, []
    logs = output/'local-logs'
    logs.mkdir(exist_ok=True)
    def stop(signum, frame):
        raise KeyboardInterrupt('Local controller stopped; completed records are checkpointed')
    signal.signal(signal.SIGTERM, stop)
    with run_lock(output/'local-controller'):
        last_report = 0
        try:
            while tasks or active or old_workers(campaign):
                while tasks and len(active)+old_workers(campaign) < 6:
                    task = tasks.pop(0)
                    if task_complete(task, campaign):
                        finished.append(task['id'])
                        continue
                    handle = (logs/(task['id']+'.log')).open('ab')
                    env = {**os.environ, 'PYTHONHASHSEED':'0', 'OMP_NUM_THREADS':'1',
                           'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1', 'NUMEXPR_NUM_THREADS':'1'}
                    child = subprocess.Popen(supervised(task['command']), cwd=root, env=env,
                            stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
                    active[task['id']] = (child, handle, task)
                for name, (child, handle, task) in list(active.items()):
                    if child.poll() is None:
                        continue
                    handle.close()
                    del active[name]
                    if child.returncode or not task_complete(task, campaign):
                        raise RuntimeError('Local task requires investigation: '+name)
                    finished.append(name)
                if time.monotonic()-last_report >= 30:
                    state = {'activePids':{key:value[0].pid for key,value in active.items()},
                             'drainingOriginalWorkers':old_workers(campaign), 'finished':finished,
                             'pending':[row['id'] for row in tasks]}
                    write_json(output/'local-progress.json', state)
                    print(json.dumps(state), flush=True)
                    last_report = time.monotonic()
                time.sleep(2)
            # The old source directory contains 5,000 archived records. Audit
            # the selected 2,500 only, without deleting or altering the archive.
            for item in plan['localBedrooms']:
                source = (campaign/'bedroom-source'/item['file']).read_bytes()
                target = json.loads((campaign/'bedroom-repaired'/item['file']).read_text())
                audit_record(json.loads(source), target, hashlib.sha256(source).hexdigest())
            for shard in plan['localLivingShards']:
                records = list((campaign/f"living-{shard['index']:02d}").glob('attempt-*.json'))
                if len(records) != shard['target'] or any(json.loads(path.read_text())['status'] != 'complete' for path in records):
                    raise RuntimeError('Local living-room allocation incomplete')
            write_json(output/'local-completion.json', {'complete':True, 'bedrooms':2500, 'livingRooms':2500})
        finally:
            for child, handle, _ in active.values():
                if child.poll() is None:
                    terminate_tree(child)
                handle.close()


if __name__ == '__main__':
    main()
