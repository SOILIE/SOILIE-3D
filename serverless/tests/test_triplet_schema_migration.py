from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest

import numpy as np

# The collector's point-cloud loader imports this optional dependency at module
# load time, although triplet calculation/export does not use it.
try:
    import plyfile  # noqa: F401
except ModuleNotFoundError:
    plyfile_stub = types.ModuleType("plyfile")
    plyfile_stub.PlyData = object
    plyfile_stub.PlyElement = object
    sys.modules["plyfile"] = plyfile_stub

from modules.collect_data import Frame, _angle_degrees
from modules.prepare_data import reconstruct_triplet_coordinates
from modules.triplet_schema import (
    LEGACY_TRIPLET_COLUMNS,
    TRIPLET_COLUMNS,
    TripletSchema,
    detect_triplet_schema,
    validate_triplet_csv_header,
    validate_triplet_columns,
)
from modules.working_combos import _coordinate_triplets
from serverless.compiler.build_runtime import iter_canonical_triplet_rows
from scripts.migrate_triplet_angles import (
    MigrationExpectations,
    REAL_FILE_BYTES,
    REAL_ROW_COUNT,
    REAL_SOURCE_SHA256,
    REAL_TARGET_SHA256,
    _migrate_triplet_angles,
    migrate_triplet_angles,
)


ROOT = Path(__file__).parents[2]
TEST_WORK_ROOT = ROOT / ".codex" / "tests"


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fixture_expectations(source: bytes, target: bytes, rows: int) -> MigrationExpectations:
    return MigrationExpectations(
        source_sha256=_digest(source),
        target_sha256=_digest(target),
        row_count=rows,
        source_bytes=len(source),
        target_bytes=len(target),
    )


class TripletSchemaTests(unittest.TestCase):
    def test_schema_detector_and_validator_are_exact(self):
        self.assertIs(TripletSchema.CANONICAL, detect_triplet_schema(TRIPLET_COLUMNS))
        self.assertIs(
            TripletSchema.LEGACY_MISLABELED_ANGLES,
            detect_triplet_schema(LEGACY_TRIPLET_COLUMNS),
        )
        self.assertIs(TripletSchema.UNKNOWN, detect_triplet_schema((*TRIPLET_COLUMNS, "extra")))
        validate_triplet_columns(TRIPLET_COLUMNS, source="fixture.csv")
        with self.assertRaisesRegex(ValueError, "migrate_triplet_angles.py"):
            validate_triplet_columns(LEGACY_TRIPLET_COLUMNS, source="fixture.csv")
        with self.assertRaisesRegex(ValueError, "unknown triplet header"):
            validate_triplet_columns((*TRIPLET_COLUMNS[:-1], "other"))

    def test_runtime_triplet_readers_reject_legacy_header(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            path = Path(temporary) / "triplets.csv"
            path.write_text(
                ",".join(LEGACY_TRIPLET_COLUMNS)
                + "\nchair,table,lamp,1,2,3,10,20,30\n",
                encoding="utf-8",
            )

            readers = (
                lambda: validate_triplet_csv_header(path),
                lambda: _coordinate_triplets(str(path.resolve())),
                lambda: list(iter_canonical_triplet_rows(path)),
            )
            for reader in readers:
                with self.subTest(reader=reader), self.assertRaisesRegex(
                    ValueError, "legacy mislabeled angle header"
                ):
                    reader()

    def test_collector_export_round_trips_distinct_named_angles_into_reconstruction(self):
        # In camera coordinates O is the origin. Translating A to the canonical
        # origin yields camera=[3, 2, sqrt(12)], B=[4, 0, 0], C=[0, 3, 0].
        camera_from_a = np.array([3.0, 2.0, math.sqrt(12.0)])
        centroid_a = -camera_from_a
        frame = Frame("synthetic", 1, 1)
        frame.centroids = {
            "['anchor a', '1']": centroid_a,
            "['anchor b', '1']": centroid_a + np.array([4.0, 0.0, 0.0]),
            "['object c', '1']": centroid_a + np.array([0.0, 3.0, 0.0]),
        }

        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            folder = Path(temporary)
            centroids = folder / "centroids.csv"
            centroids.write_text("object,X,Y,Z\n", encoding="utf-8")
            frame.getAngleDistCombos(centroids).export(folder)

            with (folder / "synthetic.csv").open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(list(TRIPLET_COLUMNS), reader.fieldnames)
                row = next(
                    candidate
                    for candidate in reader
                    if candidate["objectA"] == "anchor_a_1"
                    and candidate["objectB"] == "anchor_b_1"
                    and candidate["objectC"] == "object_c_1"
                )

            self.assertNotEqual(row["angleBAC"], row["angleOAB"])
            self.assertNotEqual(row["angleOAB"], row["angleOAC"])
            coordinates = reconstruct_triplet_coordinates(
                row["distanceAB"],
                row["distanceAC"],
                row["distanceAO"],
                row["angleBAC"],
                row["angleOAB"],
                row["angleOAC"],
            )
            np.testing.assert_allclose(coordinates[0], [0.0, 0.0, 0.0], atol=1e-12)
            np.testing.assert_allclose(coordinates[1], [4.0, 0.0, 0.0], atol=1e-12)
            np.testing.assert_allclose(coordinates[2], [0.0, 3.0, 0.0], atol=1e-12)
            np.testing.assert_allclose(coordinates[3], camera_from_a, atol=1e-12)

    def test_collector_clamps_near_parallel_angles_and_rejects_invalid_vectors(self):
        # These exact float64 coordinates produce a raw normalized dot product
        # of 1.0000000000000002 after subtracting A. Without clamping, arccos
        # emits NaN even though the vectors are valid and nearly parallel.
        centroid_a = np.array([
            float.fromhex('-0x1.3875456440995p+4'),
            float.fromhex('0x1.7795e641eb7e4p+4'),
            float.fromhex('0x1.35eb443a9a256p+3'),
        ])
        centroid_b = np.array([
            float.fromhex('-0x1.449bb8904f65cp+4'),
            float.fromhex('0x1.86054db70f7b4p+4'),
            float.fromhex('0x1.26f9fc72948a4p+3'),
        ])
        centroid_c = np.array([
            float.fromhex('-0x1.449bb8904f64fp+4'),
            float.fromhex('0x1.86054db70f87cp+4'),
            float.fromhex('0x1.26f9fc72949efp+3'),
        ])
        ba = centroid_b - centroid_a
        ca = centroid_c - centroid_a
        raw_cosine = np.dot(ba, ca) / (np.linalg.norm(ba) * np.linalg.norm(ca))
        self.assertGreater(raw_cosine, 1.0)

        frame = Frame('near-parallel', 1, 1)
        frame.centroids = {
            "['anchor a', '1']": centroid_a,
            "['anchor b', '1']": centroid_b,
            "['object c', '1']": centroid_c,
        }
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT) as temporary:
            centroids = Path(temporary) / 'centroids.csv'
            centroids.write_text('object,X,Y,Z\n', encoding='utf-8')
            frame.getAngleDistCombos(centroids)

        self.assertEqual(6, len(frame.combos))
        for row in frame.combos:
            for field in ('angleBAC', 'angleOAB', 'angleOAC'):
                angle = float(row[field])
                self.assertTrue(math.isfinite(angle), field)
                self.assertGreaterEqual(angle, 0.0, field)
                self.assertLessEqual(angle, 180.0, field)

        with self.assertRaisesRegex(ValueError, 'zero-length'):
            _angle_degrees([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], angle_name='fixture')
        with self.assertRaisesRegex(ValueError, 'finite vectors'):
            _angle_degrees([math.nan, 0.0, 0.0], [1.0, 0.0, 0.0], angle_name='fixture')


class TripletMigrationTests(unittest.TestCase):
    def setUp(self):
        TEST_WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_WORK_ROOT)
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _fixture(self, rows: list[str], newline: bytes = b"\r\n"):
        legacy_header = ",".join(LEGACY_TRIPLET_COLUMNS).encode("utf-8") + newline
        canonical_header = ",".join(TRIPLET_COLUMNS).encode("utf-8") + newline
        row_payload = newline.join(row.encode("utf-8") for row in rows) + newline
        source_payload = legacy_header + row_payload
        target_payload = canonical_header + row_payload
        source = self.folder / "legacy.csv"
        destination = self.folder / "canonical.csv"
        receipt = self.folder / "receipt.json"
        source.write_bytes(source_payload)
        return (
            source,
            destination,
            receipt,
            source_payload,
            target_payload,
            _fixture_expectations(source_payload, target_payload, len(rows)),
        )

    def test_migration_changes_only_header_and_preserves_crlf_payload(self):
        fixture = self._fixture([
            "a,b,c,1,2,3,10,20,30",
            "d,e,f,4,5,6,40,50,60",
        ])
        source, destination, receipt_path, source_bytes, target_bytes, expectations = fixture
        receipt = _migrate_triplet_angles(
            source,
            destination,
            receipt_path=receipt_path,
            expectations=expectations,
        )

        self.assertEqual(target_bytes, destination.read_bytes())
        self.assertEqual(source_bytes.split(b"\r\n", 1)[1], target_bytes.split(b"\r\n", 1)[1])
        self.assertEqual(expectations.target_sha256, receipt["output"]["sha256"])
        self.assertEqual(receipt, json.loads(receipt_path.read_text(encoding="utf-8")))

    def test_migration_refuses_same_path_and_existing_outputs(self):
        source, destination, receipt, _, _, expectations = self._fixture([
            "a,b,c,1,2,3,10,20,30"
        ])
        with self.assertRaisesRegex(ValueError, "different paths"):
            _migrate_triplet_angles(
                source,
                source,
                receipt_path=receipt,
                expectations=expectations,
            )

        destination.write_bytes(b"keep")
        with self.assertRaisesRegex(FileExistsError, "existing output"):
            _migrate_triplet_angles(
                source,
                destination,
                receipt_path=receipt,
                expectations=expectations,
            )
        self.assertEqual(b"keep", destination.read_bytes())

    def test_migration_refuses_wrong_digest_and_already_canonical_input(self):
        source, destination, receipt, source_bytes, target_bytes, expectations = self._fixture([
            "a,b,c,1,2,3,10,20,30"
        ])
        wrong_digest = MigrationExpectations(
            source_sha256="0" * 64,
            target_sha256=expectations.target_sha256,
            row_count=expectations.row_count,
            source_bytes=expectations.source_bytes,
            target_bytes=expectations.target_bytes,
        )
        with self.assertRaisesRegex(ValueError, "source SHA-256"):
            _migrate_triplet_angles(
                source,
                destination,
                receipt_path=receipt,
                expectations=wrong_digest,
            )

        source.write_bytes(target_bytes)
        canonical_expectations = _fixture_expectations(target_bytes, target_bytes, 1)
        with self.assertRaisesRegex(ValueError, "already canonical"):
            _migrate_triplet_angles(
                source,
                destination,
                receipt_path=receipt,
                expectations=canonical_expectations,
            )
        self.assertFalse(destination.exists())

    def test_migration_rejects_malformed_numeric_rows_and_counts(self):
        invalid_cases = {
            "field count": ("a,b,c,1,2,3,10,20", "has 8 fields"),
            "non-finite distance": ("a,b,c,nan,2,3,10,20,30", "non-finite distanceAB"),
            "non-finite angle": ("a,b,c,1,2,3,10,inf,30", "non-finite angleOAB"),
            "angle range": ("a,b,c,1,2,3,181,20,30", "outside 0..180"),
        }
        for name, (row, error) in invalid_cases.items():
            with self.subTest(name=name):
                case_folder = self.folder / name.replace(" ", "-")
                case_folder.mkdir()
                newline = b"\n"
                source_payload = (
                    ",".join(LEGACY_TRIPLET_COLUMNS).encode("utf-8")
                    + newline
                    + row.encode("utf-8")
                    + newline
                )
                target_payload = (
                    ",".join(TRIPLET_COLUMNS).encode("utf-8")
                    + newline
                    + row.encode("utf-8")
                    + newline
                )
                source = case_folder / "legacy.csv"
                destination = case_folder / "canonical.csv"
                source.write_bytes(source_payload)
                with self.assertRaisesRegex(ValueError, error):
                    _migrate_triplet_angles(
                        source,
                        destination,
                        receipt_path=case_folder / "receipt.json",
                        expectations=_fixture_expectations(source_payload, target_payload, 1),
                    )
                self.assertFalse(destination.exists())

        source, destination, receipt, source_bytes, target_bytes, expectations = self._fixture([
            "a,b,c,1,2,3,10,20,30"
        ])
        wrong_count = MigrationExpectations(
            source_sha256=expectations.source_sha256,
            target_sha256=expectations.target_sha256,
            row_count=2,
            source_bytes=len(source_bytes),
            target_bytes=len(target_bytes),
        )
        with self.assertRaisesRegex(ValueError, "has 1 data rows; expected 2"):
            _migrate_triplet_angles(
                source,
                destination,
                receipt_path=receipt,
                expectations=wrong_count,
            )

    def test_target_digest_mismatch_removes_unverified_output(self):
        source, destination, receipt, _, _, expectations = self._fixture([
            "a,b,c,1,2,3,10,20,30"
        ])
        wrong_target = MigrationExpectations(
            source_sha256=expectations.source_sha256,
            target_sha256="0" * 64,
            row_count=expectations.row_count,
            source_bytes=expectations.source_bytes,
            target_bytes=expectations.target_bytes,
        )
        with self.assertRaisesRegex(ValueError, "target SHA-256"):
            _migrate_triplet_angles(
                source,
                destination,
                receipt_path=receipt,
                expectations=wrong_target,
            )
        self.assertFalse(destination.exists())
        self.assertFalse(receipt.exists())

    def test_reviewed_real_table_produces_exact_target_when_available(self):
        source = ROOT / "data" / "triplets.csv"
        if not source.exists():
            self.skipTest("ignored runtime triplet table is not hydrated")
        self.assertEqual(REAL_FILE_BYTES, source.stat().st_size)
        self.assertEqual(REAL_SOURCE_SHA256, hashlib.sha256(source.read_bytes()).hexdigest())

        destination = self.folder / "triplets.csv"
        receipt_path = self.folder / "triplets.csv.migration.json"
        receipt = migrate_triplet_angles(source, destination, receipt_path=receipt_path)
        self.assertEqual(REAL_FILE_BYTES, destination.stat().st_size)
        self.assertEqual(REAL_ROW_COUNT, receipt["dataRows"])
        self.assertEqual(REAL_TARGET_SHA256, hashlib.sha256(destination.read_bytes()).hexdigest())
        self.assertTrue(receipt_path.is_file())


if __name__ == "__main__":
    unittest.main()
