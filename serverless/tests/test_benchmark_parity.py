from copy import deepcopy
import unittest

from serverless.benchmark.verify_parity import compare


class ParityTests(unittest.TestCase):
    def row(self):
        stage = {"units": "m", "room": {"polygon": [[0,0],[3,0],[3,3],[0,3]], "floorZ": 0},
                 "objects": [{"id": "a", "corners": [[0,0,0]], "transform": [[1,0,0,0]]}]}
        return {"request": {"roomType": "bedroom", "seed": 1, "objectCount": 3}, "selection": ["bed"],
                "status": "complete", "stages": {name: deepcopy(stage) for name in ("beforeSeparation", "afterSeparation", "final")}}

    def test_support_observation_does_not_change_equality(self):
        original, replay = self.row(), self.row()
        replay["stages"]["final"]["objects"][0]["support"] = {"gapM": 0}
        self.assertTrue(compare(original, replay)["exactPlacementEquality"])

    def test_derived_front_direction_does_not_change_equality(self):
        original, replay = self.row(), self.row()
        replay["stages"]["final"]["objects"][0].update({
            "frontDirection": [1, 0],
            "frontConvention": "V4 asset-corrected local +X",
        })
        self.assertTrue(compare(original, replay)["exactPlacementEquality"])

    def test_any_moved_coordinate_is_rejected(self):
        original, replay = self.row(), self.row()
        replay["stages"]["afterSeparation"]["objects"][0]["corners"][0][0] += .000001
        with self.assertRaises(ValueError):
            compare(original, replay)

    def test_different_selection_or_completion_cannot_pass(self):
        original, replay = self.row(), self.row()
        replay["selection"] = ["chair"]
        with self.assertRaises(ValueError):
            compare(original, replay)
        replay = self.row()
        replay["status"] = "failed"
        with self.assertRaises(ValueError):
            compare(original, replay)
