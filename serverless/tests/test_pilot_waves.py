from copy import deepcopy
import unittest

from serverless.study.combine_pilots import combine


def wave(version, choice):
    return {'studyVersion':version,'respondentType':'ai_pilot','humanParticipants':0,'reviewersCompleted':10,
            'stimulusEvidence':[{'caseId':version,'soilieScene':'soilie-'+version,'baselineScene':'baseline-'+version}],
            'reviewers':[{'repeatComparisons':2,'agreements':2} for _ in range(10)],
            'responses':[{'caseId':version,'reviewerId':str(i),'promptProfile':'focus-'+str(i),'repeatOf':None,
                          'judgement':choice,'leftCondition':'soilie','rightCondition':'layoutgpt','comparisonCondition':'layoutgpt'} for i in range(10)]}


class PilotWavesTests(unittest.TestCase):
    def test_votes_are_clustered_by_pair_and_half_ties_are_explicit(self):
        a,b = wave('one','left'),wave('two','tie')
        result = combine([a,b],resamples=100)
        self.assertEqual(20,result['mainJudgements'])
        self.assertEqual(2,result['conditions'][0]['distinctPairs'])
        self.assertEqual(.75,result['conditions'][0]['soiliePreferenceShareIncludingHalfTies'])
        self.assertEqual([.5,1.0],result['conditions'][0]['pairBootstrap95PctInterval'])
        self.assertEqual(0,result['humanParticipants'])

    def test_reused_scenes_versions_humans_and_incomplete_pairs_are_rejected(self):
        original = wave('one','left')
        for changed in (original,dict(wave('two','left'),respondentType='human'),
                        dict(wave('two','left'),reviewersCompleted=9)):
            with self.assertRaises(ValueError):
                combine([original,changed])
        changed = deepcopy(wave('two','right'))
        changed['stimulusEvidence'][0]['soilieScene'] = 'soilie-one'
        with self.assertRaises(ValueError):
            combine([original,changed])
        changed = wave('two','right')
        changed['responses'].pop()
        with self.assertRaises(ValueError):
            combine([changed])
