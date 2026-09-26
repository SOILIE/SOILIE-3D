"""Reconcile durable S3 results and CloudWatch billing, without repeating work."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import boto3

from serverless.cloud_benchmark.checkpoint import write_json
from serverless.infinigen_cloud.handler import request_parameters
from serverless.benchmark.infinigen_task import COMMIT


def valid_result(event, body):
    room, condition, index, seed, count, profile = request_parameters(event)
    return (body.get('id') == f'{condition}-{room}-{index:02d}'
            and all(body.get(key) == value for key, value in event.items())
            and body.get('seed') == seed and body.get('profile') == profile
            and body.get('sourceCommit') == COMMIT and body.get('memoryMb') == 6144)


def attach_billing(entries, events, rates):
    """Pair completion IDs with REPORT in their own stream, never across calls."""
    streams = {}
    for event in events:
        streams.setdefault(event['logStreamName'], []).append(event)
    for stream in streams.values():
        identity = None
        for event in sorted(stream, key=lambda row: row['timestamp']):
            message = event['message']
            if message.startswith('START RequestId:'):
                identity = None
            try:
                body = json.loads(message)
            except (ValueError, TypeError):
                body = {}
            if body.get('stage') == 'complete':
                identity = body.get('id')
            if identity in entries and message.startswith('REPORT RequestId:'):
                billed = re.search(r'Billed Duration: ([\d.]+) ms', message)
                used = re.search(r'Max Memory Used: (\d+) MB', message)
                if billed:
                    entry = entries[identity]
                    entry['billedDurationMs'] = float(billed[1])
                    entry['usagePricedUsd'] = entry['billedDurationMs'] / 1000 * (
                        6 * rates['computeUsdPerGbSecond'] + 9.5 * rates['storageUsdPerGbSecond']) + rates['requestUsd']
                    entry['billingReport'] = message
                    if used:
                        entry['reportedMaxMemoryMb'] = int(used[1])
                identity = None


def reconcile(campaign_path, evidence_path, output_path):
    if output_path.exists():
        raise ValueError('Reconciliation cannot overwrite evidence')
    original = json.loads(campaign_path.read_bytes())
    if not original.get('cleanupComplete'):
        raise ValueError('Only closed campaigns may be reconciled')
    state = deepcopy(original)
    s3 = boto3.Session(profile_name='darkest', region_name='ca-central-1').client('s3')
    for identity, entry in state['entries'].items():
        key = f"files/outputs/runtime-pilot-2026-09-25/{state['name']}/{identity}/result.json"
        response = s3.get_object(Bucket='soilie3d-data', Key=key)
        payload = response['Body'].read()
        result = json.loads(payload)
        if not valid_result(entry['request'], result) or result['status'] != 'complete':
            raise ValueError('Invalid or incomplete durable result')
        entry.update(status='complete', result=result, resultSha256=hashlib.sha256(payload).hexdigest())
    evidence = json.loads(evidence_path.read_bytes())
    attach_billing(state['entries'], evidence['events'], state['rates'])
    if any('billedDurationMs' not in row for row in state['entries'].values()):
        raise ValueError('Missing independent billing evidence')
    state['status'] = 'verified_pilot'
    state['originalCampaignSha256'] = hashlib.sha256(campaign_path.read_bytes()).hexdigest()
    state['cloudWatchEvidenceSha256'] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    state['accountedUsd'] = state.get('priorAccountedUsd', 0) + sum(row['usagePricedUsd'] for row in state['entries'].values())
    write_json(output_path, state)
    print(json.dumps({'verified': len(state['entries']), 'accountedUsd': state['accountedUsd']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reconcile(args.campaign, args.evidence, args.output)
