"""Private, placement-only Lambda adapter for the unchanged benchmark worker.

No queue, public API or model fallback. A finite client-owned request manifest
controls invocation count; artifacts go to a private temporary bucket.
"""
from __future__ import annotations

from datetime import datetime, UTC
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def request_document(event):
    if set(event) != {'seed', 'objectCount', 'roomType'}:
        raise ValueError('Expected roomType, seed and requested object count')
    if event['roomType'] not in ('bedroom', 'living_room'):
        raise ValueError('Only bedroom and living_room are in this campaign')
    if type(event['seed']) is not int or not 0 <= event['seed'] < 2**32:
        raise ValueError('Invalid seed')
    if type(event['objectCount']) is not int or not 3 <= event['objectCount'] <= 6:
        raise ValueError('Invalid requested object count')
    return {'mode': 'room_type', 'roomType': event['roomType'], 'seed': event['seed'],
            'objectCount': event['objectCount'], 'allowDuplicates': True,
            'sameObjectsAcrossScenes': True}


@lru_cache(maxsize=1)
def verify_build_inputs():
    """Check all inherited assets and modules before creating the immutable image."""
    from serverless.compiler.runtime_assets import verify, checksum
    runtime = Path(os.environ['V4_RUNTIME_DIR'])
    manifest = Path('/var/task/serverless/runtime-assets.json')
    verify(runtime, json.loads(manifest.read_text()))
    provenance = json.loads((runtime/'v4-provenance.json').read_text())
    for relative, expected in provenance['files'].items():
        path = runtime/relative
        if not path.resolve().is_relative_to(runtime.resolve()) or checksum(path) != expected['sha256']:
            raise RuntimeError('Pinned runtime differs: '+relative)
    return provenance


@lru_cache(maxsize=1)
def verified_provenance():
    # Lambda mounts the digest-pinned image read-only. Rehashing 2 GiB of assets
    # in every cold environment forces unnecessary remote image-page reads.
    # The Docker build validates every byte; here verify its small attestation.
    raw = (Path(os.environ['V4_RUNTIME_DIR'])/'v4-provenance.json').read_bytes()
    expected = Path('/var/task/benchmark-inputs.sha256').read_text().strip()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise RuntimeError('Immutable image input attestation differs')
    return json.loads(raw)


def lambda_handler(event, context):
    import boto3
    request = request_document(event)
    provenance = verified_provenance()
    runtime = Path(os.environ['V4_RUNTIME_DIR'])
    identity = 'soilie-'+request['roomType']+'-'+str(request['seed'])
    print(json.dumps({'scene': identity, 'stage': 'placing'}), flush=True)
    row = {'id': identity, 'request': request, 'status': 'failed',
           'startedAt': datetime.now(UTC).isoformat(),
           'implementation': {'modelVersion': provenance['version'],
                              'sourceCommit': provenance['baselineCommit'],
                              'renderSha256': provenance['files']['modules/render.py']['sha256']},
           'execution': {'platform': 'AWS Lambda x86_64', 'memoryMB': context.memory_limit_in_mb,
                         'blenderThreads': 1, 'pythonVersion': sys.version.split()[0],
                         'timingScope': 'cloud placement; not local serial timing'}}
    scratch = Path('/tmp/.codex')
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, prefix='room-') as directory:
        work = Path(directory)
        (work/'request.json').write_text(json.dumps(request))
        command = [sys.executable, '-m', 'serverless.cloud_benchmark.worker', '--worker',
                   '--work', str(work), '--runtime', str(runtime),
                   '--blender', os.environ['BLENDER_PATH'], '--blender-threads', '1',
                   '--support', '--solid-mesh-overlap']
        environment = os.environ.copy()
        environment.pop('SOILIE_ROOM_REQUEST', None)
        environment['PYTHONHASHSEED'] = '0'
        # Blender also consults PWD when resolving its startup .blend. Lambda
        # inherits /var/task even for a subprocess with a different cwd.
        environment['PWD'] = str(work)
        # Observer helpers import the tracked model package as well as the
        # adapter. These have separate roots in the image, unlike a checkout.
        environment['PYTHONPATH'] = os.pathsep.join(('/var/task',str(runtime)))
        started = time.perf_counter()
        with (work/'process.log').open('wb') as output:
            process = subprocess.Popen(command, cwd=work, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                # Leave time to persist a failed attempt before Lambda's watchdog.
                process.wait(timeout=max(1, min(900, context.get_remaining_time_in_millis()/1000-20)))
                if process.returncode:
                    row['errorCode'] = 'GENERATION_FAILED'
                else:
                    row.update(json.loads((work/'capture.json').read_text()))
                    row.update(json.loads((work/'worker.json').read_text()))
                    row['status'] = 'complete'
                    for stage in row['stages'].values():
                        stage.update(id=identity, model='soilie', roomType=request['roomType'])
            except subprocess.TimeoutExpired:
                from serverless.benchmark.run_batch import terminate_tree
                terminate_tree(process)
                row['errorCode'] = 'TIMEOUT'
            finally:
                if process.poll() is None:
                    from serverless.benchmark.run_batch import terminate_tree
                    terminate_tree(process)
        row['wallSeconds'] = time.perf_counter()-started
        row['generationSeconds'] = row['wallSeconds']-row.get('observationSeconds', 0)
        logs = (work/'process.log').read_text(errors='replace')
        row['collisionRecoveryActivated'] = 'Object-aware collision recovery activated' in logs
        row['collisionRecoveryMoves'] = logs.count('---| Object-aware move')
        row['boundaryContainmentActivated'] = 'Room containment repair activated' in logs
        if (work/'selection.json').exists():
            row['selection'] = json.loads((work/'selection.json').read_text())['objects']
        if row['status'] != 'complete':
            trace = logs.find('Traceback')
            row['errorTrace'] = logs[trace:trace+2400] if trace >= 0 else ''
            row['errorTail'] = logs[-1200:]
    raw = json.dumps(row, separators=(',', ':')).encode()
    digest = hashlib.sha256(raw).hexdigest()
    # Request IDs prevent a client retry from overwriting the first attempt.
    key = 'attempts/'+identity+'/'+context.aws_request_id+'.json'
    boto3.client('s3').put_object(Bucket=os.environ['RESULT_BUCKET'], Key=key,
                                Body=raw, ContentType='application/json', ServerSideEncryption='AES256',
                                Metadata={'sha256': digest})
    print(json.dumps({'scene': identity, 'status': row['status'],
                      'generationSeconds': row['generationSeconds'], 'errorCode': row.get('errorCode')}))
    return {'key': key, 'sha256': digest, 'status': row['status'],
            'generationSeconds': row['generationSeconds'], 'wallSeconds': row['wallSeconds']}
