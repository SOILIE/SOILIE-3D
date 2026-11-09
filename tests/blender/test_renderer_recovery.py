"""Behavioral regressions for real Blender placement and mesh settlement."""
from pathlib import Path
import sys
import random
import unittest

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'modules')]
from modules import render
from modules.support_settlement import settle_objects


def cube(name, location, dimensions=(1, 1, 1)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.context.view_layer.update()
    return obj


def values(obj, size='medium', diameter=1):
    return {'blender_obj': obj, 'size': {'category': size, 'diameter': diameter}}


class PlacementRecovery(unittest.TestCase):
    def setUp(self):
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        render.create_walls_and_floor(-2, 2, -2, 2, 0, 2.6)
        bpy.context.view_layer.update()

    def test_recovery_converges_and_repeats_for_coincident_furniture(self):
        outcomes = []
        for _ in range(2):
            objects = {name: cube(name + '_0001', (0, 0, .5)) for name in ('chair', 'sofa', 'lamp')}
            entries = {name: values(obj) for name, obj in objects.items()}
            original = {name: (0, 0) for name in objects}
            render._resolve_collision_cycle(entries, original)
            boxes = {name: render._collision_box(name, obj) for name, obj in objects.items()}
            self.assertEqual([], render.overlapping_pairs(boxes))
            outcomes.append([tuple(round(v, 6) for v in obj.location) for obj in objects.values()])
            for obj in objects.values():
                bpy.data.objects.remove(obj, do_unlink=True)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_touching_recovery_is_noop_and_architecture_is_immobile(self):
        first = cube('chair_0001', (0, 0, .5))
        second = cube('sofa_0001', (1, 0, .5))
        window = cube('window_0001', (0, 0, .5))
        entries = {'chair': values(first), 'sofa': values(second), 'window': values(window)}
        before = [tuple(o.location) for o in (first, second, window)]
        render._resolve_collision_cycle(entries, {'chair': (0, 0), 'sofa': (1, 0), 'window': (0, 0)})
        self.assertEqual(before, [tuple(o.location) for o in (first, second, window)])
        second.location.x = .5
        bpy.context.view_layer.update()
        render._resolve_collision_cycle(entries, {'chair': (0, 0), 'sofa': (1, 0), 'window': (0, 0)})
        self.assertEqual(before[2], tuple(window.location))
        self.assertEqual(0, render.intersection_volume(render._collision_box('a', first), render._collision_box('b', second)))

    def test_room_expansion_contains_outlying_rotated_furniture(self):
        obj = cube('chair_0001', (3, -2.5, .5), (2, 1, 1))
        obj.rotation_euler.z = .6
        bpy.context.view_layer.update()
        self.assertTrue(render.expand_room_to_contain_objects({'chair': values(obj)}))
        bounds = render._room_bounds()
        box = render._collision_box('chair', obj)
        self.assertLessEqual(bounds[0], box.min_x)
        self.assertGreaterEqual(bounds[1], box.max_x)
        self.assertLessEqual(bounds[2], box.min_y)
        self.assertGreaterEqual(bounds[3], box.max_y)
        floor = render._collision_box('floor', bpy.data.objects['Floor'])
        for actual, expected in zip((floor.min_x, floor.max_x, floor.min_y, floor.max_y), bounds):
            self.assertAlmostEqual(expected, actual, places=5)

    def test_settlement_permutation_gives_same_support_and_heights(self):
        table = cube('table_0001', (0, 0, 1), (1, 1, 1))
        book = cube('book_0001', (0, 0, 2), (.3, .3, .2))
        phone = cube('telephone_0001', (0, 0, 3), (.1, .1, .1))
        objects = {'table': table, 'book': book, 'telephone': phone}
        initial = {name: obj.location.copy() for name, obj in objects.items()}
        positions = []
        for order in (('table', 'book', 'telephone'), ('telephone', 'book', 'table')):
            for name, obj in objects.items():
                obj.location = initial[name]
            bpy.context.view_layer.update()
            settle_objects({name: values(objects[name]) for name in order})
            positions.append([round(obj.location.z, 5) for obj in objects.values()])
        self.assertEqual(positions[0], positions[1])
        for actual, expected in zip(positions[0], (.5, 1.1, 1.25)):
            self.assertAlmostEqual(expected, actual, delta=.00002)

    def test_window_covering_stays_together_through_placement_and_boolean(self):
        # Both objects begin co-located near the back wall, away from corners.
        window = cube('window_0001', (-1.75, 0, 1.3), (.2, 1, 1))
        curtain = cube('curtain_0001', (-1.75, 0, 1.3), (.2, 1, 1))
        inputs = {'window': values(window), 'curtain': values(curtain)}
        render.adjust_overlapping_objects(inputs)
        render.finalize_room_placement(inputs)
        self.assertAlmostEqual(window.location.y, curtain.location.y, delta=.05,
                               msg='Window and curtain separated along the wall')
        wall = bpy.data.objects['Back Wall']
        # The actual cut removes wall material through the window centre.
        origin = (wall.location.x - 1, window.location.y, 1.3)
        hit, _, _, _ = wall.ray_cast(wall.matrix_world.inverted() @ Vector(origin),
                                     wall.matrix_world.inverted().to_3x3() @ Vector((1, 0, 0)))
        self.assertFalse(hit, 'Opening should be empty at its centre')


    def test_window_covering_alignment_after_full_placement(self):
        # Imported front axes must be normalized and turned toward the nearest
        # wall before collision handling, just as visualize() does. Testing an
        # arbitrarily perpendicular box bypasses that production precondition.
        for seed in (0, 1, 2, 3, 4, 5, 42):
            with self.subTest(seed=seed):
                bpy.ops.wm.read_factory_settings(use_empty=True)
                random.seed(seed)
                inputs = {
                    'window': {'coords': [0, 0, 1], 'size': {'category': 'large', 'diameter': 1.2}},
                    'curtain': {'coords': [0, 0, 1], 'size': {'category': 'large', 'diameter': 1.2}},
                    'chair': {'coords': [3, 3, 1], 'size': {'category': 'large', 'diameter': 1.2}},
                }
                render.load_assets(inputs, {})
                render.resize_objects_to_unit_scale()
                rotations = render.transform_objects(inputs)
                render.create_room_boundaries()
                render.fit_walls_around_floor()
                render.adjust_objects_to_floor()
                render.expand_floor()
                rotations = render.objects_back_against_nearest_wall(rotations)
                render.rotate_object_pairs(rotations)
                window = inputs['window']['blender_obj']
                curtain = inputs['curtain']['blender_obj']
                walls = [obj for obj in bpy.context.scene.objects if 'Wall' in obj.name]
                initial_wall = render.closest_object_to_point(window.location, walls)
                normal_axis = 0 if initial_wall.name in ('Back Wall', 'Front Wall') else 1
                tangent_axis = 1 - normal_axis
                box = render._collision_box('window', window)
                widths = (box.max_x - box.min_x, box.max_y - box.min_y)
                self.assertLess(widths[normal_axis], widths[tangent_axis],
                                'Window must face the selected wall before collision')
                render.adjust_overlapping_objects(inputs)
                render.expand_room_to_contain_objects(inputs)
                render.finalize_room_placement(inputs)
                window_wall = render.closest_object_to_point(window.location, walls)
                curtain_wall = render.closest_object_to_point(curtain.location, walls)
                self.assertEqual(window_wall.name, curtain_wall.name)
                final_tangent = 1 if window_wall.name in ('Back Wall', 'Front Wall') else 0
                self.assertAlmostEqual(window.location[final_tangent], curtain.location[final_tangent],
                                       delta=.001, msg='Covering moved along the window wall')

    def test_corner_window_and_covering_remain_on_same_wall(self):
        window = cube('window_0001', (-1.9, 1.7, 1.3), (.4, .5, 1))
        curtain = cube('curtain_0001', (-1.7, 1.9, 1.3), (.5, .4, 1))
        render.finalize_room_placement({'window': values(window), 'curtain': values(curtain)})
        same_back = abs(window.location.x + 1.9) < .12 and abs(curtain.location.x + 1.83) < .12
        same_left = abs(window.location.y - 1.9) < .12 and abs(curtain.location.y - 1.83) < .12
        self.assertTrue(same_back or same_left, f'Assembly split across walls: window {tuple(window.location)}, curtain {tuple(curtain.location)}')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PlacementRecovery))
    if not result.wasSuccessful():
        raise SystemExit(1)
