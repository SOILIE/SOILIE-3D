"""Temporary four-condition Lambda pilot, with a USD 10 hard reservation cap.

Run only after build.ps1 passes. The four first cases gate the remaining 76.
Invocations never automatically retry; ambiguous outcomes retain a worst-case
reservation. Infrastructure is removed even if the pilot or campaign fails.
Private receipts stay local; generated scene files and compact results go to S3.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading

import boto3
from botocore.config import Config

from serverless.cloud_benchmark.checkpoint import write_json
from serverless.benchmark.run_batch import run_lock

MEMORY_MB = 6144
MAX_SECONDS = 930  # Handler timeout plus conservative cold-start allowance.
CAP_USD = 10


def reserve_usd(rates):
    return MAX_SECONDS * (6 * rates['computeUsdPerGbSecond'] + 9.5 * rates['storageUsdPerGbSecond']) + rates['requestUsd']


def requests():
    return [{'roomType': room, 'condition': condition, 'index': index}
            for index in range(20) for room in ('bedroom', 'living_room')
            for condition in ('room-scale', 'controlled')]


def run(output, name, rates_path, profile='darkest', region='ca-central-1'):
    if not re.fullmatch(r'soilie-infinigen-timing-[a-z0-9-]+', name):
        raise ValueError('Dedicated temporary resource name required')
    output.mkdir(parents=True, exist_ok=True)
    with run_lock(output):
        state_path = output / 'campaign.json'
        if state_path.exists():
            raise ValueError('Existing campaign is evidence, not permission to repeat paid calls')
        rates = json.loads(rates_path.read_bytes())['lambda']
        reservation = reserve_usd(rates)
        if reservation * 80 > CAP_USD:
            raise ValueError('Worst-case campaign exceeds the spending ceiling')
        session = boto3.Session(profile_name=profile, region_name=region)
        ecr, cfn = session.client('ecr'), session.client('cloudformation')
        client = session.client('lambda', config=Config(read_timeout=960, connect_timeout=30,
                                retries={'total_max_attempts': 1}, max_pool_connections=24))
        state = {'schemaVersion': 1, 'capUsd': CAP_USD, 'memoryMb': MEMORY_MB,
                 'reservationPerCallUsd': reservation, 'rates': rates, 'name': name,
                 'entries': {}, 'status': 'preparing', 'cleanupComplete': False}
        write_json(state_path, state)
        repository_created = stack_created = False
        prefix = 'files/outputs/runtime-pilot-2026-09-25/' + name
        lock = threading.Lock()
        def account():
            return sum(row.get('usagePricedUsd', reservation) for row in state['entries'].values())
        try:
            image_size = int(subprocess.check_output(['docker', 'image', 'inspect', 'soilie-infinigen-timing:pilot', '--format', '{{.Size}}'], text=True))
            if image_size >= 9_500_000_000:
                raise ValueError('Image too large for the Lambda safety gate')
            state['imageBytes'] = image_size
            unpacked = int(subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'du',
                'soilie-infinigen-timing:pilot', '-sx', '-B1', '/'], text=True).split()[0])
            if unpacked >= 9_000_000_000:
                raise ValueError('Unpacked image exceeds the safety margin')
            state['unpackedFilesystemBytes'] = unpacked
            repository = ecr.create_repository(repositoryName=name, imageTagMutability='IMMUTABLE')['repository']
            repository_created = True
            state['repository'] = repository['repositoryUri']
            write_json(state_path, state)
            auth = ecr.get_authorization_token()['authorizationData'][0]
            token = base64.b64decode(auth['authorizationToken']).decode().split(':', 1)[1]
            subprocess.run(['docker', 'login', '--username', 'AWS', '--password-stdin', auth['proxyEndpoint']], input=token, text=True, check=True, capture_output=True)
            tag = repository['repositoryUri'] + ':pilot'
            subprocess.run(['docker', 'tag', 'soilie-infinigen-timing:pilot', tag], check=True)
            with (output / 'image-push.log').open('wb') as log:
                subprocess.run(['docker', 'push', tag], stdout=log, stderr=subprocess.STDOUT, check=True)
            digest = ecr.describe_images(repositoryName=name, imageIds=[{'imageTag': 'pilot'}])['imageDetails'][0]['imageDigest']
            state['imageDigest'] = digest
            template = Path(__file__).with_name('template.json').read_text()
            state['templateSha256'] = hashlib.sha256(template.encode()).hexdigest()
            cfn.create_stack(StackName=name, TemplateBody=template, Capabilities=['CAPABILITY_IAM'], Parameters=[
                {'ParameterKey': 'ImageUri', 'ParameterValue': repository['repositoryUri'] + '@' + digest},
                {'ParameterKey': 'OutputBucket', 'ParameterValue': 'soilie3d-data'},
                {'ParameterKey': 'OutputPrefix', 'ParameterValue': prefix}])
            stack_created = True
            write_json(state_path, state)
            cfn.get_waiter('stack_create_complete').wait(StackName=name, WaiterConfig={'Delay': 10, 'MaxAttempts': 120})
            state['status'] = 'pilot'
            write_json(state_path, state)

            def invoke(event):
                identity = f"{event['condition']}-{event['roomType']}-{event['index']:02d}"
                with lock:
                    if identity in state['entries'] or account() + reservation > CAP_USD:
                        raise ValueError('Duplicate request or exhausted budget')
                    entry = {'request': event, 'status': 'reserved', 'reservedUsd': reservation}
                    state['entries'][identity] = entry
                    write_json(state_path, state)
                try:
                    response = client.invoke(FunctionName=name, InvocationType='RequestResponse',
                                             LogType='Tail', Payload=json.dumps(event).encode())
                    body = json.loads(response['Payload'].read())
                    tail = base64.b64decode(response.get('LogResult', '')).decode(errors='replace')
                    entry['result'] = body
                    entry['status'] = 'complete' if not response.get('FunctionError') and body.get('status') == 'complete' else 'failed'
                    billed = re.search(r'Billed Duration: ([\d.]+) ms', tail)
                    used = re.search(r'Max Memory Used: (\d+) MB', tail)
                    entry['logTail'] = tail
                    if billed:
                        entry['billedDurationMs'] = float(billed[1])
                        entry['usagePricedUsd'] = entry['billedDurationMs'] / 1000 * (6 * rates['computeUsdPerGbSecond'] + 9.5 * rates['storageUsdPerGbSecond']) + rates['requestUsd']
                    if used:
                        entry['reportedMaxMemoryMb'] = int(used[1])
                except Exception as error:
                    entry.update(status='uncertain', errorCode=type(error).__name__)
                with lock:
                    state['accountedUsd'] = account()
                    write_json(state_path, state)
                    print(json.dumps({'id': identity, 'status': entry['status'], 'accountedUsd': state['accountedUsd']}), flush=True)
                return entry['status'] == 'complete'

            jobs = requests()
            with ThreadPoolExecutor(max_workers=4) as pool:
                passed = list(pool.map(invoke, jobs[:4]))
            if not all(passed):
                state['status'] = 'pilot_failed'
                return
            state['status'] = 'running'
            write_json(state_path, state)
            with ThreadPoolExecutor(max_workers=20) as pool:
                list(pool.map(invoke, jobs[4:]))
            state['status'] = 'complete' if all(row['status'] == 'complete' for row in state['entries'].values()) else 'needs_review'
        finally:
            # Delete only resources this invocation successfully created. Never
            # clean up a name merely because it resembles a benchmark resource.
            cleanup_errors = []
            if stack_created:
                try:
                    cfn.delete_stack(StackName=name)
                    cfn.get_waiter('stack_delete_complete').wait(StackName=name, WaiterConfig={'Delay': 10, 'MaxAttempts': 120})
                except Exception as error:
                    cleanup_errors.append({'resource': 'stack', 'errorCode': type(error).__name__})
            if repository_created:
                try:
                    ecr.delete_repository(repositoryName=name, force=True)
                except Exception as error:
                    cleanup_errors.append({'resource': 'repository', 'errorCode': type(error).__name__})
            state['accountedUsd'] = account()
            state['cleanupComplete'] = not cleanup_errors
            state['cleanupErrors'] = cleanup_errors
            write_json(state_path, state)
            print(json.dumps({'status': state['status'], 'attempted': len(state['entries']),
                'accountedUsd': state['accountedUsd'], 'cleanupComplete': state['cleanupComplete']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--rates', type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.name, args.rates)
