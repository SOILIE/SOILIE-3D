"""Pure helpers for V4's affine coordinate composition."""

from __future__ import annotations

from collections.abc import Sequence


def transformed_appended_coordinate(rows: Sequence[Sequence[float]]) -> list[float]:
    """Return the transformed point appended after the four affine anchors."""

    if len(rows) == 0:
        raise ValueError("The affine transform returned no coordinate rows")
    return [float(component) for component in rows[-1]]
