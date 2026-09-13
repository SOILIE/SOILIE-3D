from __future__ import annotations

import unittest

from modules.room_fit import WALL_CLEARANCE_M, fit_axis, plan_room_fit


class RoomFitTests(unittest.TestCase):
    def test_auto_is_tight_envelope_after_v4(self):
        plan = plan_room_fit((-1.0, 2.0, -0.5, 1.5, 0.0, 1.8), {"mode": "auto"})
        self.assertAlmostEqual(3.0 + 2 * WALL_CLEARANCE_M, plan["actual"]["widthM"])
        self.assertAlmostEqual(2.0 + 2 * WALL_CLEARANCE_M, plan["actual"]["depthM"])
        self.assertEqual("auto", plan["sizing"])
        self.assertFalse(plan["placementPolicy"]["interiorPlacementChanged"])

    def test_only_deficient_axis_expands(self):
        plan = plan_room_fit(
            (-1.0, 2.0, -0.5, 1.5, 0.0, 1.8),
            {"mode": "custom", "widthM": 5.0, "depthM": 1.0},
        )
        self.assertEqual(5.0, plan["actual"]["widthM"])
        self.assertGreater(plan["actual"]["depthM"], 1.0)
        self.assertEqual(["depth"], plan["adjustedAxes"])

    def test_anchor_controls_only_where_extra_boundary_space_goes(self):
        minimum = fit_axis(-1.0, 2.0, 6.0, "minimum")
        maximum = fit_axis(-1.0, 2.0, 6.0, "maximum")
        self.assertAlmostEqual(-1.0 - WALL_CLEARANCE_M, minimum[0])
        self.assertAlmostEqual(2.0 + WALL_CLEARANCE_M, maximum[1])
        self.assertEqual(minimum[3], maximum[3])

    def test_height_is_automatic(self):
        low = plan_room_fit((-1, 1, -1, 1, 0, 1.8), {"mode": "auto"})
        tall = plan_room_fit((-1, 1, -1, 1, 0, 2.8), {"mode": "auto"})
        self.assertEqual(2.4, low["actual"]["heightM"])
        self.assertEqual(3.0, tall["actual"]["heightM"])


if __name__ == "__main__":
    unittest.main()
