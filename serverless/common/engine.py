"""Public request and catalog boundary for the exact SOILIE-3D runtime.

This module intentionally does not generate or adjust layouts. Scene selection,
triplet sampling, coordinate construction, Blender placement, and depiction are
owned by the model modules staged in the renderer image. Keeping API
validation here lets unsupported work fail before it reaches the render queue
without introducing a second scene model.
"""

from __future__ import annotations

import itertools
import json
import random
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable

from serverless.common.model_version import model_document


# Keep the API package self-contained: its production image intentionally does
# not copy the renderer's ``modules`` package. These values mirror the strict
# working-catalog contract that the compiler validates before deployment.
MIN_OBJECT_COUNT = 3
MAX_OBJECT_COUNT = 8


ROOM_TYPES = ("bedroom", "living_room", "kitchen", "bathroom")
MODES = ("random", "objects", "room_type")
ARCHITECTURE = {
    "wall",
    "wooden_wall",
    "door",
    "window",
    "opaque_window",
    "power_outlet",
    "switch",
}


class RequestError(ValueError):
    """Stable, client-readable validation error."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def normalize_label(value: str) -> str:
    """Normalize browser spelling without changing the model object taxonomy."""

    return "_".join(str(value).strip().lower().replace("-", " ").split())


def _require_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RequestError("INVALID_REQUEST", f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def _require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise RequestError("INVALID_REQUEST", f"{name} must be true or false.")
    return value


def catalog(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT name, display_name, size_category FROM objects WHERE selectable = 1 ORDER BY display_name"
    ).fetchall()
    return [
        {"name": row[0], "displayName": row[1], "sizeCategory": row[2]}
        for row in rows
    ]


def _known_objects(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM objects WHERE selectable = 1")}


def _triplet_keys(connection: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return {
        (row[0], row[1], row[2])
        for row in connection.execute("SELECT object_a, object_b, object_c FROM triplets")
    }


def is_supported_combination(connection: sqlite3.Connection, objects: Iterable[str]) -> bool:
    """Mirror calculateCoords' exact ordered-triplet feasibility test."""

    names = list(objects)
    if len(names) < 3:
        return False
    triplets = _triplet_keys(connection)
    for ordering in set(itertools.permutations(names, len(names))):
        good = [
            (ordering[0], ordering[1], item)
            for item in ordering[2:]
            if (ordering[0], ordering[1], item) in triplets
        ]
        if all(name in {value for triple in good for value in triple} for name in names):
            return True
    return False


def compatible_suggestions(connection: sqlite3.Connection, objects: list[str], limit: int = 3) -> list[list[str]]:
    """Return real rows from the model's precomputed working-combination files."""

    requested = set(objects)
    candidates: list[tuple[int, tuple[str, ...]]] = []
    for (encoded,) in connection.execute(
        "SELECT objects_json FROM working_combinations "
        "WHERE object_count = ? ORDER BY preset, row_index",
        (len(objects),),
    ):
        candidate = tuple(json.loads(encoded))
        candidates.append((len(requested & set(candidate)), candidate))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    suggestions: list[list[str]] = []
    for _overlap, candidate in candidates:
        value = list(candidate)
        if value not in suggestions:
            suggestions.append(value)
        if len(suggestions) == limit:
            break
    return suggestions


def _validate_room(payload: Any) -> dict[str, Any]:
    if payload is None:
        payload = {"mode": "auto"}
    if not isinstance(payload, dict):
        raise RequestError("INVALID_REQUEST", "room must be an object.")
    mode = payload.get("mode", "auto")
    if mode not in {"auto", "custom"}:
        raise RequestError("INVALID_REQUEST", "room.mode must be auto or custom.")
    room: dict[str, Any] = {"mode": mode}
    if mode == "custom":
        for field in ("widthM", "depthM"):
            value = payload.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1.0 <= float(value) <= 20.0:
                raise RequestError("INVALID_REQUEST", f"room.{field} must be between 1.0 and 20.0 metres.")
            rounded = round(float(value), 1)
            if abs(float(value) - rounded) > 1e-9:
                raise RequestError("INVALID_REQUEST", f"room.{field} must use increments of 0.1 metre.")
            room[field] = rounded
    return room


def validate_request(connection: sqlite3.Connection, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RequestError("INVALID_REQUEST", "The request body must be a JSON object.")

    try:
        client_request_id = str(uuid.UUID(str(payload.get("clientRequestId", ""))))
    except (ValueError, TypeError, AttributeError) as error:
        raise RequestError("INVALID_REQUEST", "clientRequestId must be a UUID.") from error

    mode = normalize_label(payload.get("mode", "random"))
    if mode not in MODES:
        raise RequestError("INVALID_REQUEST", f"mode must be one of: {', '.join(MODES)}.")

    scene_count = _require_int(payload.get("sceneCount", 1), "sceneCount", 1, 3)
    seed = payload.get("seed")
    if seed is None:
        seed = random.SystemRandom().randint(0, 2_147_483_647)
    seed = _require_int(seed, "seed", 0, 2_147_483_647)

    normalized: dict[str, Any] = {
        "clientRequestId": client_request_id,
        "mode": mode,
        "sceneCount": scene_count,
        "room": _validate_room(payload.get("room")),
        "seed": seed,
    }
    known = _known_objects(connection)

    if mode == "objects":
        raw_objects = payload.get("objects")
        if (
            not isinstance(raw_objects, list)
            or not MIN_OBJECT_COUNT <= len(raw_objects) <= MAX_OBJECT_COUNT
        ):
            raise RequestError(
                "INVALID_REQUEST",
                f"objects must contain {MIN_OBJECT_COUNT} to {MAX_OBJECT_COUNT} object labels.",
            )
        objects = [normalize_label(value) for value in raw_objects]
        unknown = sorted({value for value in objects if value not in known})
        if unknown:
            raise RequestError(
                "UNKNOWN_OBJECT",
                "One or more object labels are not available in SOILIE-3D.",
                {"objects": unknown},
            )
        if not is_supported_combination(connection, objects):
            raise RequestError(
                "UNSUPPORTED_OBJECT_COMBINATION",
                "SOILIE-3D does not contain the ordered triplets required to construct that object set.",
                {"suggestions": compatible_suggestions(connection, objects)},
            )
        normalized["objects"] = objects
    else:
        object_count = _require_int(
            payload.get("objectCount", MIN_OBJECT_COUNT),
            "objectCount",
            MIN_OBJECT_COUNT,
            MAX_OBJECT_COUNT,
        )
        normalized["objectCount"] = object_count
        allow_duplicates = _require_bool(
            payload.get("allowDuplicates", False), "allowDuplicates"
        )
        normalized["allowDuplicates"] = allow_duplicates
        normalized["sameObjectsAcrossScenes"] = _require_bool(
            payload.get("sameObjectsAcrossScenes", True), "sameObjectsAcrossScenes"
        )
        preset = "random"
        if mode == "room_type":
            room_type = normalize_label(payload.get("roomType", ""))
            if room_type not in ROOM_TYPES:
                raise RequestError("INVALID_REQUEST", f"roomType must be one of: {', '.join(ROOM_TYPES)}.")
            normalized["roomType"] = room_type
            preset = room_type

        available = connection.execute(
            "SELECT 1 FROM working_combinations "
            "WHERE preset = ? AND object_count = ? "
            "AND (? = 1 OR has_duplicates = 0) LIMIT 1",
            (preset, object_count, int(allow_duplicates)),
        ).fetchone()
        if not available:
            if mode == "room_type":
                message = (
                    f"The SOILIE-3D {preset.replace('_', ' ')} preset has no "
                    f"canonical {object_count}-object combinations."
                )
                code = "UNSUPPORTED_ROOM_PRESET"
            else:
                message = (
                    f"SOILIE-3D has no canonical random {object_count}-object "
                    "combinations."
                )
                code = "UNSUPPORTED_OBJECT_COUNT"
            raise RequestError(code, message)

    return normalized


def open_runtime(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def public_catalog_document(connection: sqlite3.Connection) -> dict[str, Any]:
    presets = {
        room: [
            row[0]
            for row in connection.execute(
                "SELECT object_name FROM preset_members WHERE preset = ? ORDER BY object_name", (room,)
            )
        ]
        for room in ROOM_TYPES
    }
    preset_object_counts = {
        preset: {
            "withoutDuplicates": [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT object_count FROM working_combinations "
                    "WHERE preset = ? AND has_duplicates = 0 ORDER BY object_count",
                    (preset,),
                )
            ],
            "allowingDuplicates": [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT object_count FROM working_combinations "
                    "WHERE preset = ? ORDER BY object_count",
                    (preset,),
                )
            ],
        }
        for preset in ("random", *ROOM_TYPES)
    }
    return {
        "schemaVersion": 2,
        "model": model_document(),
        "objects": catalog(connection),
        "roomTypes": list(ROOM_TYPES),
        "presets": presets,
        "presetObjectCounts": preset_object_counts,
        "limits": {
            "sceneCount": {"minimum": 1, "maximum": 3},
            "objectCount": {
                "minimum": MIN_OBJECT_COUNT,
                "maximum": MAX_OBJECT_COUNT,
            },
            "roomDimensionM": {"minimum": 1.0, "maximum": 20.0, "step": 0.1},
        },
    }


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
