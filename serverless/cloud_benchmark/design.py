"""Frozen 2 x 2 room/platform allocation, independent of observed quality.

Local bedrooms reuse the first 2,500 archived records. Local living requests
are a prefix of the original interleaved seed schedule, not cherry-picked
successes. Both platforms receive 625 requests at each count from 3 to 6.
"""
from collections import Counter
import hashlib
import json

from serverless.benchmark.run_batch import write_json


def allocation(document):
    if document['bedroomCount'] != 5000 or document['livingRoomCount'] != 5000:
        raise ValueError('Expected the original frozen 5,000 + 5,000 design')
    local, cloud = [], []
    for shard in document['livingShards']:
        if shard['target'] != 250:
            raise ValueError('Expected twenty 250-request living-room shards')
        split = 250 if shard['index'] < 8 else 125 if shard['index'] < 12 else 0
        if split:
            local.append({**shard, 'target': split})
        for index in range(split, 250):
            cloud.append({'roomType': 'living_room', 'shard': shard['index'], 'shardAttempt': index,
                          'seed': shard['seed']+index*shard['seedStep'], 'objectCount': shard['objectCount']})
    # A disjoint, fixed seed range, fixed before inspecting any new bedrooms.
    bedrooms = [{'roomType': 'bedroom', 'seed': 120260924+997*index, 'objectCount': 3+index%4}
                for index in range(2500)]
    # Interleave room types so a cost stop does not preferentially finish one.
    cloud = [task for pair in zip(bedrooms, cloud) for task in pair]
    if Counter((row['roomType'], row['objectCount']) for row in cloud) != {
            (room, count): 625 for room in ('bedroom', 'living_room') for count in range(3, 7)}:
        raise ValueError('Cloud allocation is not balanced')
    if sum(row['target'] for row in local) != 2500 or len({row['seed'] for row in cloud}) != 5000:
        raise ValueError('Incorrect size or duplicated cloud seed')
    return local, cloud


def freeze(campaign, output):
    output.mkdir(parents=True, exist_ok=True)
    path = output/'plan.json'
    if path.exists():
        result = json.loads(path.read_text())
    else:
        document = json.loads((campaign/'campaign.json').read_text())
        local, cloud = allocation(document)
        bedrooms = document['bedroomSelection'][:2500]
        if len(bedrooms) != 2500 or len({row['file'] for row in bedrooms}) != 2500:
            raise ValueError('Exactly 2,500 distinct retained bedrooms are required')
        result = {'schemaVersion': 2, 'targetPerCondition': 2500,
                  'localBedrooms': bedrooms,
                  'localLivingShards': local, 'requests': cloud,
                  'timingPolicy': 'Report room type and platform separately. Original local bedroom generation and later support correction retain distinct timing fields. Cloud generation time excludes observation; billed duration includes it and startup.'}
        write_json(path, result)
    for row in result['localBedrooms']:
        if hashlib.sha256((campaign/'bedroom-source'/row['file']).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Retained bedroom source changed')
    allowed = {row['index']: row['target'] for row in result['localLivingShards']}
    # A local worker must never race a cloud allocation. Inspect records, not
    # stale controller counters, and reject any unexpected completed request.
    for folder in campaign.glob('living-*'):
        if not folder.is_dir() or not folder.name[7:].isdigit():
            continue
        limit = allowed.get(int(folder.name[7:]), 0)
        for scene in folder.glob('attempt-*.json'):
            row = json.loads(scene.read_text())
            # Shards 00-07 are wholly local, including any recorded retries.
            # For split shards 08-11, never cross the cloud seed boundary.
            if not limit or (int(folder.name[7:]) >= 8 and row['attempt'] >= limit):
                raise ValueError('Local attempt needs allocation review: '+str(scene))
    return result
