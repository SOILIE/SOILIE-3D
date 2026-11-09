import math
import unittest

import numpy as np

from modules import utils


class CoreUtilsTests(unittest.TestCase):
    def test_projection_inverts_camera_rotation_and_translation(self):
        # Camera is rotated 90 degrees around Z and translated in world space.
        q = math.sqrt(.5)
        pose = [q, 0, 0, q, 10, 20, 30]
        K = [[2, 0, 100], [0, 3, 200], [0, 0, 1]]
        uv, depth, transform = utils.project_3d_2d([[9, 22, 34], [10, 20, 29]], pose, K)
        np.testing.assert_allclose(uv[0], [101, 200.75], atol=1e-12)
        self.assertTrue(np.isnan(uv[1]).all())
        np.testing.assert_allclose(depth, [4, -1])
        np.testing.assert_allclose(transform[:3, 3], [10, 20, 30])

    def test_raster_depth_order_and_pixel_origins(self):
        for origin in (0, 1):
            uv = np.array([[origin, origin], [origin, origin], [origin + 639, origin + 479], [-2, -2]])
            points = np.array([[1, 2, 3], [0, 0, 0], [7, 8, 9], [4, 5, 6]])
            labels, xyz, xy = utils.construct_frames(uv, [3, 1, 2, 1], points, [1, 2, 3, 4], pixel_origin=origin)
            self.assertEqual(labels[0, 0], 2)
            self.assertEqual(labels[479, 639], 3)
            np.testing.assert_array_equal(xyz[0, 0], [0, 0, 0])
            np.testing.assert_array_equal(xy[0, 0], [origin, origin])
            self.assertTrue(np.isnan(xyz[1, 1]).all())

    def test_polygon_drops_nonfinite_samples_and_preserves_real_zero(self):
        xy, xyz = utils.mapped_polygon_samples([[0, 0], [1, 0], [np.nan, 1]], [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
        np.testing.assert_array_equal(xy, [[0, 0], [1, 0], [0, 0]])
        np.testing.assert_array_equal(xyz, [[0, 0, 0], [1, 0, 0], [0, 0, 0]])
        with self.assertRaises(ValueError):
            utils.mapped_polygon_samples([[np.nan, 0]], [[0, 0, 0]])

    def test_projection_rejects_nonfinite_input(self):
        with self.assertRaises(ValueError):
            utils.project_3d_2d([[1, np.inf, 1]], [1, 0, 0, 0, 0, 0, 0], np.eye(3))
