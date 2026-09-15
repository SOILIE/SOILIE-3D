"""Run with Blender in the background; observes fixtures without scene output."""
from pathlib import Path
import sys
import unittest

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from serverless.benchmark.solid_overlap import measure


def cube(name, location):
    bpy.ops.mesh.primitive_cube_add(size=2, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


class SolidOverlapFixtures(unittest.TestCase):
    def tearDown(self):
        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def test_disjoint_and_touching_bounds_prove_zero_without_boolean(self):
        for x in (2, 3):
            result = measure([("a", cube("a", (0, 0, 0))), ("b", cube("b", (x, 0, 0)))])
            self.assertTrue(result["complete"])
            self.assertEqual(0, result["meanWorstOverlapPct"])
            self.assertEqual(1, result["broadPhaseDisjointPairs"])
            self.tearDown()

    def test_exact_boolean_reports_half_of_each_equal_cube(self):
        result = measure([("a", cube("a", (0, 0, 0))), ("b", cube("b", (1, 0, 0)))])
        self.assertTrue(result["complete"])
        self.assertAlmostEqual(50, result["meanWorstOverlapPct"], places=5)
        self.assertAlmostEqual(4, result["overlapPairs"][0]["intersectionM3"], places=5)

    def test_open_mesh_is_unavailable_instead_of_a_false_zero(self):
        first, second = cube("open", (0, 0, 0)), cube("closed", (1, 0, 0))
        bm = bmesh.new()
        bm.from_mesh(first.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES")
        bm.to_mesh(first.data)
        bm.free()
        result = measure([("open", first), ("closed", second)])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["meanWorstOverlapPct"])
        self.assertIn("non-manifold", result["unavailablePairs"][0]["reason"])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SolidOverlapFixtures)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Solid-overlap fixture failed")
