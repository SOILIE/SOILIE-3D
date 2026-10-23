"""Record the checked-out SOILIE-3D runtime and compile an API-only index.

The SQLite file is used for catalog and pre-queue validation only. It is never
used to select, place, or render a scene. The renderer image receives the real
model data, every eligible OBJ, the original modules, and suggested_setup.blend.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

from modules.triplet_schema import validate_triplet_columns
from modules.working_combo_schema import (
    CATALOG_FILENAMES,
    MAX_OBJECT_COUNT,
    MIN_OBJECT_COUNT,
    WorkingCombinationError,
    iter_working_combinations,
)
from serverless.common.engine import ARCHITECTURE, ROOM_TYPES, normalize_label, public_catalog_document


ASSET_PATTERN = re.compile(r"_\d{4}(?:_\d+)?$")
MODEL_DATA_FILES = (
    "object_colors.csv",
    "object_sizes_manual.csv",
    "triplets.csv",
    "working-combos-refined.csv",
    "working-combos-bedroom.csv",
    "working-combos-livingroom.csv",
    "working-combos-kitchen.csv",
    "working-combos-bathroom.csv",
)
PRESET_FILES = CATALOG_FILENAMES


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_class(path: Path) -> str:
    return ASSET_PATTERN.sub("", path.stem)


def _source_commit(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def model_version(repository: Path) -> str:
    document = json.loads((repository / "package.json").read_text(encoding="utf-8"))
    version = document.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("package.json must contain a semantic SOILIE-3D version")
    return version


def record_model_provenance(repository: Path, output: Path) -> dict:
    """Checksum the source used directly by local runs and container builds."""

    version = model_version(repository)
    provenance = {
        "schemaVersion": 2,
        "model": "SOILIE-3D",
        "version": version,
        "legacySourceVersion": "24.07.05",
        "baselineCommit": _source_commit(repository),
        "channel": subprocess.run(
            ["git", "branch", "--show-current"], cwd=repository,
            capture_output=True, text=True, check=True,
        ).stdout.strip() or "detached",
        "execution": "repository modules.prepare_data + repository modules.render",
        "extensionBoundary": "optional room fitting runs after model placement and window adjustment",
        "files": {},
    }

    sources = [repository / "suggested_setup.blend", repository / "assets" / "asset_rotations.csv"]
    sources.extend(repository / "assets" / path.name for path in sorted((repository / "assets").glob("*.obj")))
    sources.extend(repository / "data" / name for name in MODEL_DATA_FILES)
    sources.extend(sorted((repository / "modules").glob("*.py")))

    for source in sources:
        if not source.exists():
            raise RuntimeError(f"Required SOILIE-3D runtime file is missing: {source}")
        relative = source.relative_to(repository)
        provenance["files"][relative.as_posix()] = {
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
        }
    provenance["extensions"] = {
        "serverless/renderer/room_fit.py": sha256(repository / "serverless" / "renderer" / "room_fit.py"),
        "renderHook": "after adjust_windows_to_walls and before add_light_to_lamps_and_windows",
        "optionalRoomFitChangesInteriorPlacement": False,
        "collisionRecoveryChangesOnlyRepeatedStates": True,
        "collisionRecovery": "deterministic whole-scene recovery after a repeated pairwise geometry state",
        "supportSettlement": "deterministic vertical mesh contact after overlap correction and final floor containment",
        "supportSettlementToleranceM": 1e-5,
    }
    provenance["assetCount"] = len(list((repository / "assets").glob("*.obj")))
    provenance["totalBytes"] = sum(entry["bytes"] for entry in provenance["files"].values())
    (output / "model-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return provenance


def load_sizes(path: Path) -> dict[str, str]:
    sizes: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            sizes[normalize_label(row["object"])] = row["sizecat"].strip().lower()
    return sizes


def load_exact_asset_classes(asset_root: Path) -> set[str]:
    rotation_assets: set[str] = set()
    with (asset_root / "asset_rotations.csv").open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            rotation_assets.add(row["asset_name"].strip())
    return {
        normalize_label(object_class(path))
        for path in asset_root.glob("*.obj")
        if path.name in rotation_assets
    }


def iter_canonical_triplet_rows(path: Path):
    """Yield relation rows only after validating the complete canonical header."""

    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        validate_triplet_columns(reader.fieldnames or (), source=str(path))
        yield from reader


def validated_working_rows(
    path: Path,
    *,
    allowed: set[str],
    triplets: set[tuple[str, str, str]],
) -> list[tuple[str, ...]]:
    """Load one strict catalog, rejecting every unsafe row instead of filtering."""

    rows: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    try:
        combinations = iter_working_combinations(path)
        for row_index, values in enumerate(combinations, start=2):
            unavailable = sorted(set(values) - allowed)
            if unavailable:
                raise RuntimeError(
                    f"{path} row {row_index} contains non-renderable objects: "
                    f"{', '.join(unavailable)}"
                )
            missing = [
                (values[0], values[1], object_c)
                for object_c in values[2:]
                if (values[0], values[1], object_c) not in triplets
            ]
            if missing:
                encoded = ", ".join("/".join(item) for item in missing)
                raise RuntimeError(
                    f"{path} row {row_index} lacks its ordered A/B triplet basis: {encoded}"
                )
            if values in seen:
                raise RuntimeError(
                    f"{path} row {row_index} duplicates an earlier canonical row"
                )
            seen.add(values)
            rows.append(values)
    except WorkingCombinationError as error:
        raise RuntimeError(str(error)) from error
    return rows


def compile_api_index(repository: Path, output: Path) -> dict[str, int]:
    data_root = repository / "data"
    sizes = load_sizes(data_root / "object_sizes_manual.csv")
    asset_classes = load_exact_asset_classes(repository / "assets")
    allowed = set(sizes) & asset_classes

    triplets: set[tuple[str, str, str]] = set()
    for row in iter_canonical_triplet_rows(data_root / "triplets.csv"):
        raw = tuple(row[key] for key in ("objectA", "objectB", "objectC"))
        normalized = tuple(normalize_label(value) for value in raw)
        if raw != normalized or any(value not in allowed for value in normalized):
            continue
        triplets.add(normalized)

    catalog_rows = {
        preset: validated_working_rows(
            data_root / filename,
            allowed=allowed,
            triplets=triplets,
        )
        for preset, filename in PRESET_FILES.items()
    }
    random_counts = {
        len(row)
        for row in catalog_rows["random"]
        if len(set(row)) == len(row)
    }
    missing_random_counts = sorted(
        set(range(MIN_OBJECT_COUNT, MAX_OBJECT_COUNT + 1)) - random_counts
    )
    if missing_random_counts:
        raise RuntimeError(
            "random working-combination catalog has no duplicate-free canonical rows for object "
            f"counts: {', '.join(map(str, missing_random_counts))}"
        )

    database_path = output / "relations.sqlite3"
    if database_path.exists():
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE objects (
          name TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          size_category TEXT NOT NULL,
          selectable INTEGER NOT NULL
        );
        CREATE TABLE triplets (
          object_a TEXT NOT NULL,
          object_b TEXT NOT NULL,
          object_c TEXT NOT NULL,
          PRIMARY KEY (object_a, object_b, object_c)
        ) WITHOUT ROWID;
        CREATE TABLE working_combinations (
          preset TEXT NOT NULL,
          row_index INTEGER NOT NULL,
          object_count INTEGER NOT NULL,
          has_duplicates INTEGER NOT NULL CHECK (has_duplicates IN (0, 1)),
          objects_json TEXT NOT NULL,
          PRIMARY KEY (preset, row_index)
        );
        CREATE TABLE preset_members (
          preset TEXT NOT NULL,
          object_name TEXT NOT NULL,
          PRIMARY KEY (preset, object_name)
        );
        CREATE INDEX working_capacity_index
          ON working_combinations(preset, object_count, has_duplicates);
        """
    )
    connection.executemany(
        "INSERT INTO objects VALUES (?, ?, ?, ?)",
        [
            (name, name.replace("_", " ").title(), sizes[name], int(name not in ARCHITECTURE))
            for name in sorted(allowed)
        ],
    )

    connection.executemany(
        "INSERT INTO triplets VALUES (?, ?, ?)",
        sorted(triplets),
    )
    triplet_count = len(triplets)

    combination_count = 0
    preset_members = {room: set() for room in ROOM_TYPES}
    for preset, rows in catalog_rows.items():
        for row_index, values in enumerate(rows):
            has_duplicates = int(len(set(values)) != len(values))
            connection.execute(
                "INSERT INTO working_combinations VALUES (?, ?, ?, ?, ?)",
                (
                    preset,
                    row_index,
                    len(values),
                    has_duplicates,
                    json.dumps(values, separators=(",", ":")),
                ),
            )
            combination_count += 1
            if preset in preset_members:
                preset_members[preset].update(values)

    for preset, members in preset_members.items():
        connection.executemany(
            "INSERT INTO preset_members VALUES (?, ?)",
            [(preset, name) for name in sorted(members)],
        )
    connection.commit()
    connection.execute("VACUUM")
    catalog_document = public_catalog_document(connection)
    (output / "catalog.json").write_text(json.dumps(catalog_document, indent=2) + "\n", encoding="utf-8")
    connection.close()
    return {"objects": len(allowed), "triplets": triplet_count, "combinations": combination_count}


def build(repository: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    copied_runtime = output / "soilie-runtime"
    if copied_runtime.exists():
        raise RuntimeError(
            f"Copied model source exists at {copied_runtime}. Remove that generated "
            "directory and pass the checked-out repository itself to --runtime."
        )
    provenance = record_model_provenance(repository, output)
    counts = compile_api_index(repository, output)
    checksums = {
        "schemaVersion": 2,
        "model": "SOILIE-3D",
        "modelVersion": model_version(repository),
        "modelProvenance": sha256(output / "model-provenance.json"),
        "apiIndex": sha256(output / "relations.sqlite3"),
        **counts,
        "assets": provenance["assetCount"],
    }
    (output / "checksums.json").write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**counts, "assets": provenance["assetCount"], "output": str(output)}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path(".codex/runtime"))
    args = parser.parse_args()
    build(args.repository.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
