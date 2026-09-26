"""Allowlist complete cloud measurements without exposing operational receipts."""
import json
import math

from serverless.infinigen_cloud.handler import request_parameters
from serverless.benchmark.infinigen_task import COMMIT


def measured_rows(path):
    campaign = json.loads(path.read_bytes())
    if campaign['status'] != 'complete' or not campaign['cleanupComplete'] or len(campaign['entries']) != 80:
        raise ValueError('Require a closed, complete four-condition cloud cohort')
    rows, identities = [], set()
    for entry in campaign['entries'].values():
        room, condition, index, seed, count, profile = request_parameters(entry['request'])
        result = entry['result']
        identity = f'{condition}-{room}-{index:02d}'
        if identity in identities:
            raise ValueError('Repeated cloud timing observation')
        identities.add(identity)
        if (entry['status'] != 'complete' or result['status'] != 'complete'
                or result['id'] != identity or result['sourceCommit'] != COMMIT
                or result['seed'] != seed or result['profile'] != profile
                or result['memoryMb'] != 6144 or result['blenderThreads'] != 4
                or result['objectCount'] < 1):
            raise ValueError('Changed or incomplete cloud generation contract')
        if condition == 'controlled' and result['objectCount'] != count:
            raise ValueError('Controlled inventory differs')
        seconds = result['generationSeconds']
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('Finite positive construction time required')
        rows.append({'id': 'infinigen-cloud-' + identity,
            'model': 'infinigen' if condition == 'room-scale' else 'infinigen_controlled',
            'roomType': room, 'basis': 'measured-generation-stage', 'seconds': seconds,
            'memoryMb': 6144, 'objectCount': result['objectCount']})
    return sorted(rows, key=lambda row: row['id'])
