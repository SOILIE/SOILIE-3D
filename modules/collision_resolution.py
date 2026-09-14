"""Deterministic geometry helpers for resolving rare V4 collision cycles.

The ordinary renderer keeps its original pairwise nudge order. These helpers
are used only after that pass repeats a geometric state, which indicates that
one correction has recreated a collision that an earlier correction removed.
They have no Blender dependency so the recovery policy can be unit tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Iterable, Mapping, Sequence


EPSILON = 1e-9


@dataclass(frozen=True)
class Box:
    """World-axis-aligned bounds for one scene object."""

    name: str
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float

    def translated(self, dx: float, dy: float) -> "Box":
        return Box(
            self.name,
            self.min_x + dx,
            self.max_x + dx,
            self.min_y + dy,
            self.max_y + dy,
            self.min_z,
            self.max_z,
        )


@dataclass(frozen=True)
class RecoveryMove:
    """A collision-reducing displacement selected for one object."""

    name: str
    dx: float
    dy: float
    overlap_before: float
    overlap_after: float


def intersection_volume(first: Box, second: Box) -> float:
    width = min(first.max_x, second.max_x) - max(first.min_x, second.min_x)
    depth = min(first.max_y, second.max_y) - max(first.min_y, second.min_y)
    height = min(first.max_z, second.max_z) - max(first.min_z, second.min_z)
    if width <= 0 or depth <= 0 or height <= 0:
        return 0.0
    return width * depth * height


def overlapping_pairs(
    boxes: Mapping[str, Box],
    fixed: frozenset[str] = frozenset(),
) -> list[tuple[str, str, float]]:
    """Return furniture collisions, excluding architecture in ``fixed``."""

    names = list(boxes)
    result = []
    for index, first_name in enumerate(names):
        for second_name in names[index + 1 :]:
            if first_name in fixed or second_name in fixed:
                continue
            volume = intersection_volume(boxes[first_name], boxes[second_name])
            if volume > EPSILON:
                result.append((first_name, second_name, volume))
    return result


def total_overlap(boxes: Mapping[str, Box], fixed: frozenset[str] = frozenset()) -> float:
    return sum(volume for _first, _second, volume in overlapping_pairs(boxes, fixed))


def _candidate_shifts(box: Box, others: Iterable[Box], clearance: float) -> list[tuple[float, float]]:
    others = list(others)
    candidates: list[tuple[float, float]] = []
    for other in others:
        if intersection_volume(box, other) <= EPSILON:
            continue
        candidates.extend(
            [
                (other.max_x - box.min_x + clearance, 0.0),
                (other.min_x - box.max_x - clearance, 0.0),
                (0.0, other.max_y - box.min_y + clearance),
                (0.0, other.min_y - box.max_y - clearance),
            ]
        )

    # Cluster-edge candidates guarantee an escape from any locally trapped
    # overlap without consuming another random choice.
    if others:
        candidates.extend(
            [
                (max(other.max_x for other in others) - box.min_x + clearance, 0.0),
                (min(other.min_x for other in others) - box.max_x - clearance, 0.0),
                (0.0, max(other.max_y for other in others) - box.min_y + clearance),
                (0.0, min(other.min_y for other in others) - box.max_y - clearance),
            ]
        )
    return list(dict.fromkeys((round(dx, 12), round(dy, 12)) for dx, dy in candidates))


def choose_recovery_move(
    boxes: Mapping[str, Box],
    movable_order: Sequence[str],
    original_centres: Mapping[str, tuple[float, float]],
    room_bounds: tuple[float, float, float, float] | None = None,
    fixed: frozenset[str] = frozenset(),
    clearance: float = 0.01,
) -> RecoveryMove:
    """Choose the least disruptive movement that reduces whole-scene overlap."""

    before = total_overlap(boxes, fixed)
    if before <= EPSILON:
        raise ValueError("Recovery was requested for a collision-free scene")
    collisions = overlapping_pairs(boxes, fixed)
    colliding_names = {name for first, second, _volume in collisions for name in (first, second)}
    ranked = []
    for priority, name in enumerate(movable_order):
        if name in fixed or name not in colliding_names:
            continue
        box = boxes[name]
        others = [
            other for other_name, other in boxes.items()
            if other_name != name and other_name not in fixed
        ]
        for candidate_index, (dx, dy) in enumerate(_candidate_shifts(box, others, clearance)):
            moved = box.translated(dx, dy)
            proposal = dict(boxes)
            proposal[name] = moved
            after = total_overlap(proposal, fixed)
            if after >= before - EPSILON:
                continue
            original_x, original_y = original_centres[name]
            centre_x = (moved.min_x + moved.max_x) / 2
            centre_y = (moved.min_y + moved.max_y) / 2
            origin_distance = hypot(centre_x - original_x, centre_y - original_y)
            displacement = hypot(dx, dy)
            room_expansion = 0.0
            if room_bounds is not None:
                min_x, max_x, min_y, max_y = room_bounds
                room_expansion = (
                    max(0.0, min_x - moved.min_x)
                    + max(0.0, moved.max_x - max_x)
                    + max(0.0, min_y - moved.min_y)
                    + max(0.0, moved.max_y - max_y)
                )
            # First prefer a complete clearance, otherwise the greatest strict
            # reduction. Among equivalent geometry outcomes, preserve the V4
            # placement as closely as possible before considering room growth.
            score = (
                0 if after <= EPSILON else 1,
                round(after, 12),
                origin_distance,
                displacement,
                room_expansion,
                priority,
                candidate_index,
            )
            ranked.append((score, RecoveryMove(name, dx, dy, before, after)))
    if not ranked:
        raise RuntimeError("No deterministic collision-reducing movement exists")
    return min(ranked, key=lambda item: item[0])[1]
