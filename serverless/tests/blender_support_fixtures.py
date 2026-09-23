"""Run with Blender --background --factory-startup --python (no scene outputs).

Actual triangle-mesh fixtures, not a replacement generator. Narrow table legs
intentionally fall between the old nine-ray grid positions.
"""
from pathlib import Path
import sys
import unittest

from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from serverless.benchmark.mesh_support import sample_support


def box(center, size):
    x, y, z = center
    w, d, h = [value/2 for value in size]
    vertices = [(x+dx*w, y+dy*d, z+dz*h)
                for dx,dy,dz in ((-1,-1,-1),(-1,-1,1),(-1,1,-1),(-1,1,1),
                                 (1,-1,-1),(1,-1,1),(1,1,-1),(1,1,1))]
    faces = [(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)]
    return vertices, faces


def mesh(*parts):
    vertices, faces = [], []
    for points, polygons in parts:
        faces.extend(tuple(index+len(vertices) for index in face) for face in polygons)
        vertices.extend(points)
    return vertices, BVHTree.FromPolygons([Vector(point) for point in vertices], faces)


class MeshSupportFixtures(unittest.TestCase):
    def setUp(self):
        _, self.floor = mesh(box((0,0,-.05), (8,8,.1)))
        self.parts = [box((0,0,.95), (2,2,.1))]+[
            box((x,y,.45), (.04,.04,.9)) for x in (-.7,.7) for y in (-.7,.7)]

    def test_grounded_narrow_legs_do_not_look_like_floating_tabletop(self):
        vertices, tree = mesh(*self.parts)
        result = sample_support(vertices, tree, [self.floor], 0)
        self.assertAlmostEqual(0, result['gapM'], places=5)

    def test_lifted_table_gap_is_continuous_distance(self):
        lifted = [([(x,y,z+.2) for x,y,z in points], faces) for points,faces in self.parts]
        vertices, tree = mesh(*lifted)
        self.assertAlmostEqual(.2, sample_support(vertices, tree, [self.floor], 0)['gapM'], places=5)

    def test_item_on_table_uses_its_surface_not_the_floor(self):
        _, table = mesh(*self.parts)
        vertices, tree = mesh(box((0,0,1.1), (.2,.2,.2)))
        self.assertAlmostEqual(0, sample_support(vertices, tree, [table,self.floor], 0)['gapM'], places=5)

    def test_no_real_surface_does_not_invent_an_infinite_floor(self):
        vertices, tree = mesh(box((10,10,1), (.2,.2,.2)))
        self.assertIsNone(sample_support(vertices, tree, [self.floor], 0)['gapM'])

    def test_floor_and_table_support_are_identified_without_changing_distance(self):
        _, table = mesh(*self.parts)
        for center, kind, identity, expected in (((0,0,1.1), 'object', 'table', 0),
                                                ((3,3,.3), 'floor', 'Floor', .2)):
            vertices, tree = mesh(box(center, (.2,.2,.2)))
            result = sample_support(vertices, tree, [self.floor, table], 0, support_metadata=[
                {'kind':'floor', 'id':'Floor'}, {'kind':'object', 'id':'table'}])
            self.assertEqual((kind, identity), (result['supportKind'], result['supportId']))
            self.assertAlmostEqual(expected, result['gapM'], places=5)

    def test_support_gap_is_not_the_height_of_a_book_above_floor(self):
        _, table = mesh(*self.parts)
        vertices, tree = mesh(box((0,0,1.3), (.2,.2,.2)))
        result = sample_support(vertices, tree, [table, self.floor], 0, support_metadata=[
            {'kind':'object','id':'table'}, {'kind':'floor','id':'Floor'}])
        self.assertAlmostEqual(.2, result['gapM'], places=5)
        self.assertEqual('object', result['supportKind'])

    def test_missing_hit_does_not_invent_support_category(self):
        vertices, tree = mesh(box((10,10,1), (.2,.2,.2)))
        result = sample_support(vertices, tree, [self.floor], 0,
                                support_metadata=[{'kind':'floor','id':'Floor'}])
        self.assertIsNone(result['gapM'])
        self.assertNotIn('supportKind', result)

    def test_below_floor_uses_actual_mesh_vertices(self):
        vertices, tree = mesh(box((0,0,0), (.2,.2,.2)))
        self.assertAlmostEqual(.1, sample_support(vertices, tree, [self.floor], 0)['belowFloorM'], places=5)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MeshSupportFixtures)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError('Mesh support fixture failed')
