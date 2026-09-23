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
    def assert_restored_vertices(self, labels, assets, initial_diameter):
        clear_scene()
        inputs = {label: {'coords':[i*.47,i*.18,.9],
                          'size':{'diameter':initial_diameter+i*.23,'category':'medium'}}
                  for i,label in enumerate(labels)}
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

    def test_restore_repeats_scaled_centroid_for_nonuniform_window_coverings(self):
        self.assert_restored_vertices(('window','curtain','blinds'),
                                     ('window_0003.obj','curtain_0002.obj','blinds_0001.obj'), 1.9)

    def test_saved_thin_curtain_bounds(self):
        # Fixed regression from the stopped replay. The previous single
        # centroid pass missed these saved bounds by 11.683 micrometres.
        clear_scene()
        row = {'id':'curtain', 'label':'curtain', 'asset':'curtain_0002',
               'transform':[[.33064189553260803,0,0,-.3836638033390045],
                            [0,.5414678454399109,0,.6914225816726685],
                            [0,0,1.2881526947021484,1.2999999523162842], [0,0,0,1]],
               'corners':[[x,y,z] for x in (-.3976556658744812,-.37390437722206116)
                          for y in (.3090325891971588,1.0805702209472656)
                          for z in (.5870131254196167,1.8508555889129639)]}
        obj = restore_object(row, render.load_rotations())
        actual = [obj.matrix_world@Vector(v) for v in obj.bound_box]
        self.assertLess(max(min((a-Vector(b)).length for b in row['corners']) for a in actual), 1e-6)
        clear_scene()

    def test_restore_preserves_mesh_vertices_including_last_import_axis_bake(self):
        self.assert_restored_vertices(('chair','speaker','cupboard'),
                                     ('chair_0002.obj','speaker_0007.obj','cupboard_0001.obj'), 1.27)


if __name__ == '__main__':
    if not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SavedSupportFixtures)).wasSuccessful():
        raise RuntimeError('Saved mesh restoration failed')
