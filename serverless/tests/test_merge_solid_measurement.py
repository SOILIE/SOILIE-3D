from copy import deepcopy
import unittest

from serverless.benchmark.merge_solid_measurement import merge


def campaign_row():
    stage = {"units": "m", "room": {"polygon": [[0, 0], [3, 0], [3, 3], [0, 3]], "floorZ": 0},
             "objects": [{"id": "a", "corners": [[0, 0, 0]], "transform": [[1, 0, 0, 0]]}]}
    return {"request": {"roomType": "bedroom", "seed": 1, "objectCount": 3}, "selection": ["bed"],
            "status": "complete", "stages": {name: deepcopy(stage)
                                                  for name in ("beforeSeparation", "afterSeparation", "final")}}


class MergeSolidMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.campaign = campaign_row()
        self.campaign["stages"]["final"]["solidMeshOverlap"] = {
            "method": "evaluated-solid-mesh-boolean-v1",
            "complete": False,
            "unavailablePairs": [{"a": "table", "b": "cap"}],
        }
        self.replay = deepcopy(self.campaign)
        for stage in self.replay["stages"].values():
            stage["objects"][0]["frontDirection"] = [1, 0]
            stage["objects"][0]["frontConvention"] = "V4 asset-corrected local +X"
        self.replay["stages"]["final"]["solidMeshOverlap"] = {
            "method": "evaluated-mesh-intersection-v2",
            "complete": True,
            "unavailablePairs": [],
            "meanWorstOverlapPct": 0,
        }

    def test_only_complete_parity_checked_measurement_is_merged(self):
        result = merge(self.campaign, self.replay)
        self.assertEqual(result["stages"]["final"]["solidMeshOverlap"]["meanWorstOverlapPct"], 0)
        self.assertNotIn("frontDirection", result["stages"]["final"]["objects"][0])
        self.assertEqual(result["request"], self.campaign["request"])

    def test_moved_replay_is_rejected(self):
        self.replay["stages"]["final"]["objects"][0]["corners"][0][0] += .01
        with self.assertRaises(ValueError):
            merge(self.campaign, self.replay)

    def test_incomplete_replay_is_rejected(self):
        self.replay["stages"]["final"]["solidMeshOverlap"]["complete"] = False
        with self.assertRaises(ValueError):
            merge(self.campaign, self.replay)


if __name__ == "__main__":
    unittest.main()
