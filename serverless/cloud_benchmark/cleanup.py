"""Verify the complete downloaded cloud corpus, archive evidence, remove workers.

Deletes ONLY the isolated temporary benchmark stack/repository and its private
bucket. Every bucket object is downloaded and checksum-verified first. Neither
production Lambda nor the public data bucket is a valid target.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import boto3

from serverless.benchmark.run_batch import write_json
from serverless.benchmark.reobserve_contacts import audit_observation


TEMPORARY = 'soilie3d-benchmark-temporary'


def validate_downloads(output):
    plan = json.loads((output/'plan.json').read_text())
    ledger = json.loads((output/'ledger.json').read_text())
    completion = json.loads((output/'completion.json').read_text())
    if not completion['complete'] or len(ledger['entries']) != 5000 or len(plan['requests']) != 5000:
        raise ValueError('Cloud corpus must be complete before deleting any resources')
    counts, hashes = Counter(), {}
    for task in plan['requests']:
        entry = ledger['entries'][str(task['seed'])]
        raw = (output/'downloads'/f"{task['seed']}.json").read_bytes()
        row = json.loads(raw)
        if entry['status'] != 'complete' or row['status'] != 'complete' or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('Downloaded evidence differs from its receipt')
        if any(row['request'][key] != task[key] for key in ('roomType','seed','objectCount')):
            raise ValueError('Downloaded request does not match the frozen plan')
        if not math.isfinite(row['generationSeconds']) or row['generationSeconds'] <= 0:
            raise ValueError('Invalid generation timing')
        # Exact non-mutating contact rechecks supplement, never overwrite, the
        # raw cloud artifact whose checksum is bound to its paid-call receipt.
        observation=output/'contact-observations'/f"{task['seed']}.json"
        if observation.exists():
            derived=json.loads(observation.read_bytes())
            if derived['contactObservation']['sourceSha256']!=entry['sha256']:
                raise ValueError('Contact observation source checksum mismatch')
            audit_observation(row,derived)
            row=derived
        final = row['stages']['final']
        solids = final['solidMeshOverlap']
        if not solids['complete'] or solids['maxOverlapPct'] > .0001:
            raise ValueError('Final solid geometry requires investigation: '+row['id'])
        for obj in final['objects']:
            support = obj.get('support')
            if support and (support['gapM'] is None or support['gapM'] > .00002 or support['belowFloorM'] > .00002):
                raise ValueError('Final support requires investigation: '+row['id']+'/'+obj['id'])
        counts[row['request']['roomType']] += 1
        hashes[entry['key']] = entry['sha256']
    if counts != {'bedroom':2500,'living_room':2500}:
        raise ValueError('Cloud conditions are not balanced')
    return ledger, hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',default='darkest')
    parser.add_argument('--region',default='ca-central-1')
    args = parser.parse_args()
    ledger, expected = validate_downloads(args.output)
    if ledger['function'] != TEMPORARY or not ledger['bucket'].startswith(TEMPORARY+'-results-'):
        raise ValueError('Cleanup is restricted to isolated temporary benchmark resources')
    session = boto3.Session(profile_name=args.profile,region_name=args.region)
    cfn, s3 = session.client('cloudformation'), session.client('s3')
    stack = cfn.describe_stacks(StackName=TEMPORARY)['Stacks'][0]
    outputs = {row['OutputKey']:row['OutputValue'] for row in stack['Outputs']}
    if outputs != {'FunctionName':TEMPORARY,'ResultBucket':ledger['bucket']}:
        raise ValueError('CloudFormation targets differ from verified evidence')
    session.client('lambda').put_function_concurrency(FunctionName=TEMPORARY,ReservedConcurrentExecutions=0)
    archive = args.output/'cloud-archive'
    archive.mkdir(exist_ok=True)
    objects = [row for page in s3.get_paginator('list_objects_v2').paginate(Bucket=ledger['bucket'])
               for row in page.get('Contents',[])]
    if not set(expected) <= {row['Key'] for row in objects} or any(not row['Key'].startswith('attempts/') for row in objects):
        raise ValueError('Bucket contains unexpected keys or is missing evidence')
    def download(obj):
        item = s3.get_object(Bucket=ledger['bucket'],Key=obj['Key'])
        raw = item['Body'].read()
        digest = hashlib.sha256(raw).hexdigest()
        if item['Metadata'].get('sha256') != digest or (obj['Key'] in expected and expected[obj['Key']] != digest):
            raise ValueError('Remote evidence checksum mismatch')
        path = archive/(digest+'.json')
        path.write_bytes(raw)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Local archive checksum mismatch')
        return {'key':obj['Key'],'sha256':digest,'bytes':len(raw)}
    with ThreadPoolExecutor(max_workers=12) as pool:
        copied = list(pool.map(download,objects))
    # Save structured cloud logs before stack deletion, including the pilot.
    logs = session.client('logs')
    events = [row for page in logs.get_paginator('filter_log_events').paginate(logGroupName='/aws/lambda/'+TEMPORARY)
              for row in page.get('events',[])]
    write_json(archive/'cloudwatch.json',events)
    write_json(archive/'objects.json',copied)
    # A second listing prevents an unnoticed invocation from writing between
    # the verified snapshot and resource removal.
    current = [row for page in s3.get_paginator('list_objects_v2').paginate(Bucket=ledger['bucket'])
               for row in page.get('Contents',[])]
    if {(row['Key'],row['ETag']) for row in current} != {(row['Key'],row['ETag']) for row in objects}:
        raise ValueError('Bucket changed during verification; do not delete')
    cfn.delete_stack(StackName=TEMPORARY)
    cfn.get_waiter('stack_delete_complete').wait(StackName=TEMPORARY)
    for offset in range(0,len(objects),1000):
        response = s3.delete_objects(Bucket=ledger['bucket'],Delete={'Objects':[{'Key':row['Key']} for row in objects[offset:offset+1000]]})
        if response.get('Errors'):
            raise RuntimeError('Some archived objects were not deleted')
    s3.delete_bucket(Bucket=ledger['bucket'])
    session.client('ecr').delete_repository(repositoryName=TEMPORARY,force=True)
    write_json(args.output/'cleanup.json',{'complete':True,'verifiedScenes':5000,'archivedObjects':len(copied),
                                         'removed':['temporary Lambda','temporary IAM role','temporary CloudWatch group','temporary S3 bucket','temporary ECR repository']})
    print('Verified 5,000 cloud scenes. Temporary AWS resources removed; evidence retained locally.',flush=True)


if __name__ == '__main__':
    main()
