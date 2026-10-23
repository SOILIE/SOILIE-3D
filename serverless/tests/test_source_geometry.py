from __future__ import annotations

import csv
import inspect
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

import numpy as np
from PIL import Image

# import_tools imports this optional dependency even though these collector
# contract tests do not exercise PLY preprocessing.
try:
    import plyfile  # noqa: F401
except ModuleNotFoundError:
    plyfile_stub = types.ModuleType('plyfile')
    plyfile_stub.PlyData = object
    plyfile_stub.PlyElement = object
    sys.modules['plyfile'] = plyfile_stub

from modules import import_tools as imp
from modules.collect_data import (
    Frame,
    calculateCentroidsForScene,
    processJSON,
)
from modules.source_geometry import (
    EXACT_FRAME_ID,
    JSON_POSITION,
    ONE_BASED_IMAGE_FRAME,
    PLAIN_UINT16_MM,
    PRINCETON_SUN3D_PROFILE,
    SUN3D_CIRCULAR_UINT16_MM,
    SUN3D_NEAREST_TIMESTAMP,
    WASHINGTON_RGBD_PROFILE,
    WORLD_POINT_CLOUD_SCHEMA_VERSION,
    FramePair,
    SourceGeometryError,
    camera_origin_world,
    decode_depth,
    pair_rgb_depth_files,
    pinned_extrinsics_filename,
    reshape_c2w_rows,
    resolve_source_profile,
    select_c2w_pose,
    reshape_intrinsics,
)


ROOT = Path(__file__).resolve().parents[2]
TEST_WORK_ROOT = ROOT / '.codex' / 'tests'


class SourceProfileTests(unittest.TestCase):
    def test_builtin_profiles_make_source_semantics_explicit(self):
        self.assertEqual(SUN3D_CIRCULAR_UINT16_MM, PRINCETON_SUN3D_PROFILE.depth_encoding)
        self.assertEqual(SUN3D_NEAREST_TIMESTAMP, PRINCETON_SUN3D_PROFILE.pairing_strategy)
        self.assertEqual(ONE_BASED_IMAGE_FRAME, PRINCETON_SUN3D_PROFILE.pose_index_strategy)
        self.assertEqual(1, PRINCETON_SUN3D_PROFILE.pixel_origin)

        self.assertEqual(PLAIN_UINT16_MM, WASHINGTON_RGBD_PROFILE.depth_encoding)
        self.assertEqual(EXACT_FRAME_ID, WASHINGTON_RGBD_PROFILE.pairing_strategy)
        self.assertEqual(JSON_POSITION, WASHINGTON_RGBD_PROFILE.pose_index_strategy)
        self.assertEqual(0, WASHINGTON_RGBD_PROFILE.pixel_origin)

    def test_known_profile_rejects_conflicting_metadata(self):
        with self.assertRaisesRegex(SourceGeometryError, 'conflicts'):
            resolve_source_profile('princeton', {
                'depthEncoding': PLAIN_UINT16_MM,
                'pairingStrategy': SUN3D_NEAREST_TIMESTAMP,
                'poseIndexStrategy': ONE_BASED_IMAGE_FRAME,
                'pixelOrigin': 1,
            })

    def test_unknown_profile_requires_complete_metadata(self):
        with self.assertRaisesRegex(SourceGeometryError, 'requires a sourceGeometry'):
            resolve_source_profile('unreviewed')

    def test_checked_in_source_json_matches_profile_and_pins_extrinsics(self):
        for dataset_name in ('princeton', 'washington'):
            profile = resolve_source_profile(dataset_name)
            for path in sorted((ROOT / 'json' / dataset_name).glob('*.json')):
                with self.subTest(path=path):
                    payload = json.loads(path.read_text(encoding='utf-8'))
                    self.assertEqual(len(payload['frames']), len(payload['fileList']))
                    pinned_extrinsics_filename(payload.get('extrinsics'))
                    if dataset_name == 'princeton':
                        depths = [
                            image.rsplit('.', 1)[0] + '.png'
                            for image in payload['fileList']
                        ]
                    else:
                        depths = [
                            image.removesuffix('-color.png') + '-depth.png'
                            for image in payload['fileList']
                        ]
                    pairs = pair_rgb_depth_files(
                        payload['fileList'],
                        depths,
                        profile.pairing_strategy,
                    )
                    self.assertEqual(len(payload['frames']), len(pairs))


class DepthContractTests(unittest.TestCase):
    @staticmethod
    def _sun3d_encode(millimetres):
        return ((millimetres << 3) & 0xFFFF) | (millimetres >> 13)

    def test_circular_and_plain_uint16_depth_are_not_interchangeable(self):
        boundary_values = [0, 1, 5_000, 8_191, 8_192, 9_000, 65_535]
        encoded_values = [self._sun3d_encode(value) for value in boundary_values]
        raw = np.array([encoded_values], dtype=np.uint16)
        decoded_millimetres = decode_depth(
            raw,
            SUN3D_CIRCULAR_UINT16_MM,
        ) * 1000.0
        np.testing.assert_allclose(decoded_millimetres[0], boundary_values, atol=1e-9)

        encoded = self._sun3d_encode(10_000)
        encoded_raw = np.array([[encoded]], dtype=np.uint16)
        self.assertEqual(
            10.0,
            decode_depth(encoded_raw, SUN3D_CIRCULAR_UINT16_MM)[0, 0],
        )
        self.assertNotEqual(10.0, decode_depth(encoded_raw, PLAIN_UINT16_MM)[0, 0])
        self.assertEqual(
            10.0,
            decode_depth(np.array([[10_000]], dtype=np.uint16), PLAIN_UINT16_MM)[0, 0],
        )

    def test_depth_reader_applies_selected_encoding(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            path = Path(temporary) / 'depth.png'
            encoded = self._sun3d_encode(10_000)
            Image.fromarray(np.array([[encoded]], dtype=np.uint16)).save(path)
            self.assertEqual(
                10.0,
                imp.depthRead(path, True, SUN3D_CIRCULAR_UINT16_MM)[0, 0],
            )

    def test_projection_uses_profile_pixel_origin(self):
        intrinsics = np.array([
            [1.0, 0.0, 1.0],
            [0.0, 1.0, 1.0],
            [0.0, 0.0, 1.0],
        ])
        depth = np.ones((1, 1), dtype=np.float64)
        princeton = imp.depth2XYZcamera(intrinsics, depth, pixel_origin=1)
        washington = imp.depth2XYZcamera(intrinsics, depth, pixel_origin=0)
        self.assertEqual(0.0, princeton[0][0, 0])
        self.assertEqual(0.0, princeton[1][0, 0])
        self.assertEqual(-1.0, washington[0][0, 0])
        self.assertEqual(-1.0, washington[1][0, 0])


class FrameBindingTests(unittest.TestCase):
    def test_sun3d_pairs_nearest_timestamp_in_annotation_order(self):
        pairs = pair_rgb_depth_files(
            ['0000002-000000000200.jpg', '0000001-000000000100.jpg'],
            [
                '0000011-000000000250.png',
                '0000010-000000000150.png',
                '0000009-000000000050.png',
            ],
            SUN3D_NEAREST_TIMESTAMP,
        )
        self.assertEqual(
            ('0000010-000000000150.png', '0000009-000000000050.png'),
            tuple(pair.depth_name for pair in pairs),
        )
        self.assertEqual((50, 50), tuple(pair.timestamp_delta for pair in pairs))

    def test_washington_requires_identical_one_to_one_frame_sets(self):
        pairs = pair_rgb_depth_files(
            ['00029-color.png', '00000-color.png'],
            ['00000-depth.png', '00029-depth.png'],
            EXACT_FRAME_ID,
        )
        self.assertEqual((29, 0), tuple(pair.depth_frame_id for pair in pairs))

        with self.assertRaisesRegex(SourceGeometryError, 'unmatched depth frame IDs'):
            pair_rgb_depth_files(
                ['00000-color.png'],
                ['00000-depth.png', '00001-depth.png'],
                EXACT_FRAME_ID,
            )
        with self.assertRaisesRegex(SourceGeometryError, 'missing depth frame IDs'):
            pair_rgb_depth_files(
                ['00000-color.png', '00001-color.png'],
                ['00000-depth.png'],
                EXACT_FRAME_ID,
            )

    def test_extrinsics_are_json_pinned_reshaped_and_pose_mapped(self):
        self.assertEqual('reviewed.txt', pinned_extrinsics_filename('reviewed.txt'))
        with self.assertRaisesRegex(SourceGeometryError, 'must declare'):
            pinned_extrinsics_filename(None)
        with self.assertRaisesRegex(SourceGeometryError, 'single filename'):
            pinned_extrinsics_filename('../latest.txt')

        expected_stack = np.array([
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
            ],
            [
                [0.0, -1.0, 0.0, 4.0],
                [1.0, 0.0, 0.0, 5.0],
                [0.0, 0.0, 1.0, 6.0],
            ],
        ])
        flattened = expected_stack.reshape(2, 12)
        stacked = reshape_c2w_rows(flattened)
        np.testing.assert_array_equal(stacked, expected_stack)
        np.testing.assert_array_equal(
            reshape_c2w_rows(flattened.reshape(6, 4)),
            stacked,
        )

        pair = FramePair('0000002-20.jpg', '0000002-21.png', 2, 2, 20, 21, 1)
        np.testing.assert_array_equal(
            select_c2w_pose(stacked, pair, 0, ONE_BASED_IMAGE_FRAME),
            stacked[1],
        )
        np.testing.assert_array_equal(
            select_c2w_pose(stacked, pair, 0, JSON_POSITION),
            stacked[0],
        )
        np.testing.assert_array_equal(camera_origin_world(stacked[1]), stacked[1, :, 3])
        with self.assertRaisesRegex(SourceGeometryError, 'right-handed orthonormal'):
            camera_origin_world(np.zeros((3, 4)))

    def test_intrinsics_remain_in_source_row_major_order(self):
        source = [
            [570.0, 0.0, 320.0],
            [0.0, 571.0, 240.0],
            [0.0, 0.0, 1.0],
        ]
        intrinsics = reshape_intrinsics(source)
        np.testing.assert_array_equal(intrinsics, source)
        self.assertEqual(320.0, intrinsics[0, 2])
        self.assertEqual(240.0, intrinsics[1, 2])


class WorldCoordinateCollectionTests(unittest.TestCase):
    def test_frame_centroid_uses_world_points_not_camera_points(self):
        frame = Frame('1', 5, 5)
        frame.valid = np.ones((5, 5), dtype=np.uint8)
        frame.xyz = np.tile([1.0, 2.0, 3.0], (25, 1))
        frame.calculateCentroids({"['chair', '1']": [
            (0.0, 0.0),
            (4.0, 0.0),
            (4.0, 4.0),
            (0.0, 4.0),
            (0.0, 0.0),
        ]})
        np.testing.assert_allclose(frame.centroids["['chair', '1']"], [1.0, 2.0, 3.0])
        points = np.asarray(frame.objects3d["['chair', '1']"])
        np.testing.assert_allclose(points, np.tile([1.0, 2.0, 3.0], (len(points), 1)))

    def test_two_moving_cameras_aggregate_the_same_world_geometry(self):
        world_points = np.array([
            [2.0, 3.0, 4.0],
            [4.0, 5.0, 6.0],
        ])
        c2w_poses = (
            np.array([
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
            ]),
            np.array([
                [0.0, -1.0, 0.0, 1.0],
                [1.0, 0.0, 0.0, 1.0],
                [0.0, 0.0, 1.0, 1.0],
            ]),
        )

        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            folder = Path(temporary)
            for frame_id, c2w in enumerate(c2w_poses, start=1):
                rotation = c2w[:, :3]
                translation = c2w[:, 3]
                camera_points = (rotation.T @ (world_points - translation).T).T
                camera_grid = [
                    camera_points[:, axis].reshape((1, -1))
                    for axis in range(3)
                ]
                camera_grid.append(np.ones((1, len(camera_points)), dtype=np.uint8))
                reconstructed, _valid = imp.camera2XYZworld(camera_grid, c2w)
                reconstructed = reconstructed.T
                np.testing.assert_allclose(reconstructed, world_points, atol=1e-12)

                frame = Frame(str(frame_id), 1, 1)
                frame.extrinsics = c2w
                frame.objects3d = {"['chair', '1']": reconstructed}
                frame.process3dPoints(folder, plot=0)

            centroids = calculateCentroidsForScene(folder, plot=0)
            np.testing.assert_allclose(
                centroids["['chair', '1']"],
                [3.0, 4.0, 5.0],
                atol=1e-12,
            )

    def test_relations_use_per_frame_c2w_camera_translation(self):
        frame = Frame('1', 1, 1)
        frame.extrinsics = np.array([
            [1.0, 0.0, 0.0, 1.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ])
        frame.centroids = {
            "['a', '1']": np.array([2.0, 0.0, 0.0]),
            "['b', '1']": np.array([3.0, 0.0, 0.0]),
            "['c', '1']": np.array([2.0, 1.0, 0.0]),
        }

        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            centroids = Path(temporary) / 'centroids.csv'
            centroids.write_text(
                "object,X,Y,Z\n"
                "\"['a', '1']\",2,0,0\n"
                "\"['b', '1']\",3,0,0\n"
                "\"['c', '1']\",2,1,0\n",
                encoding='utf-8',
            )
            frame.getAngleDistCombos(centroids)

        row = next(
            item for item in frame.combos
            if item['objectA'] == 'a_1'
            and item['objectB'] == 'b_1'
            and item['objectC'] == 'c_1'
        )
        self.assertEqual(1.0, float(row['distanceAO']))
        self.assertEqual(90.0, float(row['angleBAC']))
        self.assertEqual(180.0, float(row['angleOAB']))
        self.assertEqual(90.0, float(row['angleOAC']))

    def test_object_geometry_is_camera_invariant_while_observer_relations_change(self):
        scene_centroids = {
            "['a', '1']": np.array([2.0, 0.0, 0.0]),
            "['b', '1']": np.array([2.0, 2.0, 0.0]),
            "['c', '1']": np.array([2.0, 0.0, 2.0]),
        }
        camera_translations = (
            np.array([0.0, 0.0, 0.0]),
            np.array([2.0, -2.0, -2.0]),
        )

        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            centroid_file = Path(temporary) / 'centroids.csv'
            centroid_file.write_text(
                "object,X,Y,Z\n"
                "\"['a', '1']\",2,0,0\n"
                "\"['b', '1']\",2,2,0\n"
                "\"['c', '1']\",2,0,2\n",
                encoding='utf-8',
            )
            rows = []
            for frame_id, translation in enumerate(camera_translations, start=1):
                frame = Frame(str(frame_id), 1, 1)
                frame.centroids = {
                    name: coordinate.copy()
                    for name, coordinate in scene_centroids.items()
                }
                frame.extrinsics = np.hstack((np.eye(3), translation.reshape((3, 1))))
                frame.getAngleDistCombos(centroid_file)
                rows.append(next(
                    item for item in frame.combos
                    if item['objectA'] == 'a_1'
                    and item['objectB'] == 'b_1'
                    and item['objectC'] == 'c_1'
                ))

        for field, expected in (
            ('distanceAB', 2.0),
            ('distanceAC', 2.0),
            ('angleBAC', 90.0),
        ):
            self.assertEqual(expected, float(rows[0][field]))
            self.assertEqual(expected, float(rows[1][field]))

        self.assertEqual(2.0, float(rows[0]['distanceAO']))
        self.assertEqual(90.0, float(rows[0]['angleOAB']))
        self.assertEqual(90.0, float(rows[0]['angleOAC']))
        self.assertAlmostEqual(8.0 ** 0.5, float(rows[1]['distanceAO']))
        self.assertAlmostEqual(135.0, float(rows[1]['angleOAB']))
        self.assertAlmostEqual(135.0, float(rows[1]['angleOAC']))

    def test_world_jsonl_aggregation_rejects_legacy_and_respects_zero_plots(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        marker = {
            'schemaVersion': WORLD_POINT_CLOUD_SCHEMA_VERSION,
            'coordinateFrame': 'world',
        }
        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            folder = Path(temporary)
            records = (
                (folder / '1.3d', [[1.0, 2.0, 3.0]]),
                (folder / '2.3d', [[3.0, 4.0, 5.0]]),
            )
            for path, points in records:
                path.write_text(
                    json.dumps(marker, separators=(',', ':')) + '\n'
                    + json.dumps({
                        'object': "['chair', '1']",
                        'points': points,
                    }, separators=(',', ':')) + '\n',
                    encoding='utf-8',
                )

            result = calculateCentroidsForScene(folder, plot=0)
            np.testing.assert_allclose(result["['chair', '1']"], [2.0, 3.0, 4.0])
            self.assertFalse((folder / 'objects').exists())
            with (folder / 'centroids.csv').open(newline='', encoding='utf-8') as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual("['chair', '1']", row['object'])

            (folder / '3.3d').write_text(
                "['chair', '1']\n(9.0, 9.0, 9.0)\n",
                encoding='utf-8',
            )
            with self.assertRaisesRegex(ValueError, 'legacy or unmarked'):
                calculateCentroidsForScene(folder, plot=0)

            (folder / '3.3d').write_text(
                json.dumps({
                    'schemaVersion': WORLD_POINT_CLOUD_SCHEMA_VERSION,
                    'coordinateFrame': 'camera',
                }) + '\n'
                + json.dumps({
                    'object': "['chair', '1']",
                    'points': [[9.0, 9.0, 9.0]],
                }) + '\n',
                encoding='utf-8',
            )
            with self.assertRaisesRegex(ValueError, 'not schema-v1 world-coordinate'):
                calculateCentroidsForScene(folder, plot=0)

    def test_current_run_manifest_excludes_stale_schema_v1_point_clouds(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        marker = {
            'schemaVersion': WORLD_POINT_CLOUD_SCHEMA_VERSION,
            'coordinateFrame': 'world',
        }

        def write_cloud(path, point):
            path.write_text(
                json.dumps(marker, separators=(',', ':')) + '\n'
                + json.dumps({
                    'object': "['chair', '1']",
                    'points': [point],
                }, separators=(',', ':')) + '\n',
                encoding='utf-8',
            )

        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            folder = Path(temporary)
            current = folder / '1.3d'
            stale = folder / '2.3d'
            write_cloud(current, [1.0, 2.0, 3.0])
            write_cloud(stale, [3.0, 4.0, 5.0])

            initial = calculateCentroidsForScene(
                folder,
                plot=0,
                point_cloud_files=('1.3d', '2.3d'),
            )
            np.testing.assert_allclose(initial["['chair', '1']"], [2.0, 3.0, 4.0])

            # A later run rewrites frame 1 but no longer contains frame 2.
            # The still-valid schema-v1 file from the first run must not be
            # discovered and folded into the replacement centroid.
            write_cloud(current, [9.0, 10.0, 11.0])
            rerun = calculateCentroidsForScene(
                folder,
                plot=0,
                point_cloud_files=('1.3d',),
            )
            np.testing.assert_allclose(rerun["['chair', '1']"], [9.0, 10.0, 11.0])

            with self.assertRaisesRegex(ValueError, 'direct children'):
                calculateCentroidsForScene(
                    folder,
                    plot=0,
                    point_cloud_files=('../outside.3d',),
                )

    def test_scene_relation_export_is_named_and_enabled_by_default(self):
        parameter = inspect.signature(processJSON).parameters['export_scene_relations']
        self.assertIs(True, parameter.default)

        with self.assertRaisesRegex(TypeError, 'must be a boolean'):
            processJSON(
                {}, [], '.', 'princeton', True, 0,
                export_scene_relations='yes',
            )


if __name__ == '__main__':
    unittest.main()
