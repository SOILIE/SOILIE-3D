"""Freeze 5,000 existing bedrooms and run independent living-room shards.

Only orchestration lives here: every generation calls the tracked V4 pipeline.
Evidence is checkpointed, source bedrooms are never overwritten, and parallel
durations are not substituted for the earlier serial bedroom timing sample.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from serverless.benchmark.run_batch import run_lock, terminate_tree, write_json
from serverless.benchmark.audit_support_corrections import audit_record
from serverless.benchmark.supervise import command as supervised


def living_shards(total=5000, shards=20, seed=20260924):
    if total < shards or total % shards or shards % 4:
        raise ValueError('Use equal shards and a multiple of four for balanced requested counts')
    return [{'index': index, 'target': total//shards, 'seed': seed+997*index,
             'seedStep': 997*shards, 'objectCount': 3+index%4}
            for index in range(shards)]


def copy_verified(source, target):
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError('Existing frozen file differs: '+str(target))
    else:
        shutil.copy2(source, target)
    return digest


def prepare(source, repaired, output, count=5000):
    """Fixed ordered subset, no quality-based selection and no evidence deletion."""
    manifest = output/'campaign.json'
    if manifest.exists():
        document = json.loads(manifest.read_text())
        if document['bedroomSource'] != str(source) or document['bedroomCount'] != count:
            raise ValueError('Campaign already frozen with different inputs')
        for item in document['bedroomSelection']:
            for directory in (source, output/'bedroom-source'):
                if hashlib.sha256((directory/item['file']).read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('Frozen bedroom checksum changed: '+item['file'])
        return document
    original_dir, corrected_dir = output/'bedroom-source', output/'bedroom-repaired'
    original_dir.mkdir(parents=True, exist_ok=True)
    corrected_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(source.glob('attempt-*.json'))[:count]
    if len(paths) != count:
        raise ValueError('Not enough source bedrooms')
    selections = []
    for path in paths:
        raw = path.read_bytes()
        row = json.loads(raw)
        if row['status'] != 'complete' or row['request'].get('roomType') != 'bedroom':
            raise ValueError('Frozen source must consist of completed bedrooms')
        digest = copy_verified(path, original_dir/path.name)
        selections.append({'file': path.name, 'id': row['id'], 'sha256': digest})
        if (repaired/path.name).exists():
            derived = json.loads((repaired/path.name).read_text())
            audit_record(row, derived, digest)
            copy_verified(repaired/path.name, corrected_dir/path.name)
    config = json.loads((source/'run.json').read_text())
    config.update(targetCompletions=count, selection='first completed source records in filename order',
                  sourceRun=str(source), sourceRunTarget=10000)
    write_json(original_dir/'run.json', config)
    write_json(corrected_dir/'run.json', config)
    document = {'schemaVersion': 1, 'bedroomSource': str(source), 'bedroomCount': count,
                'bedroomSelection': selections, 'livingRoomCount': count,
                'livingShards': living_shards(count),
                'timingPolicy': 'Keep original serial bedroom timing separate. New parallel scene latency, correction time and batch elapsed time are recorded, not imputed from bedrooms.',
                'reviewPolicy': 'Freeze new matched pairs by room type after geometry validation. Re-render changed bedroom stimuli; generate living-room stimuli. Do not transfer votes between changed images.'}
    write_json(manifest, document)
    return document


def jobs_for(document, output, runtime, blender, workers):
    jobs = []
    # Two generation shards per repair shard keeps both kinds of work advancing.
    for index, shard in enumerate(document['livingShards']):
        if index % 2 == 0:
            repair_index = index//2
            chunk = document['bedroomCount']//10
            jobs.append({'id': f'bedroom-repair-{repair_index:02d}', 'kind': 'repair',
                         'start': repair_index*chunk, 'target': chunk,
                         'command': [str(blender), '--background', '--factory-startup', '--threads', '1',
                         '--python-exit-code', '2', '--python', str(runtime/'serverless/benchmark/settle_saved.py'), '--',
                         '--input', str(output/'bedroom-source'), '--output', str(output/'bedroom-repaired'),
                         '--start', str(repair_index*chunk), '--limit', str(chunk), '--compatible-replay-resume']})
        jobs.append({'id': f'living-{index:02d}', 'kind': 'generation',
                     'target': shard['target'],
                     'command': [sys.executable, '-m', 'serverless.benchmark.run_batch',
                     '--runtime', str(runtime), '--blender', str(blender),
                     '--output', str(output/f'living-{index:02d}'), '--target', str(shard['target']),
                     '--max-attempts', str(shard['target']*2), '--room-type', 'living_room',
                     '--object-count', str(shard['objectCount']), '--seed', str(shard['seed']),
                     '--seed-step', str(shard['seedStep']), '--blender-threads', '1',
                     '--parallel-workers', str(workers), '--solid-mesh-overlap', '--support']})
    return jobs


def task_complete(task, output):
    """Exit code alone cannot establish completion after an attempt cap."""
    if task['kind'] == 'repair':
        originals = sorted((output/'bedroom-source').glob('attempt-*.json'))
        selected = originals[task['start']:task['start']+task['target']]
        return len(selected) == task['target'] and all((output/'bedroom-repaired'/path.name).exists() for path in selected)
    rows = [json.loads(path.read_text()) for path in (output/task['id']).glob('attempt-*.json')]
    return sum(row['status'] == 'complete' for row in rows) == task['target']


def implementation_digest(runtime):
    """Pin the small tracked execution layer; the compiler pins research assets."""
    paths = [runtime/'package.json', runtime/'serverless/runtime-assets.json']
    for folder in ('modules', 'serverless/benchmark', 'serverless/common'):
        paths.extend(sorted((runtime/folder).glob('*.py')))
    values = [(path.relative_to(runtime).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def progress(output):
    counts = Counter()
    for path in output.glob('living-*/attempt-*.json'):
        row = json.loads(path.read_text())
        counts[row['status']] += 1
    return {'bedroomsRepaired': sum(1 for _ in (output/'bedroom-repaired').glob('attempt-*.json')),
            'livingRoomsComplete': counts['complete'], 'livingRoomFailures': counts['failed']}


def run(document, args):
    logs = args.output/'logs'
    logs.mkdir(exist_ok=True)
    tasks = jobs_for(document, args.output, args.runtime, args.blender, args.workers)
    active, finished, failed = {}, [], []
    state_path = args.output/'execution.json'
    prior = json.loads(state_path.read_text()) if state_path.exists() else {}
    digest = implementation_digest(args.runtime)
    if prior and (prior.get('implementationSha256') != digest or prior['workers'] != args.workers):
        raise ValueError('Campaign execution settings changed; inspect provenance before resuming')
    # Revisit all shards on resume. Each child's own manifest/checkpoint checks
    # reuse completed scenes without trusting a possibly stale finishedJobs list.
    started = time.monotonic()
    last_report = 0
    try:
        while tasks or active:
            while tasks and len(active) < args.workers and not (args.output/'STOP').exists():
                if shutil.disk_usage(args.output).free < 16*1024**3:
                    raise RuntimeError('Insufficient free disk space for the parallel workload')
                task = tasks.pop(0)
                handle = (logs/(task['id']+'.log')).open('ab')
                env = os.environ.copy()
                env.update(PYTHONHASHSEED='0', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                           MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
                process = subprocess.Popen(supervised(task['command']), cwd=args.runtime, env=env,
                                           stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
                active[task['id']] = (process, handle, task)
            for identity, (process, handle, task) in list(active.items()):
                result = process.poll()
                if result is None:
                    continue
                handle.close()
                (finished if result == 0 and task_complete(task, args.output) else failed).append(identity)
                del active[identity]
            if time.monotonic()-last_report >= 30 or not active:
                state = {'workers': args.workers, 'activePids': {key: pair[0].pid for key, pair in active.items()},
                         'implementationSha256': digest,
                         'finishedJobs': finished, 'failedJobs': failed, 'queuedJobs': [row['id'] for row in tasks],
                         'sessionElapsedSeconds': time.monotonic()-started, **progress(args.output)}
                write_json(state_path, state)
                print(json.dumps(state), flush=True)
                last_report = time.monotonic()
            if (args.output/'STOP').exists() and not active:
                break
            time.sleep(2)
    finally:
        for process, handle, _ in active.values():
            if process.poll() is None:
                terminate_tree(process)
            handle.close()
    if failed:
        raise RuntimeError('Some shards need investigation: '+', '.join(failed))
    if not tasks:
        totals = progress(args.output)
        if totals['bedroomsRepaired'] != document['bedroomCount'] or totals['livingRoomsComplete'] != document['livingRoomCount']:
            raise RuntimeError('Tasks stopped without producing the exact planned corpus')
        print('Generation and repair complete. Geometry audit and room-stratified AI review are still required.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--repaired', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--blender', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error('Use 1–8 local workers, with one Blender thread each')
    for name in ('source', 'repaired', 'runtime', 'blender', 'output'):
        setattr(args, name, getattr(args, name).resolve())
    if args.output == args.source or args.source in args.output.parents:
        parser.error('New campaign must not live inside the original evidence directory')
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Campaign interrupted; completed scenes are checkpointed')
    signal.signal(signal.SIGTERM, interrupted)
    with run_lock(args.output):
        document = prepare(args.source, args.repaired, args.output)
        if not args.prepare_only:
            run(document, args)


if __name__ == '__main__':
    main()
