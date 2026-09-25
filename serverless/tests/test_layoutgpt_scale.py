from copy import deepcopy
import hashlib
import json
import unittest

from serverless.benchmark.geometry import box_corners, measure
from serverless.benchmark.layoutgpt_scale import physical_scene, apply_clearance


class LayoutGPTScaleTests(unittest.TestCase):
    def scene(self):
        return {'id': 'layoutgpt-test', 'model': 'layoutgpt', 'units': 'px',
                'objects': [{'id': 'chair', 'label': 'chair', 'corners': box_corners([128, 128, 32], [32, 48, 64], 30)}],
                'room': {'polygon': [[0, 0], [384, 0], [384, 256], [0, 256]], 'floorZ': 0}}

    def floor(self):
        return [[-1, 0, -1], [5, 0, -1], [5, 0, 3], [-1, 0, 3]]

    def test_uniform_physical_conversion_preserves_predictions_and_rotation(self):
        scene = self.scene(); original = deepcopy(scene)
        scaled, scale = physical_scene(scene, self.floor())
        self.assertEqual(4 / 256, scale)
        self.assertEqual(scene, original)
        self.assertEqual(scaled['room']['polygon'][2], [6, 4])
        for before, after in zip(scene['objects'][0]['corners'], scaled['objects'][0]['corners']):
            self.assertEqual(after, [value * scale for value in before])
        metrics = measure(scaled)
        self.assertGreater(metrics['connectedClearancePct'], 0)
        self.assertLess(metrics['connectedClearancePct'], 100)
        self.assertIsNone(metrics['supportGapCm'])
        self.assertIsNone(metrics['belowFloorCm'])

    def test_controlled_prompt_mismatch_is_rejected(self):
        scene = self.scene(); scene['room']['polygon'][2][1] = 260
        with self.assertRaisesRegex(ValueError, 'prompt'):
            physical_scene(scene, self.floor())

    def test_released_layout_uses_author_renderer_scale_and_keeps_prompt_boundary(self):
        scene = self.scene(); scene['room']['polygon'][2][1] = 260
        scaled, scale = physical_scene(scene, self.floor(), require_prompt_match=False)
        self.assertEqual(scaled['room']['polygon'][2], [6, 260 * scale])

    def test_bad_units_and_degenerate_or_nonfinite_metadata_rejected(self):
        for vertices in ([[0, 0, 0]], [[0, 0, 0], [float('nan'), 0, 4]]):
            with self.assertRaises(ValueError): physical_scene(self.scene(), vertices)
        scene = self.scene(); scene['units'] = 'm'
        with self.assertRaises(ValueError): physical_scene(scene, self.floor())

    def test_clearance_supplement_binds_to_exact_scene_not_ai_geometry(self):
        scene = self.scene(); rows = [{'scene': scene, 'metrics': measure(scene)}]
        checksum = hashlib.sha256(json.dumps(scene, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        evidence = {'rooms': [{'sceneId': scene['id'], 'sceneSha256': checksum, 'connectedClearancePct': 42.5}]}
        original = deepcopy(rows)
        apply_clearance(rows, evidence)
        self.assertEqual(original[0]['scene'], rows[0]['scene'])
        self.assertEqual(42.5, rows[0]['metrics']['connectedClearancePct'])
        self.assertNotIn('clearance', rows[0]['metrics']['unavailable'])
        self.assertEqual(original[0]['metrics']['meanWorstEnvelopeOverlapPct'], rows[0]['metrics']['meanWorstEnvelopeOverlapPct'])
        self.assertIsNone(rows[0]['metrics']['supportGapCm'])
        for bad in ({'rooms': []}, {'rooms': evidence['rooms'] * 2},
                    {'rooms': [{**evidence['rooms'][0], 'sceneSha256': 'different'}]},
                    {'rooms': [{**evidence['rooms'][0], 'connectedClearancePct': float('nan')}]}):
            with self.assertRaises(ValueError): apply_clearance(rows, bad)


if __name__ == '__main__':
    unittest.main()
