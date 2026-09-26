"""Allowlist complete cloud measurements without exposing operational receipts."""
import json
import math
import argparse
from pathlib import Path

from serverless.infinigen_cloud.handler import request_parameters
from serverless.benchmark.infinigen_task import COMMIT


def completed_campaign(path):
    campaign = json.loads(path.read_bytes())
    if campaign['status'] not in ('complete', 'needs_review') or not campaign['cleanupComplete'] or len(campaign['entries']) != 80:
        raise ValueError('Require a closed, fully attempted four-condition cloud cohort')
    if any(row['status'] not in ('complete', 'failed') for row in campaign['entries'].values()):
        raise ValueError('Unresolved invocation outcomes cannot enter publication')
    return campaign


def completion_coverage(path):
    campaign = completed_campaign(path)
    coverage = {room: {model: {'attempted': 0, 'completed': 0, 'notCompleted': 0}
                         for model in ('infinigen', 'infinigen_controlled')}
                for room in ('bedroom', 'living_room')}
    for entry in campaign['entries'].values():
        room, condition, *_ = request_parameters(entry['request'])
        group = coverage[room]['infinigen' if condition == 'room-scale' else 'infinigen_controlled']
        group['attempted'] += 1
        group['completed' if entry['status'] == 'complete' else 'notCompleted'] += 1
    if any(group['attempted'] != 20 or not group['completed'] for room in coverage.values() for group in room.values()):
        raise ValueError('Each condition requires twenty attempts and at least one completed observation')
    return coverage


def measured_rows(path):
    campaign = completed_campaign(path)
    rows, identities = [], set()
    for entry in campaign['entries'].values():
        room, condition, index, seed, count, profile = request_parameters(entry['request'])
        result = entry['result']
        identity = f'{condition}-{room}-{index:02d}'
        if identity in identities:
            raise ValueError('Repeated cloud timing observation')
        identities.add(identity)
        if (entry['status'] != result['status']
                or result['id'] != identity or result['sourceCommit'] != COMMIT
                or result['seed'] != seed or result['profile'] != profile
                or result['memoryMb'] != 6144 or result['blenderThreads'] != 4):
            raise ValueError('Changed or incomplete cloud generation contract')
        # A stopped solver is an attempted case, not a fast completed layout.
        # Keep its completion count but do not insert its duration as a success.
        if entry['status'] == 'failed':
            if not result.get('errorCode'):
                raise ValueError('A failed attempt requires an explicit outcome')
            continue
        if result['objectCount'] < 1:
            raise ValueError('Completed scene has no room furniture')
        if condition == 'controlled' and result['objectCount'] != count:
            raise ValueError('Controlled inventory differs')
        seconds = result['generationSeconds']
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('Finite positive construction time required')
        rows.append({'id': 'infinigen-cloud-' + identity,
            'model': 'infinigen' if condition == 'room-scale' else 'infinigen_controlled',
            'roomType': room, 'basis': 'measured-generation-stage', 'seconds': seconds,
            'memoryMb': 6144, 'objectCount': result['objectCount']})
    completion_coverage(path)
    return sorted(rows, key=lambda row: row['id'])


def index_outputs(path, client):
    """Expose completed scene artifacts through Data, never private receipts."""
    from serverless.benchmark.archive import merge_index
    measured_rows(path)  # The same completion/provenance gate as the charts.
    campaign = completed_campaign(path)
    keys = []
    for entry in campaign['entries'].values():
        if entry['status'] != 'complete':
            continue
        artifacts = entry['result']['artifacts']
        public = [row for row in artifacts if row['file'] in ('scene.blend.gz', 'solve_state.json.gz')]
        if len(public) != 2:
            raise ValueError('A completed cloud scene must retain both geometry artifacts')
        folders = set()
        for artifact in public:
            key = artifact['key']
            if (not key.startswith('files/outputs/runtime-pilot-2026-09-25/soilie-infinigen-timing-')
                    or not key.endswith('/' + entry['result']['id'] + '/' + artifact['file'])):
                raise ValueError('Artifact outside the authorized cloud-output namespace')
            receipt = client.head_object(Bucket='soilie3d-data', Key=key)
            if receipt['ContentLength'] != artifact['bytes']:
                raise ValueError('Stored artifact size differs from the generator receipt')
            keys.append(key)
            folders.add(key.rsplit('/', 1)[0])
        if len(folders) != 1:
            raise ValueError('Scene artifacts are from different invocations')
        result_key = folders.pop() + '/result.json'
        client.head_object(Bucket='soilie3d-data', Key=result_key)
        keys.append(result_key)
    return {'sceneFiles': len(keys), 'indexedKeys': merge_index(client, 'soilie3d-data', keys)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--index-outputs', action='store_true')
    args = parser.parse_args()
    if args.index_outputs:
        import boto3
        client = boto3.Session(profile_name='darkest', region_name='ca-central-1').client('s3')
        print(json.dumps(index_outputs(args.campaign, client)))
    else:
        print(json.dumps({'completion': completion_coverage(args.campaign), 'measurements': len(measured_rows(args.campaign))}))
