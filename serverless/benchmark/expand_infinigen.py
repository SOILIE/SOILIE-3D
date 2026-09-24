"""Resume two local room-type workers until each has 120 eligible review pairs.

The already frozen pairs remain fixed. New scenes use consecutive disjoint
seeds, the same six-role profile, and matching variables only. No quality score
selects a scene. Blender files are losslessly compressed after export to keep
the campaign below the local disk budget; original bytes remain recoverable.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from serverless.benchmark.geometry import measure
from serverless.benchmark.run_batch import run_lock, write_json
from serverless.benchmark.stimuli import select_pairs, digest
from serverless.benchmark.supervise import command as supervised

ROOT = Path(__file__).resolve().parents[2]
ROOMS = ('bedroom', 'living_room')
OFFSETS = {'bedroom': 4000, 'living_room': 5000}


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


def room_worker(room, args, pool, protocols, base_count):
    directory = args.output / room
    directory.mkdir(parents=True, exist_ok=True)
    with run_lock(directory):
        state_file = directory / 'checkpoint.json'
        state = json.loads(state_file.read_bytes()) if state_file.exists() else {
            'roomType': room, 'existingPairs': base_count, 'targetPairs': args.target,
            'attempts': [], 'selectedPairs': [], 'complete': False}
        common = ['--repository', str(args.repository), '--site-packages', str(args.site_packages),
                  '--blender', str(args.blender)]
        while base_count + len(state['selectedPairs']) < args.target:
            index = len(state['attempts'])
            if index >= args.max_attempts: raise RuntimeError('Reached fixed attempt ceiling for ' + room)
            wait_for_disk(directory)
            work = directory / f'attempt-{index:03d}'
            work.mkdir(exist_ok=True)
            attempt_file = work / f'attempt-{room}-000.json'
            seed = OFFSETS[room] + index
            if not attempt_file.exists():
                run_command([sys.executable, '-m', 'serverless.benchmark.run_infinigen', *common,
                    '--output', str(work), '--per-room', '1', '--room-types', room,
                    '--profile', 'controlled-six-fast', '--timeout', '3600', '--max-attempts', '1',
                    '--' + room.replace('_', '-') + '-seed-offset', str(seed)], work / 'generation-controller.log', 3750)
            attempt = json.loads(attempt_file.read_bytes())
            entry = {'seed': attempt['seed'], 'status': attempt['status'],
                     'generationSeconds': attempt['generationSeconds'], 'id': attempt['id']}
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
                metric = measure(scene)
                measured = work / 'measured.json'
                write_json(measured, {'scene': scene, 'metrics': metric})
                entry.update(export=str(exported.relative_to(args.output)),
                             measured=str(measured.relative_to(args.output)), sceneId=scene['id'],
                             exportSha256=sha_file(exported), measuredSha256=sha_file(measured))
            else:
                entry['errorCode'] = attempt['errorCode']
            blend = work / f'scene-{room}-000' / 'scene.blend'
            if blend.exists() or blend.with_suffix('.blend.gz').exists():
                entry['archive'] = compress_blend(blend, directory)
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
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(room_worker, room, args,
                [row for row in rows if row['scene']['model'] == 'soilie' and row['scene']['roomType'] == room],
                protocols, counts[room]) for room in ROOMS]
            results = [future.result() for future in futures]
        write_json(args.output / 'complete.json', {'complete': all(row['complete'] for row in results),
                                                  'pairs': {row['roomType']: args.target for row in results}})


if __name__ == '__main__': main()
