"""Read a completed original Infinigen blend file; never move or repair a scene.

Run in standalone Blender after the timed original coarse task. The original
state determines room membership and object instances. Real evaluated meshes
determine the enclosing boxes; tagged interior floor faces define the boundary.
Missing or ambiguous evidence fails export instead of producing a substitute.
"""
import argparse
import hashlib
from itertools import product
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from serverless.benchmark.infinigen_metadata import asset_label, generated_instances, vertically_supported
from serverless.benchmark.mesh_support import sample_support
from serverless.benchmark.solid_overlap import measure as measure_solid_overlap
from infinigen.core import tagging, tags


def evaluated_mesh(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        points = np.empty(len(mesh.vertices)*3, dtype=float)
        mesh.vertices.foreach_get("co", points)
        matrix = np.asarray(evaluated.matrix_world)
        world = points.reshape(-1,3) @ matrix[:3,:3].T + matrix[:3,3]
        return world, [list(face.vertices) for face in mesh.polygons]
    finally:
        evaluated.to_mesh_clear()


def instance_geometry(root, other_roots):
    meshes, pending = [], [root]
    while pending:
        obj = pending.pop()
        if obj != root and obj.name in other_roots:
            continue # A separately declared lamp on a table is not a table part.
        if obj.name.endswith(".cutter") or any("asset_cutters" in col.name for col in obj.users_collection):
            continue # Computational boolean cutter, not an emitted object part.
        pending.extend(obj.children)
        if obj.type == "MESH" and not obj.hide_render:
            meshes.append(obj)
    if not meshes:
        raise ValueError(f"No final renderable mesh for {root.name}")
    vertices, faces = [], []
    count = 0
    for mesh in meshes:
        world, polygons = evaluated_mesh(mesh)
        vertices.append(world)
        faces.extend([[index+count for index in face] for face in polygons])
        count += len(world)
    vertices = np.concatenate(vertices)
    inverse = np.asarray(root.matrix_world.inverted())
    local = vertices @ inverse[:3,:3].T + inverse[:3,3]
    low, high = local.min(axis=0), local.max(axis=0)
    if np.any(high-low <= 1e-9):
        raise ValueError(f"Degenerate final asset geometry for {root.name}")
    corners = [[float(v) for v in root.matrix_world @ Vector(p)]
               for p in product(*zip(low, high))]
    return corners, vertices, faces, [obj.name for obj in meshes]


def floor_boundary(room):
    if tagging.COMBINED_ATTR_NAME not in room.data.attributes:
        raise ValueError("Original room has no floor tags; no guessed boundary is allowed")
    mask = tagging.tagged_face_mask(room, {tags.Subpart.SupportSurface, tags.Subpart.Visible})
    if len(mask) != len(room.data.polygons):
        raise ValueError("Floor tags do not align with the original faces")
    pieces, elevations = [], []
    for include, face in zip(mask, room.data.polygons):
        if not include:
            continue
        world = [room.matrix_world @ room.data.vertices[index].co for index in face.vertices]
        polygon = Polygon([(v.x,v.y) for v in world])
        if not polygon.is_valid or polygon.area <= 1e-12:
            raise ValueError("Tagged floor contains invalid or vertical geometry")
        pieces.append(polygon)
        elevations.extend(v.z for v in world)
    if not pieces or max(elevations)-min(elevations) > 1e-5:
        raise ValueError("Expected a nonempty planar original floor")
    boundary = unary_union(pieces)
    if boundary.geom_type != "Polygon" or not boundary.is_valid:
        raise ValueError("Floor boundary is disconnected or invalid; do not replace it with a convex hull")
    return {"polygon":list(boundary.exterior.coords)[:-1],
            "holes":[list(ring.coords)[:-1] for ring in boundary.interiors],
            "floorZ":sum(elevations)/len(elevations),
            "boundarySource":"Original tagged visible interior floor faces in the completed Infinigen scene"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state",type=Path,required=True)
    parser.add_argument("--room-type",choices=["bedroom","living_room"],required=True)
    parser.add_argument("--id",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    if bpy.context.scene.unit_settings.scale_length != 1:
        raise ValueError("Unexpected unit scale; no implicit rescaling is allowed")
    records = json.loads(args.state.read_text())["objs"]
    room_id, instances = generated_instances(records,args.room_type)
    tagging.tag_system.load_tag(args.state.with_name("MaskTag.json"))
    room_object = bpy.data.objects[records[room_id]["obj"]]
    room = floor_boundary(room_object)
    room_points, room_faces = evaluated_mesh(room_object)
    room_tree = BVHTree.FromPolygons([Vector(point) for point in room_points], room_faces)
    roots = {record["obj"] for _,record in instances}
    objects, geometry, solid_sources = [], {}, []
    for identifier, record in instances:
        root = bpy.data.objects[record["obj"]]
        corners, points, faces, parts = instance_geometry(root,roots)
        geometry[identifier] = (points, BVHTree.FromPolygons([Vector(p) for p in points],faces))
        mesh = bpy.data.meshes.new("__benchmark_"+identifier)
        mesh.from_pydata(points.tolist(), [], faces)
        mesh.update()
        solid = bpy.data.objects.new("__benchmark_"+identifier, mesh)
        bpy.context.scene.collection.objects.link(solid)
        solid_sources.append((identifier, solid))
        objects.append({"id":identifier,"assemblyId":identifier,"label":asset_label(record),"kind":"furniture",
                        "corners":corners,"transform":[list(row) for row in root.matrix_world],"meshParts":parts,
                        "sourceTags":record["tags"],"supportEligible":vertically_supported(record)})
    for obj in objects:
        if obj["supportEligible"]:
            points, own = geometry[obj["id"]]
            sampled = sample_support(points,own,[room_tree]+[tree for key,(_,tree) in geometry.items() if key != obj["id"]],room["floorZ"])
            if sampled:
                obj["support"] = sampled
    try:
        scene = {"schemaVersion":2,"model":"infinigen","id":args.id,"roomType":args.room_type,
                 "stage":"original-final-coarse","units":"m","room":room,"objects":objects,
                 "solidMeshOverlap":measure_solid_overlap(solid_sources),
                 "provenance":{"stateSha256":hashlib.sha256(args.state.read_bytes()).hexdigest(),
                               "roomId":room_id,"blenderVersion":bpy.app.version_string,
                               "geometryPolicy":"One root-oriented envelope and one evaluated triangle assembly per original semantic instance"}}
        args.output.write_text(json.dumps(scene,separators=(",",":")),encoding="utf-8")
        print(json.dumps({"id":args.id,"instances":len(objects),"roomId":room_id}))
    finally:
        for _, obj in solid_sources:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)


if __name__ == "__main__":
    main()
