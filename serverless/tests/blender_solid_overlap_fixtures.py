"""Run with Blender in the background; observes fixtures without scene output."""
from pathlib import Path
import sys
import unittest

import bmesh
import bpy
from mathutils import Vector

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

    def test_open_mesh_with_disjoint_rendered_surfaces_is_zero(self):
        first, second = cube("open", (0, 0, 0)), cube("closed", (1, 0, 0))
        bm = bmesh.new()
        bm.from_mesh(first.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES")
        bm.to_mesh(first.data)
        bm.free()
        result = measure([("open", first), ("closed", second)])
        # Removing the crossing face leaves two visually disjoint triangle
        # surfaces even though a hypothetical filled cube would overlap.
        self.assertTrue(result["complete"])
        self.assertEqual(0, result["meanWorstOverlapPct"])
        self.assertEqual(1, result["surfaceDisjointPairs"])

    def test_disjoint_open_surfaces_resolve_to_zero(self):
        def triangle(name, dz):
            mesh = bpy.data.meshes.new(name + "-mesh")
            # Parallel triangles have overlapping 3D bounds because z=x+dz,
            # but their evaluated surfaces remain strictly disjoint.
            mesh.from_pydata([(0,0,dz),(1,0,1+dz),(0,1,dz)], [], [(0,1,2)])
            mesh.update()
            obj = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            return obj
        result = measure([("a", triangle("a", 0)), ("b", triangle("b", .1))])
        self.assertTrue(result["complete"])
        self.assertEqual(0, result["meanWorstOverlapPct"])
        self.assertEqual(1, result["surfaceDisjointPairs"])

    def test_crossing_open_surfaces_remain_incomplete(self):
        def triangle(name, points):
            mesh = bpy.data.meshes.new(name + "-mesh")
            mesh.from_pydata(points, [], [(0,1,2)])
            mesh.update()
            obj = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            return obj
        first = triangle("first", [(0,0,0),(1,0,1),(0,1,0)])
        second = triangle("second", [(0,0,1),(1,0,0),(0,1,1)])
        result = measure([("first", first), ("second", second)])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["meanWorstOverlapPct"])
        self.assertIn("intersecting triangle pair", result["unavailablePairs"][0]["reason"])

    def test_one_part_per_million_penetration_is_numerical_contact(self):
        first = cube("first", (0, 0, 0))
        second = cube("second", (1.9999998, 0, 0))
        result = measure([("first", first), ("second", second)])
        self.assertTrue(result["complete"])
        self.assertEqual(0, result["meanWorstOverlapPct"])
        self.assertEqual(1, result["numericalContactPairs"])

    def test_replay_retains_disjoint_proof_for_untouched_touching_objects(self):
        first = cube('first', (0, 0, 0))
        second = cube('second', (2, 0, 0))
        source = {name: [list(obj.matrix_world @ Vector(point)) for point in obj.bound_box]
                  for name, obj in [('first', first), ('second', second)]}
        # Reconstruction noise, larger than the relative numerical-contact
        # cutoff but below the measured ten-micrometre restoration tolerance.
        second.location.x -= 4e-6
        bpy.context.view_layer.update()
        result = measure([('first', first), ('second', second)], unchanged_source_bounds=source)
        self.assertTrue(result['complete'])
        self.assertEqual(1, result['preservedBoundsDisjointPairs'])
        self.assertEqual(0, result['meanWorstOverlapPct'])

    def test_moved_object_still_gets_real_mesh_test_with_unchanged_neighbour(self):
        first = cube('first', (0, 0, 0))
        second = cube('second', (1, 0, 0))
        source = {'first': [list(first.matrix_world @ Vector(point)) for point in first.bound_box]}
        result = measure([('first', first), ('second', second)], unchanged_source_bounds=source)
        self.assertEqual(0, result['preservedBoundsDisjointPairs'])
        self.assertAlmostEqual(50, result['meanWorstOverlapPct'], places=5)

    def test_restoration_certificate_rejects_actual_displacement(self):
        first, second = cube('first', (0, 0, 0)), cube('second', (2, 0, 0))
        source = {'second': [list(second.matrix_world @ Vector(point)) for point in second.bound_box]}
        second.location.x -= .1
        bpy.context.view_layer.update()
        with self.assertRaisesRegex(ValueError, 'moved or incorrectly restored'):
            measure([('first', first), ('second', second)], unchanged_source_bounds=source)

    def test_intersecting_source_bounds_do_not_certify_separation(self):
        first, second = cube('first', (0, 0, 0)), cube('second', (1, 0, 0))
        source = {name: [list(obj.matrix_world @ Vector(point)) for point in obj.bound_box]
                  for name, obj in [('first', first), ('second', second)]}
        result = measure([('first', first), ('second', second)], unchanged_source_bounds=source)
        self.assertEqual(0, result['preservedBoundsDisjointPairs'])
        self.assertAlmostEqual(50, result['meanWorstOverlapPct'], places=5)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SolidOverlapFixtures)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Solid-overlap fixture failed")
