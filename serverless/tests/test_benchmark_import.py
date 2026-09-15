"""Source-axis and normalization fixtures, not a second scene generator."""
import unittest

import numpy as np

from serverless.benchmark.geometry import measure
from serverless.benchmark.import_layoutgpt import normalize, verify_source


class LayoutImportTests(unittest.TestCase):
    def layout(self, angle=0):
        return {"prompt": "Room: max length 10px, max width 8px.",
                "object_list": [["chair", {"left": 8, "top": 4, "depth": 1,
                                          "length": 6, "width": 2, "height": 2,
                                          "orientation": angle}]]}

    def test_original_y_up_source_maps_to_z_up_world(self):
        scene = normalize(self.layout(90), "bedroom", 0, "fixture")
        corners = np.array(scene["objects"][0]["corners"])
        np.testing.assert_allclose(corners.mean(axis=0), [8, 4, 1])
        np.testing.assert_allclose(corners.max(axis=0)-corners.min(axis=0), [2, 6, 2])
        # The source's positive Y-axis yaw maps to negative Z-axis yaw.
        np.testing.assert_allclose(corners[0], [7, 7, 0])

    def test_boundary_percentage_is_scale_invariant_not_physical_metres(self):
        scene = normalize(self.layout(), "bedroom", 0, "fixture")
        metrics = measure(scene)
        self.assertAlmostEqual(100/6, metrics["meanOutsideFootprintPct"])
        self.assertIsNone(metrics["supportGapCm"])
        self.assertIsNone(metrics["connectedClearancePct"])
        scaled = normalize(self.layout(), "bedroom", 0, "fixture")
        scaled["room"]["polygon"] = [[x*4, y*4] for x, y in scaled["room"]["polygon"]]
        scaled["objects"][0]["corners"] = [[v*4 for v in p] for p in scaled["objects"][0]["corners"]]
        self.assertAlmostEqual(metrics["meanOutsideFootprintPct"], measure(scaled)["meanOutsideFootprintPct"])

    def test_duplicate_labels_keep_distinct_instances(self):
        layout = self.layout()
        layout["object_list"] *= 2
        scene = normalize(layout, "bedroom", 0, "fixture")
        self.assertEqual(2, len({obj["id"] for obj in scene["objects"]}))
        self.assertEqual(100, measure(scene)["meanWorstEnvelopeOverlapPct"])

    def test_invalid_geometry_or_altered_source_rejected(self):
        bad = self.layout()
        bad["object_list"][0][1]["length"] = 0
        with self.assertRaises(ValueError):
            normalize(bad, "bedroom", 0, "fixture")
        with self.assertRaises(ValueError):
            verify_source(b"[]", "bedroom")


if __name__ == "__main__":
    unittest.main()
