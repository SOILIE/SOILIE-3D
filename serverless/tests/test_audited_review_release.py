import unittest
from serverless.cloud_benchmark.audited_review_release import counts
from serverless.cloud_benchmark.full_review import bucket


class FourBucketTests(unittest.TestCase):
    def test_all_nine_combinations_and_side_order(self):
        rows=[]
        for a in ('model_a','model_b','tie'):
            for b in ('model_a','model_b','tie'):
                category,subtype=bucket(a,b)
                self.assertEqual((category,subtype),bucket(b,a))
                rows.append({'bucket':category,'subtype':subtype})
        result=counts(rows)
        self.assertEqual([result[k] for k in ('soilie','baseline','tie','disagreement')],[3,3,1,2])
        self.assertEqual(result['exactAgreements'],3)
        self.assertEqual(result['tiePreferenceChanges'],4)
        self.assertEqual(result['oppositeWinners'],2)
        self.assertEqual(result['subtypes']['soilie'],{'bothPrefer':1,'preferenceAndTie':2})
        self.assertEqual(result['responses'],18)

    def test_unrecognized_or_missing_preference_is_not_a_tie(self):
        for value in (None,'left','right','unavailable'):
            with self.assertRaises(ValueError): bucket(value,'tie')


if __name__=='__main__': unittest.main()
