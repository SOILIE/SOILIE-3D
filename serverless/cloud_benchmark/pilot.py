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
    started = time.monotonic()
    response = client.invoke(FunctionName=function, InvocationType='RequestResponse',
                             LogType='Tail', Payload=json.dumps(request).encode())
    body = json.loads(response['Payload'].read())
    logs = base64.b64decode(response.get('LogResult', '')).decode(errors='replace')
    billed = re.search(r'Billed Duration:\s*([\d.]+) ms', logs)
    memory = re.search(r'Max Memory Used:\s*(\d+) MB', logs)
    record = {'request': request, 'wallSeconds': time.monotonic()-started,
              'billedSeconds': float(billed[1])/1000 if billed else None,
              'maxMemoryMB': int(memory[1]) if memory else None, 'response': body,
              'functionError': response.get('FunctionError'), 'logTail': logs}
    output.mkdir(parents=True, exist_ok=True)
    receipt = output/(str(request['seed'])+'-receipt.json')
    receipt.write_text(json.dumps(record, indent=2))
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
    parser.add_argument('--profile', default='darkest')
    parser.add_argument('--region', default='ca-central-1')
    args = parser.parse_args()
    rows = [json.loads((args.campaign/f'living-{index:02d}/attempt-00000.json').read_text()) for index in range(4)]
    if args.bedrooms:
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
