"""Public request and catalog boundary for the exact SOILIE-3D V4 runtime.

This module intentionally does not generate or adjust layouts. Scene selection,
triplet sampling, coordinate construction, Blender placement, and depiction are
owned by the original V4 modules staged in the renderer image. Keeping API
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
    """Normalize browser spelling without changing V4 object taxonomy."""

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
    """Mirror V4 calculateCoords' exact ordered-triplet feasibility test."""

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
    """Return real rows from V4's precomputed working-combination files."""

    requested = set(objects)
    candidates: list[tuple[int, tuple[str, ...]]] = []
    for (encoded,) in connection.execute(
        "SELECT objects_json FROM working_combinations ORDER BY preset, row_index",
    ):
        candidate = tuple(json.loads(encoded)[: len(objects)])
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
        if not isinstance(raw_objects, list) or not 3 <= len(raw_objects) <= 8:
            raise RequestError("INVALID_REQUEST", "objects must contain 3 to 8 object labels.")
        objects = [normalize_label(value) for value in raw_objects]
        unknown = sorted({value for value in objects if value not in known})
        if unknown:
            raise RequestError(
                "UNKNOWN_OBJECT",
                "One or more object labels are not available in SOILIE V4.",
                {"objects": unknown},
            )
        if not is_supported_combination(connection, objects):
            raise RequestError(
                "UNSUPPORTED_OBJECT_COMBINATION",
                "SOILIE V4 does not contain the ordered triplets required to construct that object set.",
                {"suggestions": compatible_suggestions(connection, objects)},
            )
        normalized["objects"] = objects
    else:
        normalized["objectCount"] = _require_int(payload.get("objectCount", 3), "objectCount", 3, 6)
        normalized["allowDuplicates"] = _require_bool(payload.get("allowDuplicates", False), "allowDuplicates")
        normalized["sameObjectsAcrossScenes"] = _require_bool(
            payload.get("sameObjectsAcrossScenes", True), "sameObjectsAcrossScenes"
        )
        if mode == "room_type":
            room_type = normalize_label(payload.get("roomType", ""))
            if room_type not in ROOM_TYPES:
                raise RequestError("INVALID_REQUEST", f"roomType must be one of: {', '.join(ROOM_TYPES)}.")
            available = connection.execute(
                "SELECT 1 FROM working_combinations WHERE preset = ? LIMIT 1", (room_type,)
            ).fetchone()
            if not available:
                raise RequestError(
                    "UNSUPPORTED_ROOM_PRESET",
                    f"The original V4 {room_type.replace('_', ' ')} working-combination file contains no usable rows.",
                )
            normalized["roomType"] = room_type

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
    return {
        "schemaVersion": 2,
        "model": model_document(),
        "objects": catalog(connection),
        "roomTypes": list(ROOM_TYPES),
        "presets": presets,
        "limits": {
            "sceneCount": {"minimum": 1, "maximum": 3},
            "objectCount": {"minimum": 3, "maximum": 6},
            "roomDimensionM": {"minimum": 1.0, "maximum": 20.0, "step": 0.1},
        },
    }


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
