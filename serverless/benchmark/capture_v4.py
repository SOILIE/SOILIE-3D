"""Blender-only observer at existing V4 function boundaries.

The imported V4 functions still perform every placement. Wrappers observe their
inputs/outputs without changing randomness or scene state. Layout-only mode exits
after the final wall treatment; full mode continues through the ordinary renders.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import time

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARCHITECTURE = {
    "window", "opaque_window", "blinds", "curtain", "wall", "wooden_wall",
    "door", "floor", "ceiling", "switch", "power_outlet",
}
WALL_MOUNTED = ARCHITECTURE | {"clock", "picture", "painting", "mirror"}


def corners(obj):
    return [[float(c) for c in obj.matrix_world @ Vector(vertex)] for vertex in obj.bound_box]


def front_direction(obj):
    """Return the V4-corrected local +X axis as a horizontal unit vector."""
    direction = obj.matrix_world.to_3x3() @ Vector((1, 0, 0))
    horizontal = Vector((direction.x, direction.y))
    if horizontal.length <= 1e-9:
        raise RuntimeError(f"Object {obj.name} has no horizontal front direction")
    horizontal.normalize()
    return [float(horizontal.x), float(horizontal.y)]


def support_samples(obj, others, floor_z):
    """Observe support using real mesh feet as well as sparse lower-surface rays."""
    from serverless.benchmark.mesh_support import sample_support
    def tree(mesh):
        evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        data = evaluated.to_mesh()
        try:
            points = [evaluated.matrix_world @ v.co for v in data.vertices]
            return points, BVHTree.FromPolygons(points, [list(p.vertices) for p in data.polygons])
        finally:
            evaluated.to_mesh_clear()
    points, own = tree(obj)
    # This includes the real floor mesh, not an infinite imagined floor plane.
    bases = [other for other in others if other != obj and other.type == "MESH" and not other.hide_render]
    supporting = [tree(other)[1] for other in bases]
    identities = [{'id': other.name, 'kind': 'floor' if other.name == 'Floor' else
                   'architecture' if other.name.endswith(' Wall') else 'object'} for other in bases]
    return sample_support(points, own, supporting, floor_z, support_metadata=identities)


def snapshot(inputs, stage, measure_support=False, measure_solids=False):
    objects = []
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    floor = bpy.data.objects["Floor"]
    floor_z = max(v[2] for v in corners(floor))
    # Measure usable interior wall faces, not the oversized floor plane.
    wall_bounds = {name: corners(bpy.data.objects[name]) for name in ("Left Wall", "Right Wall", "Front Wall", "Back Wall")}
    xmin = max(v[0] for v in wall_bounds["Back Wall"])
    xmax = min(v[0] for v in wall_bounds["Front Wall"])
    ymin = max(v[1] for v in wall_bounds["Right Wall"])
    ymax = min(v[1] for v in wall_bounds["Left Wall"])
    solid_objects = []
    for identifier, data in inputs.items():
        obj = data["blender_obj"]
        label = identifier.split(".")[0].lower()
        row = {"id": identifier, "assemblyId": identifier, "label": label,
               "kind": "architecture" if label in ARCHITECTURE else "furniture",
               "asset": data.get("asset_name"), "corners": corners(obj),
               "transform": [list(row) for row in obj.matrix_world],
               "frontDirection": front_direction(obj),
               "frontConvention": "V4 asset-corrected local +X"}
        if measure_support and label not in WALL_MOUNTED:
            support = support_samples(obj, meshes, floor_z)
            if support:
                row["support"] = support
        objects.append(row)
        if label not in ARCHITECTURE:
            solid_objects.append((identifier, obj))
    result = {"schemaVersion": 2, "stage": stage, "units": "m", "objects": objects,
            "room": {"polygon": [[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax]], "floorZ": floor_z,
                     "boundarySource": "original V4 interior wall faces, before cutaway or optional room fitting"}}
    if stage == "final":
        tolerance = 1e-6
        outside = [row["id"] for row in objects if row["kind"] == "furniture" and (
            min(point[0] for point in row["corners"]) < xmin-tolerance
            or max(point[0] for point in row["corners"]) > xmax+tolerance
            or min(point[1] for point in row["corners"]) < ymin-tolerance
            or max(point[1] for point in row["corners"]) > ymax+tolerance
        )]
        if outside:
            raise RuntimeError("Final V4 room does not contain furniture: " + ", ".join(outside))
    if measure_solids:
        from serverless.benchmark.solid_overlap import measure
        result["solidMeshOverlap"] = measure(solid_objects)
    return result


class LayoutCaptured(Exception):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--support", action="store_true")
    parser.add_argument("--solid-mesh-overlap", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if os.environ.get("SOILIE_ROOM_REQUEST"):
        raise RuntimeError("Model benchmarks must not enable the website room-size extension")
    sys.path.insert(0, str(Path.cwd()/"modules"))
    spec = importlib.util.spec_from_file_location("observed_v4", "modules/render.py")
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    original_overlap, original_windows = model.adjust_overlapping_objects, model.adjust_windows_to_walls
    stages, captured_inputs = {}, None
    observation_seconds = 0.0
    started = time.perf_counter()

    def observe(inputs, stage):
        nonlocal observation_seconds
        before = time.perf_counter()
        stages[stage] = snapshot(inputs, stage, args.support and stage == "final",
                                 args.solid_mesh_overlap and stage == "final")
        observation_seconds += time.perf_counter()-before

    def overlap(inputs):
        nonlocal captured_inputs
        captured_inputs = inputs
        observe(inputs, "beforeSeparation")
        result = original_overlap(inputs)
        observe(inputs, "afterSeparation")
        return result

    def windows(*values, **kwargs):
        result = original_windows(*values, **kwargs)
        observe(captured_inputs, "final")
        document = {"stages": stages, "placementSeconds": time.perf_counter()-started-observation_seconds,
                    "observationSeconds": observation_seconds, "blenderVersion": bpy.app.version_string,
                    "mode": "full" if args.full else "layout-only"}
        args.output.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        if not args.full:
            raise LayoutCaptured()
        return result

    model.adjust_overlapping_objects = overlap
    model.adjust_windows_to_walls = windows
    random.seed(args.seed)
    try:
        model.visualize(json.loads(args.input.read_text(encoding="utf-8")))
    except LayoutCaptured:
        pass


if __name__ == "__main__":
    main()
