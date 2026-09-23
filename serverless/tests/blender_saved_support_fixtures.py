"""Verify replayed vertices, not just boxes that can conceal an incorrect front."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules import render
from serverless.benchmark.settle_saved import clear_scene, restore_object


class SavedSupportFixtures(unittest.TestCase):
    def test_restore_preserves_mesh_vertices_including_last_import_axis_bake(self):
        clear_scene()
        assets = ('chair_0002.obj', 'speaker_0007.obj', 'cupboard_0001.obj')
        inputs = {label: {'coords':[i*.47,i*.18,.9],
                          'size':{'diameter':1.27+i*.23,'category':'medium'}}
                  for i,label in enumerate(('chair','speaker','cupboard'))}
        with patch.object(render.random, 'choice', side_effect=assets):
            render.load_assets(inputs, {})
        render.resize_objects_to_unit_scale()
        render.transform_objects(inputs)
        expected = []
        for label, values in inputs.items():
            obj = values['blender_obj']
            obj.rotation_euler.z = .67
            bpy.context.view_layer.update()
            row = {'id':label,'label':label,'asset':obj.name,
                   'corners':[list(obj.matrix_world@Vector(v)) for v in obj.bound_box],
                   'transform':[list(v) for v in obj.matrix_world]}
            expected.append((row, [obj.matrix_world@v.co for v in obj.data.vertices]))
        clear_scene()
        for index, (row, vertices) in enumerate(expected):
            restored = restore_object(row, render.load_rotations(), last_imported=index==len(expected)-1)
            actual = [restored.matrix_world@v.co for v in restored.data.vertices]
            self.assertEqual(len(vertices), len(actual))
            self.assertLess(max((a-b).length for a,b in zip(vertices,actual)), 1e-5, row['id'])
        clear_scene()


if __name__ == '__main__':
    if not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SavedSupportFixtures)).wasSuccessful():
        raise RuntimeError('Saved mesh restoration failed')
