"""Load and validate precomputed SOILIE object combinations.

Catalog construction is intentionally separate from runtime sampling. Use
``scripts/rebuild_working_combos.py`` to derive immutable candidate catalogs
from canonical triplet data without overwriting the active runtime files.
"""

from __future__ import annotations

import csv
import random
from functools import lru_cache
from itertools import permutations
from pathlib import Path

from modules.triplet_schema import validate_triplet_columns
from modules.working_combo_schema import (
    MAX_OBJECT_COUNT,
    MIN_OBJECT_COUNT,
    WorkingCombinationError,
    iter_working_combinations,
)


def determine() -> None:
    """Reject the historical in-place, nondeterministic catalog rebuild."""

    raise WorkingCombinationError(
        "in-place catalog generation was removed; run "
        "scripts/rebuild_working_combos.py with a separate output directory"
    )


@lru_cache(maxsize=16)
def _catalog_rows(
    resolved_path: str, size: int, modified_ns: int
) -> tuple[tuple[str, ...], ...]:
    # Size and mtime are cache-key provenance, not unused data. They ensure a
    # replaced development fixture is parsed again while immutable runtimes pay
    # the validation cost only once per worker.
    _ = size, modified_ns
    return tuple(iter_working_combinations(resolved_path))


def _read_catalog(filepath: str | Path) -> tuple[tuple[str, ...], ...]:
    path = Path(filepath).resolve()
    if not path.is_file():
        raise WorkingCombinationError(f"working-combination catalog is missing: {path}")
    metadata = path.stat()
    return _catalog_rows(str(path), metadata.st_size, metadata.st_mtime_ns)


def available_counts(filepath: str | Path) -> tuple[int, ...]:
    """Return the exact object counts represented by validated rows."""

    return tuple(sorted({len(row) for row in _read_catalog(filepath)}))


def _validate_requested_count(n: int) -> None:
    if (
        isinstance(n, bool)
        or not isinstance(n, int)
        or not MIN_OBJECT_COUNT <= n <= MAX_OBJECT_COUNT
    ):
        raise WorkingCombinationError(
            f"requested object count must be an integer from "
            f"{MIN_OBJECT_COUNT} to {MAX_OBJECT_COUNT}"
        )


@lru_cache(maxsize=16)
def _coordinate_triplets_cached(
    filepath: str, size: int, modified_ns: int
) -> frozenset[tuple[str, str, str]]:
    _ = size, modified_ns
    path = Path(filepath)
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        validate_triplet_columns(reader.fieldnames or (), source=str(path))
        return frozenset(
            (row["objectA"], row["objectB"], row["objectC"])
            for row in reader
        )


def _coordinate_triplets(filepath: str | Path) -> frozenset[tuple[str, str, str]]:
    path = Path(filepath).resolve()
    metadata = path.stat()
    return _coordinate_triplets_cached(
        str(path), metadata.st_size, metadata.st_mtime_ns
    )


def supports_coordinate_construction(
    objects: list[str] | tuple[str, ...],
    filepath: str | Path = "./data/triplets.csv",
) -> bool:
    """Return whether distinct objects share one complete ordered A/B basis."""

    distinct_objects = list(dict.fromkeys(objects))
    if len(distinct_objects) < MIN_OBJECT_COUNT:
        return False
    triplets = _coordinate_triplets(filepath)
    return any(
        all(
            obj in (obj_a, obj_b) or (obj_a, obj_b, obj) in triplets
            for obj in distinct_objects
        )
        for obj_a, obj_b in permutations(distinct_objects, 2)
    )


def select_exact(
    n: int,
    *,
    filepath: str | Path = "./data/working-combos-refined.csv",
    triplet_filepath: str | Path = "./data/triplets.csv",
    allow_duplicates: bool,
) -> list[str]:
    """Select exactly ``n`` model-supported instances from a finite row set."""

    _validate_requested_count(n)
    if not isinstance(allow_duplicates, bool):
        raise WorkingCombinationError("allow_duplicates must be true or false")
    candidates = tuple(row for row in _read_catalog(filepath) if len(row) == n)
    if not allow_duplicates:
        candidates = tuple(row for row in candidates if len(set(row)) == n)
    triplets = _coordinate_triplets(triplet_filepath)
    eligible = tuple(
        row
        for row in candidates
        if all((row[0], row[1], object_c) in triplets for object_c in row[2:])
    )
    if eligible:
        # The legacy "weighted" calculation cancelled to a uniform row draw.
        # Both public option names therefore intentionally retain uniformity.
        return list(random.choice(eligible))

    duplicate_policy = "allowing" if allow_duplicates else "disallowing"
    raise WorkingCombinationError(
        f"catalog contains no supported {n}-object rows while {duplicate_policy} duplicates"
    )
