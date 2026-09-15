import math
import unittest

from serverless.benchmark.geometry import Box, box_corners, intersection_volume, measure


def item(name, center, size=(1, 1, 1), yaw=0):
    return {"id": name, "label": "chair", "corners": box_corners(center, size, yaw)}


def scene(*items, units="m"):
    return {"units": units, "objects": list(items), "room": {"polygon": [[0,0],[4,0],[4,4],[0,4]], "floorZ": 0}}


class GeometryTests(unittest.TestCase):
    def test_disjoint_and_touching_have_zero_volume(self):
        for x in (1, 2):
            self.assertEqual(0, intersection_volume(Box(item("a", [0,0,0])), Box(item("b", [x,0,0]))))

    def test_half_overlap_and_containment(self):
        result = measure(scene(item("a", [1,1,.5]), item("b", [1.5,1,.5])))
        self.assertAlmostEqual(50, result["meanWorstEnvelopeOverlapPct"])
        self.assertAlmostEqual(100, measure(scene(item("a", [2,2,1], (2,2,2)), item("b", [2,2,1])))["maxEnvelopeOverlapPct"])

    def test_rotated_and_tilted_boxes(self):
        a = Box(item("a", [0,0,0], yaw=45))
        b = Box(item("b", [0,0,0]))
        self.assertAlmostEqual(2*math.sqrt(2)-2, intersection_volume(a,b), places=7)
        tilted = item("c", [0,0,0])
        tilted["corners"] = [[x, (y-z)/math.sqrt(2), (y+z)/math.sqrt(2)] for x,y,z in tilted["corners"]]
        self.assertAlmostEqual(2*math.sqrt(2)-2, intersection_volume(Box(tilted),b), places=7)

    def test_stacked_not_collision(self):
        self.assertEqual(0, measure(scene(item("a", [1,1,.5]), item("b", [1,1,1.5])))["meanWorstEnvelopeOverlapPct"])

    def test_sheared_world_transform_does_not_take_upright_shortcut(self):
        original = item('a',[0,0,0],(2,2,2))
        sheared = dict(original,corners=[[x+.5*z,y,z] for x,y,z in original['corners']])
        self.assertFalse(Box(sheared).upright)
        self.assertAlmostEqual(7,intersection_volume(Box(original),Box(sheared)),places=7)

    def test_nonfinite_support_cannot_be_exported_as_a_score(self):
        for value in (float('nan'),float('inf'),-1):
            a = item('a',[1,1,.5])
            a['support'] = {'source':'mesh-ray-samples','samplingVersion':2,'gapM':value,'belowFloorM':0}
            with self.assertRaises(ValueError):
                measure(scene(a))

    def test_continuous_boundary_and_units(self):
        a = item("a", [0,1,.5])
        self.assertAlmostEqual(50, measure(scene(a))["meanOutsideFootprintPct"])
        self.assertIsNone(measure(scene(a, units="px"))["connectedClearancePct"])
        self.assertIsNone(measure(scene(a))["supportGapCm"])

    def test_assembly_and_architecture_exclusions(self):
        a, b = item("a", [1,1,.5]), item("b", [1,1,.5])
        a["assemblyId"] = b["assemblyId"] = "one-chair"
        self.assertEqual(0, measure(scene(a,b))["meanWorstEnvelopeOverlapPct"])
        b["label"] = "window"
        self.assertEqual(1, measure(scene(a,b))["objectCount"])

    def test_invalid_geometry_is_not_successful_zero(self):
        with self.assertRaises(ValueError):
            measure(scene(item("a", [1,1,.5], (0,1,1))))
        a = item("a", [1,1,.5])
        with self.assertRaises(ValueError):
            measure(scene(a,a))

    def test_clearance_corridor_and_support(self):
        a = item("a", [2,2,1], (.1,4,2))
        a["support"] = {"source": "mesh-ray-samples", "samplingVersion":2, "gapM": .02, "belowFloorM": .01}
        result = measure(scene(a))
        self.assertAlmostEqual(2, result["supportGapCm"])
        self.assertAlmostEqual(1, result["belowFloorCm"])
        self.assertGreater(result["connectedClearancePct"], 20)
        self.assertLess(result["connectedClearancePct"], 30)

    def test_superseded_sparse_rays_cannot_be_reported_as_floating(self):
        a = item('table', [2,2,.5])
        a['support'] = {'source':'mesh-ray-samples', 'gapM':.5, 'belowFloorM':0}
        self.assertIsNone(measure(scene(a))['supportGapCm'])

    def test_no_surface_hit_is_unavailable_not_zero(self):
        a = item('a', [2,2,-.5])
        a['support'] = {'source':'mesh-ray-samples', 'samplingVersion':2, 'gapM':None, 'belowFloorM':1}
        result = measure(scene(a))
        self.assertIsNone(result['supportGapCm'])
        self.assertEqual(100, result['belowFloorCm'])

    def test_floor_holes_are_not_usable_room_area(self):
        layout = scene(item("a", [2,2,.5]))
        layout["room"]["holes"] = [[[1,1],[3,1],[3,3],[1,3]]]
        self.assertEqual(100,measure(layout)["meanOutsideFootprintPct"])
        self.assertEqual(12,measure(layout)["roomArea"])

    def test_solid_mesh_evidence_is_strict_and_separate_from_envelopes(self):
        layout = scene(item("a", [1,1,.5]), item("b", [3,3,.5]))
        layout["solidMeshOverlap"] = {
            "method": "evaluated-solid-mesh-boolean-v1", "objectCount": 2,
            "complete": True, "meanWorstOverlapPct": 0, "maxOverlapPct": 0,
            "overlapPairs": [], "unavailablePairs": [],
        }
        result = measure(layout)
        self.assertEqual(0, result["meanWorstSolidOverlapPct"])
        self.assertEqual(0, result["meanWorstEnvelopeOverlapPct"])
        self.assertNotIn("solidOverlap", result["unavailable"])

        layout["solidMeshOverlap"]["complete"] = False
        layout["solidMeshOverlap"]["unavailablePairs"] = [{"a": "a", "b": "b", "reason": "open mesh"}]
        result = measure(layout)
        self.assertIsNone(result["meanWorstSolidOverlapPct"])
        self.assertIn("solidOverlap", result["unavailable"])


if __name__ == "__main__":
    unittest.main()
