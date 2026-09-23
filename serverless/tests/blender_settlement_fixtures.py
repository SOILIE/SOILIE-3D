"""Blender fixtures for final contact, including non-vertex edge crossings."""
from pathlib import Path
import sys
import unittest

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.support_settlement import CONTACT_TOLERANCE_M, evaluated_surface, settle_objects, surface_drop
from serverless.benchmark.capture_v4 import support_samples


def cube(name, xyz, size):
    bpy.ops.mesh.primitive_cube_add(size=1, location=xyz)
    obj = bpy.context.object
    obj.name, obj.dimensions = name, size
    bpy.context.view_layer.update()
    return obj


class SettlementFixtures(unittest.TestCase):
    def setUp(self):
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_plane_add(size=10)
        bpy.context.object.name = 'Floor'
        bpy.context.view_layer.update()

    def settle(self, **objects):
        return settle_objects({name: {'blender_obj': obj} for name, obj in objects.items()})

    def test_floor_contact_changes_only_height(self):
        obj = cube('book', (1, 2, 1), (.2, .3, .1))
        obj.rotation_euler.z = .37
        before = (obj.location.x, obj.location.y, tuple(obj.rotation_euler), tuple(obj.scale))
        self.assertEqual(1, len(self.settle(book=obj)))
        self.assertAlmostEqual(.05, obj.location.z, delta=CONTACT_TOLERANCE_M)
        self.assertEqual(before, (obj.location.x, obj.location.y, tuple(obj.rotation_euler), tuple(obj.scale)))
        self.assertEqual([], self.settle(book=obj))

    def test_headboard_does_not_hold_item_above_mattress(self):
        mattress = cube('bed', (0, 0, .35), (2, 2, .7))
        board = cube('board', (0, .95, .9), (2, .1, 1.8))
        bpy.ops.object.select_all(action='DESELECT')
        mattress.select_set(True)
        board.select_set(True)
        bpy.context.view_layer.objects.active = mattress
        bpy.ops.object.join()
        phone = cube('phone', (0, 0, 1.9), (.2, .2, .2))
        self.settle(bed=mattress, telephone=phone)
        self.assertAlmostEqual(.8, phone.location.z, delta=CONTACT_TOLERANCE_M)
        self.assertLess(support_samples(phone, [phone, mattress, bpy.data.objects['Floor']], 0)['gapM'], CONTACT_TOLERANCE_M)

    def test_stack_settles_from_bottom_to_top(self):
        table = cube('table', (0, 0, 1), (2, 2, 1))
        book = cube('book', (0, 0, 2), (.3, .3, .2))
        self.settle(book=book, table=table)
        self.assertAlmostEqual(.5, table.location.z, delta=CONTACT_TOLERANCE_M)
        self.assertAlmostEqual(table.location.z+.6, book.location.z, delta=CONTACT_TOLERANCE_M)

    def test_object_beside_round_support_falls_to_floor(self):
        bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=1, depth=1, location=(0, 0, .5))
        table = bpy.context.object
        # Inside the table's square box, outside its circular surface.
        can = cube('can', (.9, .9, 1.1), (.1, .1, .2))
        self.settle(table=table, soda_can=can)
        self.assertAlmostEqual(.1, can.location.z, delta=CONTACT_TOLERANCE_M)

    def test_crossed_edges_are_contact_even_without_contained_vertices(self):
        lower = cube('lower', (0, 0, .5), (3, .2, 1))
        upper = cube('upper', (0, 0, 2), (.2, 3, .2))
        self.assertAlmostEqual(.9, surface_drop(evaluated_surface(upper), evaluated_surface(lower)), places=5)

    def test_mounted_objects_do_not_drop(self):
        window = cube('window', (0, 0, 2), (1, .1, 1))
        self.assertEqual([], self.settle(window=window))
        self.assertEqual(2, window.location.z)

    def test_fixed_surface_can_support_another_object(self):
        mounted = cube('mounted', (0, 0, 2), (1, 1, .2))
        book = cube('book', (0, 0, 3), (.2, .2, .2))
        self.settle(clock=mounted, book=book)
        self.assertEqual(2, mounted.location.z)
        self.assertAlmostEqual(2.2, book.location.z, delta=CONTACT_TOLERANCE_M)

    def test_duplicate_ground_object_is_not_stacked(self):
        from modules import render
        from unittest.mock import patch
        chair = cube('chair_0003.001', (0,0,.5), (.5,.5,1))
        cabinet = cube('cabinet_0001', (0,0,1), (1,1,2))
        with patch.object(render, 'move_obj_on_top') as stack, patch.object(render, 'move_objects_apart', return_value=True) as separate:
            render.separate_objects(chair, cabinet, 'small', 'large')
            stack.assert_not_called()
            separate.assert_called_once()

    def test_no_floor_beneath_rejects_invented_contact(self):
        obj = cube('book', (20, 0, 2), (.2, .2, .2))
        with self.assertRaisesRegex(RuntimeError, 'No supporting surface'):
            self.settle(book=obj)

    def test_below_floor_penetration_is_lifted_to_surface(self):
        obj = cube('book', (0, 0, .07), (.2, .2, .2))
        changes = self.settle(book=obj)
        self.assertEqual('below_floor', changes[0]['reason'])
        self.assertAlmostEqual(.1, obj.location.z, delta=CONTACT_TOLERANCE_M)


if __name__ == '__main__':
    if not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SettlementFixtures)).wasSuccessful():
        raise RuntimeError('Support settlement fixtures failed')
