"""Small Blender naming helpers kept independent from the Blender runtime."""

from __future__ import annotations

import re


_COPY_SUFFIX = re.compile(r"\.(\d+)$")


def blender_copy_index(name: str) -> int:
    """Return Blender's duplicate suffix, without mistaking an asset ID for it."""

    match = _COPY_SUFFIX.search(name)
    return int(match.group(1)) if match else 0


def blender_source_name(name: str) -> str:
    """Remove only Blender's instance suffix while retaining the mesh asset ID."""

    return _COPY_SUFFIX.sub("", name)
