"""Record the checked-out V4 runtime and compile an API-only index.

The SQLite file is used for catalog and pre-queue validation only. It is never
used to select, place, or render a scene. The renderer image receives the real
V4 data, every eligible OBJ, the original modules, and suggested_setup.blend.
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

from serverless.common.engine import ARCHITECTURE, ROOM_TYPES, normalize_label, public_catalog_document


ASSET_PATTERN = re.compile(r"_\d{4}(?:_\d+)?$")
V4_DATA_FILES = (
    "object_colors.csv",
    "object_sizes_manual.csv",
    "triplets.csv",
    "working-combos-refined.csv",
    "working-combos-bedroom.csv",
    "working-combos-livingroom.csv",
    "working-combos-kitchen.csv",
    "working-combos-bathroom.csv",
)
PRESET_FILES = {
    "random": "working-combos-refined.csv",
    "bedroom": "working-combos-bedroom.csv",
    "living_room": "working-combos-livingroom.csv",
    "kitchen": "working-combos-kitchen.csv",
    "bathroom": "working-combos-bathroom.csv",
}


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


def record_v4_provenance(repository: Path, output: Path) -> dict:
    """Checksum the source used directly by local runs and container builds."""

    version = model_version(repository)
    provenance = {
        "schemaVersion": 2,
        "model": "SOILIE-3D V4",
        "version": version,
        "legacySourceVersion": "24.07.05",
        "baselineCommit": _source_commit(repository),
        "channel": subprocess.run(
            ["git", "branch", "--show-current"], cwd=repository,
            capture_output=True, text=True, check=True,
        ).stdout.strip() or "detached",
        "execution": "repository modules.prepare_data + repository modules.render",
        "extensionBoundary": "optional room fitting runs after V4 placement and window adjustment",
        "files": {},
    }

    sources = [repository / "suggested_setup.blend", repository / "assets" / "asset_rotations.csv"]
    sources.extend(repository / "assets" / path.name for path in sorted((repository / "assets").glob("*.obj")))
    sources.extend(repository / "data" / name for name in V4_DATA_FILES)
    sources.extend(sorted((repository / "modules").glob("*.py")))

    for source in sources:
        if not source.exists():
            raise RuntimeError(f"Required V4 runtime file is missing: {source}")
        relative = source.relative_to(repository)
        provenance["files"][relative.as_posix()] = {
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
        }
    provenance["extensions"] = {
        "modules/room_fit.py": sha256(repository / "modules" / "room_fit.py"),
        "renderHook": "after adjust_windows_to_walls and before add_light_to_lamps_and_windows",
        "optionalRoomFitChangesInteriorPlacement": False,
        "collisionRecoveryChangesOnlyRepeatedStates": True,
        "collisionRecovery": "deterministic whole-scene recovery after a repeated pairwise geometry state",
    }
    provenance["assetCount"] = len(list((repository / "assets").glob("*.obj")))
    provenance["totalBytes"] = sum(entry["bytes"] for entry in provenance["files"].values())
    (output / "v4-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
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


def _usable_row(row: dict[str, str], allowed: set[str]) -> list[str]:
    values = [value.strip() for value in row.values() if value and value.strip()]
    # V4 performs case-sensitive dictionary and asset matching. Do not quietly
    # rewrite rows such as the legacy uppercase TV entries.
    if any(value != normalize_label(value) or value not in allowed for value in values):
        return []
    return values


def compile_api_index(repository: Path, output: Path) -> dict[str, int]:
    data_root = repository / "data"
    sizes = load_sizes(data_root / "object_sizes_manual.csv")
    asset_classes = load_exact_asset_classes(repository / "assets")
    allowed = set(sizes) & asset_classes

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
          objects_json TEXT NOT NULL,
          PRIMARY KEY (preset, row_index)
        );
        CREATE TABLE preset_members (
          preset TEXT NOT NULL,
          object_name TEXT NOT NULL,
          PRIMARY KEY (preset, object_name)
        );
        CREATE INDEX working_count_index ON working_combinations(object_count);
        """
    )
    connection.executemany(
        "INSERT INTO objects VALUES (?, ?, ?, ?)",
        [
            (name, name.replace("_", " ").title(), sizes[name], int(name not in ARCHITECTURE))
            for name in sorted(allowed)
        ],
    )

    triplet_count = 0
    with (data_root / "triplets.csv").open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            raw = [row[key].strip() for key in ("objectA", "objectB", "objectC")]
            normalized = [normalize_label(value) for value in raw]
            if raw != normalized or any(value not in allowed for value in normalized):
                continue
            before = connection.total_changes
            connection.execute("INSERT OR IGNORE INTO triplets VALUES (?, ?, ?)", normalized)
            triplet_count += connection.total_changes - before

    combination_count = 0
    preset_members = {room: set() for room in ROOM_TYPES}
    for preset, filename in PRESET_FILES.items():
        with (data_root / filename).open(newline="", encoding="utf-8-sig") as source:
            for row_index, row in enumerate(csv.DictReader(source)):
                values = _usable_row(row, allowed)
                if len(values) < 3:
                    continue
                connection.execute(
                    "INSERT INTO working_combinations VALUES (?, ?, ?, ?)",
                    (preset, row_index, len(values), json.dumps(values, separators=(",", ":"))),
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
    legacy_runtime = output / "v4"
    if legacy_runtime.exists():
        raise RuntimeError(
            f"Legacy copied model source exists at {legacy_runtime}. Remove that generated "
            "directory and pass the checked-out repository itself to --runtime."
        )
    provenance = record_v4_provenance(repository, output)
    counts = compile_api_index(repository, output)
    checksums = {
        "schemaVersion": 2,
        "model": f"SOILIE-3D V4 {model_version(repository)}",
        "v4Provenance": sha256(output / "v4-provenance.json"),
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
