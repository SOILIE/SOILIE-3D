"""Exact solid-mesh overlap observations for completed Blender scenes.

An object's evaluated triangle mesh can define a volume only when it is closed
and manifold.  The observer therefore uses bounding boxes solely as a safe
broad phase: disjoint boxes prove disjoint meshes, while intersecting boxes are
resolved with Blender's exact Boolean solver only when both meshes are valid
solids.  Invalid source topology is reported as unavailable, never as zero.
"""
from itertools import combinations
import math

import bmesh
import bpy
from mathutils import Vector


METHOD = "evaluated-solid-mesh-boolean-v1"
EPSILON_M3 = 1e-10


def _bounds(obj):
    points = [obj.matrix_world @ Vector(point) for point in obj.bound_box]
    return tuple(min(point[axis] for point in points) for axis in range(3)), tuple(
        max(point[axis] for point in points) for axis in range(3)
    )


def _boxes_overlap(first, second):
    first_low, first_high = _bounds(first)
    second_low, second_high = _bounds(second)
    return all(min(first_high[axis], second_high[axis]) > max(first_low[axis], second_low[axis])
               for axis in range(3))


def _world_mesh(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
    mesh.transform(evaluated.matrix_world)
    mesh.update()
    return mesh


def _solid_volume(mesh):
    """Return volume for a closed manifold mesh, otherwise a specific reason."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if not bm.faces:
            return None, "mesh has no faces"
        non_manifold = sum(1 for edge in bm.edges if not edge.is_manifold)
        if non_manifold:
            return None, f"mesh has {non_manifold} non-manifold edges"
        # Work on this private mesh copy so normal repair cannot touch a model
        # object. Consistent winding is needed for an enclosed-volume integral.
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        volume = abs(float(bm.calc_volume(signed=True)))
        if not math.isfinite(volume) or volume <= EPSILON_M3:
            return None, "mesh has no positive finite enclosed volume"
        return volume, None
    finally:
        bm.free()


def _boolean_intersection_volume(first, second):
    """Intersect two world-space mesh copies without changing model objects."""
    collection = bpy.context.scene.collection
    result_obj = bpy.data.objects.new("__benchmark_intersection", first.copy())
    other_obj = bpy.data.objects.new("__benchmark_operand", second.copy())
    collection.objects.link(result_obj)
    collection.objects.link(other_obj)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        result_obj.select_set(True)
        bpy.context.view_layer.objects.active = result_obj
        modifier = result_obj.modifiers.new(name="Exact solid intersection", type="BOOLEAN")
        modifier.operation = "INTERSECT"
        modifier.solver = "EXACT"
        modifier.object = other_obj
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        if not result_obj.data.polygons:
            return 0.0, None
        volume, reason = _solid_volume(result_obj.data)
        if volume is None:
            return None, "Boolean intersection did not produce a valid closed solid: " + reason
        return volume, None
    except RuntimeError as error:
        return None, "Blender exact Boolean failed: " + str(error)
    finally:
        result_mesh, other_mesh = result_obj.data, other_obj.data
        bpy.data.objects.remove(result_obj, do_unlink=True)
        bpy.data.objects.remove(other_obj, do_unlink=True)
        for mesh in (result_mesh, other_mesh):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)


def measure(objects):
    """Measure pairwise occupied-volume intrusion for semantic furniture objects."""
    rows = list(objects)
    if len({identifier for identifier, _ in rows}) != len(rows):
        raise ValueError("Solid-overlap object IDs must be unique")
    worst = {identifier: 0.0 for identifier, _ in rows}
    overlaps, unavailable = [], []
    broad_phase_zeros = 0
    meshes = {}
    volumes = {}
    try:
        for (first_id, first_obj), (second_id, second_obj) in combinations(rows, 2):
            if not _boxes_overlap(first_obj, second_obj):
                broad_phase_zeros += 1
                continue
            pair = {"a": first_id, "b": second_id}
            for identifier, obj in ((first_id, first_obj), (second_id, second_obj)):
                if identifier not in meshes:
                    meshes[identifier] = _world_mesh(obj)
                    volumes[identifier] = _solid_volume(meshes[identifier])
            reasons = [f"{identifier}: {volumes[identifier][1]}" for identifier in (first_id, second_id)
                       if volumes[identifier][0] is None]
            if reasons:
                unavailable.append({**pair, "reason": "; ".join(reasons)})
                continue
            intersection, reason = _boolean_intersection_volume(meshes[first_id], meshes[second_id])
            if intersection is None:
                unavailable.append({**pair, "reason": reason})
                continue
            ratios = [max(0.0, min(1.0, intersection / volumes[identifier][0]))
                      for identifier in (first_id, second_id)]
            worst[first_id] = max(worst[first_id], ratios[0])
            worst[second_id] = max(worst[second_id], ratios[1])
            if intersection > EPSILON_M3:
                overlaps.append({**pair, "intersectionM3": intersection, "fractions": ratios,
                                 "method": "Blender exact Boolean on closed evaluated meshes"})
    finally:
        for mesh in meshes.values():
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
    pair_count = len(rows) * (len(rows) - 1) // 2
    complete = not unavailable
    return {
        "method": METHOD,
        "objectCount": len(rows),
        "pairCount": pair_count,
        "broadPhaseDisjointPairs": broad_phase_zeros,
        "booleanPairs": pair_count - broad_phase_zeros - len(unavailable),
        "complete": complete,
        "meanWorstOverlapPct": (sum(worst.values()) / len(worst) * 100) if complete and worst else None,
        "maxOverlapPct": (max(worst.values()) * 100) if complete and worst else None,
        "objects": [{"id": identifier, "worstOverlapPct": worst[identifier] * 100} for identifier, _ in rows]
                   if complete else [],
        "overlapPairs": overlaps,
        "unavailablePairs": unavailable,
        "interpretation": (
            "Disjoint world-space bounds prove that the enclosed meshes are disjoint. "
            "Pairs whose bounds intersect require closed manifold meshes and Blender's exact Boolean solver."
        ),
    }
