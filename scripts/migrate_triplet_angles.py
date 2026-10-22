"""Guarded migration for the historical mislabeled triplet angle header."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from modules.triplet_schema import (  # noqa: E402
    ANGLE_FIELDS,
    CANONICAL_TRIPLET_FIELDS,
    DISTANCE_FIELDS,
    LEGACY_MISLABELED_TRIPLET_FIELDS,
    TripletSchema,
    detect_triplet_schema,
)


REAL_SOURCE_SHA256 = "8174680031fa71a2fddfb5a2162e9298cee574a159179777e26756dce3b0b7d1"
REAL_TARGET_SHA256 = "bed96f00a47d3cd78550c786f0291709ed93e26a151c791116d8f501af74403d"
REAL_ROW_COUNT = 608_676
REAL_FILE_BYTES = 80_377_244


@dataclass(frozen=True)
class MigrationExpectations:
    """Immutable validation gates; the CLI always uses REAL_EXPECTATIONS."""

    source_sha256: str
    target_sha256: str
    row_count: int
    source_bytes: int
    target_bytes: int


REAL_EXPECTATIONS = MigrationExpectations(
    source_sha256=REAL_SOURCE_SHA256,
    target_sha256=REAL_TARGET_SHA256,
    row_count=REAL_ROW_COUNT,
    source_bytes=REAL_FILE_BYTES,
    target_bytes=REAL_FILE_BYTES,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _split_header_line(line: bytes) -> tuple[bytes, bytes]:
    if line.endswith(b"\r\n"):
        return line[:-2], b"\r\n"
    if line.endswith(b"\n"):
        return line[:-1], b"\n"
    raise ValueError("triplet CSV header must end with LF or CRLF")


def _decode_header(line: bytes) -> tuple[tuple[str, ...], bytes]:
    content, line_ending = _split_header_line(line)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("triplet CSV header is not valid UTF-8") from exc
    return tuple(next(csv.reader([text]))), line_ending


def _validate_source_rows(source: Path, expected_rows: int) -> int:
    with source.open("rb") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        reader = csv.reader(text)
        header = next(reader, None)
        if header is None:
            raise ValueError("triplet CSV is empty")

        row_count = 0
        for line_number, row in enumerate(reader, start=2):
            if len(row) != len(CANONICAL_TRIPLET_FIELDS):
                raise ValueError(
                    f"triplet CSV row {line_number} has {len(row)} fields; expected 9"
                )

            numeric_values: dict[str, float] = {}
            for index, field in enumerate(CANONICAL_TRIPLET_FIELDS[3:], start=3):
                try:
                    value = float(row[index])
                except ValueError as exc:
                    raise ValueError(
                        f"triplet CSV row {line_number} has a non-numeric {field}"
                    ) from exc
                if not math.isfinite(value):
                    raise ValueError(
                        f"triplet CSV row {line_number} has a non-finite {field}"
                    )
                numeric_values[field] = value

            for field in DISTANCE_FIELDS:
                # Distances may include zero in degenerate historical samples,
                # but they must always be finite.
                _ = numeric_values[field]
            for field in ANGLE_FIELDS:
                if not 0.0 <= numeric_values[field] <= 180.0:
                    raise ValueError(
                        f"triplet CSV row {line_number} has {field} outside 0..180"
                    )
            row_count += 1

    if row_count != expected_rows:
        raise ValueError(
            f"triplet CSV has {row_count} data rows; expected {expected_rows}"
        )
    return row_count


def _receipt_payload(
    source: Path,
    destination: Path,
    expectations: MigrationExpectations,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "migration": "triplet-angle-header-canonicalization",
        "source": {
            "file": source.name,
            "bytes": expectations.source_bytes,
            "sha256": expectations.source_sha256,
            "header": list(LEGACY_MISLABELED_TRIPLET_FIELDS),
        },
        "output": {
            "file": destination.name,
            "bytes": expectations.target_bytes,
            "sha256": expectations.target_sha256,
            "header": list(CANONICAL_TRIPLET_FIELDS),
        },
        "dataRows": expectations.row_count,
        "transformation": "replace-header-only; preserve every subsequent byte",
    }


def _migrate_triplet_angles(
    source: Path,
    destination: Path,
    *,
    receipt_path: Path | None,
    expectations: MigrationExpectations,
) -> dict[str, Any]:
    """Migration implementation with injectable expectations for small tests."""

    source = source.resolve()
    destination = destination.resolve()
    receipt_path = (
        receipt_path.resolve()
        if receipt_path is not None
        else destination.with_name(f"{destination.name}.migration.json")
    )

    if source == destination:
        raise ValueError("source and destination must be different paths")
    if receipt_path == destination:
        raise ValueError("receipt and destination must be different paths")
    if destination.exists():
        raise FileExistsError(f"refusing to replace existing output: {destination}")
    if receipt_path.exists():
        raise FileExistsError(f"refusing to replace existing receipt: {receipt_path}")
    if not source.is_file():
        raise FileNotFoundError(f"triplet source does not exist: {source}")

    with source.open("rb") as handle:
        source_header_line = handle.readline()
    source_header, line_ending = _decode_header(source_header_line)
    schema = detect_triplet_schema(source_header)
    if schema is TripletSchema.CANONICAL:
        raise ValueError("triplet source is already canonical; refusing double migration")
    if schema is not TripletSchema.LEGACY_MISLABELED_ANGLES:
        raise ValueError(
            "triplet source has an unknown header; expected the exact legacy "
            "mislabeled angle header"
        )

    observed_bytes = source.stat().st_size
    if observed_bytes != expectations.source_bytes:
        raise ValueError(
            f"source byte count {observed_bytes} does not match reviewed input "
            f"{expectations.source_bytes}"
        )
    observed_source_sha = _sha256(source)
    if observed_source_sha != expectations.source_sha256:
        raise ValueError(
            f"source SHA-256 {observed_source_sha} does not match reviewed input "
            f"{expectations.source_sha256}"
        )

    _validate_source_rows(source, expectations.row_count)
    canonical_header = ",".join(CANONICAL_TRIPLET_FIELDS).encode("utf-8") + line_ending

    output_created = False
    receipt_created = False
    try:
        with source.open("rb") as source_handle:
            source_handle.readline()
            with destination.open("xb") as destination_handle:
                output_created = True
                destination_handle.write(canonical_header)
                shutil.copyfileobj(source_handle, destination_handle, length=1024 * 1024)

        observed_target_bytes = destination.stat().st_size
        if observed_target_bytes != expectations.target_bytes:
            raise ValueError(
                f"target byte count {observed_target_bytes} does not match reviewed "
                f"output {expectations.target_bytes}"
            )
        observed_target_sha = _sha256(destination)
        if observed_target_sha != expectations.target_sha256:
            raise ValueError(
                f"target SHA-256 {observed_target_sha} does not match reviewed output "
                f"{expectations.target_sha256}"
            )

        receipt = _receipt_payload(source, destination, expectations)
        with receipt_path.open("x", encoding="utf-8", newline="\n") as handle:
            receipt_created = True
            json.dump(receipt, handle, indent=2)
            handle.write("\n")
        return receipt
    except Exception:
        if receipt_created and receipt_path.exists():
            receipt_path.unlink()
        if output_created and destination.exists():
            destination.unlink()
        raise


def migrate_triplet_angles(
    source: Path, destination: Path, *, receipt_path: Path | None = None
) -> dict[str, Any]:
    """Migrate only the one reviewed production table to canonical labeling."""

    return _migrate_triplet_angles(
        source,
        destination,
        receipt_path=receipt_path,
        expectations=REAL_EXPECTATIONS,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replace the reviewed triplets.csv legacy angle header while preserving "
            "all data-row bytes."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--receipt",
        type=Path,
        help="receipt path (default: <destination>.migration.json)",
    )
    args = parser.parse_args(argv)
    receipt = migrate_triplet_angles(
        args.source, args.destination, receipt_path=args.receipt
    )
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
