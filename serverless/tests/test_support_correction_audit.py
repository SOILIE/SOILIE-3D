from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from serverless.benchmark.audit_support_corrections import audit_cohort, audit_record
from serverless.benchmark.geometry import box_corners


def fixture():
    row = {'id': 'book', 'label': 'book', 'asset': 'book_0001', 'kind': 'furniture',
           'corners': box_corners([1, 1, 1.5], [1, 1, 1]),
           'transform': [[1,0,0,1],[0,1,0,1],[0,0,1,1.5],[0,0,0,1]]}
    source = {'status': 'complete', 'id': 'scene-0', 'attempt': 0,
              'request': {'seed': 42}, 'generationSeconds': 2,
              'stages': {'final': {'objects': [row], 'room': {'polygon': [[0,0],[2,0],[2,2],[0,2]], 'floorZ': 0},
                                   'solidMeshOverlap': {'complete': True, 'maxOverlapPct': 0}}}}
    raw = json.dumps(source).encode()
    derived = deepcopy(source)
    obj = derived['stages']['final']['objects'][0]
    obj['transform'][2][3] -= 1
    for point in obj['corners']:
        point[2] -= 1
    obj['support'] = {'source': 'mesh-extremum-floor-contact', 'samplingVersion': 3,
                      'gapM': 0, 'belowFloorM': 0, 'supportKind': 'floor', 'supportId': 'Floor'}
    derived['supportCorrection'] = {'sourceSha256': hashlib.sha256(raw).hexdigest(),
                                   'implementation': {'observeOnly': False, 'settlementSha256': 'fixture'},
                                   'originalGenerationSeconds': 2, 'correctionSeconds': .1,
                                   'moves': [{'id': 'book', 'dropM': 1}]}
    return source, derived, raw


class SupportCorrectionAuditTests(unittest.TestCase):
    def check(self, source, derived, raw):
        return audit_record(source, derived, hashlib.sha256(raw).hexdigest())

    def test_z_only_correction_preserves_original_evidence(self):
        source, derived, raw = fixture()
        result = self.check(source, derived, raw)
        self.assertEqual(1, result['changedObjects'])
        self.assertEqual(.1, result['correctionSeconds'])

    def test_horizontal_movement_rejected(self):
        source, derived, raw = fixture()
        derived['stages']['final']['objects'][0]['transform'][0][3] += .1
        with self.assertRaisesRegex(ValueError, 'Nonvertical'):
            self.check(source, derived, raw)

    def test_remaining_gap_and_missing_measurement_rejected(self):
        for gap in (.01, None, float('nan')):
            source, derived, raw = fixture()
            derived['stages']['final']['objects'][0]['support']['gapM'] = gap
            with self.assertRaisesRegex(ValueError, 'Unresolved support'):
                self.check(source, derived, raw)

    def test_mounted_object_cannot_be_dropped(self):
        source, derived, raw = fixture()
        source['stages']['final']['objects'][0]['label'] = 'clock'
        derived['stages']['final']['objects'][0]['label'] = 'clock'
        with self.assertRaisesRegex(ValueError, 'Mounted'):
            self.check(source, derived, raw)

    def test_checksum_and_original_timing_are_enforced(self):
        source, derived, raw = fixture()
        with self.assertRaisesRegex(ValueError, 'checksum'):
            audit_record(source, derived, 'incorrect')
        derived['generationSeconds'] = .1
        with self.assertRaisesRegex(ValueError, 'timing changed'):
            self.check(source, derived, raw)

    def test_nonfinite_or_incomplete_mesh_measurement_rejected(self):
        for value in (None, float('nan'), .01):
            source, derived, raw = fixture()
            derived['stages']['final']['solidMeshOverlap']['maxOverlapPct'] = value
            with self.assertRaisesRegex(ValueError, 'Incomplete or overlapping'):
                self.check(source, derived, raw)

    def test_cohort_cannot_complete_early_and_tracks_changed_stimuli(self):
        scratch = Path(__file__).resolve().parents[2]/'.codex'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch, prefix='support-audit-test-') as directory:
            base = Path(directory)
            source_dir, target = base/'source', base/'derived'
            source_dir.mkdir()
            target.mkdir()
            _, derived, raw = fixture()
            (source_dir/'attempt-00000.json').write_bytes(raw)
            report = audit_cohort(source_dir, target, expected=1)
            self.assertFalse(report['complete'])
            self.assertEqual(['attempt-00000.json'], report['missingAttempts'])
            (target/'attempt-00000.json').write_text(json.dumps(derived))
            report = audit_cohort(source_dir, target, expected=1)
            self.assertTrue(report['complete'])
            self.assertEqual(['scene-0'], report['changedSceneIds'])
            self.assertEqual({'floor': 1, 'object': 0, 'architecture': 0}, report['contactCounts'])
            self.assertFalse(audit_cohort(source_dir, target, expected=10000)['complete'])


if __name__ == '__main__':
    unittest.main()
