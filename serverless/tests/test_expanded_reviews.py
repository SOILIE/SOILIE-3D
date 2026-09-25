import json
from pathlib import Path
import tempfile
import unittest

from serverless.benchmark.expand_infinigen import sha_file
from serverless.cloud_benchmark.expanded_reviews import completed_rows, require_same_pairs


class ExpandedReviewTests(unittest.TestCase):
    def test_review_freeze_rejects_running_strata_and_changed_geometry(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            (root / 'living_room').mkdir()
            measured = root / 'living_room/measured.json'
            measured.write_text(json.dumps({'scene': {'id': 'a', 'roomType': 'living_room',
                                                      'model': 'infinigen_controlled'}}))
            state = {'complete': False, 'targetPairs': 120, 'existingPairs': 20,
                     'selectedPairs': [{}] * 100,
                     'attempts': [{'measured': 'living_room/measured.json', 'measuredSha256': sha_file(measured)}]}
            checkpoint = root / 'living_room/checkpoint.json'
            checkpoint.write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError, 'complete 120-pair'):
                completed_rows(root, 'living_room')
            state['complete'] = True
            checkpoint.write_text(json.dumps(state))
            rows, pairs, checksum = completed_rows(root, 'living_room')
            self.assertEqual(('a', 100, sha_file(checkpoint)), (rows[0]['scene']['id'], len(pairs), checksum))
            measured.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                completed_rows(root, 'living_room')

    def test_freeze_preserves_both_scene_identity_and_geometry(self):
        row = {'soilieScene': 'a', 'baselineScene': 'b', 'soilieDigest': 'aa', 'baselineDigest': 'bb'}
        require_same_pairs({'stimulusEvidence': [row]}, [row])
        for changed in ({**row, 'soilieScene': 'c'}, {**row, 'baselineDigest': 'changed'}):
            with self.assertRaises(ValueError):
                require_same_pairs({'stimulusEvidence': [changed]}, [row])


if __name__ == '__main__':
    unittest.main()
