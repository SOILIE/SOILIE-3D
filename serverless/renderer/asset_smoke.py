"""Blender-side import smoke test for every asset SOILIE-3D can select."""
from __future__ import annotations
import csv
import json
import os
import sys
from pathlib import Path


def registered_assets(rotation_rows, manifest):
    """Require the rotation registry to match the independently tracked inventory."""
    if manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('Invalid runtime asset manifest')
    declared = [row.get('path') for row in manifest['files'] if isinstance(row, dict)]
    paths = [path for path in declared if isinstance(path, str)
             and path.startswith('assets/') and path.lower().endswith('.obj')]
    if len(paths) != 191 or len(set(paths)) != 191:
        raise ValueError('Runtime asset manifest must contain exactly 191 distinct OBJ paths')
    if any(Path(path).name != path.removeprefix('assets/') for path in paths):
        raise ValueError('Runtime asset manifest contains a noncanonical OBJ path')
    selected = {row['asset_name'] for row in rotation_rows}
    expected = {Path(path).name for path in paths}
    if selected != expected:
        raise ValueError(f'Rotation registry differs from asset manifest: missing={sorted(expected-selected)}, extra={sorted(selected-expected)}')
    return sorted(selected)


def clear_imported_data(bpy):
    """Release imports between assets so a 191-file smoke does not retain them."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(collection):
            if item.users == 0 and getattr(item, 'source', None) != 'VIEWER':
                collection.remove(item)


def main() -> None:
    import bpy
    separator = sys.argv.index('--')
    runtime = Path(sys.argv[separator + 1])
    sys.path.insert(0, str(runtime / 'modules'))
    from blender_names import blender_copy_index, blender_source_name
    from render import load_rotations

    if (blender_copy_index('cupboard_0002') != 0
            or blender_copy_index('cupboard_0002.001') != 1
            or blender_source_name('cupboard_0002.001') != 'cupboard_0002'):
        raise RuntimeError('Blender duplicate-object suffix selection is unavailable')
    with (runtime / 'assets' / 'asset_rotations.csv').open(newline='', encoding='utf-8-sig') as source:
        rotation_rows = list(csv.DictReader(source))
    # This path resolves beside the helper in both a checkout and /var/task.
    manifest_path = Path(__file__).resolve().parents[1] / 'runtime-assets.json'
    if not manifest_path.is_file():
        manifest_path = Path('/var/task/serverless/runtime-assets.json')
    assets = registered_assets(rotation_rows, json.loads(manifest_path.read_text(encoding='utf-8')))
    previous = Path.cwd()
    try:
        os.chdir(runtime)
        rotations = load_rotations()
    finally:
        os.chdir(previous)
    missing_rotations = [row['asset_name'] for row in rotation_rows
                         if row['asset_name'] not in rotations.get(row['object_name'].lower(), {})]
    if missing_rotations:
        raise RuntimeError('SOILIE-3D rotation lookup cannot resolve: ' + ', '.join(missing_rotations))
    imported = []
    for index, name in enumerate(assets, 1):
        clear_imported_data(bpy)
        path = runtime / 'assets' / name
        if not path.exists():
            raise RuntimeError(f'{name}: asset listed by SOILIE-3D is missing')
        print(f'Asset smoke {index}/{len(assets)}: {name}', flush=True)
        bpy.ops.import_scene.obj(filepath=str(path), use_split_objects=False,
                                 use_split_groups=False, axis_forward='-Y', axis_up='Z')
        if not any(obj.type == 'MESH' for obj in bpy.context.scene.objects):
            raise RuntimeError(f'{name}: no mesh imported')
        imported.append(name)
    clear_imported_data(bpy)
    print(json.dumps({'assetSmoke': 'passed', 'count': len(imported)}), flush=True)


if __name__ == '__main__':
    main()
