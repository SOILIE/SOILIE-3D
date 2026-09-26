"""Bounded, isolated timing of the pinned Infinigen construction stage.

The timer stops at scene serialization. Validation, hashing, compression and
S3 transfer remain invocation overhead, not generator latency. The original
mesh/solver code and controlled task are exactly those used in the local runs.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from serverless.benchmark.run_infinigen import COMMIT, profile_command, validate_controlled_output
from serverless.benchmark.infinigen_metadata import generated_instances


def run_construction(command, root, environment, stream):
    # Lambda freezes execution environments between calls. Kill the whole
    # owned process group on timeout so Blender children cannot survive reuse.
    process = subprocess.Popen(command, cwd=root, env=environment, stdout=stream,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait(timeout=760)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def request_parameters(event):
    if set(event) != {'condition', 'roomType', 'index'}:
        raise ValueError('Unexpected pilot fields')
    room, condition, index = event['roomType'], event['condition'], event['index']
    if room not in ('bedroom', 'living_room') or condition not in ('room-scale', 'controlled'):
        raise ValueError('Unknown condition')
    if type(index) is not int or not 0 <= index < 20:
        raise ValueError('Only the approved twenty indices per condition are allowed')
    slot = (room == 'living_room') * 2 + (condition == 'controlled')
    seed = 0x35000 + slot * 100 + index
    count = 3 + index % 4 if room == 'bedroom' and condition == 'controlled' else 6
    profile = ('controlled-count-fast' if room == 'bedroom' else 'controlled-six-fast') if condition == 'controlled' else 'matched-furniture-fast'
    return room, condition, index, seed, count, profile


def lambda_handler(event, context):
    import boto3
    room, condition, index, seed, count, profile = request_parameters(event)
    identity = f'{condition}-{room}-{index:02d}'
    root = Path('/opt/infinigen')
    folder = Path(tempfile.mkdtemp(prefix='infinigen-', dir='/tmp'))
    output = folder / 'scene'
    output.mkdir()
    configs, overrides, description = profile_command(profile, room, 'Bedroom' if room == 'bedroom' else 'LivingRoom')
    entry = (Path('/var/task/serverless/benchmark/infinigen_controlled_entry.py') if condition == 'controlled'
             else root / 'infinigen_examples/generate_indoors.py')
    command = ['/opt/blender/blender', '--background', '--threads', '4', '--python-use-system-env',
               '--python-exit-code', '2', '--python', str(entry), '--', '--seed', format(seed, 'x'),
               '--task', 'coarse', '--output_folder', str(output), '-g', *configs, '-p', *overrides]
    environment = {**os.environ, 'PWD': str(root), 'SOILIE_INFINIGEN_BEDROOM_COUNT': str(count)}
    log = folder / 'construction.log'
    result = {'id': identity, **event, 'seed': seed, 'sourceCommit': COMMIT, 'profile': profile,
              'configuration': description, 'blenderThreads': 4, 'status': 'failed',
              'memoryMb': int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '6144')),
              'stage': 'Blender startup, solving, procedural meshes, camera preparation and scene serialization; no rendering. Excludes validation and artifact transfer.'}
    try:
        started = time.perf_counter()
        with log.open('wb') as stream:
            try:
                exit_code = run_construction(command, root, environment, stream)
                result['generationSeconds'] = time.perf_counter() - started
                result['exitCode'] = exit_code
            except subprocess.TimeoutExpired:
                result['generationSeconds'] = time.perf_counter() - started
                result['errorCode'] = 'CONSTRUCTION_WATCHDOG'
        if result.get('exitCode') == 0 and (output / 'scene.blend').is_file() and (output / 'solve_state.json').is_file():
            if condition == 'controlled':
                result['roles'] = validate_controlled_output(output, room, count)
            state = json.loads((output / 'solve_state.json').read_bytes())
            _, instances = generated_instances(state['objs'], room)
            result['objectCount'] = len(instances)
            if not instances:
                raise ValueError('No room furniture in completed scene')
            result['status'] = 'complete'
        else:
            result.setdefault('errorCode', 'CONSTRUCTION_FAILED')
    except Exception as error:
        result['errorCode'] = type(error).__name__
    # Archive only owned outputs; timing samples never replace the frozen
    # geometry or review cohorts. A successful scene can be restored from S3.
    s3 = boto3.client('s3')
    prefix = os.environ['OUTPUT_PREFIX'].rstrip('/') + '/' + identity
    bucket = os.environ['OUTPUT_BUCKET']
    try:
        artifacts = []
        for path in [log, output / 'solve_state.json', output / 'scene.blend']:
            if not path.is_file():
                continue
            digest = hashlib.sha256()
            packed = folder / (path.name + '.gz')
            with path.open('rb') as source, gzip.open(packed, 'wb', compresslevel=1) as target:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
            key = prefix + '/' + packed.name
            s3.upload_file(str(packed), bucket, key, ExtraArgs={'ContentType': 'application/gzip'})
            artifacts.append({'file': packed.name, 'key': key, 'uncompressedSha256': digest.hexdigest(),
                              'bytes': packed.stat().st_size})
        result['artifacts'] = artifacts
        s3.put_object(Bucket=bucket, Key=prefix + '/result.json', Body=json.dumps(result).encode(), ContentType='application/json')
        print(json.dumps({'stage': 'complete', 'id': identity, 'status': result['status']}))
        return result
    finally:
        shutil.rmtree(folder)
