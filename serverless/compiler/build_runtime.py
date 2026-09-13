"""Stage the complete original V4 runtime and compile an API-only index.

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
import shutil
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


def _reset_directory(path: Path) -> None:
    if path.exists():
        for child in path.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    path.mkdir(parents=True, exist_ok=True)


def _copy_exact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if source.stat().st_size != destination.stat().st_size or sha256(source) != sha256(destination):
        raise RuntimeError(f"Staged V4 file failed checksum verification: {source}")


def _source_commit(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "publication-2025"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _inject_room_extension(render_path: Path) -> None:
    """Build the permitted post-V4 hook without editing the original source."""

    source = render_path.read_text(encoding="utf-8")
    replacements = (
        (
            "import os, sys\n",
            "import os, sys\nimport re\n",
        ),
        (
            "    # Do a final adjustment of windows, blinds, and curtain to walls\n"
            "    adjust_windows_to_walls()\n\n"
            "    # Add light for any lamps, if present\n",
            "    # Do a final adjustment of windows, blinds, and curtain to walls\n"
            "    adjust_windows_to_walls()\n\n"
            "    # Optional web room sizing is a boundary-only post-process.\n"
            "    room_fit_result = None\n"
            "    if os.environ.get('SOILIE_ROOM_REQUEST'):\n"
            "        from room_fit import apply_serverless_room_fit\n"
            "        room_fit_result = apply_serverless_room_fit()\n\n"
            "    # Add light for any lamps, if present\n",
        ),
        (
            "        \"filename\":filename,\n"
            "        \"data\": output}\n"
            "    result_json = json.dumps(result_dict)\n",
            "        \"filename\":filename,\n"
            "        \"data\": output}\n"
            "    if room_fit_result is not None:\n"
            "        result_dict['room'] = room_fit_result\n"
            "    result_json = json.dumps(result_dict)\n",
        ),
        (
            "    ## Remove all objects from scene\n"
            "    for obj in bpy.context.scene.objects:\n"
            "        if obj==None:\n"
            "            continue\n"
            "        if obj.name.lower() not in {'camera', 'light'}:\n"
            "            obj.select_set(True)\n"
            "        else:\n"
            "            obj.select_set(False) # Deselect camera and light objects\n"
            "        bpy.ops.object.delete() # Delete all selected objects\n"
            "        # Remove all mesh data blocks\n"
            "        for mesh in bpy.data.meshes:\n"
            "            bpy.data.meshes.remove(mesh)\n"
            "        # Remove all material data blocks\n"
            "        for material in bpy.data.materials:\n"
            "            bpy.data.materials.remove(material)\n"
            "        bpy.context.view_layer.update()\n",
            "    ## Scene cleanup is unnecessary in a one-scene Lambda process.\n"
            "    # V4's original loop removes every mesh datablock during its first\n"
            "    # iteration, then fails while selecting an already-unlinked object\n"
            "    # before it can print the completed result JSON. Skipping that\n"
            "    # post-render teardown changes no placement, camera, render, data,\n"
            "    # or artifact; Blender releases the scene when this process exits.\n",
        ),
    )
    for original, replacement in replacements:
        if source.count(original) != 1:
            raise RuntimeError("The V4 renderer changed outside the reviewed room-fit extension boundary.")
        source = source.replace(original, replacement)
    render_path.write_text(source, encoding="utf-8", newline="")


def stage_v4(repository: Path, output: Path) -> dict:
    target = output / "v4"
    target.mkdir(parents=True, exist_ok=True)
    provenance = {
        "schemaVersion": 1,
        "model": "SOILIE-3D V4",
        "version": "24.07.05",
        "baselineCommit": _source_commit(repository),
        "execution": "original modules.prepare_data + original modules.render",
        "extensionBoundary": "room walls and floor are optionally redrawn after all V4 placement operations",
        "files": {},
    }

    sources = [repository / "suggested_setup.blend", repository / "assets" / "asset_rotations.csv"]
    sources.extend(repository / "assets" / path.name for path in sorted((repository / "assets").glob("*.obj")))
    sources.extend(repository / "data" / name for name in V4_DATA_FILES)
    sources.extend(sorted((repository / "modules").glob("*.py")))

    expected = set()
    for source in sources:
        if not source.exists():
            raise RuntimeError(f"Required V4 runtime file is missing: {source}")
        relative = source.relative_to(repository)
        expected.add(relative.as_posix())
        destination = target / relative
        if (
            not destination.exists()
            or destination.stat().st_size != source.stat().st_size
            or sha256(destination) != sha256(source)
        ):
            _copy_exact(source, destination)
        provenance["files"][relative.as_posix()] = {
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
        }

    staged_render = target / "modules" / "render.py"
    _inject_room_extension(staged_render)
    provenance["files"]["modules/render.py"]["stagedSha256"] = sha256(staged_render)
    provenance["extensions"] = {
        "modules/room_fit.py": sha256(target / "modules" / "room_fit.py"),
        "renderHook": "after adjust_windows_to_walls and before add_light_to_lamps_and_windows",
        "interiorPlacementChanged": False,
        "postRenderCleanupCompatibility": (
            "Skipped only after every V4 artifact and output row is complete; the original teardown raises on Blender 3.6 before JSON output."
        ),
    }

    for child in [path for path in target.rglob("*") if path.is_file()]:
        relative = child.relative_to(target).as_posix()
        if relative != "v4-provenance.json" and relative not in expected:
            child.unlink()

    provenance["assetCount"] = len(list((repository / "assets").glob("*.obj")))
    provenance["totalBytes"] = sum(entry["bytes"] for entry in provenance["files"].values())
    (target / "v4-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
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
    provenance = stage_v4(repository, output)
    counts = compile_api_index(repository, output)
    checksums = {
        "schemaVersion": 2,
        "model": "SOILIE-3D V4 24.07.05",
        "v4Provenance": sha256(output / "v4" / "v4-provenance.json"),
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
