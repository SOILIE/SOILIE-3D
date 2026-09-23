"""Shared final-placement measurements, independent of any generator's solver.

World-space box corners remain a cross-source envelope diagnostic: rotations
are not discarded by turning every box into a world-axis-aligned box. Sources
with evaluated Blender meshes can additionally provide exact occupied-volume
overlap for closed solids and exact surface-separation evidence for open assets.
No measurement moves objects.
"""
from __future__ import annotations

from itertools import combinations, product
import math
import statistics

import numpy as np
from scipy.spatial import ConvexHull, QhullError
from shapely.geometry import MultiPoint, MultiPolygon, Polygon
from shapely.ops import unary_union

SCHEMA_VERSION = 2
ARCHITECTURE = frozenset({"wall", "floor", "ceiling", "door", "window", "blinds", "curtain", "opaque_window", "wooden_wall", "power_outlet", "switch"})
WALL_MOUNTED = ARCHITECTURE | {"picture", "painting", "mirror", "clock"}


def canonical_label(label):
    return label.lower().replace(" ", "_").split(".")[0]


def box_corners(center, size, yaw=0):
    angle = math.radians(yaw)
    c, s = math.cos(angle), math.sin(angle)
    return [[center[0] + c*x-s*y, center[1]+s*x+c*y, center[2]+z]
            for x, y, z in product(*[(-float(v)/2, float(v)/2) for v in size])]


class Box:
    def __init__(self, item):
        self.item = item
        self.points = np.asarray(item["corners"], dtype=float)
        if self.points.shape != (8, 3) or not np.isfinite(self.points).all():
            raise ValueError("An object must supply eight finite world-space box corners")
        self.low, self.high = self.points.min(axis=0), self.points.max(axis=0)
        try:
            self.hull = ConvexHull(self.points)
        except QhullError as error:
            raise ValueError("Degenerate object bounding volume") from error
        if self.hull.volume <= 1e-12:
            raise ValueError("Degenerate object bounding volume")
        self.volume = float(self.hull.volume)
        self.footprint = MultiPoint(self.points[:, :2]).convex_hull
        self.upright = np.all(np.minimum(abs(self.points[:, 2]-self.low[2]), abs(self.points[:, 2]-self.high[2])) < 1e-8)
        # Two horizontal levels alone do not establish an upright prism: a
        # parent transform can shear the top sideways. Its footprint extrusion
        # must also equal its true hull volume before taking the fast path.
        self.upright = self.upright and math.isclose(self.footprint.area*(self.high[2]-self.low[2]),self.volume,rel_tol=1e-10,abs_tol=1e-12)


def intersection_volume(first, second):
    if np.any(np.minimum(first.high, second.high) <= np.maximum(first.low, second.low)):
        return 0.0
    # Most indoor furniture rotates only about vertical. This exact fast path
    # avoids expensive polyhedron clipping for the 10,000-scene batch.
    if first.upright and second.upright:
        return float(first.footprint.intersection(second.footprint).area *
                     (min(first.high[2], second.high[2])-max(first.low[2], second.low[2])))
    # A box is the intersection of its six halfspaces. Vertices of the
    # intersection occur where three face planes meet; retain only feasible ones.
    planes = np.unique(np.round(np.concatenate([first.hull.equations, second.hull.equations]), 10), axis=0)
    vertices = []
    scale = max(float(np.ptp(np.concatenate([first.points, second.points]), axis=0).max()), 1)
    tolerance = scale * 1e-8
    for triplet in combinations(planes, 3):
        system = np.asarray(triplet)
        if abs(np.linalg.det(system[:, :3])) < 1e-10:
            continue
        vertex = np.linalg.solve(system[:, :3], -system[:, 3])
        if np.all(planes[:, :3] @ vertex + planes[:, 3] <= tolerance):
            vertices.append(vertex)
    if len(vertices) < 4:
        return 0.0
    try:
        return min(float(ConvexHull(np.asarray(vertices)).volume), first.volume, second.volume)
    except QhullError:
        return 0.0


def furniture(scene):
    return [item for item in scene["objects"]
            if item.get("kind", "furniture") == "furniture"
            and canonical_label(item["label"]) not in ARCHITECTURE]


def room_regions(room):
    """Return explicit room regions, preserving disconnected emitted floors."""
    regions = room.get("regions")
    if regions is None:
        regions = [{"polygon":room["polygon"], "holes":room.get("holes", [])}]
    if not regions:
        raise ValueError("Room boundary must contain at least one region")
    return regions


def room_geometry(room):
    polygons = [Polygon(region["polygon"], region.get("holes", []))
                for region in room_regions(room)]
    geometry = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    if not geometry.is_valid or geometry.area <= 1e-12:
        raise ValueError("Room boundary is invalid")
    return geometry


def measure(scene):
    items = furniture(scene)
    if len({item["id"] for item in scene["objects"]}) != len(scene["objects"]):
        raise ValueError("Object instance IDs must be unique")
    if not items:
        raise ValueError("A measured scene must contain furniture")
    boxes = [Box(item) for item in items]
    floor_indices = [index for index,item in enumerate(items) if item.get("supportEligible", True)]
    if not floor_indices:
        raise ValueError("A physical room must contain at least one floor-supported object")
    room = room_geometry(scene["room"])
    worst = [0.0] * len(boxes)
    pairs = []
    for a, b in combinations(range(len(boxes)), 2):
        # Multiple meshes in one semantic object are one assembly, not collisions.
        if items[a].get("assemblyId", items[a]["id"]) == items[b].get("assemblyId", items[b]["id"]):
            continue
        volume = intersection_volume(boxes[a], boxes[b])
        ratios = [max(0.0, min(1.0, volume / boxes[i].volume)) for i in (a, b)]
        worst[a], worst[b] = max(worst[a], ratios[0]), max(worst[b], ratios[1])
        if volume > 1e-10:
            pairs.append({"a": items[a]["id"], "b": items[b]["id"], "fractions": ratios})
    outside = {index:max(0.0, min(1.0, boxes[index].footprint.difference(room).area / boxes[index].footprint.area))
               for index in floor_indices}
    metric = {
        "objectCount": len(items), "roomArea": room.area,
        "furnitureDensity": sum(boxes[index].footprint.area for index in floor_indices)/room.area,
        "meanWorstEnvelopeOverlapPct": statistics.fmean(worst)*100,
        "maxEnvelopeOverlapPct": max(worst)*100,
        "meanOutsideFootprintPct": statistics.fmean(outside.values())*100,
        "maxOutsideFootprintPct": max(outside.values())*100,
        "envelopeOverlapPairs": pairs,
        "objects": [{"id": item["id"], "worstEnvelopeOverlapPct": worst[i]*100,
                     "outsideFootprintPct": outside[i]*100 if i in outside else None}
                    for i, item in enumerate(items)],
        "meanWorstSolidOverlapPct": None, "maxSolidOverlapPct": None,
        "solidOverlapPairs": [], "solidOverlapMethod": None,
        "connectedClearancePct": None, "supportGapCm": None, "belowFloorCm": None,
        "unavailable": {}, "boundaryObjectsMeasured": len(floor_indices),
    }
    solid = scene.get("solidMeshOverlap")
    if solid is None:
        metric["unavailable"]["solidOverlap"] = "The source artifact does not contain evaluated solid-mesh evidence."
    else:
        if solid.get("method") not in {"evaluated-solid-mesh-boolean-v1", "evaluated-mesh-intersection-v2"}:
            raise ValueError("Unknown solid-overlap measurement method")
        if solid.get("objectCount") != len(items):
            raise ValueError("Solid-overlap evidence does not cover the measured furniture set")
        metric["solidOverlapMethod"] = solid["method"]
        if solid.get("complete"):
            for key in ("meanWorstOverlapPct", "maxOverlapPct"):
                value = solid.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError("Solid-overlap percentage must be finite and between zero and 100")
            metric["meanWorstSolidOverlapPct"] = solid["meanWorstOverlapPct"]
            metric["maxSolidOverlapPct"] = solid["maxOverlapPct"]
            metric["solidOverlapPairs"] = solid.get("overlapPairs", [])
        else:
            count = len(solid.get("unavailablePairs", []))
            metric["unavailable"]["solidOverlap"] = (
                f"{count} pair(s) had crossing open surfaces without a defined enclosed volume."
            )
    if scene["units"] == "m":
        floor_z = scene["room"]["floorZ"]
        if not isinstance(floor_z, (int,float)) or not math.isfinite(floor_z):
            raise ValueError("Physical floor elevation must be finite")
        # Configuration-space clearance for a 0.6 m-wide, 1.8 m-tall cylinder.
        obstacles = [boxes[index].footprint.buffer(0.3, quad_segs=16) for index in floor_indices
                     if boxes[index].high[2] > floor_z and boxes[index].low[2] < floor_z+1.8]
        free = room.buffer(-0.3, quad_segs=16).difference(unary_union(obstacles))
        components = list(free.geoms) if hasattr(free, "geoms") else [free]
        metric["connectedClearancePct"] = max((part.area for part in components), default=0)/room.area*100
        # Version 1 used only nine XY rays, which can miss narrow feet and
        # incorrectly report a grounded table as floating. Never publish those
        # superseded gap observations as physical-support evidence.
        samples = [item["support"] for item in items
                   if item.get("support", {}).get("source") == "mesh-ray-samples"
                   and item["support"].get("samplingVersion") == 2]
        if any(type(sample[key]) not in (int,float) or not math.isfinite(sample[key]) or sample[key] < 0
               for sample in samples for key in ("gapM","belowFloorM") if sample[key] is not None):
            raise ValueError("Measured support distances must be finite and non-negative")
        metric["supportObjectsMeasured"] = len(samples)
        if samples:
            for source, target in (("gapM","supportGapCm"), ("belowFloorM","belowFloorCm")):
                values = [sample[source] for sample in samples if sample[source] is not None]
                if values:
                    metric[target] = statistics.fmean(values)*100
            if metric["supportGapCm"] is None:
                metric["unavailable"]["supportGap"] = "No real supporting surface was hit by the probes; no gap can be assigned."
        else:
            metric["unavailable"]["supportGap"] = "Validated mesh support samples were not provided; boxes or superseded sparse probes cannot establish physical support."
            metric["unavailable"]["belowFloor"] = "Evaluated mesh vertices were not provided, so depth below the floor cannot be measured."
    else:
        metric["unavailable"]["clearance"] = "The released layout uses pixels without verified physical scale."
        metric["unavailable"]["supportGap"] = "Physical units and real mesh support samples are unavailable."
        metric["unavailable"]["belowFloor"] = "Physical units and real mesh vertices are unavailable."
    return metric


def summarize(values):
    values = sorted(float(value) for value in values if value is not None)
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None, "values": []}
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "p95": values[math.ceil(.95*len(values))-1], "values": values}


def stratum(scene, metrics):
    # Density is summed furniture footprint / room area, NOT a quality filter.
    # No scene is rejected because its overlap or boundary score is poor.
    return (scene["roomType"], metrics["objectCount"], math.floor(metrics["furnitureDensity"]/.25))
