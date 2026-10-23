from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


os.environ.setdefault("AWS_DEFAULT_REGION", "ca-central-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("JOB_TABLE", "test-jobs")

from serverless.renderer.handler import _parse_model_result, _validate_room_boundary_result  # noqa: E402
from serverless.common.model_runtime import ModelGenerationError, select_model_objects  # noqa: E402
from modules.working_combo_schema import WorkingCombinationError  # noqa: E402


class ModelEntrypointTests(unittest.TestCase):
    def test_duplicate_disabled_selection_returns_the_exact_requested_count(self):
        combinations = SimpleNamespace(
            select_exact=lambda *_args, **_kwargs: ["chair", "blinds", "table", "lamp"],
            WorkingCombinationError=WorkingCombinationError,
        )
        request = {
            "mode": "room_type", "roomType": "bedroom", "objectCount": 4,
            "seed": 17, "allowDuplicates": False, "sameObjectsAcrossScenes": True,
        }
        with patch("serverless.common.model_runtime._load_original_modules", return_value=(None, combinations, None)), \
             patch("serverless.common.model_runtime._model_working_directory", return_value=nullcontext()), \
             patch("serverless.common.model_runtime.seed_model"):
            self.assertEqual(
                ["chair", "blinds", "table", "lamp"],
                select_model_objects(Path("."), request, 0),
            )

    def test_catalog_selection_failure_does_not_loop_or_shrink_the_request(self):
        def reject(*_args, **_kwargs):
            raise WorkingCombinationError("no exact row")

        combinations = SimpleNamespace(
            select_exact=reject,
            WorkingCombinationError=WorkingCombinationError,
        )
        request = {
            "mode": "room_type", "roomType": "bedroom", "objectCount": 8,
            "seed": 17, "allowDuplicates": False, "sameObjectsAcrossScenes": True,
        }
        with patch("serverless.common.model_runtime._load_original_modules", return_value=(None, combinations, None)), \
             patch("serverless.common.model_runtime._model_working_directory", return_value=nullcontext()), \
             patch("serverless.common.model_runtime.seed_model"):
            with self.assertRaisesRegex(ModelGenerationError, "exact requested object count"):
                select_model_objects(Path("."), request, 0)

    def test_result_parser_ignores_blender_shutdown_output(self):
        result = {"status": "complete", "filename": "bed_lamp", "path": "/tmp/output", "data": []}
        stdout = f"Blender 3.6\n{json.dumps(result, separators=(',', ':'))}\nBlender quit\n"
        self.assertEqual(result, _parse_model_result(stdout))

    def test_result_parser_rejects_unrelated_json(self):
        with self.assertRaisesRegex(RuntimeError, "did not return"):
            _parse_model_result('{"event":"render"}\nBlender quit\n')

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
