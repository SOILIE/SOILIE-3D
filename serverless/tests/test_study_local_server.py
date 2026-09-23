import unittest
from pathlib import Path
import tempfile

from serverless.study.local_server import clean_static_path, reviewer_model


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

    def test_clean_website_route_resolves_to_local_html(self):
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder)
            (site/"study.html").write_text("study")
            self.assertEqual("/study.html", clean_static_path(site, "/study"))
            self.assertEqual("/missing", clean_static_path(site, "/missing"))
            self.assertEqual("/asset.svg", clean_static_path(site, "/asset.svg"))


if __name__ == "__main__":
    unittest.main()
