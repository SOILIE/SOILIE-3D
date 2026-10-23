from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from modules.triplet_schema import CANONICAL_TRIPLET_FIELDS
from modules.working_combo_schema import (
    WORKING_COMBINATION_FIELDS,
    WorkingCombinationError,
    iter_working_combinations,
)
from modules import working_combos
from scripts.rebuild_working_combos import (
    KNOWN_INVALID_TRIPLET_SHA256S,
    build_working_combo_catalogs,
)
from serverless.common.engine import RequestError, validate_request
from serverless.compiler.build_runtime import compile_api_index, validated_working_rows


TEST_TEMP_ROOT = Path(__file__).parents[2] / ".codex" / "test-working-combos"
LABELS = ("chair", "table", "bed", "lamp", "book", "plant", "desk", "clock")


def _write_csv(path: Path, header, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.writer(destination, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _triplet_row(object_a: str, object_b: str, object_c: str) -> tuple[object, ...]:
    return (object_a, object_b, object_c, 1, 1, 1, 60, 60, 60)


class WorkingComboFixture(unittest.TestCase):
    def setUp(self):
        TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
        self.root = Path(self._temporary.name)

    def tearDown(self):
        self._temporary.cleanup()
        try:
            TEST_TEMP_ROOT.rmdir()
        except OSError:
            pass

    def write_catalog(self, name: str, rows, header=WORKING_COMBINATION_FIELDS) -> Path:
        path = self.root / name
        _write_csv(path, header, rows)
        return path

    def write_triplets(self, rows) -> Path:
        path = self.root / "triplets.csv"
        _write_csv(path, CANONICAL_TRIPLET_FIELDS, rows)
        return path

    def make_generator_sources(self) -> tuple[Path, Path, Path, str]:
        triplets = self.write_triplets(
            [_triplet_row("chair", "table", label) for label in LABELS[2:]]
            + [_triplet_row("chair", "table", "bed")]
            + [_triplet_row("chair", "table", "TV")]
            + [_triplet_row("chair", "table", "ghost")]
        )
        sizes = self.root / "object_sizes_manual.csv"
        _write_csv(
            sizes,
            ("object", "diameter", "count", "sizecat"),
            [(label, 1, 1, "medium") for label in LABELS],
        )
        assets = self.root / "assets"
        assets.mkdir()
        _write_csv(
            assets / "asset_rotations.csv",
            ("object_name", "asset_name", "x", "y", "z"),
            [
                (label.title() if label == "chair" else label, f"{label}_0001.obj", 0, 0, 0)
                for label in LABELS
            ],
        )
        for label in LABELS:
            (assets / f"{label}_0001.obj").write_text("fixture\n", encoding="utf-8")
        digest = hashlib.sha256(triplets.read_bytes()).hexdigest()
        return triplets, sizes, assets, digest


class WorkingComboSchemaTests(WorkingComboFixture):
    def test_strict_rows_preserve_instances_and_reject_legacy_headers_and_holes(self):
        valid = self.write_catalog(
            "valid.csv",
            [("chair", "table", "chair", "", "", "", "", "")],
        )
        self.assertEqual(
            [("chair", "table", "chair")],
            list(iter_working_combinations(valid)),
        )

        legacy = self.write_catalog(
            "legacy.csv",
            [("chair", "table", "bed", "", "", "")],
            tuple(f"obj{index}" for index in range(6)),
        )
        with self.assertRaisesRegex(WorkingCombinationError, "legacy six-column"):
            list(iter_working_combinations(legacy))

        hole = self.write_catalog(
            "hole.csv",
            [("chair", "table", "bed", "", "lamp", "", "", "")],
        )
        with self.assertRaisesRegex(WorkingCombinationError, "after a blank"):
            list(iter_working_combinations(hole))

    def test_load_and_selection_return_exact_counts_from_supported_rows(self):
        rows = []
        for count in range(3, 9):
            values = LABELS[:count] + ("",) * (8 - count)
            rows.append(values)
        rows.append(("chair", "table", "bed", "bed", "", "", "", ""))
        catalog = self.write_catalog("catalog.csv", rows)
        triplets = self.write_triplets(
            [_triplet_row("chair", "table", label) for label in LABELS[2:]]
        )

        self.assertEqual(tuple(range(3, 9)), working_combos.available_counts(catalog))
        for count in range(3, 9):
            self.assertEqual(
                count,
                len(working_combos.select_exact(
                    count,
                    filepath=catalog,
                    triplet_filepath=triplets,
                    allow_duplicates=True,
                )),
            )

        with patch("modules.working_combos.random.choice", side_effect=lambda values: values[-1]):
            self.assertEqual(
                ["chair", "table", "bed", "bed"],
                working_combos.select_exact(
                    4,
                    filepath=catalog,
                    triplet_filepath=triplets,
                    allow_duplicates=True,
                ),
            )
            selected = working_combos.select_exact(
                4,
                filepath=catalog,
                triplet_filepath=triplets,
                allow_duplicates=False,
            )
        self.assertEqual(4, len(selected))
        self.assertEqual(4, len(set(selected)))

    def test_exact_selection_fails_without_shrinking_or_unbounded_retry(self):
        catalog = self.write_catalog(
            "duplicates.csv",
            [("chair", "table", "bed", "bed", "", "", "", "")],
        )
        triplets = self.write_triplets([_triplet_row("chair", "table", "bed")])
        with self.assertRaisesRegex(WorkingCombinationError, "no supported 4-object"):
            working_combos.select_exact(
                4,
                filepath=catalog,
                triplet_filepath=triplets,
                allow_duplicates=False,
            )


class WorkingComboGeneratorTests(WorkingComboFixture):
    def test_guarded_generation_is_deterministic_and_receipt_counts_are_unambiguous(self):
        triplets, sizes, assets, digest = self.make_generator_sources()
        outputs = []
        receipts = []
        for seed in ("1", "98765"):
            output = self.root / f"catalogs-{seed}"
            receipt = self.root / f"receipt-{seed}.json"
            environment = os.environ.copy()
            environment["PYTHONHASHSEED"] = seed
            subprocess.run(
                [
                    sys.executable,
                    "scripts/rebuild_working_combos.py",
                    "--triplets", str(triplets),
                    "--sizes", str(sizes),
                    "--assets", str(assets),
                    "--output", str(output),
                    "--receipt", str(receipt),
                    "--expected-triplets-sha256", digest,
                ],
                cwd=Path(__file__).parents[2],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            outputs.append(output)
            receipts.append(json.loads(receipt.read_text(encoding="utf-8")))

        first_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in outputs[0].iterdir()
        }
        second_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in outputs[1].iterdir()
        }
        self.assertEqual(first_hashes, second_hashes)
        self.assertEqual(receipts[0], receipts[1])

        counts = receipts[0]["inputs"]["triplets"]
        self.assertEqual(9, counts["sourceRows"])
        self.assertEqual(7, counts["canonicalRenderableRows"])
        self.assertEqual(6, counts["uniqueRenderableTriplets"])
        self.assertEqual(1, counts["duplicateRenderableRowsCollapsed"])
        self.assertEqual(2, counts["rejectedRows"])
        self.assertEqual(
            {str(count): value for count, value in zip(range(3, 9), (6, 15, 20, 15, 6, 1))},
            receipts[0]["catalogs"]["working-combos-refined.csv"]["rowsByObjectCount"],
        )
        self.assertEqual(
            receipts[0]["catalogs"]["working-combos-refined.csv"]["rowsByObjectCount"],
            receipts[0]["catalogs"]["working-combos-refined.csv"][
                "duplicateFreeRowsByObjectCount"
            ],
        )

    def test_generation_requires_reviewed_digest_and_separate_new_destinations(self):
        triplets, sizes, assets, digest = self.make_generator_sources()
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_working_combo_catalogs(
                triplets_path=triplets,
                sizes_path=sizes,
                asset_root=assets,
                output_dir=self.root / "bad-output",
                receipt_path=self.root / "bad-receipt.json",
                expected_triplets_sha256="0" * 64,
            )
        output = self.root / "existing-output"
        output.mkdir()
        with self.assertRaisesRegex(FileExistsError, "existing output"):
            build_working_combo_catalogs(
                triplets_path=triplets,
                sizes_path=sizes,
                asset_root=assets,
                output_dir=output,
                receipt_path=self.root / "unused-receipt.json",
                expected_triplets_sha256=digest,
            )

    def test_generation_refuses_known_camera_frame_derived_triplet_hashes(self):
        triplets, sizes, assets, _digest = self.make_generator_sources()
        for digest in KNOWN_INVALID_TRIPLET_SHA256S:
            with self.subTest(digest=digest), patch(
                "scripts.rebuild_working_combos.sha256", return_value=digest
            ), self.assertRaisesRegex(ValueError, "camera-frame-derived"):
                build_working_combo_catalogs(
                    triplets_path=triplets,
                    sizes_path=sizes,
                    asset_root=assets,
                    output_dir=self.root / f"known-invalid-{digest[:8]}",
                    receipt_path=self.root / f"known-invalid-{digest[:8]}.json",
                    expected_triplets_sha256=digest,
                )

    def test_generation_rejects_noncanonical_triplet_spacing(self):
        triplets, sizes, assets, _digest = self.make_generator_sources()
        with triplets.open("a", newline="", encoding="utf-8") as destination:
            csv.writer(destination, lineterminator="\n").writerow(
                _triplet_row("chair", "table", " bed")
            )
        digest = hashlib.sha256(triplets.read_bytes()).hexdigest()
        receipt = build_working_combo_catalogs(
            triplets_path=triplets,
            sizes_path=sizes,
            asset_root=assets,
            output_dir=self.root / "spaced-output",
            receipt_path=self.root / "spaced-receipt.json",
            expected_triplets_sha256=digest,
        )
        self.assertEqual(3, receipt["inputs"]["triplets"]["rejectedRows"])

    def test_generation_rejects_assets_outside_flat_obj_contract(self):
        triplets, sizes, assets, digest = self.make_generator_sources()
        with (assets / "asset_rotations.csv").open(
            "a", newline="", encoding="utf-8"
        ) as destination:
            csv.writer(destination, lineterminator="\n").writerow(
                ("chair", "nested/chair_0002.obj", 0, 0, 0)
            )
        with self.assertRaisesRegex(ValueError, "flat \\.obj asset"):
            build_working_combo_catalogs(
                triplets_path=triplets,
                sizes_path=sizes,
                asset_root=assets,
                output_dir=self.root / "nested-asset-output",
                receipt_path=self.root / "nested-asset-receipt.json",
                expected_triplets_sha256=digest,
            )

    def test_incomplete_random_capacity_is_rejected_without_partial_outputs(self):
        _triplets, sizes, assets, _digest = self.make_generator_sources()
        sparse_triplets = self.root / "sparse-triplets.csv"
        _write_csv(
            sparse_triplets,
            CANONICAL_TRIPLET_FIELDS,
            [_triplet_row("chair", "table", "bed")],
        )
        digest = hashlib.sha256(sparse_triplets.read_bytes()).hexdigest()
        output = self.root / "partial-output"
        receipt = self.root / "partial-receipt.json"
        with self.assertRaisesRegex(ValueError, "no duplicate-free rows"):
            build_working_combo_catalogs(
                triplets_path=sparse_triplets,
                sizes_path=sizes,
                asset_root=assets,
                output_dir=output,
                receipt_path=receipt,
                expected_triplets_sha256=digest,
            )
        self.assertFalse(output.exists())
        self.assertFalse(receipt.exists())


class CompilerCatalogValidationTests(WorkingComboFixture):
    def test_compiler_rejects_legacy_headers_and_interior_holes(self):
        legacy = self.write_catalog(
            "legacy-compiler.csv",
            [("chair", "table", "bed", "", "", "")],
            tuple(f"obj{index}" for index in range(6)),
        )
        with self.assertRaisesRegex(RuntimeError, "legacy six-column"):
            validated_working_rows(
                legacy,
                allowed={"chair", "table", "bed"},
                triplets={("chair", "table", "bed")},
            )

        hole = self.write_catalog(
            "hole-compiler.csv",
            [("chair", "table", "bed", "", "lamp", "", "", "")],
        )
        with self.assertRaisesRegex(RuntimeError, "after a blank"):
            validated_working_rows(
                hole,
                allowed={"chair", "table", "bed", "lamp"},
                triplets={("chair", "table", "bed"), ("chair", "table", "lamp")},
            )

    def test_compiler_rejects_nonrenderable_missing_basis_and_duplicate_rows(self):
        allowed = {"chair", "table", "bed", "lamp"}
        triplets = {("chair", "table", "bed")}

        nonrenderable = self.write_catalog(
            "nonrenderable.csv",
            [("chair", "table", "ghost", "", "", "", "", "")],
        )
        with self.assertRaisesRegex(RuntimeError, "non-renderable"):
            validated_working_rows(nonrenderable, allowed=allowed, triplets=triplets)

        missing_basis = self.write_catalog(
            "missing-basis.csv",
            [("chair", "table", "bed", "lamp", "", "", "", "")],
        )
        with self.assertRaisesRegex(RuntimeError, "lacks its ordered A/B triplet basis"):
            validated_working_rows(missing_basis, allowed=allowed, triplets=triplets)

        duplicate = self.write_catalog(
            "duplicate.csv",
            [
                ("chair", "table", "bed", "", "", "", "", ""),
                ("chair", "table", "bed", "", "", "", "", ""),
            ],
        )
        with self.assertRaisesRegex(RuntimeError, "duplicates an earlier"):
            validated_working_rows(duplicate, allowed=allowed, triplets=triplets)

    def test_compiler_accepts_explicit_duplicate_instances_when_the_basis_exists(self):
        catalog = self.write_catalog(
            "instances.csv",
            [("chair", "table", "bed", "bed", "", "", "", "")],
        )
        self.assertEqual(
            [("chair", "table", "bed", "bed")],
            validated_working_rows(
                catalog,
                allowed={"chair", "table", "bed"},
                triplets={("chair", "table", "bed")},
            ),
        )

    def test_generated_catalogs_compile_with_exact_count_and_duplicate_metadata(self):
        triplets, sizes, assets, digest = self.make_generator_sources()
        generated = self.root / "generated"
        build_working_combo_catalogs(
            triplets_path=triplets,
            sizes_path=sizes,
            asset_root=assets,
            output_dir=generated,
            receipt_path=self.root / "receipt.json",
            expected_triplets_sha256=digest,
        )

        repository = self.root / "repository"
        (repository / "data").mkdir(parents=True)
        shutil.copy2(triplets, repository / "data" / "triplets.csv")
        shutil.copy2(sizes, repository / "data" / "object_sizes_manual.csv")
        shutil.copytree(assets, repository / "assets")
        for path in generated.iterdir():
            shutil.copy2(path, repository / "data" / path.name)
        output = self.root / "runtime"
        output.mkdir()
        result = compile_api_index(repository, output)
        self.assertGreater(result["combinations"], 0)
        public_catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(
            list(range(3, 9)),
            public_catalog["presetObjectCounts"]["random"]["withoutDuplicates"],
        )
        connection = sqlite3.connect(output / "relations.sqlite3")
        try:
            counts = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT object_count FROM working_combinations "
                    "WHERE preset = 'random'"
                )
            }
            self.assertEqual(set(range(3, 9)), counts)
            self.assertEqual(
                0,
                connection.execute(
                    "SELECT COUNT(*) FROM working_combinations WHERE has_duplicates = 1"
                ).fetchone()[0],
            )
        finally:
            connection.close()


class EngineCapacityTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
            """
            CREATE TABLE objects (name TEXT, selectable INTEGER);
            CREATE TABLE working_combinations (
              preset TEXT, object_count INTEGER, has_duplicates INTEGER
            );
            """
        )

    def tearDown(self):
        self.connection.close()

    def request(self, **changes):
        payload = {
            "clientRequestId": str(uuid.uuid4()),
            "mode": "random",
            "sceneCount": 1,
            "objectCount": 8,
            "allowDuplicates": False,
            "sameObjectsAcrossScenes": True,
            "room": {"mode": "auto"},
            "seed": 1,
        }
        payload.update(changes)
        return validate_request(self.connection, payload)

    def test_validation_uses_exact_preset_count_and_duplicate_policy_capacity(self):
        self.connection.executemany(
            "INSERT INTO working_combinations VALUES (?, ?, ?)",
            [("random", 8, 0), ("random", 7, 1), ("bedroom", 4, 0)],
        )
        self.assertEqual(8, self.request()["objectCount"])
        with self.assertRaises(RequestError) as unavailable:
            self.request(objectCount=7)
        self.assertEqual("UNSUPPORTED_OBJECT_COUNT", unavailable.exception.code)
        self.assertEqual(7, self.request(objectCount=7, allowDuplicates=True)["objectCount"])

        self.assertEqual(
            4,
            self.request(mode="room_type", roomType="bedroom", objectCount=4)["objectCount"],
        )
        with self.assertRaises(RequestError) as unavailable_preset:
            self.request(mode="room_type", roomType="bedroom", objectCount=5)
        self.assertEqual("UNSUPPORTED_ROOM_PRESET", unavailable_preset.exception.code)
        with self.assertRaisesRegex(RequestError, "3 to 8"):
            self.request(objectCount=9)

    def test_api_engine_does_not_import_renderer_only_modules(self):
        """Mirror the API image, which copies serverless/ but not modules/."""

        script = """
import builtins

original_import = builtins.__import__

def api_image_import(name, *args, **kwargs):
    if name == 'modules' or name.startswith('modules.'):
        raise ModuleNotFoundError('renderer modules are absent from the API image')
    return original_import(name, *args, **kwargs)

builtins.__import__ = api_image_import
import serverless.common.engine
"""
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).parents[2],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
