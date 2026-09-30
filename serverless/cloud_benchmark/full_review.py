"""Frozen, counterbalanced full rerun and descriptive four-bucket reporting.

Two independent judgments per pair/dimension see opposite sides. These are
paired measurements, not 4,800 independent room samples. Earlier answers may
be retained only after an exact delivered-input audit. No disagreement is
discarded, and no acceptance target is imposed.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path

from serverless.benchmark.functional_evidence import reference_catalog, spatial_facts, task_evidence
from serverless.benchmark.stimuli import diagram
from serverless.cloud_benchmark.staged_pilot import (MODEL, EFFORT, SCHEMA, STRATA, SYSTEM,
    read, write_new, digest, load_frozen, delivery_prompt, validate_answer, validate_receipt)
from serverless.study import structured_rubric as v2
from serverless.study import functional_rubric_v3 as rubric
from serverless.study import functional_rubric_v4 as inventory_rubric

SEED = 'functional-use-v3-full-counterbalanced'


def make_assignments(cases):
    rows = []
    for dimension, profile in enumerate(rubric.PROFILES):
        for stratum in STRATA:
            group = sorted((c for c in cases if (c['baseline'], c['roomType']) == stratum),
                           key=lambda c: digest([SEED, profile, c['pairId']]))
            for index, case in enumerate(group):
                pair = []
                for stream in (0, 1):
                    reviewer = f'reviewer-{dimension*2+stream+1:02}'
                    swap = (index + stream) % 2
                    row = {'assignmentId': digest([rubric.VERSION, reviewer, case['pairId']])[:24],
                           'reviewerId': reviewer, 'profile': profile, 'pairId': case['pairId'],
                           'baseline': case['baseline'], 'roomType': case['roomType'], 'title': case['title'],
                           'leftCondition': case['baseline'] if swap else 'soilie',
                           'rightCondition': 'soilie' if swap else case['baseline'],
                           'leftSource': case['comparisonImage'] if swap else case['relationImage'],
                           'rightSource': case['relationImage'] if swap else case['comparisonImage'],
                           'repeatOf': None, 'presentation': stream + 1}
                    pair.append(row)
                pair[0]['pairedWith'], pair[1]['pairedWith'] = pair[1]['assignmentId'], pair[0]['assignmentId']
                rows.extend(pair)
    # Mix dimensions and presentations; every task is still an isolated process.
    return sorted(rows, key=lambda r: digest([SEED, 'delivery-order', r['assignmentId']]))


def prepare(source, output, catalog=None, expected_counts=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('A full campaign must use a new immutable directory')
    if not read(source / 'preflight.json')['passed']:
        raise ValueError('Source preflight failed')
    cases, provenance, source_hashes = [], {}, {}
    for name in ('set-a', 'set-b'):
        path = source / name / 'protocol.json'
        protocol = read(path)
        source_hashes[name] = digest(path.read_bytes())
        evidence = {row['caseId']: row for row in protocol['stimulusEvidence']}
        for case in protocol['cases']:
            case = {**case, 'pairId': name + ':' + case['id'], 'baseline': case['comparisonCondition'],
                    'roomType': case['balanceStratum']}
            case.pop('profileImages', None)
            cases.append(case)
            provenance[case['pairId']] = evidence[case['id']]
    expected_counts = {s:120 for s in STRATA} if expected_counts is None else expected_counts
    if set(expected_counts) != set(STRATA) or any(n <= 0 for n in expected_counts.values()):
        raise ValueError('Positive predetermined counts required in all four strata')
    if Counter((c['baseline'], c['roomType']) for c in cases) != Counter(expected_counts):
        raise ValueError('Pair counts differ from the audited cohort')
    scene_file = source / 'source-scenes.json'
    scenes = {s['id']: s for s in read(scene_file)['scenes']}
    source_hashes['source-scenes.json'] = digest(scene_file.read_bytes())
    images, evidence_cache, scene_provenance = {}, {}, {}
    for case in cases:
        for role, field in (('soilie','relationImage'), ('baseline','comparisonImage')):
            expected = provenance[case['pairId']]
            scene = scenes[expected[role + 'Scene']]
            if digest(scene) != expected[role + 'Digest']:
                raise ValueError('Original geometry changed')
            raw = diagram(scene, numbered=True, clear_fronts=True).encode()
            url = '/benchmarks/stimuli/' + digest(raw)[:24] + '.svg'
            if url not in images:
                file = 'source-images/' + Path(url).name
                path = output / file
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(raw)
                images[url] = {'file': file, 'sha256': digest(raw)}
                facts = spatial_facts(scene)
                evidence_cache[url] = {p: task_evidence(scene, p, facts, catalog) for p in rubric.PROFILES}
            case[field] = url
            scene_provenance[case['pairId'] + ':' + role] = {
                'sceneId': scene['id'], 'sceneSha256': digest(scene), 'image': url}
    rows, reviewers = make_assignments(cases), []
    system = SYSTEM.replace('attached image only', 'attached image and supplied neutral case evidence only')
    for index, profile in enumerate(p for p in rubric.PROFILES for _ in range(2)):
        exact = rubric.prompt(profile)
        dimension_version = rubric.VERSION
        if catalog is not None:
            exact = inventory_rubric.prompt(profile)
            dimension_version = inventory_rubric.version(profile)
        reviewers.append({'reviewerId': f'reviewer-{index+1:02}', 'profile': profile,
            'rubricVersion': dimension_version, 'reviewPrompt': exact, 'promptHash': digest(exact.encode()),
            'systemPrompt': system, 'systemPromptHash': digest(system.encode()), 'model': MODEL, 'reasoningEffort': EFFORT,
            'reportedModel': 'GPT-5.6 Sol', 'reportedReasoningEffort': 'Extra High',
            'collectionStatus': 'collect_missing_with_exact_input_retention', 'checklistInstructions': rubric.CHECKLIST,
            'responseSchema': v2.schema(SCHEMA), 'stimulusVersion': rubric.STIMULUS_VERSION,
            'evidenceInstruction': rubric.EVIDENCE_INSTRUCTION})
    # Historical calibration exposure is recorded, never secretly removed from
    # the fixed sample or passed off as a fresh held-out test.
    development = set(read(source / 'preflight.json').get('developmentPairIds', []))
    for directory in source.parent.glob('review-functional-use-pilot-v*'):
        if (directory / 'protocol.json').exists():
            prior = load_frozen(directory)
            development.update(prior['selectedPairs'])
            development.update(prior['excludedCalibrationPairs'])
    manifest = {'schemaVersion': 3, 'stage': 'full_counterbalanced', 'rubricVersion': 'functional-use-v4' if catalog is not None else rubric.VERSION,
        'stimulusVersion': rubric.STIMULUS_VERSION, 'seed': SEED, 'sourceRoot': str(source),
        'sourceProtocolHashes': source_hashes, 'sceneProvenance': scene_provenance,
        'fullPairIds': sorted(c['pairId'] for c in cases), 'fullPairSetSha256': digest(sorted(c['pairId'] for c in cases)),
        'developmentPairIds': sorted(development), 'reviewers': reviewers, 'assignments': rows,
        'sourceImages': images, 'model': MODEL, 'reasoningEffort': EFFORT, 'outputSchema': v2.schema(SCHEMA),
        'evidenceInstruction': rubric.EVIDENCE_INSTRUCTION, 'referenceCatalog': reference_catalog() if catalog is None else catalog,
        'referenceCatalogSha256': digest(reference_catalog() if catalog is None else catalog), 'expectedMainJudgements': len(rows), 'expectedControls': 0,
        'expectedPairedComparisons': len(rows)//2, 'fullCampaignAuthorized': True, 'releaseEligible': False,
        'retainedRoomFunctionSha256': digest([]),
        'aggregation': {'unit': 'room pair within dimension', 'buckets': ['model_a','model_b','tie','disagreement'],
            'model_a': 'soilie', 'model_b': 'the named baseline',
            'rule': 'A/A or A/tie -> A; B/B or B/tie -> B; tie/tie -> tie; A/B -> disagreement.',
            'reportSubtypes': ['win_win','win_tie'],
            'interpretation': 'Directional support, not calibrated confidence or accuracy. Preserve exact agreement and disagreements; neither ties nor reversals are discarded.'}}
    for row in rows:
        row['evidence'] = {side: deepcopy(evidence_cache[row[side + 'Source']][row['profile']]) for side in ('left','right')}
        row['evidence']['requiredObservations'] = [{'side': side, 'objects': list(ids)}
            for side, ids in v2.checklist_keys(row['evidence'], row['profile'])]
        row['evidenceSha256'] = digest(row['evidence'])
        row['deliveryPromptHash'] = digest(delivery_prompt(manifest, row).encode())
    write_new(output / 'private/retained-room-function.json', [])
    write_new(output / 'protocol.json', manifest)
    write_new(output / 'protocol-sha256.json', {'sha256': digest(manifest)})
    return {'assignments': len(rows), 'pairs': len(cases), 'sourceImages': len(images), 'protocolSha256': digest(manifest)}


def bucket(first, second):
    if first not in ('model_a','model_b','tie') or second not in ('model_a','model_b','tie'):
        raise ValueError('Unknown physical-room preference')
    if first == second:
        return (first, 'same_tie' if first == 'tie' else 'win_win')
    if 'tie' in (first, second):
        return (second if first == 'tie' else first, 'win_tie')
    return ('disagreement', 'opposite_winner')


def results(root):
    root = Path(root)
    protocol = load_frozen(root)
    assignments = {r['assignmentId']: r for r in protocol['assignments']}
    reviewers = {r['reviewerId']: r for r in protocol['reviewers']}
    packets = read(root / 'packets/index.json')
    answers, threads = {}, set()
    for path in (root / 'responses').glob('*.json'):
        row = read(path)
        key = row['assignmentId']
        if key not in assignments or key in answers or any(row.get(k) != v for k,v in assignments[key].items()):
            raise ValueError('Unknown or altered response assignment')
        validate_answer({k: row[k] for k in protocol['outputSchema']['required']}, protocol['rubricVersion'], row['evidence'], row['profile'])
        validate_receipt(row['receipt'], protocol, reviewers[row['reviewerId']], packets[key], row)
        thread = row['receipt']['threadId']
        if thread in threads:
            raise ValueError('Judgment context was reused')
        threads.add(thread)
        answers[key] = row
    paired = []
    for key, row in answers.items():
        other = row['pairedWith']
        if key > other or other not in answers:
            continue
        def physical(r):
            return 'tie' if r['judgement'] == 'tie' else 'model_a' if r[r['judgement']+'Condition'] == 'soilie' else 'model_b'
        first, second = physical(row), physical(answers[other])
        category, subtype = bucket(first, second)
        paired.append({k: row[k] for k in ('pairId','profile','baseline','roomType')} | {
            'bucket': category, 'subtype': subtype, 'preferences': [first,second],
            'assignmentIds': [key,other], 'developmentPair': row['pairId'] in protocol['developmentPairIds']})
    def counts(rows):
        categories = Counter(r['bucket'] for r in rows)
        subtypes = Counter((r['bucket'],r['subtype']) for r in rows)
        return {'pairedComparisons': len(rows), 'buckets': {k: categories[k] for k in ('model_a','model_b','tie','disagreement')},
                'directionalSubtypes': {k:{t:subtypes[k,t] for t in ('win_win','win_tie')} for k in ('model_a','model_b')},
                'exactAgreements': sum(r['subtype'] in ('win_win','same_tie') for r in rows)}
    grouped = {}
    for row in paired:
        key = ':'.join(row[k] for k in ('profile','baseline','roomType'))
        grouped.setdefault(key, []).append(row)
    return {'rubricVersion': protocol['rubricVersion'], 'protocolSha256': digest(protocol), 'responses': len(answers),
        'expected': len(assignments), 'complete': len(answers)==len(assignments), 'releaseEligible': False,
        'remainingAssignmentIds': [k for k in assignments if k not in answers], 'overall': counts(paired),
        'byDimensionBaselineRoom': {k:counts(v) for k,v in grouped.items()},
        'nonDevelopmentPairs': counts([r for r in paired if not r['developmentPair']]), 'pairs': paired,
        'interpretation': protocol['aggregation']['interpretation']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--catalog', type=Path, help='Explicit audited expanded catalog for a new inventory review')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.root, catalog=read(args.catalog) if args.catalog else None)
                     if args.prepare else results(args.root)))
