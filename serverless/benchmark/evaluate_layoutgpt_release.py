"""Audit the official LayoutGPT GPT-4 indoor-layout release.

This is deliberately an artifact audit, not a rerun or a matched comparison
with SOILIE-3D.  LayoutGPT predicts its own categories and dimensions from a
room type and rectangular floor size, while SOILIE starts from a fixed object
set and measured pair relations.  The audit therefore reports only properties
that can be recomputed from the two JSON files published by LayoutGPT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOM_RE = re.compile(r"max length\s+([0-9.]+)px,\s+max width\s+([0-9.]+)px", re.IGNORECASE)


def _vertices(box: dict[str, float]) -> list[tuple[float, float]]:
    angle = math.radians(float(box["orientation"]))
    cosine, sine = math.cos(angle), math.sin(angle)
    half_length = float(box["length"]) / 2
    half_width = float(box["width"]) / 2
    result: list[tuple[float, float]] = []
    for x, y in ((-half_length, -half_width), (half_length, -half_width), (half_length, half_width), (-half_length, half_width)):
        result.append((float(box["left"]) + x * cosine + y * sine, float(box["top"]) - x * sine + y * cosine))
    return result


def _projections(vertices: Iterable[tuple[float, float]], axis: tuple[float, float]) -> tuple[float, float]:
    values = [x * axis[0] + y * axis[1] for x, y in vertices]
    return min(values), max(values)


def _footprints_overlap(first: list[tuple[float, float]], second: list[tuple[float, float]]) -> bool:
    """Use the separating-axis theorem for two rotated rectangles."""
    for polygon in (first, second):
        for index in range(2):
            edge = (
                polygon[(index + 1) % 4][0] - polygon[index][0],
                polygon[(index + 1) % 4][1] - polygon[index][1],
            )
            length = math.hypot(*edge)
            if length <= 1e-9:
                return True
            axis = (-edge[1] / length, edge[0] / length)
            a_min, a_max = _projections(first, axis)
            b_min, b_max = _projections(second, axis)
            if a_max <= b_min + 1e-6 or b_max <= a_min + 1e-6:
                return False
    return True


def _vertical_overlap(first: dict[str, float], second: dict[str, float]) -> bool:
    first_min = float(first["depth"]) - float(first["height"]) / 2
    first_max = float(first["depth"]) + float(first["height"]) / 2
    second_min = float(second["depth"]) - float(second["height"]) / 2
    second_max = float(second["depth"]) + float(second["height"]) / 2
    return first_max > second_min + 1e-6 and second_max > first_min + 1e-6


def _percentile(values: list[int], percentile: float) -> float:
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(len(ordered) * percentile) - 1)])


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _room_size(prompt: str) -> tuple[float, float]:
    match = ROOM_RE.search(prompt)
    if not match:
        raise ValueError(f"Room dimensions are absent from prompt: {prompt!r}")
    return float(match.group(1)), float(match.group(2))


def audit(files: list[Path], source_commit: str) -> dict[str, Any]:
    layouts: list[tuple[str, dict[str, Any]]] = []
    sources: list[dict[str, Any]] = []
    for path in files:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, list):
            raise ValueError(f"Expected a list in {path}")
        room_type = "livingRoom" if "livingroom" in path.name else "bedroom"
        layouts.extend((room_type, layout) for layout in document)
        sources.append({"file": path.name, "layouts": len(document), "sha256": _file_hash(path)})

    object_counts: list[int] = []
    category_counts: Counter[str] = Counter()
    strict_oob_scenes = 0
    strict_oob_objects = 0
    collision_scenes = 0
    collision_pairs = 0
    pair_count = 0
    duplicate_scenes = 0
    cardinal_orientations = 0
    total_objects = 0
    utilization: list[float] = []
    room_types: Counter[str] = Counter()

    for room_type, layout in layouts:
        room_length, room_width = _room_size(str(layout["prompt"]))
        objects = layout.get("object_list", [])
        object_counts.append(len(objects))
        room_types[room_type] += 1
        labels = [str(label) for label, _ in objects]
        category_counts.update(labels)
        duplicate_scenes += len(labels) != len(set(labels))
        total_objects += len(objects)
        footprint_sum = 0.0
        scene_oob = False
        scene_collision = False
        prepared: list[tuple[str, dict[str, float], list[tuple[float, float]]]] = []
        for label, raw_box in objects:
            box = {key: float(value) for key, value in raw_box.items()}
            vertices = _vertices(box)
            prepared.append((str(label), box, vertices))
            footprint_sum += box["length"] * box["width"]
            cardinal_orientations += math.isclose(box["orientation"] % 90.0, 0.0, abs_tol=1e-6)
            outside = any(x < 0 or x > room_length or y < 0 or y > room_width for x, y in vertices)
            strict_oob_objects += outside
            scene_oob = scene_oob or outside
        utilization.append(footprint_sum / (room_length * room_width))
        strict_oob_scenes += scene_oob
        for index, (_, first_box, first_vertices) in enumerate(prepared):
            for _, second_box, second_vertices in prepared[index + 1 :]:
                pair_count += 1
                if _footprints_overlap(first_vertices, second_vertices) and _vertical_overlap(first_box, second_box):
                    collision_pairs += 1
                    scene_collision = True
        collision_scenes += scene_collision

    layout_count = len(layouts)
    return {
        "schemaVersion": 1,
        "benchmarkId": "layoutgpt-official-gpt4-release-audit-v0.1.7",
        "runDate": "2026-09-13",
        "method": "Independent geometry audit of the official released GPT-4 JSON outputs; no model inference was rerun.",
        "source": {
            "repository": "https://github.com/UCSB-AI/LayoutGPT",
            "commit": source_commit,
            "files": sources,
        },
        "scope": {
            "layouts": layout_count,
            "bedrooms": room_types["bedroom"],
            "livingRooms": room_types["livingRoom"],
            "objects": total_objects,
            "uniqueCategories": len(category_counts),
            "categoryCounts": dict(sorted(category_counts.items())),
        },
        "recomputedMetrics": {
            "parseSuccessRate": 1.0,
            "meanObjectsPerScene": round(statistics.fmean(object_counts), 4),
            "medianObjectsPerScene": statistics.median(object_counts),
            "p95ObjectsPerScene": _percentile(object_counts, 0.95),
            "scenesWithRepeatedCategoriesRate": round(duplicate_scenes / layout_count, 4),
            "strictOutOfBoundsSceneRate": round(strict_oob_scenes / layout_count, 4),
            "strictOutOfBoundsObjectRate": round(strict_oob_objects / total_objects, 4),
            "threeDimensionalCollisionSceneRate": round(collision_scenes / layout_count, 4),
            "threeDimensionalCollisionPairRate": round(collision_pairs / pair_count, 4),
            "cardinalOrientationRate": round(cardinal_orientations / total_objects, 4),
            "meanSummedFootprintUtilization": round(statistics.fmean(utilization), 4),
        },
        "metricNotes": {
            "strictOutOfBounds": "Rotated furniture footprints must lie wholly inside the rectangular pixel room; unlike the paper metric, no 0.1 m tolerance is applied because the source room dimensions in metres are not included in the released output.",
            "threeDimensionalCollision": "A pair is counted when its rotated floor footprints and vertical intervals overlap. This metric is not reported by LayoutGPT and is our independent diagnostic.",
            "footprintUtilization": "Sum of unoccluded rectangle areas divided by room area; it is descriptive and may exceed union occupancy when footprints overlap.",
        },
        "comparabilityWarning": "SOILIE and LayoutGPT do not share an input contract, object catalog, asset geometry, dataset, or published evaluation protocol. These values must not be used as a head-to-head ranking.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    document = audit(arguments.files, arguments.source_commit)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
