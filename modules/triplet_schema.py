"""Canonical schema definitions for SOILIE triplet relationship data."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from enum import Enum
from pathlib import Path


CANONICAL_TRIPLET_FIELDS = (
    "objectA",
    "objectB",
    "objectC",
    "distanceAB",
    "distanceAC",
    "distanceAO",
    "angleBAC",
    "angleOAB",
    "angleOAC",
)

# Historical exporters wrote BAC, OAB, OAC values in that order but assigned
# these three labels cyclically. Migrating the table therefore changes only
# this header; rearranging the data columns would corrupt their meaning.
LEGACY_MISLABELED_TRIPLET_FIELDS = (
    "objectA",
    "objectB",
    "objectC",
    "distanceAB",
    "distanceAC",
    "distanceAO",
    "angleOAB",
    "angleOAC",
    "angleBAC",
)

DISTANCE_FIELDS = ("distanceAB", "distanceAC", "distanceAO")
ANGLE_FIELDS = ("angleBAC", "angleOAB", "angleOAC")

# Stable public names used by triplet readers. The longer aliases above retain
# the reason the historical header is unsafe while these keep call sites terse.
TRIPLET_COLUMNS = CANONICAL_TRIPLET_FIELDS
LEGACY_TRIPLET_COLUMNS = LEGACY_MISLABELED_TRIPLET_FIELDS


class TripletSchema(str, Enum):
    """The recognized triplet CSV header variants."""

    CANONICAL = "canonical"
    LEGACY_MISLABELED_ANGLES = "legacy_mislabeled_angles"
    UNKNOWN = "unknown"


def detect_triplet_schema(fields: Iterable[str]) -> TripletSchema:
    """Classify an exact triplet field sequence without guessing intent."""

    normalized = tuple(fields)
    if normalized == CANONICAL_TRIPLET_FIELDS:
        return TripletSchema.CANONICAL
    if normalized == LEGACY_MISLABELED_TRIPLET_FIELDS:
        return TripletSchema.LEGACY_MISLABELED_ANGLES
    return TripletSchema.UNKNOWN


def require_canonical_triplet_schema(
    fields: Iterable[str], *, source: str = "triplet data"
) -> None:
    """Reject legacy or unrecognized headers with an actionable error."""

    observed = tuple(fields)
    schema = detect_triplet_schema(observed)
    if schema is TripletSchema.CANONICAL:
        return
    if schema is TripletSchema.LEGACY_MISLABELED_ANGLES:
        raise ValueError(
            f"{source} uses the legacy mislabeled angle header; run "
            "scripts/migrate_triplet_angles.py before reading it"
        )
    raise ValueError(
        f"{source} has an unknown triplet header: {','.join(observed)}; "
        f"expected {','.join(CANONICAL_TRIPLET_FIELDS)}"
    )


def validate_triplet_columns(
    columns: Iterable[str], *, source: str = "triplet data"
) -> None:
    """Validate the public canonical column contract."""

    require_canonical_triplet_schema(columns, source=source)


def validate_triplet_csv_header(path: str | Path) -> None:
    """Validate a triplet file's complete header without loading its data rows."""

    source_path = Path(path)
    with source_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source)
        fields = next(reader, ())
    validate_triplet_columns(fields, source=str(source_path))
