"""Freeze additional living-room pairs without modifying completed reviews."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets

from serverless.benchmark.geometry import measure
from serverless.benchmark.stimuli import freeze, digest
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.reviews import PLAN
from serverless.study.service import StudyService, prompt_text
from serverless.study.store import SQLiteStudyStore


def prepare(evidence, original, layoutgpt, infinigen, output):
    cohort = json.loads((evidence / 'cohort.json').read_bytes())
    raw = (evidence / 'measured-scenes.json').read_bytes()
    if not cohort['complete'] or hashlib.sha256(raw).hexdigest() != cohort['measurementsSha256']:
        raise ValueError('Complete audited SOILIE cohort required')
    pool = [row for row in json.loads(raw)['rows'] if row['scene']['model'] == 'soilie'
            and row['scene']['roomType'] == 'living_room']
    release = json.loads(layoutgpt.read_bytes())
    if not release['complete'] or len(release['attempts']) != 120:
        raise ValueError('Complete fixed LayoutGPT pilot required before reviewing')
    supplement = json.loads(infinigen.read_bytes())
    if len(supplement['scenes']) != 1 or supplement['invalidArtifacts']:
        raise ValueError('One validated Infinigen supplement required')
    if output.exists() and (output / 'manifest.json').exists():
        raise ValueError('Review extension is already frozen')
    manifest = []
    for name, baseline, rows, similarity, density, limit, previous in (
        ('set-a', 'layoutgpt', release['rows'], .4, .25, 120, ()),
        ('set-b', 'infinigen_controlled', [{'scene': scene, 'metrics': measure(scene)} for scene in supplement['scenes']],
         1/3, 1, 1, (json.loads((original / 'set-b/protocol.json').read_bytes()),)),
    ):
        folder = output / name
        path = folder / 'protocol.json'
        if path.exists():
            protocol = json.loads(path.read_bytes())
            if protocol['sampling']['cohortSceneIdsSha256'] != digest(sorted(row['scene']['id'] for row in pool + rows)):
                raise ValueError('Cannot resume an extension with different source scenes')
        else:
            protocol = freeze(pool + rows, output / 'site/benchmarks/stimuli', path,
                limit=limit, previous_protocols=previous, minimum_semantic_similarity=similarity,
                maximum_density_difference=density, baselines=(baseline,), decision_scope='focus_only',
                reviewer_plan=PLAN, reviewer_model='GPT-5.6 Sol', reasoning_effort='Extra High')
        if not protocol['cases']:
            raise ValueError('No eligible additional pair')
        if 'reviewerSideOffsets' not in protocol:
            protocol['reviewerSideOffsets'] = {f'reviewer-{index + 1:02d}': index % 2 for index in range(len(PLAN))}
            protocol['studyVersion'] = 'living-extension-' + digest(protocol)[:20]
            write_json(path, protocol)
        state = folder / 'private'; state.mkdir(parents=True, exist_ok=True)
        secret = state / 'session-secret'
        if not secret.exists(): secret.write_bytes(secrets.token_bytes(32))
        service = StudyService(protocol, SQLiteStudyStore(state / 'pilot.sqlite3'), secret.read_bytes(), enabled=True)
        for index, profile in enumerate(PLAN):
            reviewer = f'reviewer-{index + 1:02d}'
            session = service.start({'invitation': service.invite(reviewer, profile, 'GPT-5.6 Sol (Extra High reasoning effort)')})
            write_json(state / (reviewer + '.json'), session)
            public = {key: value for key, value in session.items() if key not in ('sessionId', 'sessionToken')}
            public['prompt'] = prompt_text(protocol, profile)
            packet = output / 'packets' / reviewer
            packet.mkdir(parents=True, exist_ok=True)
            write_json(packet / (name + '.json'), public)
        manifest.append({'set': name, 'baseline': baseline, 'livingRoomPairs': len(protocol['cases']),
                         'studyVersion': protocol['studyVersion']})
    write_json(output / 'manifest.json', {'protocols': manifest, 'reviewerPlan': PLAN})
    print(json.dumps(manifest), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence', 'original', 'layoutgpt', 'infinigen', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    prepare(**vars(parser.parse_args()))


if __name__ == '__main__': main()
