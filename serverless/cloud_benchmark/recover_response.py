"""Recover an already-paid invocation from private S3 and CloudWatch evidence.

This never invokes Lambda. Ambiguous multiple attempts, missing billing logs,
wrong requests and checksum mismatches are rejected rather than guessed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

import boto3

from serverless.benchmark.run_batch import run_lock
from serverless.cloud_benchmark.checkpoint import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--profile',default='darkest')
    parser.add_argument('--region',default='ca-central-1')
    args=parser.parse_args()
    with run_lock(args.output):
        ledger=json.loads((args.output/'ledger.json').read_text())
        task=ledger['entries'][str(args.seed)]['task']
        event={key:task[key] for key in ('seed','roomType','objectCount')}
        session=boto3.Session(profile_name=args.profile,region_name=args.region)
        s3=session.client('s3')
        prefix=f"attempts/soilie-{task['roomType']}-{args.seed}/"
        objects=s3.list_objects_v2(Bucket=ledger['bucket'],Prefix=prefix).get('Contents',[])
        if len(objects)!=1:
            raise ValueError('Need exactly one identifiable invocation artifact; inspect manually')
        key=objects[0]['Key']
        response=s3.get_object(Bucket=ledger['bucket'],Key=key)
        raw=response['Body'].read()
        digest=hashlib.sha256(raw).hexdigest()
        row=json.loads(raw)
        if response['Metadata'].get('sha256')!=digest or any(row['request'][field]!=event[field] for field in event):
            raise ValueError('Remote checksum/request mismatch')
        request_id=key.rsplit('/',1)[1].removesuffix('.json')
        logs=session.client('logs')
        lines=[item['message'] for page in logs.get_paginator('filter_log_events').paginate(
            logGroupName='/aws/lambda/'+ledger['function'],filterPattern='"'+request_id+'"') for item in page.get('events',[])]
        reports=[line for line in lines if 'REPORT RequestId: '+request_id in line]
        if len(reports)!=1:
            raise ValueError('No unique CloudWatch billing report; do not invent duration')
        billed=re.search(r'Billed Duration:\s*([\d.]+) ms',reports[0])
        memory=re.search(r'Max Memory Used:\s*(\d+) MB',reports[0])
        if not billed:
            raise ValueError('Billing duration missing')
        receipt={'request':event,'wallSeconds':None,'billedSeconds':float(billed[1])/1000,
                 'maxMemoryMB':int(memory[1]) if memory else None,'functionError':None,
                 'response':{'key':key,'sha256':digest,'status':row['status'],
                             'generationSeconds':row['generationSeconds'],'wallSeconds':row['wallSeconds']},
                 'logTail':'\n'.join(lines),'recoveredFrom':'S3 artifact and CloudWatch REPORT; no invocation replayed'}
        folder=args.output/'downloads'
        path=folder/(str(args.seed)+'-receipt.json')
        if path.exists():
            raise ValueError('A receipt already exists; do not replace it')
        (folder/(str(args.seed)+'.json')).write_bytes(raw)
        write_json(path,receipt)
        print(json.dumps({'seed':args.seed,'status':row['status'],'billedSeconds':receipt['billedSeconds']}),flush=True)


if __name__=='__main__':
    main()
