"""Resume bounded local workers until each room type has 120 eligible pairs.

The already frozen pairs remain fixed. New scenes use consecutive disjoint
seeds, a disclosed inventory schedule, and matching variables only. No quality score
selects a scene. Blender files are losslessly compressed after export to keep
the campaign below the local disk budget; original bytes remain recoverable.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from serverless.benchmark.geometry import measure
from serverless.benchmark.run_batch import run_lock, write_json
from serverless.benchmark.stimuli import select_pairs, digest, semantic_signature
from serverless.benchmark.supervise import command as supervised
from serverless.benchmark.infinigen_task import BEDROOM_COUNT_CYCLE, controlled_role_counts

ROOT = Path(__file__).resolve().parents[2]
ROOMS = ('bedroom', 'living_room')
OFFSETS = {'bedroom': 4000, 'living_room': 5000}


def matching_capacity(pool, protocols, room, counts=(6,)):
    """Upper bound for the permitted inventories, independent of quality.

    More baseline generations cannot create new unique SOILIE counterparts.
    Semantic and density gates can only reduce this count, never increase it.
    """
    reserved = set()
    for protocol in protocols:
        conditions = {case['id']: case['comparisonCondition'] for case in protocol['cases']}
        reserved.update(row['soilieScene'] for row in protocol['stimulusEvidence']
                        if conditions[row['caseId']] == 'infinigen_controlled'
                        and row['matchingStratum'][0] == room)
    anchor = {'bedroom': 'bed', 'living_room': 'sofa'}[room]
    candidates = {count: set() for count in counts}
    for row in pool:
        scene = row['scene']
        if scene['model'] != 'soilie' or scene['roomType'] != room or scene['id'] in reserved:
            continue
        signature = semantic_signature(scene)
        count = sum(signature.values())
        if count in candidates and signature[anchor] == 1:
            candidates[count].add(scene['id'])
    available = sum(map(len, candidates.values()))
    return {'roomType': room, 'furnitureCounts': list(counts), 'anchor': anchor, 'anchorCount': 1,
            'availableByCount': {str(n): len(peers) for n, peers in candidates.items()},
            'frozenPairs': len(reserved), 'remainingInventoryPeers': available,
            'maximumTotalPairs': len(reserved) + available,
            'scope': 'Inventory-only upper bound; semantic and density gates can reduce it.'}


def bedroom_schedule(output, enabled, pool, protocols):
    """Freeze the authorized input amendment without rewriting original records.

    An already-started attempt retains its original configuration. New count
    assignments depend only on the attempt index, never success or geometry.
    """
    path = output / 'bedroom-count-schedule.json'
    if not enabled:
        if path.exists():
            raise ValueError('Resume requires --vary-bedroom-counts for the frozen amendment')
        return None
    campaign_hash = sha_file(output / 'campaign.json')
    expected = {'schemaVersion': 1, 'campaignSha256': campaign_hash,
                'profile': 'controlled-count-fast', 'countCycle': list(BEDROOM_COUNT_CYCLE),
                'inventories': {str(n): controlled_role_counts('bedroom', n) for n in BEDROOM_COUNT_CYCLE},
                'assignment': 'Cycle by new attempt index; failures advance the same fixed sequence.',
                'qualitySelection': False, 'preserveFrozenPairs': True}
    if path.exists():
        schedule = json.loads(path.read_bytes())
        if any(schedule.get(key) != value for key, value in expected.items()):
            raise ValueError('Bedroom count schedule changed')
        if type(schedule.get('startAttempt')) is not int or schedule['startAttempt'] < 0:
            raise ValueError('Invalid bedroom count schedule offset')
        return schedule
    checkpoint = output / 'bedroom/checkpoint.json'
    state = json.loads(checkpoint.read_bytes()) if checkpoint.exists() else {'attempts': []}
    start = len(state['attempts'])
    # Preserve an interrupted or completed-but-not-yet-imported six-object run.
    while (output / 'bedroom' / f'attempt-{start:03d}' / 'run.json').exists():
        start += 1
    schedule = {**expected, 'startAttempt': start,
                'checkpointAtAmendmentSha256': sha_file(checkpoint) if checkpoint.exists() else None,
                'inventoryCapacity': matching_capacity(pool, protocols, 'bedroom', BEDROOM_COUNT_CYCLE)}
    write_json(path, schedule)
    return schedule


def attempt_task(room, index, schedule=None):
    if room == 'bedroom' and schedule and index >= schedule['startAttempt']:
        return schedule['profile'], schedule['countCycle'][(index - schedule['startAttempt']) % len(schedule['countCycle'])]
    return 'controlled-six-fast', 6


def wait_for_disk(directory):
    """Keep a checkpointed worker alive while verified uploads reclaim space.

    A 5 GiB recovery margin avoids immediately starting/stopping another scene.
    No model timeout or active generation is altered by this between-scene wait.
    """
    free = shutil.disk_usage(directory).free
    if free >= 15 * 1024**3:
        return
    print(json.dumps({'status': 'waiting-for-disk', 'freeGiB': round(free / 1024**3, 2),
                      'resumeAtGiB': 20}), flush=True)
    while shutil.disk_usage(directory).free < 20 * 1024**3:
        time.sleep(30)
    print(json.dumps({'status': 'disk-headroom-restored'}), flush=True)


def sha_file(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): value.update(chunk)
    return value.hexdigest()


def compress_blend(path, owner):
    """Replace only an owned generated blend with a verified lossless gzip."""
    path, owner = path.resolve(), owner.resolve()
    if not path.is_relative_to(owner) or path.name != 'scene.blend' or owner == ROOT:
        raise ValueError('Compression is limited to owned scene.blend files')
    archive = path.with_suffix('.blend.gz')
    receipt = path.with_name('blend-archive.json')
    if not path.exists():
        if not archive.exists() or not receipt.exists(): raise ValueError('Missing scene archive')
        return json.loads(receipt.read_bytes())
    original_hash, original_bytes = sha_file(path), path.stat().st_size
    temporary = archive.with_suffix('.gz.tmp')
    with path.open('rb') as source, gzip.open(temporary, 'wb', compresslevel=1) as destination:
        shutil.copyfileobj(source, destination, 1024 * 1024)
    check = hashlib.sha256()
    with gzip.open(temporary, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): check.update(chunk)
    if check.hexdigest() != original_hash: raise ValueError('Lossless archive verification failed')
    temporary.replace(archive)
    result = {'originalSha256': original_hash, 'archiveSha256': sha_file(archive),
              'originalBytes': original_bytes, 'archiveBytes': archive.stat().st_size,
              'restore': 'Decompress scene.blend.gz to scene.blend; verify originalSha256.'}
    write_json(receipt, result)
    path.unlink()  # Recoverable from the verified gzip; never deletes raw evidence.
    return result


def matching(pool, additions, protocols, target):
    pairs = select_pairs(pool + additions, limit=target, previous_protocols=protocols,
                         minimum_semantic_similarity=1/3, maximum_density_difference=1,
                         baselines=('infinigen_controlled',))
    return [{'soilieScene': a['id'], 'baselineScene': b['id'], 'matching': list(key),
             'soilieDigest': digest(a), 'baselineDigest': digest(b)} for _, key, a, b in pairs]


def run_command(command, log, seconds):
    with log.open('ab') as stream:
        # A supervised controller owns its native children if the outer worker
        # disappears. The existing generator records its own per-scene timeout.
        result = subprocess.run(supervised(command), cwd=ROOT, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=seconds)
    if result.returncode: raise RuntimeError('Campaign stage failed; inspect ' + str(log))


def run_attempt(room, index, args):
    """Own one attempt directory; only the coordinator writes room checkpoints.

    Reuse the thread count of interrupted runs. New workers do not mutate a
    started seed's inventory or configuration, and never resample on failure.
    """
    directory = args.output / room
    work = directory / f'attempt-{index:03d}'
    work.mkdir(exist_ok=True)
    receipt = work / 'expansion-entry.json'
    if receipt.exists():
        return json.loads(receipt.read_bytes())
    attempt_file = work / f'attempt-{room}-000.json'
    seed = OFFSETS[room] + index
    profile, object_count = attempt_task(room, index, args.bedroom_schedule)
    config_path = work / 'run.json'
    threads = (json.loads(config_path.read_bytes())['blenderThreads']
               if config_path.exists() else args.blender_threads)
    common = ['--repository', str(args.repository), '--site-packages', str(args.site_packages),
              '--blender', str(args.blender)]
    if not attempt_file.exists():
        run_command([sys.executable, '-m', 'serverless.benchmark.run_infinigen', *common,
            '--output', str(work), '--per-room', '1', '--room-types', room,
            '--profile', profile, '--object-count', str(object_count), '--blender-threads', str(threads),
            '--timeout', '3600', '--max-attempts', '1',
            '--' + room.replace('_', '-') + '-seed-offset', str(seed)], work / 'generation-controller.log', 3750)
    attempt = json.loads(attempt_file.read_bytes())
    run_config = json.loads(config_path.read_bytes())
    if run_config['profile'] != profile or run_config.get('objectCount', 6) != object_count:
        raise ValueError('Attempt configuration does not match its frozen input schedule')
    entry = {'seed': attempt['seed'], 'status': attempt['status'],
             'generationSeconds': attempt['generationSeconds'], 'id': attempt['id'],
             'profile': profile, 'objectCount': object_count, 'blenderThreads': threads}
    if attempt['status'] == 'complete':
        exported = work / 'export.json'
        if not exported.exists():
            run_command([sys.executable, '-m', 'serverless.benchmark.import_infinigen', *common,
                '--run', str(work), '--output', str(exported), '--timing-ineligible'],
                work / 'export-controller.log', 1000)
        document = json.loads(exported.read_bytes())
        if document['invalidArtifacts'] or len(document['scenes']) != 1:
            raise ValueError('A completed expansion scene must have validated geometry')
        scene = document['scenes'][0]
        measured = work / 'measured.json'
        write_json(measured, {'scene': scene, 'metrics': measure(scene)})
        entry.update(export=str(exported.relative_to(args.output)),
                     measured=str(measured.relative_to(args.output)), sceneId=scene['id'],
                     exportSha256=sha_file(exported), measuredSha256=sha_file(measured))
    else:
        entry['errorCode'] = attempt['errorCode']
    blend = work / f'scene-{room}-000' / 'scene.blend'
    if blend.exists() or blend.with_suffix('.blend.gz').exists():
        entry['archive'] = compress_blend(blend, directory)
    write_json(receipt, entry)
    return entry


def ordered_attempts(room, start, count, args):
    """Commit in seed order, never completion-speed or quality order.

    A bounded batch is drained even if the target is reached mid-batch. Durable
    per-attempt receipts let restart import finished work without regenerating it.
    """
    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(run_attempt, room, index, args) for index in range(start, start + count)]
        for future in futures:
            yield future.result()


def room_worker(room, args, pool, protocols, base_count):
    directory = args.output / room
    directory.mkdir(parents=True, exist_ok=True)
    with run_lock(directory):
        state_file = directory / 'checkpoint.json'
        state = json.loads(state_file.read_bytes()) if state_file.exists() else {
            'roomType': room, 'existingPairs': base_count, 'targetPairs': args.target,
            'attempts': [], 'selectedPairs': [], 'complete': False}
        counts = BEDROOM_COUNT_CYCLE if room == 'bedroom' and args.bedroom_schedule else (6,)
        capacity = matching_capacity(pool, protocols, room, counts)
        write_json(directory / 'matching-capacity.json', capacity)
        if capacity['maximumTotalPairs'] < args.target:
            print(json.dumps({'status': 'matching-capacity-limit', **capacity,
                              'targetPairs': args.target}), flush=True)
            # Stop only this room type; another feasible room worker continues.
            # Preserve all attempts and selected pairs for the next agreed design.
            return {**state, 'capacityLimit': capacity}
        while base_count + len(state['selectedPairs']) < args.target:
            # An operator can request a clean per-room checkpoint stop without
            # killing a native generation or conflating pause time with compute.
            if (directory / 'STOP').exists():
                print(json.dumps({'roomType': room, 'status': 'paused-at-checkpoint'}), flush=True)
                return state
            index = len(state['attempts'])
            if index >= args.max_attempts: raise RuntimeError('Reached fixed attempt ceiling for ' + room)
            wait_for_disk(directory)
            count = min(args.workers, args.max_attempts - index,
                        args.target - base_count - len(state['selectedPairs']))
            for entry in ordered_attempts(room, index, count, args):
                state['attempts'].append(entry)
                additions = []
                for row in state['attempts']:
                    if 'measured' not in row: continue
                    path = args.output / row['measured']
                    if sha_file(path) != row['measuredSha256']: raise ValueError('Changed exported scene')
                    additions.append(json.loads(path.read_bytes()))
                state['selectedPairs'] = matching(pool, additions, protocols, args.target - base_count)
                state['complete'] = base_count + len(state['selectedPairs']) == args.target
                state['updatedAtUnix'] = time.time()
                write_json(state_file, state)
                print(json.dumps({'roomType': room, 'attemptedNew': len(state['attempts']),
                    'validNewScenes': len(additions), 'matchedPairs': base_count + len(state['selectedPairs']),
                    'targetPairs': args.target, 'complete': state['complete']}), flush=True)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('output', 'evidence', 'original_protocol', 'supplement_protocol',
                'repository', 'site_packages', 'blender'):
        parser.add_argument('--' + key.replace('_', '-'), type=Path, required=True)
    parser.add_argument('--target', type=int, default=120)
    parser.add_argument('--max-attempts', type=int, default=200)
    parser.add_argument('--vary-bedroom-counts', action='store_true',
                        help='Apply the authorized, immutable 3–6-object bedroom continuation schedule')
    parser.add_argument('--workers', type=int, choices=range(1, 7), default=1,
                        help='Concurrent attempts per room; multiworker resume requires only one unfinished room')
    parser.add_argument('--blender-threads', type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    if args.target != 120 or args.max_attempts < 100 or args.max_attempts > 200:
        parser.error('Expected 120 pairs per room, with at most 200 additional attempts per room')
    for key, value in vars(args).items():
        if isinstance(value, Path): setattr(args, key, value.resolve())
    if not args.output.is_relative_to(ROOT / '.codex/benchmark'):
        raise ValueError('Campaign scratch data must stay inside the project benchmark directory')
    cohort = json.loads((args.evidence / 'cohort.json').read_bytes())
    measurements = args.evidence / 'measured-scenes.json'
    if not cohort['complete'] or sha_file(measurements) != cohort['measurementsSha256']:
        raise ValueError('Complete audited source cohort required')
    rows = json.loads(measurements.read_bytes())['rows']
    protocols = [json.loads(path.read_bytes()) for path in (args.original_protocol, args.supplement_protocol)]
    counts = {room: sum(row['matchingStratum'][0] == room for protocol in protocols
                        for row in protocol['stimulusEvidence']) for room in ROOMS}
    if counts != {'bedroom': 20, 'living_room': 20}:
        raise ValueError('Expected the forty already frozen, distinct pairs')
    identifiers = [row['soilieScene'] for protocol in protocols for row in protocol['stimulusEvidence']]
    if len(set(identifiers)) != 40: raise ValueError('Repeated reserved SOILIE scene')
    args.output.mkdir(parents=True, exist_ok=True)
    config = {'targetPerRoom': args.target, 'maxAdditionalAttemptsPerRoom': args.max_attempts,
              'sourceMeasurementsSha256': cohort['measurementsSha256'],
              'existingProtocols': [protocol['studyVersion'] for protocol in protocols],
              'seedOffsets': OFFSETS, 'workers': 2, 'blenderThreadsPerWorker': 4,
              'profile': 'controlled-six-fast', 'qualitySelection': False,
              'timingScope': 'Concurrent geometry expansion; excluded from isolated latency comparisons.'}
    with run_lock(args.output):
        path = args.output / 'campaign.json'
        if path.exists() and json.loads(path.read_bytes()) != config: raise ValueError('Campaign configuration changed')
        write_json(path, config)
        args.bedroom_schedule = bedroom_schedule(args.output, args.vary_bedroom_counts, rows, protocols)
        unfinished = [room for room in ROOMS if not (args.output / room / 'checkpoint.json').exists()
                      or not json.loads((args.output / room / 'checkpoint.json').read_bytes())['complete']]
        if args.workers > 1 and len(unfinished) > 1:
            raise ValueError('Multiworker expansion requires only one unfinished room type')
        # Operational scheduling is separate from immutable sampling/model inputs.
        execution = {'workersPerRoom': args.workers, 'newAttemptBlenderThreads': args.blender_threads,
                     'startedAtUnix': time.time(), 'pid': os.getpid(), 'unfinishedRooms': unfinished,
                     'timingEligible': False, 'commitOrder': 'ascending attempt index'}
        write_json(args.output / f'execution-{time.time_ns()}.json', execution)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(room_worker, room, args,
                [row for row in rows if row['scene']['model'] == 'soilie' and row['scene']['roomType'] == room],
                protocols, counts[room]) for room in ROOMS]
            results = [future.result() for future in futures]
        complete = all(row['complete'] for row in results)
        summary = {'complete': complete,
                   'pairs': {row['roomType']: row['existingPairs'] + len(row['selectedPairs']) for row in results}}
        # The streaming uploader treats complete.json as a terminal marker.
        # Never emit it while a room is awaiting a study-design decision.
        if complete:
            write_json(args.output / 'complete.json', summary)
        else:
            summary['capacityLimits'] = [row['capacityLimit'] for row in results if 'capacityLimit' in row]
            write_json(args.output / 'needs-decision.json', summary)


if __name__ == '__main__': main()
