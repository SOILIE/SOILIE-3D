from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# import_tools imports this optional dependency before the focused tests replace
# the PLY reader with a deterministic fixture.
try:
    import plyfile  # noqa: F401
except ModuleNotFoundError:
    plyfile_stub = types.ModuleType('plyfile')
    plyfile_stub.PlyData = object
    plyfile_stub.PlyElement = object
    sys.modules['plyfile'] = plyfile_stub

from modules import import_tools as imp
from modules.source_geometry import (
    WASHINGTON_RGBD_SCENES_V2_NATIVE_UINT16_TENTH_MM,
)


TEST_WORK_ROOT = ROOT / '.codex' / 'tests'
REVIEWED_INTRINSICS = np.array([
    [600.0, 0.0, 320.0],
    [0.0, 601.0, 240.0],
    [0.0, 0.0, 1.0],
])
WASHINGTON_SOURCE_GEOMETRY = {
    'depthEncoding': WASHINGTON_RGBD_SCENES_V2_NATIVE_UINT16_TENTH_MM,
    'pairingStrategy': 'exact_frame_id',
    'poseIndexStrategy': 'json_position',
    'pixelOrigin': 0,
}


class WashingtonPreprocessTests(unittest.TestCase):
    def setUp(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT)
        self.root = Path(self.temporary.name)
        self.data_dir = self.root / 'data' / 'washington'
        self.scene_dir = self.data_dir / 'scene_01'
        self.pc_dir = self.data_dir / 'pc'
        self.json_dir = self.root / 'json' / 'washington'
        (self.scene_dir / 'image').mkdir(parents=True)
        (self.scene_dir / 'depth').mkdir()
        self.pc_dir.mkdir()
        self.json_dir.mkdir(parents=True)

        for frame_id in (1, 2):
            image_name = f'{frame_id:05d}-color.png'
            depth_name = f'{frame_id:05d}-depth.png'
            Image.new('RGB', (640, 480), (frame_id, 2, 3)).save(
                self.scene_dir / 'image' / image_name,
            )
            Image.new('I;16', (640, 480), frame_id).save(
                self.scene_dir / 'depth' / depth_name,
            )

        self.labels = self.pc_dir / 'scene_01_labels.txt'
        self.poses = self.pc_dir / 'scene_01_poses.txt'
        self.point_cloud = self.pc_dir / 'scene_01.ply'
        self.labels.write_text('0\n1\n1\n1\n1\n', encoding='utf-8')
        self.poses.write_text(
            '1 0 0 0 0 0 0\n'
            '1 0 0 0 1 0 0\n',
            encoding='utf-8',
        )
        self.point_cloud.write_bytes(b'fixture ply bytes\n')

    def tearDown(self):
        self.temporary.cleanup()
        try:
            TEST_WORK_ROOT.rmdir()
            TEST_WORK_ROOT.parent.rmdir()
        except OSError:
            # Other tests or agents may own files in the shared .codex tree.
            pass

    @staticmethod
    def _fake_ply():
        data = {
            'x': np.array([1.0, 2.0, 2.0, 1.0]),
            'y': np.array([1.0, 1.0, 2.0, 2.0]),
            'z': np.array([1.0, 1.0, 1.0, 1.0]),
        }
        return types.SimpleNamespace(elements=[types.SimpleNamespace(data=data)])

    @staticmethod
    def _project(points, frame_pos, _origin, _intrinsics):
        pose = imp.get_extrinsic_matrix(frame_pos)
        return np.ones((len(points), 2)), np.ones((len(points), 3)), pose

    @staticmethod
    def _frames(_uv, _points, _labels, _image):
        return (
            np.ones((2, 2), dtype=np.uint8),
            np.ones((2, 2, 3), dtype=np.float64),
            np.ones((2, 2, 2), dtype=np.float64),
            np.ones((2, 2, 2), dtype=np.float64),
        )

    @staticmethod
    def _clean(_frame, _image, _previous_objects, _previous_labels):
        return [np.ones((2, 2), dtype=np.uint8)], ['1_1']

    def _rebuild(self, frame_samples=2):
        with (
            patch.object(
                imp,
                'PlyData',
                types.SimpleNamespace(read=lambda _path: self._fake_ply()),
            ),
            patch.object(imp, 'project_3d_2d', side_effect=self._project),
            patch.object(imp, 'construct_frames', side_effect=self._frames),
            patch.object(imp, 'clean_label_mask', side_effect=self._clean),
        ):
            return imp.pre_process_input_files(
                dataDir=str(self.data_dir),
                pcDir=str(self.pc_dir),
                jsonDir=str(self.json_dir),
                nFrame_samples=frame_samples,
                rebuild=True,
                reviewed_intrinsics=REVIEWED_INTRINSICS,
                calibration_provenance='Reviewed calibration fixture, revision 7',
            )

    def test_projection_requires_reviewed_intrinsics_and_provenance(self):
        annotations = [str(self.labels), str(self.poses), str(self.point_cloud)]
        with self.assertRaisesRegex(imp.WashingtonPreprocessError, 'must not guess'):
            imp.project_annotations_3D_to_2D(
                str(self.scene_dir), 'scene_01', str(self.json_dir), annotations, 2,
                calibration_provenance='reviewed source',
            )
        with self.assertRaisesRegex(imp.WashingtonPreprocessError, 'non-empty'):
            imp.project_annotations_3D_to_2D(
                str(self.scene_dir), 'scene_01', str(self.json_dir), annotations, 2,
                reviewed_intrinsics=REVIEWED_INTRINSICS,
                calibration_provenance='   ',
            )
        invalid = REVIEWED_INTRINSICS.copy()
        invalid[0, 0] = np.nan
        with self.assertRaisesRegex(imp.WashingtonPreprocessError, 'finite 3x3'):
            imp.project_annotations_3D_to_2D(
                str(self.scene_dir), 'scene_01', str(self.json_dir), annotations, 2,
                reviewed_intrinsics=invalid,
                calibration_provenance='reviewed source',
            )

    def test_json_alone_never_skips_preprocessing(self):
        (self.json_dir / 'scene_01.json').write_text(
            json.dumps({'name': 'scene_01'}),
            encoding='utf-8',
        )
        with self.assertRaisesRegex(
            imp.WashingtonPreprocessError,
            'incomplete preprocessing artifacts; explicit rebuild required',
        ):
            imp.pre_process_input_files(
                dataDir=str(self.data_dir),
                pcDir=str(self.pc_dir),
                jsonDir=str(self.json_dir),
                nFrame_samples=2,
            )

    def test_json_without_a_raw_scene_directory_is_incomplete(self):
        (self.json_dir / 'scene_99.json').write_text(
            json.dumps({'name': 'scene_99'}),
            encoding='utf-8',
        )
        with self.assertRaisesRegex(
            imp.WashingtonPreprocessError,
            'incomplete preprocessing artifacts; explicit rebuild required',
        ):
            imp.pre_process_input_files(
                dataDir=str(self.root / 'missing-washington-data'),
                pcDir=str(self.pc_dir),
                jsonDir=str(self.json_dir),
                nFrame_samples=2,
            )

    def test_duplicate_json_scene_names_are_rejected(self):
        payload = json.dumps({'name': 'scene_01'})
        (self.json_dir / 'one.json').write_text(payload, encoding='utf-8')
        (self.json_dir / 'two.json').write_text(payload, encoding='utf-8')
        with self.assertRaisesRegex(
            imp.WashingtonPreprocessError,
            'Duplicate Washington JSON scene name',
        ):
            imp.pre_process_input_files(
                dataDir=str(self.data_dir),
                pcDir=str(self.pc_dir),
                jsonDir=str(self.json_dir),
                nFrame_samples=2,
            )

    def test_explicit_rebuild_writes_a_strict_receipt_last_and_can_be_reused(self):
        receipts = self._rebuild()
        self.assertEqual(1, len(receipts))
        receipt = receipts[0]
        self.assertEqual(imp.WASHINGTON_PREPROCESS_RECEIPT_KIND, receipt['kind'])
        self.assertEqual(2, receipt['schemaVersion'])
        self.assertEqual(WASHINGTON_SOURCE_GEOMETRY, receipt['sourceGeometry'])
        self.assertEqual(
            'Reviewed calibration fixture, revision 7',
            receipt['calibrationProvenance'],
        )
        self.assertEqual(2, receipt['sourceFrameCount'])
        self.assertEqual(2, receipt['sourcePoseCount'])
        self.assertEqual([0, 1], [sample['poseIndex'] for sample in receipt['samples']])
        self.assertTrue((self.scene_dir / 'intrinsics.txt').is_file())
        self.assertTrue((self.scene_dir / 'extrinsics' / 'extrinsics.txt').is_file())
        self.assertTrue((self.json_dir / 'scene_01.json').is_file())
        receipt_path = self.scene_dir / imp.WASHINGTON_PREPROCESS_RECEIPT
        self.assertTrue(receipt_path.is_file())
        prepared_annotation = json.loads(
            (self.json_dir / 'scene_01.json').read_text(encoding='utf-8'),
        )
        self.assertEqual(
            WASHINGTON_SOURCE_GEOMETRY,
            prepared_annotation['sourceGeometry'],
        )
        np.testing.assert_array_equal(
            REVIEWED_INTRINSICS,
            np.loadtxt(self.scene_dir / 'intrinsics.txt'),
        )

        reused = imp.pre_process_input_files(
            dataDir=str(self.data_dir),
            pcDir=str(self.pc_dir),
            jsonDir=str(self.json_dir),
            nFrame_samples=2,
        )
        self.assertEqual(receipts, reused)

        # The normal collector menu leaves the sample count unset after all
        # scenes have prepared JSON.  In that reuse-only mode each receipt is
        # authoritative for its own reviewed sample count.
        reused_from_receipts = imp.pre_process_input_files(
            dataDir=str(self.data_dir),
            pcDir=str(self.pc_dir),
            jsonDir=str(self.json_dir),
            nFrame_samples=None,
        )
        self.assertEqual(receipts, reused_from_receipts)

    def test_v1_or_ambiguous_receipt_requires_an_explicit_rebuild(self):
        self._rebuild()
        receipt_path = self.scene_dir / imp.WASHINGTON_PREPROCESS_RECEIPT
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        receipt['schemaVersion'] = 1
        del receipt['sourceGeometry']
        receipt_path.write_text(
            json.dumps(receipt, indent=2) + '\n',
            encoding='utf-8',
        )

        with self.assertRaisesRegex(
            imp.WashingtonPreprocessError,
            'explicit rebuild required.*schemaVersion 1',
        ):
            imp.pre_process_input_files(
                dataDir=str(self.data_dir),
                pcDir=str(self.pc_dir),
                jsonDir=str(self.json_dir),
                nFrame_samples=2,
            )

    def test_receipt_and_annotation_reject_plain_or_missing_source_geometry(self):
        receipt_path = self.scene_dir / imp.WASHINGTON_PREPROCESS_RECEIPT
        annotation_path = self.json_dir / 'scene_01.json'

        for mutation, message in (
            ('missing-receipt', r"missing \['sourceGeometry'\]"),
            ('plain-receipt', "conflicts with the 'washington' profile"),
            ('missing-annotation', 'JSON sourceGeometry does not match'),
            ('plain-annotation', 'JSON sourceGeometry does not match'),
        ):
            with self.subTest(mutation=mutation):
                self._rebuild()
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
                if mutation == 'missing-receipt':
                    del receipt['sourceGeometry']
                elif mutation == 'plain-receipt':
                    receipt['sourceGeometry']['depthEncoding'] = 'plain_uint16_mm'
                else:
                    annotation = json.loads(annotation_path.read_text(encoding='utf-8'))
                    if mutation == 'missing-annotation':
                        del annotation['sourceGeometry']
                    else:
                        annotation['sourceGeometry']['depthEncoding'] = 'plain_uint16_mm'
                    annotation_path.write_text(
                        json.dumps(annotation, indent=4) + '\n',
                        encoding='utf-8',
                    )
                    receipt['outputs']['json']['sha256'] = hashlib.sha256(
                        annotation_path.read_bytes(),
                    ).hexdigest()
                receipt_path.write_text(
                    json.dumps(receipt, indent=2) + '\n',
                    encoding='utf-8',
                )

                with self.assertRaisesRegex(imp.WashingtonPreprocessError, message):
                    imp.pre_process_input_files(
                        dataDir=str(self.data_dir),
                        pcDir=str(self.pc_dir),
                        jsonDir=str(self.json_dir),
                        nFrame_samples=2,
                    )

    def test_one_frame_rebuild_preserves_extrinsics_row_shape(self):
        receipts = self._rebuild(frame_samples=1)
        self.assertEqual(1, receipts[0]['requestedFrameSamples'])
        extrinsics_path = self.scene_dir / 'extrinsics' / 'extrinsics.txt'
        self.assertEqual((1, 12), np.loadtxt(extrinsics_path, ndmin=2).shape)

        reused = imp.pre_process_input_files(
            dataDir=str(self.data_dir),
            pcDir=str(self.pc_dir),
            jsonDir=str(self.json_dir),
            nFrame_samples=None,
        )
        self.assertEqual(receipts, reused)

    def test_selected_empty_washington_corpus_is_not_silent_success(self):
        empty_data = self.root / 'empty' / 'data' / 'washington'
        empty_json = self.root / 'empty' / 'json' / 'washington'
        empty_pc = empty_data / 'pc'
        empty_pc.mkdir(parents=True)
        empty_json.mkdir(parents=True)

        with self.assertRaisesRegex(
            imp.WashingtonPreprocessError,
            'found no source scenes',
        ):
            imp.pre_process_input_files(
                dataDir=str(empty_data),
                pcDir=str(empty_pc),
                jsonDir=str(empty_json),
                nFrame_samples=None,
            )

    def test_stale_output_or_sample_content_requires_an_explicit_rebuild(self):
        self._rebuild()
        (self.scene_dir / 'intrinsics.txt').write_text(
            '1 0 0\n0 1 0\n0 0 1\n',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(
            imp.WashingtonPreprocessError,
            'incomplete preprocessing artifacts; explicit rebuild required',
        ):
            imp.pre_process_input_files(
                dataDir=str(self.data_dir),
                pcDir=str(self.pc_dir),
                jsonDir=str(self.json_dir),
                nFrame_samples=2,
            )

        self._rebuild()
        Image.new('RGB', (640, 480), (9, 9, 9)).save(
            self.scene_dir / 'image' / '00001-color.png',
        )
        with self.assertRaisesRegex(
            imp.WashingtonPreprocessError,
            'incomplete preprocessing artifacts; explicit rebuild required',
        ):
            imp.pre_process_input_files(
                dataDir=str(self.data_dir),
                pcDir=str(self.pc_dir),
                jsonDir=str(self.json_dir),
                nFrame_samples=2,
            )

    def test_rebuild_requires_explicit_calibration_even_with_legacy_json(self):
        (self.json_dir / 'scene_01.json').write_text(
            json.dumps({'name': 'scene_01'}),
            encoding='utf-8',
        )
        with self.assertRaisesRegex(imp.WashingtonPreprocessError, 'must not guess'):
            imp.pre_process_input_files(
                dataDir=str(self.data_dir),
                pcDir=str(self.pc_dir),
                jsonDir=str(self.json_dir),
                nFrame_samples=2,
                rebuild=True,
            )


if __name__ == '__main__':
    unittest.main()
