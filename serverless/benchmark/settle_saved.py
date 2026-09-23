"""Reapply final support placement to saved scenes, without resampling V4.

Run in Blender. Source attempts stay immutable. Every derived record binds its
source checksum and the correction source; correction time is kept separate
from the original generation timing. No AI judgement is transferred to a
changed stimulus. Mesh restoration is rejected unless saved bounds agree.
"""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.blender_names import blender_source_name
from modules.support_settlement import (
    CONTACT_TOLERANCE_M, FIXED_CLASSES, evaluated_surface, settle_objects, surface_drop,
)
from modules import render
from serverless.benchmark.solid_overlap import measure


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for blocks in (bpy.data.materials, bpy.data.images):
        for block in list(blocks):
            if block.users == 0:
                blocks.remove(block)


def restore_object(row, rotations, *, last_imported=False):
    """Recover the corrected asset-local geometry, then its recorded transform.

V4 bakes asset-front rotations and some scales into vertices. Stored bounds in
local coordinates recover that baked uniform scale. Nonuniform scale or wrong
asset orientation fails the parity check rather than approximating the mesh.
"""
    asset = blender_source_name(row['asset'])
    bpy.ops.object.select_all(action='DESELECT')
    bpy.ops.import_scene.obj(filepath=str(ROOT/'assets'/f'{asset}.obj'), use_split_objects=False,
                            use_split_groups=False, axis_forward='-Y', axis_up='Z')
    imported = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    if len(imported) != 1:
        raise RuntimeError(f'Expected one mesh for {asset}, got {len(imported)}')
    obj = imported[0]
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.origin_set(type='ORIGIN_CENTER_OF_MASS', center='BOUNDS')
    obj.location = (0, 0, 0)
    imported_max_dimension = max(obj.dimensions)
    if last_imported:
        # V4's normalization operator acts on the last selected import, baking
        # its import-axis rotation before the asset-front Euler correction.
        # Adding those Euler angles directly is NOT the same operation. Cubic
        # bounds can hide the resulting wrong-facing mesh, so retain the order.
        obj.scale *= 1/imported_max_dimension
        bpy.ops.object.transform_apply(scale=True)
        bpy.ops.object.origin_set(type='ORIGIN_CENTER_OF_MASS', center='BOUNDS')
        obj.location = (0, 0, 0)
    import math
    angles = rotations[row['label']][asset+'.obj']
    for axis, angle in enumerate(angles):
        obj.rotation_euler[axis] += math.radians(angle)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.context.view_layer.update()
    target_matrix = Matrix(row['transform'])
    local = [target_matrix.inverted() @ Vector(point) for point in row['corners']]
    target_low = Vector(min(v[i] for v in local) for i in range(3))
    target_high = Vector(max(v[i] for v in local) for i in range(3))
    current_low = Vector(min(v.co[i] for v in obj.data.vertices) for i in range(3))
    current_high = Vector(max(v.co[i] for v in obj.data.vertices) for i in range(3))
    scales = [(target_high[i]-target_low[i])/(current_high[i]-current_low[i]) for i in range(3)
              if current_high[i]-current_low[i] > 1e-9]
    scale = sum(scales)/len(scales)
    if max(abs(value/scale-1) for value in scales) > 1e-4:
        raise RuntimeError('Saved mesh cannot be restored by uniform scaling: '+row['id'])
    offset = target_low-current_low*scale
    # Inverting a float32 world matrix introduces small errors in recovered
    # bounds. Preserve original vertices when those bounds identify the source
    # mesh's known baked scale; do not perturb flat feet to fit noisy corners.
    for original_scale in (1.0, 1.0/imported_max_dimension):
        if abs(scale/original_scale-1) <= 1e-5 and offset.length <= 1e-5:
            scale, offset = original_scale, Vector((0, 0, 0))
            break
    for vertex in obj.data.vertices:
        vertex.co = vertex.co*scale+offset
    obj.matrix_world = target_matrix
    obj.name = row['asset']
    bpy.context.view_layer.update()
    restored = [obj.matrix_world @ Vector(point) for point in obj.bound_box]
    error = max(min((a-Vector(b)).length for b in row['corners']) for a in restored)
    if error > 1e-5:
        raise RuntimeError(f'Mesh restoration bounds differ for {row["id"]}: {error}')
    return obj


def floor_contact(row, room):
    """Prove real vertex contact when world Z is a single local mesh axis.

A min/max along one local axis is attained by a vertex, unlike a corner of a
rotated 3D box. The finite floor must contain the full projected object box.
"""
    coefficients = row['transform'][2][:3]
    if sum(abs(value) > 1e-8 for value in coefficients) != 1:
        return False
    low = min(v[2] for v in row['corners'])
    xmin, xmax = min(p[0] for p in room['polygon']), max(p[0] for p in room['polygon'])
    ymin, ymax = min(p[1] for p in room['polygon']), max(p[1] for p in room['polygon'])
    return abs(low-room['floorZ']) <= CONTACT_TOLERANCE_M and all(
        xmin-1e-6 <= v[0] <= xmax+1e-6 and ymin-1e-6 <= v[1] <= ymax+1e-6 for v in row['corners'])


def correction(attempt, rotations, audit_original=False, observe_only=False):
    result = deepcopy(attempt)
    final = result['stages']['final']
    movable = [row for row in final['objects'] if row['label'] not in FIXED_CLASSES]
    needs_mesh = audit_original or observe_only or any(not floor_contact(row, final['room']) for row in movable)
    changes, audit = [], []
    elapsed = 0.0
    if needs_mesh:
        clear_scene()
        room = final['room']
        vertices = [(x, y, room['floorZ']) for x, y in room['polygon']]
        mesh = bpy.data.meshes.new('Floor')
        mesh.from_pydata(vertices, [], [tuple(range(len(vertices)))])
        floor = bpy.data.objects.new('Floor', mesh)
        bpy.context.collection.objects.link(floor)
        objects = {row['id']: restore_object(row, rotations, last_imported=index == len(final['objects'])-1)
                   for index, row in enumerate(final['objects'])}
        bpy.context.view_layer.update()
        if audit_original or observe_only:
            from serverless.benchmark.capture_v4 import support_samples
            all_objects = list(objects.values())+[floor]
            for row in movable:
                if row.get('support'):
                    observation = support_samples(objects[row['id']], all_objects, room['floorZ'])
                    expected = row['support']['gapM']
                    if expected is not None and (observation['gapM'] is None or abs(expected-observation['gapM']) > 1e-5):
                        raise RuntimeError(f'Restored support differs for {row["id"]}: saved {expected}, restored {observation["gapM"]}')
                    audit.append({'id': row['id'], 'savedGapM': expected, 'restoredGapM': observation['gapM']})
                if observe_only:
                    row['support'] = support_samples(objects[row['id']], all_objects, room['floorZ'])
            if observe_only:
                return result, {'moves': [], 'correctionSeconds': 0, 'restorationAudit': audit}
        before = time.perf_counter()
        changes = settle_objects({name: {'blender_obj': obj} for name, obj in objects.items()})
        elapsed = time.perf_counter()-before
        surfaces = {}
        floor_surface = evaluated_surface(floor)
        for row in movable:
            obj = objects[row['id']]
            row['corners'] = [list(obj.matrix_world @ Vector(point)) for point in obj.bound_box]
            row['transform'] = [list(line) for line in obj.matrix_world]
            if floor_contact(row, room):
                low = min(v[2] for v in row['corners'])
                row['support'] = {'source': 'mesh-extremum-floor-contact', 'samplingVersion': 3,
                                  'gapM': max(0, low-room['floorZ']), 'belowFloorM': max(0, room['floorZ']-low),
                                  'supportKind': 'floor', 'supportId': 'Floor'}
                continue
            if row['id'] not in surfaces:
                surfaces[row['id']] = evaluated_surface(obj)
            own = surfaces[row['id']]
            gap = surface_drop(own, floor_surface)
            support_kind, support_id = 'floor', 'Floor'
            for name, base in objects.items():
                if name != row['id']:
                    if name not in surfaces:
                        surfaces[name] = evaluated_surface(base)
                    surface = surfaces[name]
                    candidate = surface_drop(own, surface, gap)
                    if candidate < gap:
                        gap, support_kind, support_id = candidate, 'object', name
            if gap > CONTACT_TOLERANCE_M:
                raise RuntimeError('Unsupported final mesh: '+row['id'])
            row['support'] = {'source': 'mesh-vertical-contact', 'samplingVersion': 3,
                              'gapM': max(0.0, gap), 'belowFloorM': max(0.0, room['floorZ']-own['low'][2]),
                              'supportKind': support_kind, 'supportId': support_id}
        if changes:
            final['solidMeshOverlap'] = measure([(row['id'], objects[row['id']])
                for row in final['objects'] if row.get('kind') != 'architecture'])
            if not final['solidMeshOverlap']['complete'] or final['solidMeshOverlap']['maxOverlapPct'] > .0001:
                raise RuntimeError('Settlement produced unverified or intersecting meshes: '+json.dumps(final['solidMeshOverlap']))
    else:
        for row in movable:
            low = min(v[2] for v in row['corners'])
            row['support'] = {'source': 'mesh-extremum-floor-contact', 'samplingVersion': 3,
                              'gapM': max(0.0, low-final['room']['floorZ']),
                              'belowFloorM': max(0.0, final['room']['floorZ']-low),
                              'supportKind': 'floor', 'supportId': 'Floor'}
    return result, {'moves': changes, 'correctionSeconds': elapsed, 'restorationAudit': audit}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-original', action='store_true')
    parser.add_argument('--observe-only', action='store_true',
                        help='Add support surface identities without changing any placement or original measurement')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--start', type=int, default=0)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    if args.input.resolve() == args.output.resolve():
        raise ValueError('Derived evidence must not overwrite source attempts')
    args.output.mkdir(parents=True, exist_ok=True)
    from serverless.compiler.runtime_assets import verify
    manifest_path = ROOT/'serverless/runtime-assets.json'
    verify(ROOT, json.loads(manifest_path.read_text()))
    rotations = render.load_rotations()
    implementation = {'settlementSha256': checksum(ROOT/'modules/support_settlement.py'),
                      'replaySha256': checksum(Path(__file__)),
                      'samplerSha256': checksum(Path(__file__).with_name('mesh_support.py')),
                      'observerSha256': checksum(Path(__file__).with_name('capture_v4.py')),
                      'assetManifestSha256': checksum(manifest_path),
                      'observeOnly': args.observe_only}
    if args.observe_only:
        config = json.loads((args.input/'run.json').read_text())
        (args.output/'run.json').write_text(json.dumps(config, separators=(',', ':')), encoding='utf-8')
    paths = sorted(args.input.glob('attempt-*.json'))
    selected_paths = paths[args.start:args.start+args.limit] if args.limit else paths[args.start:]
    for path in selected_paths:
        target = args.output/path.name
        source_hash = checksum(path)
        if target.exists():
            prior = json.loads(target.read_text())['supportCorrection']
            if prior['sourceSha256'] != source_hash or prior['implementation'] != implementation:
                raise ValueError('Replay checkpoint differs from source or correction code')
            continue
        attempt = json.loads(path.read_text())
        if attempt['status'] != 'complete':
            raise ValueError('Support replay accepts completed attempts only')
        result, report = correction(attempt, rotations, args.audit_original, args.observe_only)
        result['supportCorrection'] = dict(report, sourceSha256=source_hash, implementation=implementation,
                                          originalGenerationSeconds=attempt['generationSeconds'])
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, separators=(',', ':')), encoding='utf-8')
        temporary.replace(target)
        print(json.dumps({'attempt': path.name, 'changedObjects': len(report['moves']),
                          'correctionSeconds': report['correctionSeconds']}), flush=True)
    clear_scene()


if __name__ == '__main__':
    main()
