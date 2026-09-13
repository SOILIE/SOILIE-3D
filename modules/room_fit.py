"""Optional final room-boundary fitting for serverless SOILIE-3D V4.

This module is intentionally downstream of every original V4 placement step.
It never moves, rotates, rescales, replaces, or retries an interior object. It
only measures the completed arrangement and redraws the floor and four walls.
"""

from __future__ import annotations

import json
import math
import os


WALL_CLEARANCE_M = 0.15
MINIMUM_HEIGHT_M = 2.4
ROOM_BLOCKING_OBJECTS = (
    "sofa",
    "couch",
    "bookcase",
    "bed",
    "night_stand",
    "bathtub",
    "cabinet",
    "closet",
    "cupboard",
    "desk",
    "fridge",
    "microwave_oven",
    "sink",
    "toilet",
    "tv",
    "tv_stand",
)


def fit_axis(object_min, object_max, requested_size=None, anchor="center"):
    """Return a containing axis interval while preserving its chosen anchor."""

    minimum_size = object_max - object_min + 2 * WALL_CLEARANCE_M
    actual_size = max(minimum_size, requested_size or minimum_size)
    if anchor == "minimum":
        lower = object_min - WALL_CLEARANCE_M
        upper = lower + actual_size
    elif anchor == "maximum":
        upper = object_max + WALL_CLEARANCE_M
        lower = upper - actual_size
    else:
        midpoint = (object_min + object_max) / 2
        lower = midpoint - actual_size / 2
        upper = midpoint + actual_size / 2
    return lower, upper, minimum_size, actual_size


def plan_room_fit(bounds, room_request, anchors=("center", "center")):
    """Calculate final boundaries from completed V4 mesh bounds."""

    min_x, max_x, min_y, max_y, _min_z, max_z = bounds
    custom = room_request.get("mode") == "custom"
    requested_width = float(room_request["widthM"]) if custom else None
    requested_depth = float(room_request["depthM"]) if custom else None
    room_min_x, room_max_x, minimum_width, actual_width = fit_axis(
        min_x, max_x, requested_width, anchors[0]
    )
    room_min_y, room_max_y, minimum_depth, actual_depth = fit_axis(
        min_y, max_y, requested_depth, anchors[1]
    )
    height = max(MINIMUM_HEIGHT_M, max_z + 0.2)
    adjusted_axes = []
    if custom and actual_width > requested_width + 1e-9:
        adjusted_axes.append("width")
    if custom and actual_depth > requested_depth + 1e-9:
        adjusted_axes.append("depth")
    return {
        "bounds": {
            "minX": room_min_x,
            "maxX": room_max_x,
            "minY": room_min_y,
            "maxY": room_max_y,
        },
        "minimum": {
            "widthM": minimum_width,
            "depthM": minimum_depth,
            "heightM": height,
        },
        "requested": (
            {"widthM": requested_width, "depthM": requested_depth}
            if custom
            else None
        ),
        "actual": {
            "widthM": actual_width,
            "depthM": actual_depth,
            "heightM": height,
        },
        "sizing": "adjusted_to_fit" if adjusted_axes else ("exact" if custom else "auto"),
        "adjustedAxes": adjusted_axes,
        "placementPolicy": {
            "stage": "post_v4_room_boundary_only",
            "xAnchor": anchors[0],
            "yAnchor": anchors[1],
            "interiorPlacementChanged": False,
        },
    }


def _world_bounds(obj):
    from mathutils import Vector

    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        min(corner.x for corner in corners),
        max(corner.x for corner in corners),
        min(corner.y for corner in corners),
        max(corner.y for corner in corners),
        min(corner.z for corner in corners),
        max(corner.z for corner in corners),
    )


def _completed_scene_bounds(objects):
    object_bounds = [_world_bounds(obj) for obj in objects]
    return tuple(
        operation(values[index] for values in object_bounds)
        for index, operation in enumerate((min, max, min, max, min, max))
    )


def _infer_axis_anchor(objects, floor_bounds, axis):
    """Keep V4's large wall-oriented objects near their nearest old wall."""

    low_index, high_index = (0, 1) if axis == "x" else (2, 3)
    blockers = [
        obj
        for obj in objects
        if any(name in obj.name.lower() for name in ROOM_BLOCKING_OBJECTS)
    ]
    if not blockers:
        return "center"
    low_gap = min(abs(_world_bounds(obj)[low_index] - floor_bounds[low_index]) for obj in blockers)
    high_gap = min(abs(floor_bounds[high_index] - _world_bounds(obj)[high_index]) for obj in blockers)
    if math.isclose(low_gap, high_gap, abs_tol=0.1):
        return "center"
    return "minimum" if low_gap < high_gap else "maximum"


def _apply_boundaries(plan, floor, walls):
    import bpy

    bounds = plan["bounds"]
    actual = plan["actual"]
    centre_x = (bounds["minX"] + bounds["maxX"]) / 2
    centre_y = (bounds["minY"] + bounds["maxY"]) / 2
    floor_z = floor.location.z

    floor.location.x = centre_x
    floor.location.y = centre_y
    # Assign each object's complete vector once. Blender recalculates scale
    # when a dimensions component changes, so sequential component writes can
    # silently restore an earlier dimension from a stale dependency graph.
    floor.dimensions = (actual["widthM"], actual["depthM"], floor.dimensions.z)

    for wall in walls:
        wall.location.z = floor_z + actual["heightM"] / 2
        name = wall.name.lower()
        if "left" in name:
            wall.location.x = centre_x
            wall.location.y = bounds["maxY"]
            wall.dimensions = (actual["widthM"], 0.2, actual["heightM"])
        elif "right" in name:
            wall.location.x = centre_x
            wall.location.y = bounds["minY"]
            wall.dimensions = (actual["widthM"], 0.2, actual["heightM"])
        elif "front" in name:
            wall.location.x = bounds["maxX"]
            wall.location.y = centre_y
            wall.dimensions = (0.2, actual["depthM"], actual["heightM"])
        elif "back" in name:
            wall.location.x = bounds["minX"]
            wall.location.y = centre_y
            wall.dimensions = (0.2, actual["depthM"], actual["heightM"])
    bpy.context.view_layer.update()


def apply_serverless_room_fit():
    """Apply the optional room wrapper after the original V4 pipeline finishes."""

    encoded = os.environ.get("SOILIE_ROOM_REQUEST")
    if not encoded:
        return None

    import bpy

    room_request = json.loads(encoded)
    floor = bpy.data.objects["Floor"]
    walls = [obj for obj in bpy.context.scene.objects if "Wall" in obj.name]
    interior = [
        obj
        for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj is not floor and obj not in walls
    ]
    bounds = _completed_scene_bounds(interior)
    floor_bounds = _world_bounds(floor)
    anchors = (
        _infer_axis_anchor(interior, floor_bounds, "x"),
        _infer_axis_anchor(interior, floor_bounds, "y"),
    )
    plan = plan_room_fit(bounds, room_request, anchors)
    _apply_boundaries(plan, floor, walls)
    if plan["adjustedAxes"]:
        axes = " and ".join(plan["adjustedAxes"])
        plan["warning"] = {
            "code": "ROOM_SIZE_ADJUSTED",
            "message": (
                f"The requested room was too small along the {axes}. "
                "The room boundary was expanded just enough to contain the completed SOILIE V4 arrangement; "
                "V4's object placements were not changed."
            ),
        }
    return plan
