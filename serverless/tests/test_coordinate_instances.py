"""Instance-aware coordinate-construction and request-eligibility contracts."""

from __future__ import annotations

from contextlib import nullcontext
import csv
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from modules import prepare_data as prepare_data_module
from modules.prepare_data import (
    TripletCoverageError,
    calculateCoords,
    suffix_duplicates,
)
from modules.triplet_schema import CANONICAL_TRIPLET_FIELDS
from modules.working_combos import (
    ordered_anchor_witnesses,
    supports_coordinate_construction,
)
from serverless.common.engine import is_supported_combination
from serverless.common.model_runtime import generate_model_inputs


TEST_TEMP_ROOT = Path(__file__).parents[2] / ".codex" / "test-coordinate-instances"


def _row(object_a: str, object_b: str, object_c: str) -> dict[str, object]:
    # This is a finite, nondegenerate coplanar reconstruction: C and the camera
    # both lie perpendicular to A--B, so repeated rows need no incidental shear.
    return {
        "objectA": object_a,
        "objectB": object_b,
        "objectC": object_c,
        "distanceAB": 4.0,
        "distanceAC": 3.0,
        "distanceAO": 2.0,
        "angleBAC": 90.0,
        "angleOAB": 90.0,
        "angleOAC": 0.0,
    }


class InstanceConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()
        try:
            TEST_TEMP_ROOT.rmdir()
        except OSError:
            pass

    def write_triplets(self, name: str, rows: list[dict[str, object]]) -> Path:
        path = self.root / name
        with path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=CANONICAL_TRIPLET_FIELDS,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        return path

    def calculate(
        self,
        objects: list[str],
        rows: list[dict[str, object]],
        *,
        chooser=None,
    ):
        colors = pd.DataFrame([
            {"object_name": "fixture", "R": 1, "G": 2, "B": 3, "count": 1},
        ])
        sizes = {
            instance: [0.5, "medium"]
            for instance in suffix_duplicates(objects)
        }

        def read_csv(path, *_args, **_kwargs):
            if str(path).endswith("triplets.csv"):
                return pd.DataFrame(rows)
            return colors.copy()

        if chooser is None:
            chooser = lambda values: values[0]

        with (
            patch("modules.prepare_data.pd.read_csv", side_effect=read_csv),
            patch("modules.prepare_data.loadObjectSizes", return_value=sizes),
            patch(
                "modules.prepare_data.retrieve_realistic_object_color",
                return_value=(1, 2, 3),
            ),
            patch("modules.prepare_data.random.choice", side_effect=chooser),
        ):
            return calculateCoords(objects, return_alignment_diagnostics=True)

    @staticmethod
    def row_with_camera_range(
        object_a: str,
        object_b: str,
        object_c: str,
        camera_range: float,
    ) -> dict[str, object]:
        row = _row(object_a, object_b, object_c)
        row["distanceAO"] = camera_range
        return row

    def test_regression_preserves_both_chair_instances(self) -> None:
        objects = ["chair", "desk", "chair", "lamp"]
        rows = [
            _row("chair", "desk", "chair"),
            _row("chair", "desk", "lamp"),
            # This partial alternate basis triggered the former label-coverage
            # false positive when its missing second chair was silently skipped.
            _row("chair", "lamp", "desk"),
        ]

        coordinates, _progress, diagnostics = self.calculate(objects, rows)

        self.assertTrue({"chair", "desk", "chair.1", "lamp"} <= coordinates.keys())
        self.assertEqual(
            {"chair", "desk", "chair.1", "lamp"},
            set(coordinates) - {"CAMERA", "WALL", "FLOOR"},
        )
        self.assertEqual("chair.1", diagnostics[0]["baseObjectName"])
        self.assertEqual("lamp", diagnostics[0]["objectName"])
        self.assertEqual(["chair", "desk"], diagnostics[0]["anchorNames"])

    def test_incomplete_duplicate_instance_basis_is_rejected(self) -> None:
        with self.assertRaisesRegex(TripletCoverageError, "every requested partner"):
            self.calculate(
                ["chair", "desk", "chair", "lamp"],
                [
                    _row("chair", "desk", "lamp"),
                    _row("chair", "lamp", "desk"),
                ],
            )

    def test_window_assembly_preserves_every_requested_instance(self) -> None:
        objects = ["window", "desk", "curtain", "chair"]
        coordinates, _progress, _diagnostics = self.calculate(
            objects,
            [
                _row("window", "desk", "curtain"),
                _row("window", "desk", "chair"),
            ],
        )

        semantic = set(coordinates) - {"CAMERA", "WALL", "FLOOR"}
        self.assertEqual(set(objects), semantic)
        self.assertEqual(
            coordinates["window"]["coords"],
            coordinates["curtain"]["coords"],
        )

    def test_multiple_repeated_partners_keep_all_eight_instance_ids(self) -> None:
        objects = [
            "chair", "desk", "chair", "lamp",
            "chair", "lamp", "plant", "plant",
        ]
        rows = [
            _row("chair", "desk", label)
            for label in ("chair", "lamp", "plant")
        ]

        coordinates, _progress, _diagnostics = self.calculate(objects, rows)

        expected = set(suffix_duplicates(objects))
        self.assertEqual(
            expected,
            set(coordinates) - {"CAMERA", "WALL", "FLOOR"},
        )

    def test_every_supported_size_constructs_every_instance(self) -> None:
        labels = ["anchor_a", "anchor_b", *[f"object_{index}" for index in range(6)]]
        rows = [_row("anchor_a", "anchor_b", label) for label in labels[2:]]

        for count in range(3, 9):
            with self.subTest(count=count):
                requested = labels[:count]
                coordinates, _progress, diagnostics = self.calculate(requested, rows)
                self.assertTrue(set(requested) <= coordinates.keys())
                self.assertEqual(count - 3, len(diagnostics))

    def test_partner_base_selection_is_independent_of_input_order(self) -> None:
        rows = [
            self.row_with_camera_range("a", "b", "c", 2.0),
            self.row_with_camera_range("a", "b", "d", 4.0),
        ]

        for objects in (["a", "b", "c", "d"], ["a", "b", "d", "c"]):
            for base_label, expected_range in (("c", 2.0), ("d", 4.0)):
                calls = []

                def choose(values):
                    options = tuple(values)
                    calls.append(options)
                    if len(calls) == 1:
                        return options[0]
                    return next(index for index in options if objects[index] == base_label)

                with self.subTest(objects=objects, base=base_label):
                    coordinates, _progress, diagnostics = self.calculate(
                        list(objects), rows, chooser=choose,
                    )
                    camera = coordinates["CAMERA"]["coords"]
                    self.assertAlmostEqual(
                        expected_range,
                        math.sqrt(sum(float(component) ** 2 for component in camera)),
                    )
                    self.assertEqual(2, len(calls))
                    self.assertEqual((2, 3), calls[1])
                    self.assertEqual(base_label, diagnostics[0]["baseObjectName"])
                    self.assertEqual(
                        next(label for label in ("c", "d") if label != base_label),
                        diagnostics[0]["objectName"],
                    )

    def test_partner_base_selection_uses_repeated_instance_identity(self) -> None:
        calls = []

        def choose(values):
            options = tuple(values)
            calls.append(options)
            return options[0] if len(calls) == 1 else options[-1]

        coordinates, _progress, diagnostics = self.calculate(
            ["chair", "desk", "chair", "chair"],
            [_row("chair", "desk", "chair")],
            chooser=choose,
        )

        self.assertEqual((2, 3), calls[1])
        self.assertEqual(
            {"chair", "desk", "chair.1", "chair.2"},
            set(coordinates) - {"CAMERA", "WALL", "FLOOR"},
        )
        self.assertEqual("chair.2", diagnostics[0]["baseObjectName"])
        self.assertEqual("chair.1", diagnostics[0]["objectName"])

    def test_runtime_retry_can_select_a_different_partner_base(self) -> None:
        objects = ["a", "b", "d", "c"]
        rows = [
            self.row_with_camera_range("a", "b", "c", 2.0),
            self.row_with_camera_range("a", "b", "d", 8.0),
        ]
        colors = pd.DataFrame([
            {"object_name": "fixture", "R": 1, "G": 2, "B": 3, "count": 1},
        ])
        sizes = {
            instance: [0.5, "medium"]
            for instance in suffix_duplicates(objects)
        }
        choice_calls = []

        def read_csv(path, *_args, **_kwargs):
            if str(path).endswith("triplets.csv"):
                return pd.DataFrame(rows)
            return colors.copy()

        def choose(values):
            options = tuple(values)
            choice_calls.append(options)
            if len(choice_calls) in (1, 3):
                return options[0]
            # Attempt one retains d (AO=8) and is rejected by the runtime's
            # five-metre gate; attempt two retains c (AO=2) and succeeds.
            return options[0] if len(choice_calls) == 2 else options[1]

        with (
            patch("modules.prepare_data.pd.read_csv", side_effect=read_csv),
            patch("modules.prepare_data.loadObjectSizes", return_value=sizes),
            patch(
                "modules.prepare_data.retrieve_realistic_object_color",
                return_value=(1, 2, 3),
            ),
            patch("modules.prepare_data.random.choice", side_effect=choose),
            patch(
                "serverless.common.model_runtime._load_original_modules",
                return_value=(prepare_data_module, None, None),
            ),
            patch(
                "serverless.common.model_runtime._model_working_directory",
                return_value=nullcontext(),
            ),
            patch("serverless.common.model_runtime.seed_model"),
        ):
            coordinates = generate_model_inputs(Path("."), objects, 73)

        self.assertEqual(4, len(choice_calls))
        self.assertEqual((2, 3), choice_calls[1])
        self.assertEqual((2, 3), choice_calls[3])
        camera = coordinates["CAMERA"]["coords"]
        self.assertAlmostEqual(
            2.0,
            math.sqrt(sum(float(component) ** 2 for component in camera)),
        )

    def test_runtime_and_api_require_the_same_duplicate_instance_witness(self) -> None:
        objects = ["chair", "desk", "chair", "lamp"]
        incomplete_rows = [
            _row("chair", "desk", "lamp"),
            _row("chair", "lamp", "desk"),
        ]
        complete_rows = [
            _row("chair", "desk", "chair"),
            *incomplete_rows,
        ]
        incomplete_path = self.write_triplets("incomplete.csv", incomplete_rows)
        complete_path = self.write_triplets("complete.csv", complete_rows)

        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE triplets (object_a TEXT, object_b TEXT, object_c TEXT)"
        )
        connection.executemany(
            "INSERT INTO triplets VALUES (?, ?, ?)",
            [
                (row["objectA"], row["objectB"], row["objectC"])
                for row in incomplete_rows
            ],
        )

        self.assertFalse(supports_coordinate_construction(objects, incomplete_path))
        self.assertFalse(is_supported_combination(connection, objects))

        connection.execute(
            "INSERT INTO triplets VALUES (?, ?, ?)",
            ("chair", "desk", "chair"),
        )
        self.assertTrue(supports_coordinate_construction(objects, complete_path))
        self.assertTrue(is_supported_combination(connection, objects))

        witnesses = ordered_anchor_witnesses(
            objects,
            {
                (row["objectA"], row["objectB"], row["objectC"])
                for row in complete_rows
            },
        )
        self.assertEqual(
            (
                (0, 1, (2, 3)),
                (2, 1, (0, 3)),
            ),
            witnesses,
        )

    def test_support_checks_enforce_the_three_to_eight_instance_policy(self) -> None:
        self.assertFalse(supports_coordinate_construction(["a", "b"], self.root / "missing.csv"))
        self.assertFalse(
            supports_coordinate_construction(
                [f"object_{index}" for index in range(9)],
                self.root / "missing.csv",
            )
        )

        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE triplets (object_a TEXT, object_b TEXT, object_c TEXT)"
        )
        self.assertFalse(is_supported_combination(connection, ["a", "b"]))
        self.assertFalse(
            is_supported_combination(
                connection,
                [f"object_{index}" for index in range(9)],
            )
        )

    def test_coordinate_constructor_enforces_the_three_to_eight_instance_policy(self) -> None:
        for objects in (
            ["anchor_a", "anchor_b"],
            [f"object_{index}" for index in range(9)],
        ):
            with self.subTest(count=len(objects)):
                with self.assertRaisesRegex(
                    TripletCoverageError,
                    rf"requires 3 to 8 object instances; received {len(objects)}",
                ):
                    calculateCoords(objects)


if __name__ == "__main__":
    unittest.main()
