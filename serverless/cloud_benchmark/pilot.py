"""Four fixed-seed parity probes before approving a finite cloud campaign."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import time

import boto3
from botocore.config import Config

from serverless.cloud_benchmark.checkpoint import write_json


def compare_placements(local, remote, tolerance=1e-5):
    """Cloud timing is different; random choices and geometry must agree."""
    for key in ('request', 'selection', 'implementation'):
        if local[key] != remote[key]:
            raise ValueError('Parity mismatch: '+key)
    if local['status'] != 'complete' or remote['status'] != 'complete':
        raise ValueError('Both attempts must complete')
    maximum = 0.0
    def compare(left, right, label):
        nonlocal maximum
        if isinstance(left, (int, float)):
            delta = abs(left-right)
            if not math.isfinite(delta) or delta > tolerance:
                raise ValueError('Placement mismatch: '+label)
            maximum = max(maximum, delta)
        elif isinstance(left, list):
            if len(left) != len(right):
                raise ValueError('Length mismatch: '+label)
            for index, (a, b) in enumerate(zip(left, right)):
                compare(a, b, label+'/'+str(index))
        elif left != right:
            raise ValueError('Placement mismatch: '+label)
    for stage in ('beforeSeparation', 'afterSeparation', 'final'):
        a, b = local['stages'][stage], remote['stages'][stage]
        compare(a['room']['polygon'], b['room']['polygon'], stage+'/room')
        compare(a['room']['floorZ'], b['room']['floorZ'], stage+'/floor')
        if len(a['objects']) != len(b['objects']):
            raise ValueError('Object count mismatch')
        for obj, other in zip(a['objects'], b['objects']):
            for key in ('id', 'label', 'asset', 'kind', 'corners', 'transform', 'frontDirection'):
                compare(obj[key], other[key], stage+'/'+obj['id']+'/'+key)
    return maximum


def invoke_and_download(session, function, bucket, request, output, require_success=True, clients=None):
    # Disable invisible SDK retries: ambiguous network outcomes need reconciliation
    # against S3 before another paid call, not automatic duplicate compute.
    client = clients['lambda'] if clients else session.client('lambda', config=Config(read_timeout=920, connect_timeout=15,
                                                   retries={'total_max_attempts': 1}))
    output.mkdir(parents=True, exist_ok=True)
    identity_path = output/(str(request['seed'])+'-invocation.json')
    if identity_path.exists():
        raise ValueError('Invocation identity already exists; reconcile instead of replaying paid work')
    started = time.monotonic()
    response = client.invoke(FunctionName=function, InvocationType='RequestResponse',
                             LogType='Tail', Payload=json.dumps(request).encode())
    logs = base64.b64decode(response.get('LogResult', '')).decode(errors='replace')
    report_lines = [line for line in logs.splitlines() if re.match(r'^REPORT RequestId:\s*[\da-fA-F-]{36}\b', line)]
    request_ids = set(re.findall(r'(?:START|END|REPORT) RequestId:\s*([\da-fA-F-]{36})\b', logs))
    identity = {'schemaVersion': 1, 'request': request, 'function': function, 'bucket': bucket,
                'executedVersion': response.get('ExecutedVersion'),
                # The Invoke API request ID and execution request ID are distinct
                # fields. Only Lambda's own execution marker binds its REPORT.
                'invokeApiRequestId': response.get('ResponseMetadata', {}).get('RequestId'),
                'awsRequestId': next(iter(request_ids)) if len(request_ids) == 1 else None,
                'logTail': logs}
    # Headers and the service log tail arrive before the streaming body is
    # consumed. Persist them first so an interrupted read does not lose the
    # original invocation identity or provoke another paid invocation.
    write_json(identity_path, identity)
    body = json.loads(response['Payload'].read())
    report = report_lines[0] if len(report_lines) == 1 else ''
    billed = re.search(r'Billed Duration:\s*([\d.]+) ms', report)
    memory = re.search(r'Max Memory Used:\s*(\d+) MB', report)
    record = {'request': request, 'wallSeconds': time.monotonic()-started,
              'billedSeconds': float(billed[1])/1000 if billed else None,
              'maxMemoryMB': int(memory[1]) if memory else None, 'response': body,
              'functionError': response.get('FunctionError'), 'logTail': logs,
              'awsRequestId': identity['awsRequestId'],
              'invocationIdentitySha256': hashlib.sha256(identity_path.read_bytes()).hexdigest()}
    receipt = output/(str(request['seed'])+'-receipt.json')
    write_json(receipt, record)
    if response.get('FunctionError'):
        raise RuntimeError('Lambda failed; see '+str(receipt))
    storage = clients['s3'] if clients else session.client('s3')
    raw = storage.get_object(Bucket=bucket, Key=body['key'])['Body'].read()
    if hashlib.sha256(raw).hexdigest() != body['sha256']:
        raise RuntimeError('Downloaded evidence checksum mismatch')
    (output/(str(request['seed'])+'.json')).write_bytes(raw)
    row = json.loads(raw)
    if require_success and row['status'] != 'complete':
        raise RuntimeError('Generation failed: '+str(row.get('errorCode')))
    return row, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--function', required=True)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bedrooms', type=Path)
    parser.add_argument('--references', type=Path, help='Explicit list of eight local artifact paths')
    parser.add_argument('--profile', default='darkest')
    parser.add_argument('--region', default='ca-central-1')
    args = parser.parse_args()
    rows = ([json.loads(Path(path).read_text()) for path in json.loads(args.references.read_text())] if args.references else
            [json.loads((args.campaign/f'living-{index:02d}/attempt-00000.json').read_text()) for index in range(4)])
    if args.bedrooms and not args.references:
        rows += [json.loads((args.bedrooms/f'attempt-{index:05d}.json').read_text()) for index in range(4)]
    def run(local):
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        event = {key: local['request'][key] for key in ('seed', 'objectCount', 'roomType')}
        remote, receipt = invoke_and_download(session, args.function, args.bucket, event, args.output)
        result = {'seed': event['seed'], 'objectCount': event['objectCount'], 'roomType': event['roomType'],
                  'maxGeometryErrorM': compare_placements(local, remote),
                  'billedSeconds': receipt['billedSeconds'], 'maxMemoryMB': receipt['maxMemoryMB'],
                  'generationSeconds': remote['generationSeconds']}
        print(json.dumps(result), flush=True)
        return result
    # Independent SDK sessions avoid shared credential-refresh races in threads.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, rows))
    config = boto3.Session(profile_name=args.profile, region_name=args.region).client('lambda').get_function(FunctionName=args.function)
    (args.output/'parity.json').write_text(json.dumps({'passed': True, 'scenes': results,
        'function': args.function, 'image': config['Code']['ResolvedImageUri'],
        'memoryMB': config['Configuration']['MemorySize']}, indent=2))


if __name__ == '__main__':
    main()
