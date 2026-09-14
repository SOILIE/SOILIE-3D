"""Thin invocation adapter for the repository's original SOILIE-3D V4 code.

No placement, rendering, recency, asset, or recovery logic is duplicated here.
The adapter seeds V4's existing random sources, maps the public request to the
same working-combination files used by the terminal menu, and calls
``modules.prepare_data.calculateCoords`` unchanged.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Iterator

from serverless.common.model_version import MODEL_VERSION


V4_VERSION = MODEL_VERSION
SCENE_SEED_STEP = 104_729
ROOM_COMBINATION_FILES = {
    "bedroom": "working-combos-bedroom.csv",
    "living_room": "working-combos-livingroom.csv",
    "kitchen": "working-combos-kitchen.csv",
    "bathroom": "working-combos-bathroom.csv",
}


class V4GenerationError(RuntimeError):
    """The original V4 pipeline could not construct the requested scene."""


@contextlib.contextmanager
def _v4_working_directory(runtime_root: Path) -> Iterator[None]:
    previous = Path.cwd()
    root = runtime_root.resolve()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(previous)


def _load_original_modules(runtime_root: Path):
    with _v4_working_directory(runtime_root):
        prepare_data = importlib.import_module("modules.prepare_data")
        working_combos = importlib.import_module("modules.working_combos")
        png2gif = importlib.import_module("modules.png2gif")
    return prepare_data, working_combos, png2gif


def seed_v4(seed: int) -> None:
    """Seed only the random generators already consumed by V4."""

    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed % (2**32))
    except ImportError as error:  # pragma: no cover - the renderer image installs V4 requirements.
        raise V4GenerationError("The original V4 numerical runtime is unavailable.") from error


def select_v4_objects(runtime_root: Path, request: dict[str, Any], scene_index: int) -> list[str]:
    """Map a public generation mode to V4's original terminal selection path."""

    if request["mode"] == "objects":
        return list(request["objects"])

    _prepare_data, working_combos, _png2gif = _load_original_modules(runtime_root)
    selection_seed = request["seed"]
    if not request.get("sameObjectsAcrossScenes", True):
        selection_seed += scene_index * SCENE_SEED_STEP
    seed_v4(selection_seed)

    filename = "working-combos-refined.csv"
    if request["mode"] == "room_type":
        filename = ROOM_COMBINATION_FILES[request["roomType"]]

    # This loop is the terminal interface's existing duplicate-removal loop.
    # There is deliberately no substitute catalog or fallback selection path.
    with _v4_working_directory(runtime_root):
        while True:
            objects = working_combos.load(
                request["objectCount"],
                filepath=str(runtime_root / "data" / filename),
            )
            if not request.get("allowDuplicates", False):
                objects = list(set(objects))
            if len(objects) >= 3:
                return objects


def generate_v4_inputs(
    runtime_root: Path,
    objects: list[str],
    seed: int,
) -> dict[str, dict[str, Any]]:
    """Run V4's original triplet sampler and coordinate construction exactly."""

    prepare_data, _working_combos, _png2gif = _load_original_modules(runtime_root)
    seed_v4(seed)
    attempt = 1
    with _v4_working_directory(runtime_root):
        while True:
            try:
                coordinates, _progress = prepare_data.calculateCoords(
                    objects,
                    prev_text_len=0,
                    progbar_prefix=f"V4 coordinate pass {attempt}",
                )
            except SystemExit as error:
                raise V4GenerationError(
                    "SOILIE V4 could not find the ordered triplets required for this scene."
                ) from error
            if coordinates is not None and all(
                all(abs(component) < 5 for component in value["coords"])
                for value in coordinates.values()
            ):
                return coordinates
            attempt += 1


def animate_v4_output(runtime_root: Path, filename: str, output_path: Path) -> Path:
    """Call V4's original five-interpolation-frame GIF builder unchanged."""

    _prepare_data, _working_combos, png2gif = _load_original_modules(runtime_root)
    with _v4_working_directory(runtime_root):
        png2gif.animate_output(filename, str(output_path))
    return output_path / f"{Path(filename).stem}.gif"


def load_v4_provenance(runtime_root: Path) -> dict[str, Any]:
    path = runtime_root / "v4-provenance.json"
    if not path.exists():
        path = runtime_root / ".codex" / "runtime" / "v4-provenance.json"
    return json.loads(path.read_text(encoding="utf-8"))
