"""Pure recency schedules shared by rendering and non-Blender tests.

The sequence is cyclic because the rendered artifact is a looping GIF.  A
settled focal state therefore contains the current object and its five most
recent predecessors (or every object when the scene contains fewer than six).
Transitions use a two-phase handoff: the outgoing oldest predecessor reaches
zero before the incoming focus begins to appear.  That keeps the hard maximum
of six visible objects without introducing a discontinuity at the loop seam.
"""

from __future__ import annotations

import math


# Indexes are ages: zero is the current focus, one is its predecessor, etc.
FOCUS_ALPHA_BY_AGE = (1.0, 0.50, 0.35, 0.20, 0.10, 0.05)
REMESH_DEPTH_BY_AGE = (None, 7, 6, 5, 4, 3)
LIGHT_SUFFIX = ".Light"


def _validate_position(object_count: int, focus_index: int) -> None:
    if isinstance(object_count, bool) or not isinstance(object_count, int) or object_count <= 0:
        raise ValueError("object_count must be a positive integer")
    if isinstance(focus_index, bool) or not isinstance(focus_index, int):
        raise ValueError("focus_index must be an integer")
    if not 0 <= focus_index < object_count:
        raise ValueError("focus_index must identify an object in the sequence")


def stable_visibility(object_count: int, focus_index: int) -> tuple[float, ...]:
    """Return cyclic opacity for the current object and five predecessors."""

    _validate_position(object_count, focus_index)
    result = []
    for object_index in range(object_count):
        age = (focus_index - object_index) % object_count
        result.append(FOCUS_ALPHA_BY_AGE[age] if 0 <= age < len(FOCUS_ALPHA_BY_AGE) else 0.0)
    return tuple(result)


def transition_visibility(
    object_count: int,
    focus_index: int,
    progress: float,
) -> tuple[float, ...]:
    """Continuously hand the current cyclic focal state to the next one.

    Retained objects interpolate for the full transition.  When the two focal
    states have different supports, outgoing objects fade during the first
    half and incoming objects rise during the second.  Thus their positive
    intervals never overlap and the six-object visibility cap is invariant.
    """

    _validate_position(object_count, focus_index)
    if isinstance(progress, bool) or not isinstance(progress, (int, float)):
        raise ValueError("progress must be a finite number between 0 and 1")
    progress = float(progress)
    if not math.isfinite(progress) or not 0.0 <= progress <= 1.0:
        raise ValueError("progress must be a finite number between 0 and 1")

    current = stable_visibility(object_count, focus_index)
    following = stable_visibility(object_count, (focus_index + 1) % object_count)
    values = []
    for start, end in zip(current, following):
        if start > 0.0 and end == 0.0:
            values.append(start * max(0.0, 1.0 - 2.0 * progress))
        elif start == 0.0 and end > 0.0:
            values.append(end * max(0.0, 2.0 * progress - 1.0))
        else:
            values.append(start + progress * (end - start))
    return tuple(values)


def stable_remesh_depths(object_count: int, focus_index: int) -> tuple[int | None, ...]:
    """Return cyclic detail levels for a settled focal state."""

    _validate_position(object_count, focus_index)
    result = []
    for object_index in range(object_count):
        age = (focus_index - object_index) % object_count
        result.append(REMESH_DEPTH_BY_AGE[age] if 0 <= age < len(REMESH_DEPTH_BY_AGE) else None)
    return tuple(result)


def transition_remesh_depths(
    object_count: int,
    focus_index: int,
    progress: float,
) -> tuple[int | None, ...]:
    """Switch discrete detail levels at the visibility handoff midpoint."""

    # Reuse transition validation so opacity and geometry reject identical bad
    # inputs.  At the midpoint outgoing and incoming objects are both hidden.
    visibility = transition_visibility(object_count, focus_index, progress)
    active_focus = focus_index if float(progress) < 0.5 else (focus_index + 1) % object_count
    depths = stable_remesh_depths(object_count, active_focus)
    return tuple(depth if alpha > 0.0 else None for depth, alpha in zip(depths, visibility))


def light_energy(maximum_energy: float, alpha: float) -> float:
    """Return light energy coupled exactly to the owning object's opacity."""

    if isinstance(maximum_energy, bool) or not isinstance(maximum_energy, (int, float)):
        raise ValueError("maximum_energy must be a finite non-negative number")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise ValueError("alpha must be a finite number between 0 and 1")
    maximum_energy = float(maximum_energy)
    alpha = float(alpha)
    if not math.isfinite(maximum_energy) or maximum_energy < 0.0:
        raise ValueError("maximum_energy must be a finite non-negative number")
    if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be a finite number between 0 and 1")
    return maximum_energy * alpha


def luminous_object_type(object_name: str) -> str | None:
    """Return the supported light role owned by an imported scene object."""

    if not isinstance(object_name, str):
        return None
    normalized = object_name.casefold()
    if normalized.startswith("lamp"):
        return "lamp"
    if normalized.startswith("window") or normalized.startswith("opaque_window"):
        return "window"
    return None


def light_belongs_to_object(object_name: str, light_name: str) -> bool:
    """Match only the light Blender names for this exact owning object."""

    if not isinstance(object_name, str) or not isinstance(light_name, str):
        return False
    expected = f"{object_name}{LIGHT_SUFFIX}".casefold()
    actual = light_name.casefold()
    return actual == expected or actual.startswith(f"{expected}.")
