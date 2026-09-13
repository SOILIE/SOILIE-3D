from __future__ import annotations

import json
import os
import unittest


os.environ.setdefault("AWS_DEFAULT_REGION", "ca-central-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("JOB_TABLE", "test-jobs")

from serverless.renderer.handler import _parse_v4_result, _validate_room_boundary_result  # noqa: E402


class V4EntrypointTests(unittest.TestCase):
    def test_result_parser_ignores_blender_shutdown_output(self):
        result = {"status": "complete", "filename": "bed_lamp", "path": "/tmp/output", "data": []}
        stdout = f"Blender 3.6\n{json.dumps(result, separators=(',', ':'))}\nBlender quit\n"
        self.assertEqual(result, _parse_v4_result(stdout))

    def test_result_parser_rejects_unrelated_json(self):
        with self.assertRaisesRegex(RuntimeError, "did not return"):
            _parse_v4_result('{"event":"render"}\nBlender quit\n')

    def test_boundary_validator_requires_visible_dimensions_to_match_manifest(self):
        result = {
            "room": {"actual": {"widthM": 4.2, "depthM": 3.1, "heightM": 2.4}},
            "data": [
                {"obj_name": "FLOOR", "dim_x": 4.2, "dim_y": 3.1, "dim_z": 0},
                {"obj_name": "LEFT_WALL", "dim_x": 4.2, "dim_y": 0.2, "dim_z": 2.4},
                {"obj_name": "RIGHT_WALL", "dim_x": 4.2, "dim_y": 0.2, "dim_z": 2.4},
                {"obj_name": "FRONT_WALL", "dim_x": 0.2, "dim_y": 3.1, "dim_z": 2.4},
                {"obj_name": "BACK_WALL", "dim_x": 0.2, "dim_y": 3.1, "dim_z": 2.4},
            ],
        }
        _validate_room_boundary_result(result)
        result["data"][0]["dim_x"] = 7.0
        with self.assertRaisesRegex(RuntimeError, "disagree"):
            _validate_room_boundary_result(result)


if __name__ == "__main__":
    unittest.main()
