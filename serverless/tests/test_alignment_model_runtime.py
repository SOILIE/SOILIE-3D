"""Compatibility tests for coordinate generation with alignment diagnostics."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from modules.prepare_data import TripletGeometryError
from serverless.common.model_runtime import (
    ModelGenerationError,
    generate_model_inputs,
    generate_model_inputs_with_diagnostics,
)


class AlignmentModelRuntimeTests(unittest.TestCase):
    def invoke_with_prepare_data(self, function, calculate_coords):
        prepare_data = SimpleNamespace(
            calculateCoords=calculate_coords,
            TripletGeometryError=TripletGeometryError,
        )
        with (
            patch(
                "serverless.common.model_runtime._load_original_modules",
                return_value=(prepare_data, None, None),
            ),
            patch(
                "serverless.common.model_runtime._model_working_directory",
                return_value=nullcontext(),
            ),
            patch("serverless.common.model_runtime.seed_model"),
        ):
            result = function(Path("."), ["chair", "table", "lamp"], 73)
        return result, calculate_coords

    def test_coordinate_only_wrapper_preserves_legacy_return_shape(self):
        coordinates = {"chair": {"coords": [0.0, 0.0, 0.0]}}
        calculate_coords = Mock(return_value=(coordinates, 19))

        result, calculate_coords = self.invoke_with_prepare_data(
            generate_model_inputs,
            calculate_coords,
        )

        self.assertIs(coordinates, result)
        calculate_coords.assert_called_once_with(
            ["chair", "table", "lamp"],
            prev_text_len=0,
            progbar_prefix="SOILIE-3D coordinate pass 1",
        )

    def test_detailed_wrapper_returns_coordinates_and_diagnostics(self):
        coordinates = {"lamp": {"coords": [1.0, 2.0, 3.0]}}
        diagnostics = [{"objectName": "lamp", "rotationDegrees": -90.0}]
        calculate_coords = Mock(return_value=(coordinates, 21, diagnostics))

        result, calculate_coords = self.invoke_with_prepare_data(
            generate_model_inputs_with_diagnostics,
            calculate_coords,
        )

        self.assertEqual((coordinates, diagnostics), result)
        calculate_coords.assert_called_once_with(
            ["chair", "table", "lamp"],
            prev_text_len=0,
            progbar_prefix="SOILIE-3D coordinate pass 1",
            return_alignment_diagnostics=True,
        )

    def test_detailed_wrapper_retries_without_leaking_rejected_diagnostics(self):
        rejected_coordinates = {"lamp": {"coords": [5.0, 0.0, 0.0]}}
        accepted_coordinates = {"lamp": {"coords": [4.999, 0.0, 0.0]}}
        accepted_diagnostics = [{"objectName": "lamp", "status": "aligned"}]
        calculate_coords = Mock(side_effect=(
            (rejected_coordinates, 0, [{"objectName": "rejected"}]),
            (accepted_coordinates, 0, accepted_diagnostics),
        ))

        result, calculate_coords = self.invoke_with_prepare_data(
            generate_model_inputs_with_diagnostics,
            calculate_coords,
        )

        self.assertEqual((accepted_coordinates, accepted_diagnostics), result)
        self.assertEqual(2, calculate_coords.call_count)
        self.assertEqual(
            "SOILIE-3D coordinate pass 2",
            calculate_coords.call_args_list[1].kwargs["progbar_prefix"],
        )

    def test_invalid_triplet_geometry_is_translated_to_public_runtime_error(self):
        calculate_coords = Mock(
            side_effect=TripletGeometryError("angles cannot form real coordinates")
        )

        with self.assertRaisesRegex(
            ModelGenerationError,
            "rejected invalid triplet geometry: angles cannot form real coordinates",
        ):
            self.invoke_with_prepare_data(generate_model_inputs, calculate_coords)

        self.assertEqual(1, calculate_coords.call_count)
