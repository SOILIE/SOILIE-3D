"""Freeze completed expansion strata without moving any already reviewed pair."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import secrets

from serverless.benchmark.expand_infinigen import sha_file
from serverless.benchmark.geometry import measure
from serverless.benchmark.run_batch import run_lock, write_json
from serverless.benchmark.stimuli import digest, freeze
from serverless.cloud_benchmark.reviews import PLAN
from serverless.study.service import StudyService, prompt_text
from serverless.study.store import SQLiteStudyStore


def completed_rows(campaign, room):
    """Read only a terminal room checkpoint and checksum-verified geometry."""
    with run_lock(campaign / room):
        checkpoint = campaign / room / 'checkpoint.json'
        state = json.loads(checkpoint.read_bytes())
        if (not state['complete'] or state['targetPairs'] != 120
                or state['existingPairs'] + len(state['selectedPairs']) != 120):
            raise ValueError('Only a complete 120-pair room stratum can be frozen')
        rows = []
        for attempt in state['attempts']:
            if 'measured' not in attempt:
                continue
            path = (campaign / attempt['measured']).resolve()
            if not path.is_relative_to(campaign.resolve()) or sha_file(path) != attempt['measuredSha256']:
                raise ValueError('Expansion geometry checksum or path differs')
            row = json.loads(path.read_bytes())
            if row['scene']['roomType'] != room or row['scene']['model'] != 'infinigen_controlled':
                raise ValueError('Unexpected expansion room or generator')
            rows.append(row)
        return rows, state['selectedPairs'], sha_file(checkpoint)


def require_same_pairs(protocol, expected):
    def identities(rows):
        return Counter((row['soilieScene'], row['baselineScene'], row['soilieDigest'], row['baselineDigest'])
                       for row in rows)
    if identities(protocol['stimulusEvidence']) != identities(expected):
        raise ValueError('Frozen review differs from completed campaign selection')


def prepare(evidence, original, extension, campaign, output, room_types, layoutgpt=None):
    if (output / 'manifest.json').exists():
        raise ValueError('Review packet already frozen; do not overwrite reviewers')
    cohort = json.loads((evidence / 'cohort.json').read_bytes())
    source = evidence / 'measured-scenes.json'
    if not cohort['complete'] or sha_file(source) != cohort['measurementsSha256']:
        raise ValueError('Complete audited source cohort required')
    pool = [row for row in json.loads(source.read_bytes())['rows'] if row['scene']['model'] == 'soilie']
    new_rows, expected, checkpoints = [], [], {}
    for room in room_types:
        rows, pairs, checksum = completed_rows(campaign, room)
        new_rows.extend(rows)
        expected.extend(pairs)
        checkpoints[room] = checksum
    inputs = [('set-b', 'infinigen_controlled', new_rows, 1/3, 1, len(expected), expected)]
    if layoutgpt:
        document = json.loads(layoutgpt.read_bytes())
        if document.get('invalidArtifacts') or len(document['scenes']) != 1:
            raise ValueError('One validated LayoutGPT supplement required')
        additions = [{'scene': scene, 'metrics': measure(scene)} for scene in document['scenes']]
        inputs.insert(0, ('set-a', 'layoutgpt', additions, .4, .25, 1, None))
    manifest, exported = [], []
    for name, baseline, additions, similarity, density, limit, required in inputs:
        rooms = {row['scene']['roomType'] for row in additions}
        rows = [row for row in pool if row['scene']['roomType'] in rooms] + additions
        previous = [json.loads((root / name / 'protocol.json').read_bytes()) for root in (original, extension)]
        path = output / name / 'protocol.json'
        if path.exists():
            protocol = json.loads(path.read_bytes())
            if protocol['sampling']['cohortSceneIdsSha256'] != digest(sorted(row['scene']['id'] for row in rows)):
                raise ValueError('Cannot resume with changed source scenes')
        else:
            protocol = freeze(rows, output / 'site/benchmarks/stimuli', path,
                limit=limit, previous_protocols=previous, minimum_semantic_similarity=similarity,
                maximum_density_difference=density, baselines=(baseline,), decision_scope='focus_only',
                reviewer_plan=PLAN, reviewer_model='GPT-5.6 Sol', reasoning_effort='Extra High')
            protocol['reviewerSideOffsets'] = {f'reviewer-{index+1:02d}': index % 2 for index in range(len(PLAN))}
            protocol['studyVersion'] = 'completed-expansion-' + digest(protocol)[:20]
            write_json(path, protocol)
        if len(protocol['cases']) != limit:
            raise ValueError('Unexpected pair coverage')
        if required is not None:
            require_same_pairs(protocol, required)
        state = output / name / 'private'
        state.mkdir(parents=True, exist_ok=True)
        secret = state / 'session-secret'
        if not secret.exists():
            secret.write_bytes(secrets.token_bytes(32))
        service = StudyService(protocol, SQLiteStudyStore(state / 'pilot.sqlite3'), secret.read_bytes(), enabled=True)
        for index, profile in enumerate(PLAN):
            reviewer = f'reviewer-{index+1:02d}'
            private = state / (reviewer + '.json')
            if private.exists():
                credentials = json.loads(private.read_bytes())
                session = service.resume(credentials['sessionId'], {'sessionToken': credentials['sessionToken']})
            else:
                session = service.start({'invitation': service.invite(reviewer, profile, 'GPT-5.6 Sol (Extra High reasoning effort)')})
                write_json(private, session)
            public = {key: value for key, value in session.items() if key not in ('sessionId', 'sessionToken')}
            public['prompt'] = prompt_text(protocol, profile)
            # New packets must use exactly the already registered question.
            prior = json.loads((original / 'packets' / reviewer / (name + '.json')).read_bytes())
            if public['prompt'] != prior['prompt']:
                raise ValueError('Reviewer prompt changed')
            packet = output / 'packets' / reviewer
            packet.mkdir(parents=True, exist_ok=True)
            write_json(packet / (name + '.json'), public)
        exported.extend(row['scene'] for row in additions)
        manifest.append({'set': name, 'baseline': baseline,
                         'pairs': dict(Counter(row['matchingStratum'][0] for row in protocol['stimulusEvidence'])),
                         'studyVersion': protocol['studyVersion']})
    write_json(output / 'source-scenes.json', {'scenes': exported, 'invalidArtifacts': []})
    write_json(output / 'manifest.json', {'protocols': manifest, 'reviewerPlan': PLAN,
        'cohortSha256': cohort['measurementsSha256'], 'expansionCheckpointSha256': checkpoints,
        'qualitySelection': False, 'preservePriorPairs': True})
    print(json.dumps(manifest), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence', 'original', 'extension', 'campaign', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--room-types', nargs='+', choices=('bedroom', 'living_room'), required=True)
    parser.add_argument('--layoutgpt', type=Path)
    prepare(**vars(parser.parse_args()))


if __name__ == '__main__':
    main()
