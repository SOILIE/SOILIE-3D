"""The public counting contract, without importing the renderer's core modules.

The API image intentionally has no ``modules`` package. The compiler checks
these constants against the human-authored core schema before emitting an
index. Counting never selects, drops, duplicates, or reorders scene objects.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
import re
from typing import Any

MIN_OBJECT_COUNT = 3
MAX_OBJECT_COUNT = 8
MAX_WINDOW_EXTRA_COUNT = 1
MAX_TOTAL_OBJECT_COUNT = MAX_OBJECT_COUNT + MAX_WINDOW_EXTRA_COUNT
WINDOW_FAMILY = frozenset({"window", "opaque_window"})
OBJECT_COUNT_DEFINITION = "non-window-instances-v1"
EXPLICIT_COUNT_DEFINITION = "explicit-list-length-v1"
RUNTIME_INDEX_VERSION = 3
OBJECT_COUNT_POLICY = {
    "definition": OBJECT_COUNT_DEFINITION,
    "minimum": MIN_OBJECT_COUNT,
    "maximum": MAX_OBJECT_COUNT,
    "windowFamily": sorted(WINDOW_FAMILY),
    "maximumSelectedWindowExtras": MAX_WINDOW_EXTRA_COUNT,
    "maximumSelectedTotal": MAX_TOTAL_OBJECT_COUNT,
    "selectionPolicy": "uniform-available-window-bands-then-uniform-supported-row-v1",
    "explicitList": {
        "definition": EXPLICIT_COUNT_DEFINITION,
        "minimumTotal": MIN_OBJECT_COUNT,
        "maximumTotal": MAX_OBJECT_COUNT,
        "preserveExactList": True,
    },
}
_CANONICAL_LABEL = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*\Z")


def window_extra_count(objects: Iterable[str]) -> int:
    return sum(label in WINDOW_FAMILY for label in objects)


def counted_object_count(objects: Iterable[str]) -> int:
    return sum(label not in WINDOW_FAMILY for label in objects)


def inventory_counts(objects: Iterable[str]) -> dict[str, int]:
    """Describe the complete selected inventory; renderer companions are separate."""
    values = tuple(objects)
    return {
        "countedObjectCount": counted_object_count(values),
        "selectedWindowCount": window_extra_count(values),
        "selectedTotalCount": len(values),
    }


def validate_selection(request: dict[str, Any], objects: Sequence[str]) -> dict[str, Any]:
    """Validate one already-selected inventory without changing any instance.

Explicit object lists keep their ordered literal meaning, including all-window
lists. Only count-driven modes use the non-window quota and optional-extra cap.
"""
    if (not isinstance(objects, (list, tuple))
            or any(not isinstance(label, str) or not _CANONICAL_LABEL.fullmatch(label) for label in objects)):
        raise ValueError("Selected objects must be a complete list of canonical object labels")
    counts = inventory_counts(objects)
    mode = request.get("mode")
    if mode == "objects":
        supplied = request.get("objects")
        if (not isinstance(supplied, list)
                or not MIN_OBJECT_COUNT <= len(supplied) <= MAX_OBJECT_COUNT
                or list(objects) != supplied):
            raise ValueError("Explicit object selection must exactly preserve the supplied 3-8-instance list")
        definition = EXPLICIT_COUNT_DEFINITION
        requested = len(supplied)
    elif mode in ("random", "room_type"):
        requested = request.get("objectCount")
        if type(requested) is not int or not MIN_OBJECT_COUNT <= requested <= MAX_OBJECT_COUNT:
            raise ValueError("Count-driven requests require an integer from 3 to 8 non-window instances")
        if (counts["countedObjectCount"] != requested
                or counts["selectedWindowCount"] > MAX_WINDOW_EXTRA_COUNT
                or counts["selectedTotalCount"] > MAX_TOTAL_OBJECT_COUNT):
            raise ValueError("Selection differs from the requested non-window count or optional-window capacity")
        allow_duplicates = request.get("allowDuplicates", False)
        if not isinstance(allow_duplicates, bool):
            raise ValueError("allowDuplicates must be true or false")
        if not allow_duplicates and len(set(objects)) != len(objects):
            raise ValueError("Selection contains duplicate instances while duplicates are disabled")
        definition = OBJECT_COUNT_DEFINITION
    else:
        raise ValueError("Selection requires an explicit objects, random, or room_type mode")
    return {
        "objectCountDefinition": definition,
        "requestedObjectCount": requested,
        **counts,
        # Retain the legacy field's literal meaning; it is not the quota anymore.
        "selectedObjectCount": counts["selectedTotalCount"],
    }
