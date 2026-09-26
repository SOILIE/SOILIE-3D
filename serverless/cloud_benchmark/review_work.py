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


def validate_answers(work, answers, *, complete=True):
    """Validate the entire batch before any immutable response is persisted."""
    expected = [(row['set'], row['caseId']) for row in work]
    if len(set(expected)) != len(expected):
        raise ValueError('Work queue contains duplicate assignments')
    if not isinstance(answers, list):
        raise ValueError('Answers must be an array')
    keys = {'set', 'caseId', 'judgement', 'errorChoice', 'confidence', 'note'}
    actual = []
    for row in answers:
        if (not isinstance(row, dict) or set(row) != keys
                or not isinstance(row['set'], str) or not isinstance(row['caseId'], str)
                or row['judgement'] not in ('left', 'tie', 'right')
                or row['errorChoice'] not in ('left', 'right', 'both', 'neither', 'uncertain')
                or type(row['confidence']) is not int or not 1 <= row['confidence'] <= 5
                or not isinstance(row['note'], str) or len(row['note']) > 500):
            raise ValueError('Invalid answer schema or response value')
        actual.append((row['set'], row['caseId']))
    # Checkpointed reviewers append answers in their frozen presentation order.
    if actual != expected[:len(actual)] or (complete and len(actual) != len(expected)):
        raise ValueError('Answers must cover the assigned cases once, in their frozen order')
    return len(actual)


def status(output):
    """Report saved work, not whether an external reviewer process is running.

    A receipt is trusted only while it matches the complete current answers.
    Never print judgements, private routing, session credentials or model sides.
    """
    manifest = load(output / 'manifest.json')
    roster = manifest.get('reviewers') or {f'reviewer-{i+1:02d}': {'profile': profile}
                                        for i, profile in enumerate(manifest['reviewerPlan'])}
    reviewers = {}
    for reviewer, entry in roster.items():
        folder = output / 'packets' / reviewer
        work = load(folder / 'cases.json')
        row = {'profile': entry['profile'], 'assigned': len(work),
               'written': 0, 'saved': 0, 'state': 'no_answers_yet'}
        try:
            if (folder / 'answers.json').exists():
                raw = (folder / 'answers.json').read_bytes()
                row['written'] = validate_answers(work, json.loads(raw), complete=False)
                row['state'] = 'awaiting_submission' if row['written'] == len(work) else 'partial_answers'
                if (folder / 'submitted.json').exists():
                    receipt = load(folder / 'submitted.json')
                    if (row['written'] != len(work) or receipt.get('saved') != len(work)
                            or receipt.get('respondentType') != 'ai_pilot'
                            or receipt.get('answersSha256') != hashlib.sha256(raw).hexdigest()):
                        raise ValueError('Submission receipt does not match complete answers')
                    row.update(saved=len(work), state='saved')
            elif (folder / 'submitted.json').exists():
                raise ValueError('Submission receipt exists without its answers')
        except (ValueError, TypeError, KeyError):
            row.update(saved=0, state='invalid_or_being_written')
        reviewers[reviewer] = row
    totals = {key: sum(row[key] for row in reviewers.values()) for key in ('assigned', 'written', 'saved')}
    result = {'scope': 'This frozen missing-work queue only; counts include repeat presentations.',
              'frozenPairs': manifest.get('frozenPairs', sum(row['pairs'] for row in manifest.get('protocols', []))),
              'reviewers': reviewers, 'totals': totals,
              'queueComplete': all(row['state'] == 'saved' for row in reviewers.values())}
    print(json.dumps(result), flush=True)
    return result


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
    return queue_sources(cohort, scenes, sources, output)


def prepare_additional(evidence, source, output):
    """Queue missing work from completed expansion packets without new sampling."""
    raw = (evidence / 'measured-scenes.json').read_bytes()
    cohort = load(evidence / 'cohort.json')
    if not cohort['complete'] or hashlib.sha256(raw).hexdigest() != cohort['measurementsSha256']:
        raise ValueError('Audited complete SOILIE evidence required')
    scenes = {row['scene']['id']: row['scene'] for row in json.loads(raw)['rows']}
    sources = []
    for root in source:
        for scene in load(root / 'source-scenes.json')['scenes']:
            if scene['id'] in scenes and digest(scene) != digest(scenes[scene['id']]):
                raise ValueError('Conflicting source scene')
            scenes[scene['id']] = scene
        sources.extend((root, name, None) for name in ('set-a', 'set-b')
                       if (root / name / 'protocol.json').exists())
    return queue_sources(cohort, scenes, sources, output)


def queue_sources(cohort, scenes, sources, output):
    """Keep source sessions immutable; route only their still-missing responses."""
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
        'scope': 'Only missing assignments from the listed immutable source protocols.'})
    print(json.dumps({'validatedProtocols': len(protocols), 'frozenPairs': sum(counts.values()), 'reviewers': totals}), flush=True)


def submit(output, reviewer):
    folder = output / 'packets' / reviewer
    work, answers = load(folder / 'cases.json'), load(folder / 'answers.json')
    expected = {(row['set'], row['caseId']) for row in work}
    validate_answers(work, answers)
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
    additional = sub.add_parser('prepare-additional')
    additional.add_argument('--evidence', type=Path, required=True)
    additional.add_argument('--source', type=Path, action='append', required=True)
    additional.add_argument('--output', type=Path, required=True)
    submit_parser = sub.add_parser('submit')
    submit_parser.add_argument('--output', type=Path, required=True)
    submit_parser.add_argument('--reviewer', choices=[f'reviewer-{i:02d}' for i in range(1, 11)], required=True)
    status_parser = sub.add_parser('status')
    status_parser.add_argument('--output', type=Path, required=True)
    args = vars(parser.parse_args())
    command = args.pop('command')
    {'prepare': prepare, 'prepare-additional': prepare_additional,
     'submit': submit, 'status': status}[command](**args)


if __name__ == '__main__': main()
