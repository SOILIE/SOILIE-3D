"""Blender-level visibility fixtures for duplicate and assembled objects."""

from pathlib import Path
import hashlib
import os
import sys
import unittest

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(os.environ.get('SOILIE_RUNTIME_DIR', ROOT))
sys.path[:0] = [str(ROOT), str(RUNTIME), str(RUNTIME / 'modules')]
from modules import render
from modules.recency import transition_remesh_depths, transition_visibility


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def cube(name, location):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


class RecencyFixtures(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.names = [
            'window_0001', 'blinds_0001', 'curtain_0001', 'chair_0001',
            'chair_0001.001', 'desk_0001', 'lamp_0001', 'table_0001',
        ]
        self.colors = {
            'wall': Vector((0.5, 0.5, 0.5)),
            'floor': Vector((0.5, 0.5, 0.5)),
        }
        for index, name in enumerate(self.names):
            obj = cube(name, (index * 2, 0, 1))
            self.colors[name] = Vector((0.5, 0.3, 0.2))
            obj.data.materials.append(bpy.data.materials.new(f'{name}-primary'))

        # A second slot catches the historical partial-fade behavior.
        window = bpy.data.objects['window_0001']
        window.data.materials.append(bpy.data.materials.new('window-secondary'))

        # Simulate two imported instances sharing one source material, then
        # enforce the renderer's per-instance mesh/material ownership contract.
        first_chair = bpy.data.objects['chair_0001']
        second_chair = bpy.data.objects['chair_0001.001']
        second_chair.data = first_chair.data
        shared = bpy.data.materials.new('shared-chair')
        for name in ('chair_0001', 'chair_0001.001'):
            obj = bpy.data.objects[name]
            obj.data.materials.clear()
            obj.data.materials.append(shared)
            render.make_materials_single_user(obj)

        cube('Floor', (7, 0, -1))
        cube('Back Wall', (7, 2, 1))
        bpy.ops.object.camera_add(location=(7, -10, 6))
        bpy.context.object.name = 'Camera'
        bpy.context.scene.camera = bpy.context.object
        self.lights, self.lamp_energy, self.window_energy = render.add_light_to_lamps_and_windows()

    @staticmethod
    def material_alphas(obj):
        return [
            float(material.node_tree.nodes.get('Principled BSDF').inputs['Alpha'].default_value)
            for material in obj.data.materials
        ]

    def test_five_item_window_assembly_and_duplicate_instance_visibility(self):
        render.change_imagination_focus(
            7, self.names, 0, 4, self.colors, self.lights,
            self.lamp_energy, self.window_energy,
        )
        expected = transition_visibility(8, 7, 0.0)
        observed = [self.material_alphas(bpy.data.objects[name])[0] for name in self.names]
        for expected_alpha, observed_alpha in zip(expected, observed):
            self.assertAlmostEqual(expected_alpha, observed_alpha)
        self.assertEqual(5, sum(alpha > 0 for alpha in observed))

        first = bpy.data.objects['chair_0001']
        second = bpy.data.objects['chair_0001.001']
        self.assertIsNot(first, second)
        self.assertIsNot(first.data, second.data)
        self.assertIsNot(first.data.materials[0], second.data.materials[0])
        self.assertNotEqual(self.material_alphas(first), self.material_alphas(second))

        for name in ('window_0001', 'blinds_0001', 'curtain_0001'):
            obj = bpy.data.objects[name]
            self.assertTrue(obj.hide_render)
            self.assertTrue(all(alpha == 0 for alpha in self.material_alphas(obj)))
            self.assertTrue(all(material.shadow_method == 'NONE' for material in obj.data.materials))

        energies = {light.name: light.data.energy for light in self.lights}
        self.assertEqual(0, energies['window_0001.Light'])
        self.assertEqual(self.lamp_energy * expected[6], energies['lamp_0001.Light'])

    def test_duplicate_instance_drives_focus_and_serialized_dimensions(self):
        first = bpy.data.objects['chair_0001']
        duplicate = bpy.data.objects['chair_0001.001']
        first.dimensions = (1.0, 1.0, 1.0)
        duplicate.dimensions = (2.0, 3.0, 4.0)
        bpy.context.view_layer.update()

        observed_focus = []
        original_look_at = render.look_at

        def capture_focus(_camera, focus_point):
            observed_focus.append(focus_point.copy())

        render.look_at = capture_focus
        try:
            render.change_imagination_focus(
                self.names.index('chair_0001.001'),
                self.names,
                0,
                4,
                self.colors,
                self.lights,
                self.lamp_energy,
                self.window_energy,
            )
        finally:
            render.look_at = original_look_at

        expected_focus = render.get_centroid(duplicate)
        self.assertEqual(1, len(observed_focus))
        self.assertLess((observed_focus[0] - expected_focus).length, 1e-7)
        self.assertGreater(
            (observed_focus[0] - render.get_centroid(first)).length,
            1.0,
        )

        inputs = {
            'chair.001': {
                'color': (10, 20, 30),
                'coords': (7.0, 8.0, 9.0),
                'size': {'category': 'large'},
            },
        }
        remesh = duplicate.modifiers.new(
            name=render.RECENCY_REMESH_MODIFIER,
            type='REMESH',
        )
        remesh.mode = 'BLOCKS'
        remesh.octree_depth = 4
        bpy.context.view_layer.update()
        render.set_imagination_objects_opaque(
            self.names, self.colors, self.lights,
            self.lamp_energy, self.window_energy,
        )
        self.assertFalse(any(
            modifier.name.startswith(render.RECENCY_REMESH_MODIFIER)
            for modifier in duplicate.modifiers
        ))
        record = render.imagination_object_output_record(
            'chair.001',
            'chair_0001.001',
            inputs,
        )
        self.assertEqual('chair_0001.001', record['asset_name'])
        self.assertEqual((2.0, 3.0, 4.0), tuple(
            round(float(record[field]), 6)
            for field in ('dim_x', 'dim_y', 'dim_z')
        ))
        self.assertNotEqual(
            tuple(round(float(value), 6) for value in first.dimensions),
            tuple(round(float(record[field]), 6) for field in ('dim_x', 'dim_y', 'dim_z')),
        )

    def test_opaque_evidence_capture_restores_state_before_snapshot(self):
        render.change_imagination_focus(
            7, self.names, 0, 4, self.colors, self.lights,
            self.lamp_energy, self.window_energy,
        )
        state_during_snapshot = []
        original_snapshot = render.take_snapshot_from_above

        def inspect_snapshot(_filepath):
            state_during_snapshot.extend(
                self.material_alphas(bpy.data.objects[name])[0]
                for name in self.names
            )

        render.take_snapshot_from_above = inspect_snapshot
        try:
            render.take_opaque_snapshot_from_above(
                'unused.png', self.names, self.colors, self.lights,
                self.lamp_energy, self.window_energy,
            )
        finally:
            render.take_snapshot_from_above = original_snapshot

        self.assertEqual([1.0] * len(self.names), state_during_snapshot)
        for name in self.names:
            obj = bpy.data.objects[name]
            self.assertFalse(obj.hide_render)
            self.assertTrue(all(alpha == 1 for alpha in self.material_alphas(obj)))
            self.assertTrue(all(material.shadow_method == 'OPAQUE' for material in obj.data.materials))
        energies = {light.name: light.data.energy for light in self.lights}
        self.assertEqual(self.window_energy, energies['window_0001.Light'])
        self.assertEqual(self.lamp_energy, energies['lamp_0001.Light'])

    def test_every_eight_object_transition_frame_restores_complete_state(self):
        for focus_index in range(len(self.names)):
            for frame_index in range(4):
                render.change_imagination_focus(
                    focus_index, self.names, frame_index, 4, self.colors,
                    self.lights, self.lamp_energy, self.window_energy,
                )
                progress = frame_index / 4
                expected_alphas = transition_visibility(
                    len(self.names), focus_index, progress,
                )
                expected_depths = transition_remesh_depths(
                    len(self.names), focus_index, progress,
                )
                self.assertLessEqual(
                    sum(alpha > 0 for alpha in expected_alphas),
                    5,
                )
                for name, alpha, depth in zip(
                    self.names, expected_alphas, expected_depths,
                ):
                    obj = bpy.data.objects[name]
                    self.assertTrue(all(
                        abs(observed - alpha) < 1e-7
                        for observed in self.material_alphas(obj)
                    ))
                    self.assertEqual(alpha == 0, obj.hide_render)
                    self.assertEqual(alpha > 0, obj.visible_shadow)
                    self.assertTrue(all(
                        material.shadow_method == ('NONE' if alpha == 0 else 'HASHED')
                        for material in obj.data.materials
                    ))
                    remesh = [
                        modifier for modifier in obj.modifiers
                        if modifier.name.startswith(render.RECENCY_REMESH_MODIFIER)
                    ]
                    self.assertEqual(0 if depth is None else 1, len(remesh))
                    if depth is not None:
                        self.assertEqual(depth, remesh[0].octree_depth)

                energies = {light.name: light.data.energy for light in self.lights}
                window_index = self.names.index('window_0001')
                lamp_index = self.names.index('lamp_0001')
                self.assertAlmostEqual(
                    self.window_energy * expected_alphas[window_index],
                    energies['window_0001.Light'],
                )
                self.assertAlmostEqual(
                    self.lamp_energy * expected_alphas[lamp_index],
                    energies['lamp_0001.Light'],
                )

        # The explicit endpoint of the final handoff is the first settled
        # state, which proves the rendered loop can close without stale state.
        render.change_imagination_focus(
            len(self.names) - 1, self.names, 4, 4, self.colors,
            self.lights, self.lamp_energy, self.window_energy,
        )
        observed = [self.material_alphas(bpy.data.objects[name])[0] for name in self.names]
        for expected_alpha, observed_alpha in zip(
            transition_visibility(8, 0, 0.0), observed,
        ):
            self.assertAlmostEqual(expected_alpha, observed_alpha)

    def test_opaque_and_imagined_stills_render_as_distinct_images(self):
        output_dir = ROOT / '.codex' / 'blender-recency-fixtures'
        ordinary = output_dir / 'ordinary.png'
        imagined = output_dir / 'imagined.png'
        output_dir.mkdir(parents=True, exist_ok=True)
        scene = bpy.context.scene
        scene.render.engine = 'BLENDER_EEVEE'
        scene.eevee.taa_render_samples = 1
        scene.render.resolution_x = 64
        scene.render.resolution_y = 48
        scene.render.resolution_percentage = 100
        try:
            render.set_imagination_objects_opaque(
                self.names, self.colors, self.lights,
                self.lamp_energy, self.window_energy,
            )
            render.take_snapshot_from_above(str(ordinary))
            render.change_imagination_focus(
                7, self.names, 0, 4, self.colors, self.lights,
                self.lamp_energy, self.window_energy,
            )
            render.take_snapshot_from_above(str(imagined))

            sizes = []
            for path in (ordinary, imagined):
                self.assertTrue(path.is_file())
                loaded = bpy.data.images.load(str(path), check_existing=False)
                try:
                    sizes.append(tuple(loaded.size))
                finally:
                    bpy.data.images.remove(loaded)
            self.assertEqual([(64, 48), (64, 48)], sizes)
            self.assertNotEqual(
                hashlib.sha256(ordinary.read_bytes()).digest(),
                hashlib.sha256(imagined.read_bytes()).digest(),
            )
        finally:
            ordinary.unlink(missing_ok=True)
            imagined.unlink(missing_ok=True)
            try:
                output_dir.rmdir()
            except OSError:
                pass


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RecencyFixtures)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError('Recency Blender fixtures failed')
