"""Reconcile already-paid responses after an interrupted client, without calls.

Requires every dispatched request's receipt and checksum-verified artifact.
Unknown/missing responses remain blocked; this never silently retries a scene.
"""
import argparse
import hashlib
import json
from pathlib import Path

from serverless.benchmark.run_batch import run_lock
from serverless.cloud_benchmark.campaign import compute_cost
from serverless.cloud_benchmark.checkpoint import write_json


def reconcile(output):
    path = output/'ledger.json'
    ledger = json.loads(path.read_text())
    changed, failed = 0, []
    for key, entry in ledger['entries'].items():
        receipt = output/'downloads'/(key+'-receipt.json')
        if not receipt.exists():
            raise ValueError('Missing response; inspect private S3 and CloudWatch for seed '+key)
        response = json.loads(receipt.read_text())
        event = {field:entry['task'][field] for field in ('roomType','seed','objectCount')}
        if response['request'] != event or response.get('billedSeconds') is None:
            raise ValueError('Receipt identity or billing evidence missing')
        if response['functionError']:
            entry.update(status='failed',error=response['functionError'],
                         billedSeconds=response['billedSeconds'],costUSD=compute_cost(response['billedSeconds'],ledger['memoryMB']))
            failed.append(key)
            continue
        raw = (output/'downloads'/(key+'.json')).read_bytes()
        row = json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != response['response']['sha256'] or any(row['request'][field] != event[field] for field in event):
            raise ValueError('Artifact differs from the recorded response')
        if entry['status'] != row['status']:
            changed += 1
        entry.update(status=row['status'],costUSD=compute_cost(response['billedSeconds'],ledger['memoryMB']),
                     billedSeconds=response['billedSeconds'],sha256=digest,key=response['response']['key'])
        if row['status'] != 'complete':
            failed.append(key)
    write_json(path,ledger)
    return {'reconciled':changed,'completed':sum(row['status']=='complete' for row in ledger['entries'].values()),'failed':failed}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with run_lock(args.output):
        print(json.dumps(reconcile(args.output)),flush=True)


if __name__=='__main__':
    main()
