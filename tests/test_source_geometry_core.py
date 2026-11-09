import unittest
import sys
import types

import numpy as np

from modules import source_geometry as geom

# PLY is only needed for file preprocessing, not these pure transforms.
try:
    import plyfile  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType('plyfile')
    stub.PlyData = stub.PlyElement = object
    sys.modules['plyfile'] = stub
from modules import import_tools


class SourceGeometryCoreTests(unittest.TestCase):
    def test_unprojection_and_camera_to_world_for_both_pixel_origins(self):
        depth = [[2, 0], [4, 2]]
        pose = [[0, -1, 0, 10], [1, 0, 0, 20], [0, 0, 1, 30]]
        for origin in (0, 1):
            # Principal point follows the source origin, giving the same rays.
            K = [[2, 0, origin], [0, 2, origin], [0, 0, 1]]
            camera = import_tools.depth2XYZcamera(K, depth, pixel_origin=origin)
            np.testing.assert_allclose(camera[0], [[0, 0], [0, 1]])
            np.testing.assert_allclose(camera[1], [[0, 0], [2, 1]])
            self.assertEqual(camera[3], [[1, 0], [1, 1]])
            world, valid = import_tools.camera2XYZworld(camera, pose)
            # Point order is column-major; the zero-depth pixel is omitted.
            np.testing.assert_allclose(world, [[10, 8, 9], [20, 20, 21], [32, 34, 32]])
            self.assertEqual(valid, [[1, 0], [1, 1]])

    def test_unprojection_rejects_nonfinite_and_bad_origins(self):
        for origin in (True, -1, 2):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                import_tools.depth2XYZcamera(np.eye(3), [[1]], pixel_origin=origin)
        with self.assertRaises(ValueError):
            import_tools.depth2XYZcamera(np.eye(3), [[np.inf]], pixel_origin=0)
        with self.assertRaises(ValueError):
            import_tools.transformPointCloud([[0, np.nan, 1]], np.eye(4)[:3])

    def test_sun3d_all_uint16_values_decode_as_inverse_rotation(self):
        values = np.arange(65536, dtype=np.uint16).reshape(256, 256)
        # Encoding is a 16-bit left rotation by three bits.
        encoded = (((values.astype(np.uint32) << 3) | (values.astype(np.uint32) >> 13)) & 0xffff).astype(np.uint16)
        np.testing.assert_allclose(geom.decode_depth(encoded, geom.SUN3D_CIRCULAR_UINT16_MM), values / 1000.0)

    def test_invalid_depth_and_intrinsics(self):
        for data in ([[1.5]], [[-1]], [[65536]], [1, 2]):
            with self.subTest(data=data), self.assertRaises(geom.SourceGeometryError):
                geom.decode_depth(data, geom.PLAIN_UINT16_MM)
        for matrix in (np.diag([0, 1, 1]), [[1, 0, 0], [0, 1, 0], [0, 1, 1]], np.full((3, 3), np.nan)):
            with self.subTest(matrix=matrix), self.assertRaises(geom.SourceGeometryError):
                geom.reshape_intrinsics(matrix)

    def test_pose_basis_validation_and_world_origin(self):
        pose = np.array([[0, -1, 0, 2], [1, 0, 0, -3], [0, 0, 1, 4]], dtype=float)
        origin = geom.camera_origin_world(pose)
        np.testing.assert_array_equal(origin, [2, -3, 4])
        origin[0] = 100
        self.assertEqual(pose[0, 3], 2)
        for rotation in (np.diag([-1, 1, 1]), np.diag([2, 1, 1]), np.full((3, 3), np.inf)):
            bad = np.column_stack((rotation, [0, 0, 0]))
            with self.subTest(rotation=rotation), self.assertRaises(geom.SourceGeometryError):
                geom.validate_c2w(bad)

    def test_timestamp_tie_is_earlier_and_input_order_is_preserved(self):
        pairs = geom.pair_rgb_depth_files(['2-20.jpg', '1-10.jpg'], ['3-22.png', '4-18.png', '5-10.png'], geom.SUN3D_NEAREST_TIMESTAMP)
        self.assertEqual([(p.image_frame_id, p.depth_frame_id, p.timestamp_delta) for p in pairs], [(2, 4, 2), (1, 5, 0)])
        with self.assertRaises(geom.SourceGeometryError):
            geom.pair_rgb_depth_files(['1-color.png'], ['2-depth.png'], geom.EXACT_FRAME_ID)
