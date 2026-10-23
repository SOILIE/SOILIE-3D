"""Contract tests for rigid triplet alignment about the shared A-B axis."""

from __future__ import annotations

import json
import math
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from modules.prepare_data import align_triplet_about_axis, calculateCoords


class CoordinateTransformTests(unittest.TestCase):
    BASE_A = np.array([0.0, 0.0, 0.0], dtype=np.float64)
    BASE_B = np.array([5.0, 0.0, 0.0], dtype=np.float64)
    BASE_CAMERA = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    NAMES = ("chair", "table")

    def align(self, source_camera, source_c, **overrides):
        arguments = {
            "base_a": self.BASE_A.copy(),
            "base_b": self.BASE_B.copy(),
            "base_camera": self.BASE_CAMERA.copy(),
            "source_a": self.BASE_A.copy(),
            "source_b": self.BASE_B.copy(),
            "source_camera": np.asarray(source_camera, dtype=np.float64),
            "source_c": np.asarray(source_c, dtype=np.float64),
            "object_name": "lamp",
            "base_object_name": "bed",
            "anchor_names": self.NAMES,
        }
        arguments.update(overrides)
        return align_triplet_about_axis(**arguments)

    def assertPointAlmostEqual(self, expected, observed):
        np.testing.assert_allclose(
            np.asarray(observed, dtype=np.float64),
            np.asarray(expected, dtype=np.float64),
            rtol=0.0,
            atol=1e-12,
        )

    def test_signed_quarter_turns_align_camera_direction(self):
        cases = (
            ([0.0, 0.0, 1.0], [0.0, 0.0, 2.0], -90.0),
            ([0.0, 0.0, -1.0], [0.0, 0.0, -2.0], 90.0),
        )

        for source_camera, source_c, expected_degrees in cases:
            with self.subTest(expected_degrees=expected_degrees):
                aligned_c, rotated_camera, diagnostic = self.align(source_camera, source_c)

                self.assertPointAlmostEqual([0.0, 2.0, 0.0], aligned_c)
                self.assertPointAlmostEqual(self.BASE_CAMERA, rotated_camera)
                self.assertAlmostEqual(expected_degrees, diagnostic["rotationDegrees"], places=12)
                self.assertEqual("aligned", diagnostic["status"])

    def test_antiparallel_camera_direction_uses_positive_half_turn(self):
        aligned_c, rotated_camera, diagnostic = self.align(
            source_camera=[0.0, -1.0, 0.0],
            source_c=[0.0, -2.0, 0.0],
        )

        self.assertPointAlmostEqual([0.0, 2.0, 0.0], aligned_c)
        self.assertPointAlmostEqual(self.BASE_CAMERA, rotated_camera)
        self.assertAlmostEqual(180.0, diagnostic["rotationDegrees"], places=12)

    def test_rigid_rotation_preserves_pairwise_distances_and_reports_mismatch(self):
        base_camera = np.array([1.0, 3.0, 0.0], dtype=np.float64)
        source_camera = np.array([2.0, 0.0, 4.0], dtype=np.float64)
        source_c = np.array([0.0, 0.0, 2.0], dtype=np.float64)
        aligned_c, rotated_camera, diagnostic = self.align(
            source_camera,
            source_c,
            base_camera=base_camera,
        )

        self.assertPointAlmostEqual([0.0, 2.0, 0.0], aligned_c)
        self.assertPointAlmostEqual([2.0, 4.0, 0.0], rotated_camera)

        for anchor in (self.BASE_A, self.BASE_B):
            self.assertAlmostEqual(
                np.linalg.norm(source_c - anchor),
                np.linalg.norm(np.asarray(aligned_c) - anchor),
                places=12,
            )
            self.assertAlmostEqual(
                np.linalg.norm(source_camera - anchor),
                np.linalg.norm(np.asarray(rotated_camera) - anchor),
                places=12,
            )
        self.assertAlmostEqual(
            np.linalg.norm(source_c - source_camera),
            np.linalg.norm(np.asarray(aligned_c) - np.asarray(rotated_camera)),
            places=12,
        )

        expected_direction_residual = math.degrees(
            math.acos(14.0 / math.sqrt(200.0))
        )
        required = {
            "objectName": "lamp",
            "baseObjectName": "bed",
            "anchorNames": list(self.NAMES),
            "status": "aligned",
            "rotationDegrees": -90.0,
            "sourceCameraRange": math.sqrt(20.0),
            "baseCameraRange": math.sqrt(10.0),
            "cameraRangeDelta": math.sqrt(20.0) - math.sqrt(10.0),
            "cameraAxialDelta": 1.0,
            "cameraRadialDelta": 1.0,
            "postRotationCameraPositionResidual": math.sqrt(2.0),
            "postRotationCameraDirectionResidualDegrees": expected_direction_residual,
            "sourceObjectCameraDistance": math.sqrt(8.0),
            "retainedBaseCameraObjectDistance": math.sqrt(2.0),
            "objectCameraDistanceDelta": math.sqrt(2.0) - math.sqrt(8.0),
        }
        for key, expected in required.items():
            self.assertIn(key, diagnostic)
            if isinstance(expected, float):
                self.assertAlmostEqual(expected, diagnostic[key], places=12, msg=key)
            else:
                self.assertEqual(expected, diagnostic[key], key)

        # Diagnostics are persisted as JSON by later campaign tooling. NaN and
        # numpy scalar leakage would make that evidence non-portable.
        json.dumps(diagnostic, allow_nan=False)

    def test_anchor_inputs_are_not_mutated(self):
        base_a = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        base_b = np.array([4.0, 6.0, 3.0], dtype=np.float64)
        source_a = base_a.copy()
        source_b = base_b.copy()
        snapshots = tuple(value.copy() for value in (base_a, base_b, source_a, source_b))

        align_triplet_about_axis(
            base_a,
            base_b,
            [1.0, 2.0, 4.0],
            source_a,
            source_b,
            [1.0, 2.0, 4.0],
            [2.0, 3.0, 5.0],
            object_name="lamp",
            base_object_name="bed",
            anchor_names=self.NAMES,
        )

        for observed, expected in zip((base_a, base_b, source_a, source_b), snapshots):
            self.assertTrue(np.array_equal(expected, observed))

    def test_shared_axis_rotation_fixes_both_anchor_points_exactly(self):
        for anchor in (self.BASE_A, self.BASE_B):
            with self.subTest(anchor=anchor.tolist()):
                aligned_anchor, _rotated_camera, _diagnostic = self.align(
                    [0.0, 0.0, 1.0],
                    anchor,
                )
                self.assertTrue(np.array_equal(anchor, np.asarray(aligned_anchor)))

    def test_camera_on_axis_is_deterministic_and_json_safe(self):
        cases = (
            (self.BASE_CAMERA, [2.0, 0.0, 0.0]),
            ([2.0, 0.0, 0.0], self.BASE_CAMERA),
        )
        source_c = np.array([1.0, 2.0, 3.0], dtype=np.float64)

        for base_camera, source_camera in cases:
            with self.subTest(base_camera=base_camera, source_camera=source_camera):
                aligned_c, rotated_camera, diagnostic = self.align(
                    source_camera,
                    source_c,
                    base_camera=np.asarray(base_camera, dtype=np.float64),
                )

                self.assertPointAlmostEqual(source_c, aligned_c)
                self.assertPointAlmostEqual(source_camera, rotated_camera)
                self.assertEqual("underdetermined_camera_axis", diagnostic["status"])
                self.assertEqual(0.0, diagnostic["rotationDegrees"])
                json.dumps(diagnostic, allow_nan=False)

    def test_mismatched_shared_anchors_are_rejected(self):
        mismatches = (
            {"source_a": np.array([0.001, 0.0, 0.0])},
            {"source_b": np.array([5.0, 0.001, 0.0])},
        )
        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch), self.assertRaisesRegex(ValueError, "anchor"):
                self.align([0.0, 0.0, 1.0], [0.0, 0.0, 2.0], **mismatch)

    def test_zero_length_axis_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "axis|A.*B"):
            self.align(
                [0.0, 0.0, 1.0],
                [0.0, 0.0, 2.0],
                base_b=self.BASE_A.copy(),
                source_b=self.BASE_A.copy(),
            )

    def test_non_finite_coordinates_are_rejected(self):
        invalid_inputs = (
            {"base_camera": np.array([0.0, np.inf, 0.0])},
            {"source_camera": np.array([0.0, 0.0, np.nan])},
            {"source_c": np.array([0.0, np.inf, 1.0])},
        )
        for invalid in invalid_inputs:
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "finite"):
                source_camera = invalid.get("source_camera", [0.0, 0.0, 1.0])
                source_c = invalid.get("source_c", [0.0, 0.0, 2.0])
                other_overrides = {
                    key: value
                    for key, value in invalid.items()
                    if key not in {"source_camera", "source_c"}
                }
                self.align(source_camera, source_c, **other_overrides)

    def test_calculate_coords_keeps_legacy_shape_and_opt_in_diagnostics(self):
        triplets = pd.DataFrame([
            {
                "objectA": "chair", "objectB": "table", "objectC": "bed",
                "distanceAB": 5.0, "distanceAC": 2.0, "distanceAO": 1.0,
                "angleBAC": 90.0, "angleOAB": 90.0, "angleOAC": 0.0,
            },
            {
                "objectA": "chair", "objectB": "table", "objectC": "lamp",
                "distanceAB": 5.0, "distanceAC": 2.0, "distanceAO": 1.0,
                "angleBAC": 90.0, "angleOAB": 90.0, "angleOAC": 90.0,
            },
        ])
        colors = pd.DataFrame([
            {"object_name": "chair", "R": 1, "G": 2, "B": 3, "count": 1},
        ])
        sizes = {
            name: [0.5, "medium"]
            for name in ("chair", "table", "bed", "lamp")
        }

        def read_csv(path, *_args, **_kwargs):
            if str(path).endswith("triplets.csv"):
                return triplets.copy()
            return colors.copy()

        patches = (
            patch("modules.prepare_data.pd.read_csv", side_effect=read_csv),
            patch("modules.prepare_data.loadObjectSizes", return_value=sizes),
            patch(
                "modules.prepare_data.retrieve_realistic_object_color",
                return_value=(1, 2, 3),
            ),
            # Keep the first requested pair as A/B so this fixture deterministically
            # exercises one base triplet plus one appended triplet.
            patch("modules.prepare_data.random.shuffle"),
        )
        with patches[0], patches[1], patches[2], patches[3]:
            legacy = calculateCoords(["chair", "table", "bed", "lamp"])
            detailed = calculateCoords(
                ["chair", "table", "bed", "lamp"],
                return_alignment_diagnostics=True,
            )

        self.assertEqual(2, len(legacy))
        self.assertEqual(3, len(detailed))
        self.assertEqual(legacy[0], detailed[0])
        self.assertEqual(1, len(detailed[2]))
        self.assertEqual("lamp", detailed[2][0]["objectName"])
        self.assertEqual("bed", detailed[2][0]["baseObjectName"])
        self.assertEqual(["chair", "table"], detailed[2][0]["anchorNames"])


if __name__ == "__main__":
    unittest.main()
