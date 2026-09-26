"""Exercise publication boundaries without fabricating publishable reviews."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.cloud_benchmark.finalize_fresh_reviews import finalize


class FreshReviewDeliveryTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'output'
        self.write('preflight.json', {'passed': True, 'geometryChanged': False})
        self.write('source-scenes.json', {'scenes': []})
        self.write('measured-scenes.json', [])
        self.write('cohort.json', {'complete': True, 'soilieScenes': 10000,
            'measurementsSha256': hashlib.sha256((self.root / 'measured-scenes.json').read_bytes()).hexdigest()})

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def test_changed_geometry_cannot_create_release(self):
        self.write('preflight.json', {'passed': True, 'geometryChanged': True})
        with self.assertRaisesRegex(ValueError, 'geometry-preserving preflight'):
            finalize(self.root, self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_incomplete_review_cannot_create_release(self):
        with patch('serverless.cloud_benchmark.finalize_fresh_reviews.source_report', return_value={}), \
                patch('serverless.cloud_benchmark.finalize_fresh_reviews.combine_focused',
                      side_effect=ValueError('Incomplete registered reviewers')):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                finalize(self.root, self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_export_does_not_claim_an_undelivered_interface_reminder(self):
        # This isolated fixture stubs the independently tested review gates.
        # It is written only to a disposable test directory, never an archive.
        fixture = {'reviewers': [{'reviewPrompt': 'Frozen assigned prompt',
                                 'interfaceEmphasis': 'Unused UI copy'}],
                   'responses': [], 'stimuli': [], 'roomTypePairs': {}, 'releaseEligible': True}
        with patch('serverless.cloud_benchmark.finalize_fresh_reviews.source_report', return_value={}), \
                patch('serverless.cloud_benchmark.finalize_fresh_reviews.combine_focused', return_value=fixture), \
                patch('serverless.cloud_benchmark.finalize_fresh_reviews.public_summary', side_effect=lambda report, _: report):
            finalize(self.root, self.root, self.output)
        report = json.loads((self.output / 'ai-pilot-responses.json').read_bytes())
        self.assertNotIn('interfaceEmphasis', report['reviewers'][0])
        self.assertEqual('Frozen assigned prompt', report['reviewers'][0]['reviewPrompt'])
        self.assertFalse(report['delivery']['additionalInterfaceReminderShown'])
        self.assertIn('proportions only', report['delivery']['numericEvidence'])


if __name__ == '__main__':
    unittest.main()
