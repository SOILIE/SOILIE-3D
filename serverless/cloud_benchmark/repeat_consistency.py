"""Audit reversed-side controls without changing or filtering any judgement."""
from collections import Counter, defaultdict

from serverless.cloud_benchmark.reviews import PLAN
from serverless.study.export_pilot import preference


MINIMUM_AGREEMENT_PCT = 90
REPEATS_PER_BASELINE_PER_REVIEWER = 2


def audit_repeat_assignments(assignments, packet_cases):
    """Check the actual delivered image pair, not just the response-side labels."""
    by_id = {row['caseId']: row for row in assignments}
    delivered = {row['caseId']: row for row in packet_cases}
    if (len(by_id) != len(assignments) or len(delivered) != len(packet_cases)
            or set(by_id) != set(delivered)):
        raise ValueError('Repeat audit requires unique, complete packet assignments')
    repeats = 0
    for row in assignments:
        packet = delivered[row['caseId']]
        if any(packet[key] != row[key] for key in ('leftImage', 'rightImage')):
            raise ValueError('Delivered repeat images differ from assigned images')
        if not row['repeatOf']:
            continue
        original = by_id.get(row['repeatOf'])
        if not original or original['repeatOf']:
            raise ValueError('Repeat must refer to an original case')
        for left, right in (('leftImage', 'rightImage'), ('leftCondition', 'rightCondition')):
            if row[left] != original[right] or row[right] != original[left]:
                raise ValueError('Repeat must swap both images and condition labels')
        repeats += 1
    if repeats != REPEATS_PER_BASELINE_PER_REVIEWER:
        raise ValueError('Exactly two reversed-side repeats per baseline and reviewer required')
    return repeats


def repeat_consistency(reports):
    """Recompute agreement from raw votes; reject missing or malformed controls.

    A left preference followed by right can agree, while two left preferences
    after a side swap disagree. Ties agree only with ties. No disagreement is
    discarded, and this operational release threshold is not proof of accuracy.
    """
    expected = {f'reviewer-{i + 1:02d}': profile for i, profile in enumerate(PLAN)}
    if set(reports) != {'layoutgpt', 'infinigen_controlled'}:
        raise ValueError('Both complete baseline comparisons required')
    rows = []
    for baseline, report in reports.items():
        index = {(row['reviewerId'], row['caseId']): row for row in report['responses']}
        if len(index) != len(report['responses']):
            raise ValueError('Duplicate response in repeat audit')
        counts = Counter()
        for row in report['responses']:
            if not row['repeatOf']:
                continue
            original = index.get((row['reviewerId'], row['repeatOf']))
            if not original or original['repeatOf']:
                raise ValueError('Missing original response for reversed-side repeat')
            if (row['reviewerId'] not in expected
                    or row['promptProfile'] != expected[row['reviewerId']]
                    or row['comparisonCondition'] != baseline
                    or {row['leftCondition'], row['rightCondition']} != {'soilie', baseline}
                    or any(row[key] != original[key] for key in
                           ('promptProfile', 'promptHash', 'studyVersion', 'comparisonCondition'))
                    or row['leftCondition'] != original['rightCondition']
                    or row['rightCondition'] != original['leftCondition']):
                raise ValueError('Reversed-side repeat provenance or mapping changed')
            counts[row['reviewerId']] += 1
            first, second = preference(original), preference(row)
            rows.append({'baseline': baseline, 'reviewerId': row['reviewerId'],
                         'dimension': row['promptProfile'], 'caseId': original['caseId'],
                         'repeatCaseId': row['caseId'], 'agreed': first == second,
                         'transition': 'same' if first == second else
                             ('tie_to_preference' if first == 'tie' else
                              'preference_to_tie' if second == 'tie' else 'opposite_room')})
        if counts != {reviewer: REPEATS_PER_BASELINE_PER_REVIEWER for reviewer in expected}:
            raise ValueError('Every reviewer requires two repeat responses per baseline')

    def summarize(controls):
        agreed = sum(row['agreed'] for row in controls)
        return {'agreements': agreed, 'comparisons': len(controls),
                'agreementPct': 100 * agreed / len(controls),
                'transitions': dict(sorted(Counter(row['transition'] for row in controls).items()))}

    def grouped(field):
        groups = defaultdict(list)
        for row in rows:
            groups[row[field]].append(row)
        return {key: summarize(group) for key, group in sorted(groups.items())}

    total = summarize(rows)
    return {'schemaVersion': 1, **total, 'minimumAgreementPct': MINIMUM_AGREEMENT_PCT,
            'passed': total['agreements'] * 100 >= MINIMUM_AGREEMENT_PCT * total['comparisons'],
            'byReviewer': grouped('reviewerId'), 'byDimension': grouped('dimension'),
            'byBaseline': grouped('baseline'), 'controls': rows,
            'interpretation': 'The same room must be preferred after reversing sides; ties must remain ties. '
                'Agreement measures stability, not correctness. Forty checks, clustered within ten reviewers, '
                'do not establish that the population agreement rate is at least 90%. '
                'The release threshold was set during review collection, before inspecting aggregate results.'}
