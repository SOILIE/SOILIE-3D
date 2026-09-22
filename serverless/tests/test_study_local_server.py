import unittest

from serverless.study.local_server import reviewer_model


class LocalStudyServerTests(unittest.TestCase):
    def test_invitation_uses_frozen_model_and_reasoning_effort(self):
        document = {"reviewerConfiguration": {
            "model": "GPT-5.6 Sol",
            "reasoningEffort": "Extra High",
        }}
        self.assertEqual(reviewer_model(document), "GPT-5.6 Sol (Extra High reasoning effort)")

    def test_missing_provenance_is_rejected(self):
        with self.assertRaises(ValueError):
            reviewer_model({})


if __name__ == "__main__":
    unittest.main()
