"""Small, real-Blender renderer regressions; no image render or corpus required."""
from pathlib import Path
import math
import os
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'modules')]
from modules import render
from modules.support_settlement import settle_objects


def cube(name, xyz=(0, 0, 0), dimensions=(1, 1, 1)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=xyz)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.context.view_layer.update()
    return obj


class RendererIntegration(unittest.TestCase):
    def setUp(self):
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)

    def test_rotated_scaled_world_bounds_and_record_exact_duplicate(self):
        obj = cube('chair_0001.001', (3, -2, 4), (2, 4, 6))
        obj.rotation_euler.z = math.pi / 2
        bpy.context.view_layer.update()
        box = render._collision_box('chair.1', obj)
        for actual, expected in zip((box.min_x, box.max_x, box.min_y, box.max_y, box.min_z, box.max_z),
                                    (1, 5, -3, -1, 1, 7)):
            self.assertAlmostEqual(expected, actual, places=5)
        inputs = {'chair.1': {'color': (0.1, 0.2, 0.3), 'coords': (7, 8, 9), 'size': {'category': 'large'}}}
        record = render.imagination_object_output_record('chair.1', obj.name, inputs)
        self.assertEqual('chair_0001.001', record['asset_name'])
        self.assertEqual((7, 8, 9), (record['og_x'], record['og_y'], record['og_z']))
        self.assertAlmostEqual(90, record['rot_z'], places=4)
        self.assertAlmostEqual(2, record['dim_x'], places=5)
        self.assertEqual('large', record['sizecat'])

    def test_recovery_clears_two_overlapping_meshes_without_z_motion(self):
        a, b = cube('a'), cube('b', (.5, 0, 0))
        objects = {'a': a, 'b': b}
        boxes = {n: render._collision_box(n, o) for n, o in objects.items()}
        move = render.choose_recovery_move(boxes, ['a', 'b'], {'a': (0, 0), 'b': (.5, 0)})
        selected = objects[move.name]
        selected.location.x += move.dx
        selected.location.y += move.dy
        bpy.context.view_layer.update()
        result = {n: render._collision_box(n, o) for n, o in objects.items()}
        self.assertEqual(0, render.intersection_volume(result['a'], result['b']))
        self.assertEqual(0, selected.location.z)

    def test_floor_and_stacked_support_with_window_covering(self):
        cube('Floor', (0, 0, -.05), (10, 10, .1))
        table = cube('table', (0, 0, 1), (1, 1, 1))
        book = cube('book', (0, 0, 2), (.2, .2, .2))
        curtain = cube('curtain_0001', (0, 0, 1.5), (1, 1, .1))
        settle_objects({n: {'blender_obj': o} for n, o in
                        [('curtain', curtain), ('book', book), ('table', table)]})
        self.assertAlmostEqual(.5, table.location.z, delta=1e-5)
        self.assertAlmostEqual(1.1, book.location.z, delta=1.1e-5)
        self.assertAlmostEqual(1.5, curtain.location.z, delta=1e-6)

    def test_window_and_coverings_follow_wall_but_cut_only_once(self):
        cube('Back Wall', (0, 0, 1), (.2, 4, 2))
        objs = [cube(name, (.2, 0, 1), (.15, 1, 1))
                for name in ('window_0001', 'curtain_0001', 'blinds_0001')]
        with patch.object(render, 'cut_window_opening') as cut:
            render.adjust_windows_to_walls()
        self.assertEqual(1, cut.call_count)
        self.assertEqual('window_0001', cut.call_args.args[0].name)
        for obj in objs:
            self.assertAlmostEqual(1.3, obj.location.z, places=5)
        self.assertAlmostEqual(.1, objs[0].location.x, places=5)
        self.assertAlmostEqual(.17, objs[1].location.x, places=5)

    def test_recency_changes_materials_and_owned_light_then_restores(self):
        names = ['lamp_0001', 'chair_0001', 'table_0001', 'book_0001', 'sofa_0001', 'window_0001']
        objs = [cube(name, (i, 0, 0)) for i, name in enumerate(names)]
        cube('Back Wall', (0, -4, 1), (.2, 4, 2))
        bpy.ops.object.camera_add(location=(0, 0, 10))
        bpy.context.object.name = 'Camera'
        lights, lamp_max, window_max = render.add_light_to_lamps_and_windows()
        colors = {name: (.2, .3, .4) for name in names}
        with patch.object(render, 'focus_walls_floor'), patch.object(render, 'apply_colors_to_objects'):
            render.change_imagination_focus(0, names, 0, 1, colors, lights, lamp_max, window_max)
        lamp_light = bpy.data.objects['lamp_0001.Light']
        window_light = bpy.data.objects['window_0001.Light']
        self.assertAlmostEqual(400, lamp_light.data.energy)
        self.assertAlmostEqual(37.5, window_light.data.energy)
        self.assertTrue(objs[1].hide_render)
        self.assertAlmostEqual(0, objs[1].data.materials[0].node_tree.nodes.get('Principled BSDF').inputs['Alpha'].default_value)
        render.set_imagination_objects_opaque(names, colors, lights, lamp_max, window_max)
        self.assertTrue(all(not obj.hide_render for obj in objs))
        self.assertAlmostEqual(75, window_light.data.energy)
        self.assertFalse(any(m.name.startswith(render.RECENCY_REMESH_MODIFIER) for o in objs for m in o.modifiers))

    def test_seeded_import_ignores_asset_directory_enumeration_order(self):
        # Identical tiny triangle OBJ files suffice to exercise real importer and selection.
        sandbox = ROOT / '.codex/tests/blender'
        sandbox.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(sandbox)) as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                Path('assets').mkdir()
                names = ['chair_0001.obj', 'chair_0002.obj', 'chair_0003.obj']
                for name in names:
                    (Path('assets') / name).write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
                selections = []
                for listing in (names, list(reversed(names))):
                    bpy.ops.object.select_all(action='SELECT')
                    bpy.ops.object.delete(use_global=False)
                    for mesh in list(bpy.data.meshes):
                        if not mesh.users:
                            bpy.data.meshes.remove(mesh)
                    random.seed(1234)
                    inputs = {'chair': {}, 'chair.1': {}}
                    with patch.object(render, 'load_rotations', return_value={'chair': dict.fromkeys(names)}), patch.object(render.os, 'listdir', return_value=listing):
                        render.load_assets(inputs, {})
                    selections.append([inputs[key]['asset_name'] for key in inputs])
                self.assertEqual(selections[0], selections[1])
                self.assertEqual(2, len(set(selections[0])))
            finally:
                os.chdir(old_cwd)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RendererIntegration))
    if not result.wasSuccessful():
        raise SystemExit(1)
