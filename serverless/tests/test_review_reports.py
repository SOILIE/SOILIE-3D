from copy import deepcopy
import hashlib
import unittest

from serverless.cloud_benchmark.review_reports import combine_focused, SelectedStore
from serverless.cloud_benchmark.reviews import PLAN


def report(version, room, choice='left'):
    reviewers, responses = [], []
    for index, profile in enumerate(PLAN):
        identity = f'reviewer-{index+1:02d}'
        prompt = 'Question: ' + profile
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        reviewers.append({'reviewerId': identity, 'profile': profile, 'model': 'GPT-5.6 Sol',
            'reportedModel': 'GPT-5.6 Sol', 'reportedReasoningEffort': 'Extra High',
            'promptHash': prompt_hash, 'reviewPrompt': prompt, 'decisionRubric': 'one question',
            'dimensionRubric': profile, 'evidenceRubric': 'visual only', 'interfaceEmphasis': profile,
            'complete': True, 'responses': 1, 'repeatComparisons': 1, 'agreements': 1,
            'votes': {'soilie' if choice == 'left' else 'layoutgpt': 1}})
        for key, repeated in (('a', None), ('r', 'a')):
            responses.append({'caseId': key, 'repeatOf': repeated, 'studyVersion': version,
                'reviewerId': identity, 'promptProfile': profile, 'promptHash': prompt_hash,
                'respondentType': 'ai_pilot', 'evidenceMode': 'visual_only',
                'comparisonCondition': 'layoutgpt', 'leftCondition': 'soilie', 'rightCondition': 'layoutgpt',
                'judgement': choice, 'errorChoice': 'neither', 'confidence': 3, 'note': 'Test fixture.'})
    return {'studyVersion': version, 'decisionScope': 'focus_only', 'evidenceMode': 'visual_only',
        'respondentType': 'ai_pilot', 'humanParticipants': 0, 'reviewersCompleted': 10,
        'reviewerConfiguration': {'model': 'GPT-5.6 Sol', 'reasoningEffort': 'Extra High'},
        'reviewers': reviewers, 'responses': responses, 'sourceProtocolSha256': hashlib.sha256(version.encode()).hexdigest(),
        'sampling': {'minimumSemanticSimilarity': .4, 'maximumFurnitureDensityDifference': .25},
        'stimuli': [{'caseId': 'a', 'baseline': 'layoutgpt'}],
        'stimulusEvidence': [{'caseId': 'a', 'soilieScene': 's-' + version, 'baselineScene': 'l-' + version,
                             'matchingStratum': [room, 3]}]}


class ReviewReportsTests(unittest.TestCase):
    def test_room_strata_count_pairs_not_repeats_and_preserve_original_ids(self):
        first, second = report('one', 'bedroom'), report('two', 'living_room', 'right')
        original = deepcopy(first)
        result = combine_focused([first, second], 'cohort', target_per_room=1)
        self.assertTrue(result['releaseEligible'])
        self.assertEqual({'bedroom': 1, 'living_room': 1}, result['roomTypePairs'])
        self.assertEqual(40, len(result['responses']))
        self.assertEqual(2, len({row['caseId'] for row in result['stimuli']}))
        self.assertEqual({'one', 'two'}, {row['studyVersion'] for row in result['responses']})
        self.assertEqual({'a', 'r'}, {row['sourceCaseId'] for row in result['responses']})
        for row in result['dimensionResults']:
            self.assertEqual((2, 4, 2, 2), (row['pairs'], row['responses'], row['soilie'], row['baseline']))
        for row in result['dimensionsByRoomType']['bedroom']:
            self.assertEqual((1, 2, 2, 0), (row['pairs'], row['responses'], row['soilie'], row['baseline']))
        for row in result['dimensionsByRoomType']['living_room']:
            self.assertEqual((1, 2, 0, 2), (row['pairs'], row['responses'], row['soilie'], row['baseline']))
        self.assertEqual(2, result['reviewers'][0]['repeatComparisons'])
        self.assertEqual(original, first)

    def test_partial_coverage_is_preview_only(self):
        first = report('one', 'bedroom')
        with self.assertRaises(ValueError): combine_focused([first], 'cohort')
        preview = combine_focused([first], 'cohort', preview=True)
        self.assertFalse(preview['complete'])
        self.assertFalse(preview['releaseEligible'])
        self.assertEqual(0, preview['dimensionsByRoomType']['living_room'][0]['responses'])

    def test_invalid_mixed_or_incomplete_evidence_is_rejected(self):
        first = report('one', 'bedroom')
        changes = []
        changed = report('two', 'living_room'); changed['responses'].pop(0); changes.append(changed)
        changed = report('two', 'living_room'); changed['responses'].append(changed['responses'][0]); changes.append(changed)
        changed = report('two', 'living_room'); changed['humanParticipants'] = 1; changes.append(changed)
        changed = report('two', 'living_room'); changed['reviewers'][0]['promptHash'] = 'wrong'; changes.append(changed)
        changed = report('two', 'living_room'); changed['responses'][0]['studyVersion'] = 'other'; changes.append(changed)
        changed = report('two', 'living_room'); changed['stimulusEvidence'][0]['soilieScene'] = 's-one'; changes.append(changed)
        changed = report('two', 'living_room'); changed['stimuli'][0]['baseline'] = 'infinigen_controlled'; changes.append(changed)
        changed = report('two', 'living_room'); changed['sampling']['minimumSemanticSimilarity'] = .1; changes.append(changed)
        changes.append(first)
        for changed in changes:
            with self.subTest(changed=changes.index(changed)), self.assertRaises(ValueError):
                combine_focused([first, changed], 'cohort', target_per_room=1)

    def test_selection_is_read_only_and_excludes_unselected_repeats(self):
        original = {'studyVersion': 'v', 'respondentType': 'ai_pilot', 'sessionId': 'private', 'assignments': [
            {'caseId': 'a', 'repeatOf': None}, {'caseId': 'r-a', 'repeatOf': 'a'},
            {'caseId': 'b', 'repeatOf': None}, {'caseId': 'r-b', 'repeatOf': 'b'}]}
        class Store:
            def sessions(self): return [original, {'studyVersion': 'v', 'respondentType': 'human'}]
            def responses(self, _identity): return original['assignments']
        protocol = {'studyVersion': 'v', 'stimulusEvidence': [
            {'caseId': 'a', 'matchingStratum': ['bedroom']}, {'caseId': 'b', 'matchingStratum': ['living_room']}]}
        selected = SelectedStore(Store(), protocol, 'bedroom')
        sessions = list(selected.sessions())
        self.assertEqual(2, len(sessions[0]['assignments']))
        self.assertEqual(2, len(selected.responses('private')))
        self.assertEqual(4, len(original['assignments']))


if __name__ == '__main__': unittest.main()
