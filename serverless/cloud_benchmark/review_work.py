"""Queue only missing judgements from frozen protocols, without resampling.

Public packets contain neutral images and prompts only. Private routing retains
the original study/session identity so incremental work never rewrites evidence.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from serverless.benchmark.stimuli import digest
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.reviews import PLAN
from serverless.study.service import StudyService
from serverless.study.store import SQLiteStudyStore


def load(path):
    return json.loads(path.read_bytes())


def selected_assignment_ids(protocol, assignments, room=None):
    selected = {row['caseId'] for row in protocol['stimulusEvidence']
                if room is None or row['matchingStratum'][0] == room}
    return {row['caseId'] for row in assignments if (row['repeatOf'] or row['caseId']) in selected}


def audit_protocol(root, name, scenes):
    protocol = load(root / name / 'protocol.json')
    if (protocol['evidenceMode'] != 'visual_only' or protocol['decisionScope'] != 'focus_only'
            or protocol['reviewerPlan'] != PLAN
            or protocol['reviewerConfiguration'] != {'model': 'GPT-5.6 Sol', 'reasoningEffort': 'Extra High'}):
        raise ValueError('Frozen reviewer configuration differs')
    for row in protocol['stimulusEvidence']:
        for kind in ('soilie', 'baseline'):
            if digest(scenes[row[kind + 'Scene']]) != row[kind + 'Digest']:
                raise ValueError('Frozen stimulus geometry differs from validated scene')
    for case in protocol['cases']:
        for field in ('relationImage', 'comparisonImage'):
            url = case[field]
            if not url.startswith('/benchmarks/stimuli/') or not url.endswith('.svg'):
                raise ValueError('Unexpected stimulus path')
            svg = (root / 'site' / url.lstrip('/')).read_bytes()
            if hashlib.sha256(svg).hexdigest()[:24] != Path(url).stem:
                raise ValueError('Stimulus image checksum differs')
    return protocol


def prepare(evidence, original, extension, layoutgpt, infinigen, output):
    raw = (evidence / 'measured-scenes.json').read_bytes()
    cohort = load(evidence / 'cohort.json')
    if not cohort['complete'] or hashlib.sha256(raw).hexdigest() != cohort['measurementsSha256']:
        raise ValueError('Audited complete SOILIE evidence required')
    scenes = {row['scene']['id']: row['scene'] for row in json.loads(raw)['rows']}
    for export in (load(layoutgpt), load(infinigen)):
        scenes.update({scene['id']: scene for scene in export['scenes']})
    sources = [(original, 'set-a', 'bedroom'), (original, 'set-b', None),
               (extension, 'set-a', None), (extension, 'set-b', None)]
    protocols = [audit_protocol(root, name, scenes) for root, name, _ in sources]
    if (output / 'manifest.json').exists():
        raise ValueError('Work queue already frozen; do not overwrite running reviewer inputs')
    counts = Counter((case['comparisonCondition'], evidence_row['matchingStratum'][0])
                     for protocol, (_, _, room) in zip(protocols, sources)
                     for case, evidence_row in zip(protocol['cases'], protocol['stimulusEvidence'])
                     if room is None or evidence_row['matchingStratum'][0] == room)
    all_routes, totals = {}, {}
    for index, profile in enumerate(PLAN):
        reviewer = f'reviewer-{index + 1:02d}'
        work, routes, prompts = [], {}, set()
        for source_index, ((root, name, room), protocol) in enumerate(zip(sources, protocols)):
            state = root / name / 'private'
            credentials = load(state / (reviewer + '.json'))
            store = SQLiteStudyStore(state / 'pilot.sqlite3')
            session = store.get(credentials['sessionId'])
            service = StudyService(protocol, store, (state / 'session-secret').read_bytes(), enabled=True)
            public = service.resume(credentials['sessionId'], {'sessionToken': credentials['sessionToken']})
            completed = set(public['completedCaseIds'])
            selected = selected_assignment_ids(protocol, session['assignments'], room)
            packet = load(root / 'packets' / reviewer / (name + '.json'))
            prompts.add(packet['prompt'])
            if hashlib.sha256(packet['prompt'].encode()).hexdigest() != session['promptHash']:
                raise ValueError('Recorded reviewer prompt differs')
            for case in load(root / 'packets' / reviewer / 'cases.json'):
                if case['set'] != name or case['caseId'] not in selected or case['caseId'] in completed:
                    continue
                image = Path(case['image']).resolve()
                image.relative_to((root / 'review-images').resolve())
                if image.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
                    raise ValueError('Missing or invalid rendered review image')
                # Opaque routing codes do not identify either model or wave.
                task = f'group-{source_index + 1}'
                work.append({'set': task, 'caseId': case['caseId'], 'title': case['title'], 'image': str(image)})
                routes[task] = {'root': str(root.resolve()), 'set': name, 'studyVersion': protocol['studyVersion'],
                                'protocolSha256': hashlib.sha256((root / name / 'protocol.json').read_bytes()).hexdigest()}
        if len(prompts) != 1:
            raise ValueError('Do not combine different reviewer prompts into one context')
        work.sort(key=lambda row: digest([reviewer, row['set'], row['caseId']]))
        folder = output / 'packets' / reviewer
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / 'cases.json', work)
        (folder / 'prompt.txt').write_text(prompts.pop() + '\n', encoding='utf-8')
        all_routes[reviewer] = routes
        totals[reviewer] = {'profile': profile, 'missingAssignmentsIncludingRepeats': len(work)}
    (output / 'private').mkdir(parents=True, exist_ok=True)
    write_json(output / 'private/routing.json', all_routes)
    write_json(output / 'manifest.json', {'cohortSha256': cohort['measurementsSha256'],
        'frozenPairs': [{'model': model, 'roomType': room, 'pairs': n} for (model, room), n in counts.items()],
        'reviewers': totals, 'modelsOrScoresDisclosedToReviewers': False,
        'laterWork': 'Last supplemental LayoutGPT pair and the unfinished Infinigen expansion are not in this queue.'})
    print(json.dumps({'validatedProtocols': len(protocols), 'frozenPairs': sum(counts.values()), 'reviewers': totals}), flush=True)


def submit(output, reviewer):
    folder = output / 'packets' / reviewer
    work, answers = load(folder / 'cases.json'), load(folder / 'answers.json')
    expected = {(row['set'], row['caseId']) for row in work}
    actual = Counter((row['set'], row['caseId']) for row in answers)
    if set(actual) != expected or any(n != 1 for n in actual.values()):
        raise ValueError('Exactly one answer per assigned case required')
    routing = load(output / 'private/routing.json')[reviewer]
    services = {}
    for group, route in routing.items():
        root = Path(route['root']) / route['set']
        if hashlib.sha256((root / 'protocol.json').read_bytes()).hexdigest() != route['protocolSha256']:
            raise ValueError('Source protocol changed during review')
        state = root / 'private'
        credentials = load(state / (reviewer + '.json'))
        service = StudyService(load(root / 'protocol.json'), SQLiteStudyStore(state / 'pilot.sqlite3'),
                               (state / 'session-secret').read_bytes(), enabled=True)
        services[group] = service, credentials
    for row in answers:
        service, credentials = services[row['set']]
        service.respond(credentials['sessionId'], {key: value for key, value in
            {**row, 'sessionToken': credentials['sessionToken']}.items() if key != 'set'})
    for group, (service, credentials) in services.items():
        saved = set(service.resume(credentials['sessionId'], {'sessionToken': credentials['sessionToken']})['completedCaseIds'])
        if not {case_id for name, case_id in expected if name == group} <= saved:
            raise ValueError('Not all increment responses persisted')
    write_json(folder / 'submitted.json', {'saved': len(answers), 'respondentType': 'ai_pilot',
        'answersSha256': hashlib.sha256((folder / 'answers.json').read_bytes()).hexdigest()})
    print(json.dumps({'reviewer': reviewer, 'saved': len(answers)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare_parser = sub.add_parser('prepare')
    for name in ('evidence', 'original', 'extension', 'layoutgpt', 'infinigen', 'output'):
        prepare_parser.add_argument('--' + name, type=Path, required=True)
    submit_parser = sub.add_parser('submit')
    submit_parser.add_argument('--output', type=Path, required=True)
    submit_parser.add_argument('--reviewer', choices=[f'reviewer-{i:02d}' for i in range(1, 11)], required=True)
    args = vars(parser.parse_args())
    command = args.pop('command')
    (prepare if command == 'prepare' else submit)(**args)


if __name__ == '__main__': main()
