"""Deterministically derive canonical 3-8 object working-combination catalogs."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
from itertools import combinations
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from modules.triplet_schema import validate_triplet_csv_header  # noqa: E402
from modules.working_combo_schema import (  # noqa: E402
    CATALOG_FILENAMES,
    MAX_OBJECT_COUNT,
    MIN_OBJECT_COUNT,
    WORKING_COMBINATION_FIELDS,
    normalize_object_label,
    padded_catalog_row,
)


_ASSET_SUFFIX = re.compile(r"_\d{4}(?:_\d+)?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
KNOWN_INVALID_TRIPLET_SHA256S = frozenset({
    # Original mislabeled header and the verified header-only migration. Both
    # retain relations collected before world-coordinate scene aggregation was
    # repaired, so neither is a scientific source for new runtime catalogs.
    "8174680031fa71a2fddfb5a2162e9298cee574a159179777e26756dce3b0b7d1",
    "bed96f00a47d3cd78550c786f0291709ed93e26a151c791116d8f501af74403d",
})

# These are the model's existing semantic preset boundaries, canonicalized to
# the exact lower-case labels used by asset lookup. Renderability is checked
# separately, so stale allowlist entries cannot enter a generated catalog.
PRESET_ALLOWLISTS = {
    "random": frozenset(
        "bathtub bed bin blinds book bookcase boots bowl cabinet cap cereal_box "
        "chair clock closet coffee_machine coffee_mug coffee_table computer_bag "
        "cupboard curtain desk dining_table fridge helmet lamp laptop microwave_oven "
        "night_stand office_chair pillow plant printer projector sink soda_can sofa "
        "sofa_chair speaker stand suitcase table telephone toilet towel trash_can "
        "trash_bin tv tv_stand window".split()
    ),
    "bedroom": frozenset(
        "bed bin blinds book bookcase boots bowl cabinet cap chair clock closet "
        "coffee_mug computer_bag cupboard curtain desk helmet lamp laptop night_stand "
        "office_chair pillow plant printer projector soda_can sofa_chair speaker stand "
        "suitcase table telephone trash_bin tv tv_stand window".split()
    ),
    "living_room": frozenset(
        "bin blinds book bookcase boots bowl cabinet cap cereal_box chair clock "
        "coffee_mug coffee_table computer_bag cupboard curtain desk dining_table "
        "helmet lamp laptop office_chair plant printer projector soda_can sofa "
        "sofa_chair speaker stand suitcase table telephone trash_bin tv tv_stand "
        "window".split()
    ),
    "kitchen": frozenset(
        "bin blinds cabinet cap cereal_box chair clock coffee_machine coffee_mug "
        "coffee_table cupboard curtain dining_table fridge lamp microwave_oven plant "
        "sink soda_can table telephone towel trash_can trash_bin window".split()
    ),
    "bathroom": frozenset(
        "bathtub bin cabinet lamp sink toilet towel trash_bin".split()
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_size_labels(path: Path) -> set[str]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != ("object", "diameter", "count", "sizecat"):
            raise ValueError(f"{path} has an unknown object-size header")
        labels = {
            row["object"].strip()
            for row in reader
            if row["object"].strip() == normalize_object_label(row["object"])
        }
    if not labels:
        raise ValueError(f"{path} contains no canonical object labels")
    return labels


def _load_renderable_assets(asset_root: Path) -> tuple[set[str], str, int]:
    rotations_path = asset_root / "asset_rotations.csv"
    with rotations_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != ("object_name", "asset_name", "x", "y", "z"):
            raise ValueError(f"{rotations_path} has an unknown asset-rotation header")
        rows = list(reader)

    labels: set[str] = set()
    used_files: set[Path] = set()
    for line_number, row in enumerate(rows, start=2):
        label = normalize_object_label(row["object_name"])
        asset_name = row["asset_name"].strip()
        if Path(asset_name).name != asset_name or Path(asset_name).suffix != ".obj":
            raise ValueError(
                f"{rotations_path} row {line_number} must register one flat .obj asset"
            )
        asset_path = asset_root / asset_name
        asset_class = normalize_object_label(
            _ASSET_SUFFIX.sub("", Path(asset_name).stem)
        )
        if asset_class != label:
            raise ValueError(
                f"{rotations_path} row {line_number} has inconsistent canonical asset labels"
            )
        if not asset_path.is_file():
            raise FileNotFoundError(f"registered asset is missing: {asset_path}")
        labels.add(label)
        used_files.add(asset_path)

    digest = hashlib.sha256()
    for path in sorted(used_files, key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256(path).encode("ascii"))
        digest.update(b"\n")
    return labels, digest.hexdigest(), len(used_files)


def _load_triplets(
    path: Path, renderable: set[str]
) -> tuple[set[tuple[str, str, str]], dict[str, int]]:
    validate_triplet_csv_header(path)
    retained: set[tuple[str, str, str]] = set()
    source_rows = 0
    eligible_rows = 0
    with path.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            source_rows += 1
            values = tuple(row[field] for field in ("objectA", "objectB", "objectC"))
            if any(value != normalize_object_label(value) for value in values):
                continue
            if not set(values) <= renderable:
                continue
            eligible_rows += 1
            retained.add(values)
    if not retained:
        raise ValueError(f"{path} contains no canonical renderable triplets")
    return retained, {
        "sourceRows": source_rows,
        "canonicalRenderableRows": eligible_rows,
        "uniqueRenderableTriplets": len(retained),
        "duplicateRenderableRowsCollapsed": eligible_rows - len(retained),
        "rejectedRows": source_rows - eligible_rows,
    }


def _preset_rows(
    triplets: set[tuple[str, str, str]], allowed: set[str]
):
    by_anchor: dict[tuple[str, str], set[str]] = defaultdict(set)
    for object_a, object_b, object_c in triplets:
        if object_a in allowed and object_b in allowed and object_c in allowed:
            by_anchor[(object_a, object_b)].add(object_c)

    for object_count in range(MIN_OBJECT_COUNT, MAX_OBJECT_COUNT + 1):
        tail_count = object_count - 2
        for (object_a, object_b), candidates in sorted(by_anchor.items()):
            for tail in combinations(sorted(candidates), tail_count):
                yield (object_a, object_b, *tail)


def _write_catalog(
    path: Path,
    triplets: set[tuple[str, str, str]],
    allowed: set[str],
) -> dict[str, Any]:
    rows_by_count = {
        str(count): 0 for count in range(MIN_OBJECT_COUNT, MAX_OBJECT_COUNT + 1)
    }
    duplicate_free_rows_by_count = dict.fromkeys(rows_by_count, 0)
    row_count = 0
    with path.open("x", newline="", encoding="utf-8") as destination:
        writer = csv.writer(destination, lineterminator="\n")
        writer.writerow(WORKING_COMBINATION_FIELDS)
        for objects in _preset_rows(triplets, allowed):
            writer.writerow(padded_catalog_row(objects))
            rows_by_count[str(len(objects))] += 1
            if len(set(objects)) == len(objects):
                duplicate_free_rows_by_count[str(len(objects))] += 1
            row_count += 1
    return {
        "rows": row_count,
        "rowsByObjectCount": rows_by_count,
        "duplicateFreeRowsByObjectCount": duplicate_free_rows_by_count,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def build_working_combo_catalogs(
    *,
    triplets_path: Path,
    sizes_path: Path,
    asset_root: Path,
    output_dir: Path,
    receipt_path: Path,
    expected_triplets_sha256: str,
) -> dict[str, Any]:
    """Build a separately staged, reproducible catalog set and receipt."""

    triplets_path = triplets_path.resolve()
    sizes_path = sizes_path.resolve()
    asset_root = asset_root.resolve()
    output_dir = output_dir.resolve()
    receipt_path = receipt_path.resolve()

    if not isinstance(expected_triplets_sha256, str):
        raise ValueError("expected triplet SHA-256 must be a string")
    expected_triplets_sha256 = expected_triplets_sha256.strip().lower()
    if not _SHA256.fullmatch(expected_triplets_sha256):
        raise ValueError("expected triplet SHA-256 must be exactly 64 hexadecimal characters")

    for source in (triplets_path, sizes_path, asset_root / "asset_rotations.csv"):
        if not source.is_file():
            raise FileNotFoundError(f"required catalog input is missing: {source}")
    if output_dir.exists():
        raise FileExistsError(f"refusing to replace existing output directory: {output_dir}")
    if receipt_path.exists():
        raise FileExistsError(f"refusing to replace existing receipt: {receipt_path}")
    if receipt_path.is_relative_to(output_dir):
        raise ValueError("receipt path must be separate from the generated catalog directory")

    observed_triplet_sha = sha256(triplets_path)
    if observed_triplet_sha in KNOWN_INVALID_TRIPLET_SHA256S:
        raise ValueError(
            "refusing known camera-frame-derived triplet data; regenerate relations "
            "from the raw RGB-D sources with the corrected collector and review its "
            "new SHA-256"
        )
    if observed_triplet_sha != expected_triplets_sha256:
        raise ValueError(
            f"triplet SHA-256 {observed_triplet_sha} does not match reviewed canonical "
            f"input {expected_triplets_sha256}"
        )

    size_labels = _load_size_labels(sizes_path)
    asset_labels, asset_digest, asset_count = _load_renderable_assets(asset_root)
    renderable = size_labels & asset_labels
    triplets, triplet_counts = _load_triplets(triplets_path, renderable)

    output_created = False
    receipt_created = False
    try:
        output_dir.mkdir(parents=True)
        output_created = True
        catalogs: dict[str, Any] = {}
        for preset, filename in CATALOG_FILENAMES.items():
            allowed = set(PRESET_ALLOWLISTS[preset]) & renderable
            catalogs[filename] = {
                "preset": preset,
                **_write_catalog(output_dir / filename, triplets, allowed),
            }
        random_counts = catalogs[CATALOG_FILENAMES["random"]][
            "duplicateFreeRowsByObjectCount"
        ]
        missing_random_counts = [
            count for count, rows in random_counts.items() if rows == 0
        ]
        if missing_random_counts:
            raise ValueError(
                "random catalog has no duplicate-free rows for object counts: "
                f"{', '.join(missing_random_counts)}"
            )

        receipt = {
            "schemaVersion": 1,
            "generation": "working-combination-catalogs-3-to-8",
            "header": list(WORKING_COMBINATION_FIELDS),
            "objectCount": {"minimum": MIN_OBJECT_COUNT, "maximum": MAX_OBJECT_COUNT},
            "inputs": {
                "triplets": {
                    "file": triplets_path.name,
                    "bytes": triplets_path.stat().st_size,
                    "sha256": observed_triplet_sha,
                    **triplet_counts,
                },
                "objectSizes": {
                    "file": sizes_path.name,
                    "bytes": sizes_path.stat().st_size,
                    "sha256": sha256(sizes_path),
                },
                "assets": {
                    "registeredFiles": asset_count,
                    "fileSetSha256": asset_digest,
                    "rotationsSha256": sha256(asset_root / "asset_rotations.csv"),
                },
            },
            "catalogs": catalogs,
            "transformation": (
                "filter canonical renderable labels per preset, group by ordered A/B, "
                "enumerate deterministic exact-size C combinations"
            ),
        }
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with receipt_path.open("x", encoding="utf-8", newline="\n") as destination:
            receipt_created = True
            json.dump(receipt, destination, indent=2, sort_keys=True)
            destination.write("\n")
        return receipt
    except Exception:
        if receipt_created and receipt_path.exists():
            receipt_path.unlink()
        if output_created and output_dir.exists():
            shutil.rmtree(output_dir)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build guarded canonical 3-8 object SOILIE working catalogs."
    )
    parser.add_argument("--triplets", type=Path, required=True)
    parser.add_argument("--sizes", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--expected-triplets-sha256",
        required=True,
        help="reviewed SHA-256 of the canonical, scientifically corrected triplet input",
    )
    args = parser.parse_args(argv)
    receipt = build_working_combo_catalogs(
        triplets_path=args.triplets,
        sizes_path=args.sizes,
        asset_root=args.assets,
        output_dir=args.output,
        receipt_path=args.receipt,
        expected_triplets_sha256=args.expected_triplets_sha256,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
