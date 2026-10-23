"""Canonical CSV contract for precomputed SOILIE working combinations."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Iterator
from pathlib import Path


MIN_OBJECT_COUNT = 3
MAX_OBJECT_COUNT = 8
WORKING_COMBINATION_FIELDS = tuple(
    f"obj{index}" for index in range(MAX_OBJECT_COUNT)
)
LEGACY_SIX_COLUMN_FIELDS = tuple(f"obj{index}" for index in range(6))

CATALOG_FILENAMES = {
    "random": "working-combos-refined.csv",
    "bedroom": "working-combos-bedroom.csv",
    "living_room": "working-combos-livingroom.csv",
    "kitchen": "working-combos-kitchen.csv",
    "bathroom": "working-combos-bathroom.csv",
}

_CANONICAL_LABEL = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*\Z")


class WorkingCombinationError(ValueError):
    """A catalog cannot safely satisfy the canonical object-count contract."""


def normalize_object_label(value: object) -> str:
    """Return the API/model spelling used by canonical catalog rows."""

    return "_".join(str(value).strip().lower().replace("-", " ").split())


def validate_working_combination_header(
    fields: Iterable[str], *, source: str = "working-combination catalog"
) -> None:
    """Require the exact eight-column header; never guess at legacy data."""

    observed = tuple(fields)
    if observed == WORKING_COMBINATION_FIELDS:
        return
    if observed == LEGACY_SIX_COLUMN_FIELDS:
        raise WorkingCombinationError(
            f"{source} uses the legacy six-column header; rebuild it with "
            "scripts/rebuild_working_combos.py before requesting 3-8 objects"
        )
    raise WorkingCombinationError(
        f"{source} has an unknown header: {','.join(observed)}; expected "
        f"{','.join(WORKING_COMBINATION_FIELDS)}"
    )


def _parse_row(
    row: dict[str | None, str | list[str] | None], *, source: str, line_number: int
) -> tuple[str, ...]:
    extras = row.get(None)
    if extras:
        raise WorkingCombinationError(
            f"{source} row {line_number} has more than {MAX_OBJECT_COUNT} fields"
        )

    raw_values: list[str] = []
    for field in WORKING_COMBINATION_FIELDS:
        value = row.get(field)
        if not isinstance(value, str):
            raise WorkingCombinationError(
                f"{source} row {line_number} is missing {field}"
            )
        raw_values.append(value.strip())

    first_blank = next(
        (index for index, value in enumerate(raw_values) if not value),
        len(raw_values),
    )
    if any(raw_values[first_blank:]):
        raise WorkingCombinationError(
            f"{source} row {line_number} has a nonempty field after a blank; "
            "catalog padding must be trailing"
        )

    objects = tuple(raw_values[:first_blank])
    if not MIN_OBJECT_COUNT <= len(objects) <= MAX_OBJECT_COUNT:
        raise WorkingCombinationError(
            f"{source} row {line_number} contains {len(objects)} objects; expected "
            f"{MIN_OBJECT_COUNT}-{MAX_OBJECT_COUNT}"
        )
    for label in objects:
        if label != normalize_object_label(label) or not _CANONICAL_LABEL.fullmatch(label):
            raise WorkingCombinationError(
                f"{source} row {line_number} contains noncanonical label {label!r}"
            )
    return objects


def iter_working_combinations(path: str | Path) -> Iterator[tuple[str, ...]]:
    """Yield validated rows while retaining repeated labels as instances."""

    source_path = Path(path)
    with source_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        validate_working_combination_header(
            reader.fieldnames or (), source=str(source_path)
        )
        for line_number, row in enumerate(reader, start=2):
            yield _parse_row(row, source=str(source_path), line_number=line_number)


def padded_catalog_row(objects: Iterable[str]) -> tuple[str, ...]:
    """Return one canonical fixed-width CSV row."""

    values = tuple(objects)
    if not MIN_OBJECT_COUNT <= len(values) <= MAX_OBJECT_COUNT:
        raise WorkingCombinationError(
            f"working combination contains {len(values)} objects; expected "
            f"{MIN_OBJECT_COUNT}-{MAX_OBJECT_COUNT}"
        )
    for label in values:
        if label != normalize_object_label(label) or not _CANONICAL_LABEL.fullmatch(label):
            raise WorkingCombinationError(
                f"working combination contains noncanonical label {label!r}"
            )
    return values + ("",) * (MAX_OBJECT_COUNT - len(values))
