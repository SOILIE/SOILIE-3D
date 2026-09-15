"""Blender-side import smoke test for every asset V4 can select."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import bpy


def main() -> None:
    separator = sys.argv.index("--")
    runtime = Path(sys.argv[separator + 1])
    sys.path.insert(0, str(runtime / "modules"))
    from blender_names import blender_copy_index, blender_source_name

    if (
        blender_copy_index("cupboard_0002") != 0
        or blender_copy_index("cupboard_0002.001") != 1
        or blender_source_name("cupboard_0002.001") != "cupboard_0002"
    ):
        raise RuntimeError("Blender duplicate-object suffix selection is unavailable")
    with (runtime / "assets" / "asset_rotations.csv").open(newline="", encoding="utf-8-sig") as source:
        assets = sorted({row["asset_name"] for row in csv.DictReader(source)})
    imported = []
    for name in assets:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        path = runtime / "assets" / name
        if not path.exists():
            raise RuntimeError(f"{name}: asset listed by V4 is missing")
        bpy.ops.import_scene.obj(
            filepath=str(path),
            use_split_objects=False,
            use_split_groups=False,
            axis_forward="-Y",
            axis_up="Z",
        )
        meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
        if not meshes:
            raise RuntimeError(f"{name}: no mesh imported")
        imported.append(name)
    print(json.dumps({"assetSmoke": "passed", "count": len(imported)}))


if __name__ == "__main__":
    main()
