import unittest

from serverless.cloud_benchmark.review_work import selected_assignment_ids


class ReviewWorkTests(unittest.TestCase):
    def test_filter_preserves_repeat_trials_only_for_retained_pairs(self):
        protocol = {'stimulusEvidence': [
            {'caseId': 'a', 'matchingStratum': ['bedroom']},
            {'caseId': 'b', 'matchingStratum': ['living_room']}]}
        assignments = [{'caseId': 'a', 'repeatOf': None}, {'caseId': 'b', 'repeatOf': None},
                       {'caseId': 'repeat-a', 'repeatOf': 'a'}, {'caseId': 'repeat-b', 'repeatOf': 'b'}]
        self.assertEqual({'a', 'repeat-a'}, selected_assignment_ids(protocol, assignments, 'bedroom'))
        self.assertEqual({'b', 'repeat-b'}, selected_assignment_ids(protocol, assignments, 'living_room'))
        self.assertEqual({'a', 'b', 'repeat-a', 'repeat-b'}, selected_assignment_ids(protocol, assignments))


if __name__ == '__main__': unittest.main()
