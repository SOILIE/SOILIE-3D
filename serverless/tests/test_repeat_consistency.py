"""Synthetic control fixtures; never use these records as review evidence."""
from copy import deepcopy
import unittest

from serverless.cloud_benchmark.repeat_consistency import audit_repeat_assignments, repeat_consistency
from serverless.cloud_benchmark.reviews import PLAN


def fixtures():
    reports = {}
    for baseline in ('layoutgpt', 'infinigen_controlled'):
        rows = []
        for i, profile in enumerate(PLAN):
            for case in range(2):
                original = {'caseId': f'case-{case}', 'repeatOf': None,
                            'reviewerId': f'reviewer-{i + 1:02d}', 'promptProfile': profile,
                            'promptHash': 'synthetic', 'studyVersion': 'synthetic',
                            'comparisonCondition': baseline, 'leftCondition': 'soilie',
                            'rightCondition': baseline, 'judgement': 'left' if case == 0 else 'tie'}
                repeat = {**original, 'caseId': f'repeat-{case}', 'repeatOf': original['caseId'],
                          'leftCondition': baseline, 'rightCondition': 'soilie',
                          'judgement': 'right' if case == 0 else 'tie'}
                rows.extend((original, repeat))
        reports[baseline] = {'responses': rows}
    return reports


class RepeatConsistencyTests(unittest.TestCase):
    def test_swapped_buttons_and_unchanged_ties_agree(self):
        result = repeat_consistency(fixtures())
        self.assertEqual((40, 40, 100), (result['agreements'], result['comparisons'], result['agreementPct']))
        self.assertTrue(result['passed'])
        self.assertEqual(10, len(result['byReviewer']))
        self.assertTrue(all(row['comparisons'] == 8 for row in result['byDimension'].values()))

    def test_threshold_and_same_button_disagreement(self):
        source = fixtures()
        repeats = [row for row in source['layoutgpt']['responses'] if row['repeatOf']]
        for row in repeats[:4]:
            row['judgement'] = 'left'  # Opposite room, or tie changing to preference.
        result = repeat_consistency(source)
        self.assertTrue(result['passed'])
        self.assertEqual((36, 90), (result['agreements'], result['agreementPct']))
        self.assertEqual(2, result['transitions']['opposite_room'])
        self.assertEqual(2, result['transitions']['tie_to_preference'])
        repeats[4]['judgement'] = 'tie'
        result = repeat_consistency(source)
        self.assertFalse(result['passed'])
        self.assertEqual(35, result['agreements'])
        self.assertEqual(1, result['transitions']['preference_to_tie'])

    def test_missing_duplicate_or_incorrectly_mapped_controls_fail(self):
        source = fixtures()
        for mutation, message in (
                (lambda rows: rows.pop(), 'two repeat responses'),
                (lambda rows: rows.append(deepcopy(rows[-1])), 'Duplicate'),
                (lambda rows: rows[1].update(leftCondition='soilie'), 'mapping'),
                (lambda rows: rows[1].update(promptHash='changed'), 'provenance'),
                (lambda rows: rows[1].update(repeatOf='missing'), 'Missing original')):
            candidate = deepcopy(source)
            mutation(candidate['layoutgpt']['responses'])
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                repeat_consistency(candidate)

    def test_delivered_images_must_really_swap(self):
        assignments = fixtures()['layoutgpt']['responses'][:4]
        for row in assignments:
            row['leftImage'], row['rightImage'] = ('B', 'A') if row['repeatOf'] else ('A', 'B')
        packet = [{key: row[key] for key in ('caseId', 'leftImage', 'rightImage')} for row in assignments]
        self.assertEqual(2, audit_repeat_assignments(assignments, packet))
        packet[1]['leftImage'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'Delivered repeat images'):
            audit_repeat_assignments(assignments, packet)
        packet[1]['leftImage'] = 'B'
        assignments[1]['rightCondition'] = 'layoutgpt'
        with self.assertRaisesRegex(ValueError, 'swap both images and condition labels'):
            audit_repeat_assignments(assignments, packet)


if __name__ == '__main__':
    unittest.main()
