"""Evaluated-mesh overlap observations for completed Blender scenes.

World-space bounds are only a broad phase. Closed manifold meshes use Blender's
exact Boolean solver so an occupied-volume percentage can be calculated. Some
curated research assets are open or contain disconnected surfaces and therefore
do not define a mathematical solid. For those assets, an exact triangle-surface
test can still prove that the rendered geometry does not intersect. A pair is
incomplete only when non-solid surfaces actually cross, because no defensible
occupied-volume percentage exists in that case.
"""
from itertools import combinations
import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


METHOD = "evaluated-mesh-intersection-v2"
EPSILON_M3 = 1e-10
# The published validity tests already treat one part per million as numerical
# contact. Apply the same scale-free threshold before an asset-topology branch
# so a supported object resting microscopically inside a surface is not called
# a physical collision merely because floating-point transforms share triangles.
NUMERICAL_CONTACT_FRACTION = 1e-6


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


def _box_overlap_fractions(first, second):
    first_low, first_high = _bounds(first)
    second_low, second_high = _bounds(second)
    extents = [max(0.0, min(first_high[axis], second_high[axis])
                   - max(first_low[axis], second_low[axis])) for axis in range(3)]
    intersection = math.prod(extents)
    volumes = [math.prod(high[axis]-low[axis] for axis in range(3))
               for low, high in ((first_low, first_high), (second_low, second_high))]
    return [intersection/volume if volume > EPSILON_M3 else math.inf for volume in volumes]


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


def _surface_intersections(first, second):
    """Return intersecting triangle pairs for evaluated world-space meshes.

    BVHTree performs a triangle-level overlap test. It does not require closed
    topology, so a zero result proves that the actual rendered surfaces are
    disjoint even when either mesh cannot supply an enclosed volume.
    """
    def tree(mesh):
        vertices = [vertex.co.copy() for vertex in mesh.vertices]
        polygons = [tuple(polygon.vertices) for polygon in mesh.polygons]
        if not vertices or not polygons:
            return None
        return BVHTree.FromPolygons(vertices, polygons, all_triangles=False, epsilon=0.0)

    first_tree, second_tree = tree(first), tree(second)
    if first_tree is None or second_tree is None:
        return None
    return first_tree.overlap(second_tree)


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


def measure(objects, *, unchanged_source_bounds=None):
    """Measure pairwise occupied-volume intrusion for semantic furniture objects.

    Saved-scene replay may supply original world-space corners for objects that
    did not move. Two disjoint original boxes remain a separation proof for that
    unchanged pair, despite float32 error when restoring mesh vertices. Bounds
    may only be reused within the replay's validated reconstruction tolerance;
    moved objects must not be present in this mapping. Ordinary observations do
    not supply it and continue to measure their live geometry directly.
    """
    rows = list(objects)
    if len({identifier for identifier, _ in rows}) != len(rows):
        raise ValueError("Solid-overlap object IDs must be unique")
    worst = {identifier: 0.0 for identifier, _ in rows}
    overlaps, unavailable = [], []
    broad_phase_zeros = 0
    numerical_contact_pairs = 0
    boolean_pairs = 0
    surface_disjoint_pairs = 0
    preserved_disjoint_pairs = 0
    source_bounds = {}
    for identifier, obj in rows:
        if unchanged_source_bounds is not None and identifier in unchanged_source_bounds:
            points = unchanged_source_bounds[identifier]
            if len(points) != 8 or any(len(point) != 3 or not all(math.isfinite(v) for v in point) for point in points):
                raise ValueError('Invalid original mesh bounds: ' + identifier)
            saved = tuple(tuple(fn(point[axis] for point in points) for axis in range(3)) for fn in (min, max))
            observed = _bounds(obj)
            if max(abs(saved[side][axis]-observed[side][axis]) for side in (0, 1) for axis in range(3)) > 1e-5:
                raise ValueError('Original bounds cannot certify a moved or incorrectly restored object: ' + identifier)
            source_bounds[identifier] = saved
    meshes = {}
    volumes = {}
    try:
        for (first_id, first_obj), (second_id, second_obj) in combinations(rows, 2):
            if first_id in source_bounds and second_id in source_bounds:
                first, second = source_bounds[first_id], source_bounds[second_id]
                if any(min(first[1][axis], second[1][axis]) <= max(first[0][axis], second[0][axis]) for axis in range(3)):
                    preserved_disjoint_pairs += 1
                    continue
            if not _boxes_overlap(first_obj, second_obj):
                broad_phase_zeros += 1
                continue
            if max(_box_overlap_fractions(first_obj, second_obj)) <= NUMERICAL_CONTACT_FRACTION:
                numerical_contact_pairs += 1
                continue
            pair = {"a": first_id, "b": second_id}
            for identifier, obj in ((first_id, first_obj), (second_id, second_obj)):
                if identifier not in meshes:
                    meshes[identifier] = _world_mesh(obj)
                    volumes[identifier] = _solid_volume(meshes[identifier])
            reasons = [f"{identifier}: {volumes[identifier][1]}" for identifier in (first_id, second_id)
                       if volumes[identifier][0] is None]
            if reasons:
                intersections = _surface_intersections(meshes[first_id], meshes[second_id])
                if intersections == []:
                    surface_disjoint_pairs += 1
                    continue
                suffix = ("; triangle-surface test could not be constructed" if intersections is None
                          else f"; {len(intersections)} intersecting triangle pair(s)")
                unavailable.append({**pair, "reason": "; ".join(reasons) + suffix})
                continue
            intersection, reason = _boolean_intersection_volume(meshes[first_id], meshes[second_id])
            if intersection is None:
                unavailable.append({**pair, "reason": reason})
                continue
            boolean_pairs += 1
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
        "numericalContactPairs": numerical_contact_pairs,
        "booleanPairs": boolean_pairs,
        "surfaceDisjointPairs": surface_disjoint_pairs,
        "preservedBoundsDisjointPairs": preserved_disjoint_pairs,
        "complete": complete,
        "meanWorstOverlapPct": (sum(worst.values()) / len(worst) * 100) if complete and worst else None,
        "maxOverlapPct": (max(worst.values()) * 100) if complete and worst else None,
        "objects": [{"id": identifier, "worstOverlapPct": worst[identifier] * 100} for identifier, _ in rows]
                   if complete else [],
        "overlapPairs": overlaps,
        "unavailablePairs": unavailable,
        "interpretation": (
            "Disjoint world-space bounds prove zero intersection. Intersecting bounds use Blender's exact "
            "Boolean solver for closed solids. Non-solid assets use an exact triangle-surface test to prove "
            "zero rendered-geometry intersection; crossing open surfaces remain incomplete."
        ),
    }
