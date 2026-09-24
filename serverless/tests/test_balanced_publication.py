from copy import deepcopy
import unittest

from serverless.cloud_benchmark.publish import timing_conditions
from serverless.study.export_pilot import focused_dimension_results, matching_description


class BalancedPublicationTests(unittest.TestCase):
    def sources(self):
        return [{'seed': i, 'platform': platform, 'roomType': room,
                 'generationSeconds': seconds, 'correctionSeconds': 2 if platform == 'local' else 0}
                for i, (platform, room, seconds) in enumerate(
                    [(platform, room, seconds) for platform, room, seconds in
                     [('local', 'bedroom', 10), ('local', 'living_room', 20),
                      ('AWS Lambda', 'bedroom', 5), ('AWS Lambda', 'living_room', 8)]
                     for _ in range(2500)])]

    def test_platform_timers_are_not_pooled_or_overwritten_by_corrections(self):
        groups = timing_conditions(self.sources())
        self.assertEqual(4, len(groups))
        self.assertEqual([10, 20, 5, 8], [row['completedLatencySeconds']['median'] for row in groups])
        self.assertEqual([6, 3, 12, 7.5], [row['completedPerMinute'] for row in groups])
        self.assertEqual([2500, 2500, 0, 0], [row['correctedScenes'] for row in groups])

    def test_missing_duplicate_or_wrong_condition_cannot_publish(self):
        sources = self.sources()
        for mutation in ('missing', 'duplicate', 'condition'):
            rows = deepcopy(sources)
            if mutation == 'missing': rows.pop()
            elif mutation == 'duplicate': rows[-1]['seed'] = rows[0]['seed']
            else: rows[-1]['roomType'] = 'bedroom'
            with self.assertRaises(ValueError): timing_conditions(rows)

    def test_invalid_timers_cannot_create_nan_chart_values(self):
        for key, value in [('generationSeconds', float('nan')), ('generationSeconds', True),
                           ('correctionSeconds', float('inf'))]:
            rows = self.sources()
            rows[0][key] = value
            with self.assertRaises(ValueError): timing_conditions(rows)

    def test_room_strata_keep_repeats_and_other_rooms_out_of_totals(self):
        protocol = {'decisionScope': 'focus_only', 'reviewerPlan': ['orientation', 'orientation'],
                    'cases': [{'id': name, 'comparisonCondition': 'layoutgpt'} for name in ('bed', 'living')],
                    'stimulusEvidence': [{'caseId': 'bed', 'matchingStratum': ['bedroom']},
                                         {'caseId': 'living', 'matchingStratum': ['living_room']}]}
        rows = [{'caseId': name, 'promptProfile': 'orientation', 'repeatOf': None,
                 'judgement': side, 'leftCondition': 'soilie', 'rightCondition': 'layoutgpt'}
                for name, side in [('bed', 'left'), ('bed', 'left'), ('living', 'right'), ('living', 'tie')]]
        rows.append({**rows[0], 'repeatOf': 'bed'})
        reviewers = [{'profile': 'orientation'}] * 2
        bedroom = focused_dimension_results(protocol, rows, reviewers, 'bedroom')[0]
        living = focused_dimension_results(protocol, rows, reviewers, 'living_room')[0]
        self.assertEqual((1, 2, 2, 0), (bedroom['pairs'], bedroom['responses'], bedroom['soilie'], bedroom['baseline']))
        self.assertEqual((1, 2, 0, 1, 1), (living['pairs'], living['responses'], living['soilie'], living['baseline'], living['tie']))

    def test_methods_use_actual_density_and_role_threshold(self):
        text = matching_description({'sampling': {'minimumSemanticSimilarity': 1/3,
                                                   'maximumFurnitureDensityDifference': 1}})
        self.assertIn('33.3%', text)
        self.assertIn('at most 1;', text)
        self.assertIn('sofas in living rooms', text)
        self.assertNotIn('40%', text)


if __name__ == '__main__':
    unittest.main()
