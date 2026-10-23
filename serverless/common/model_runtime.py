"""Thin invocation adapter for the repository's SOILIE-3D model code.

No placement, rendering, recency, asset, or recovery logic is duplicated here.
The adapter seeds the model's existing random sources, maps the public request to the
same working-combination files used by the terminal menu, and calls
``modules.prepare_data.calculateCoords`` directly.
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

SCENE_SEED_STEP = 104_729
ROOM_COMBINATION_FILES = {
    "bedroom": "working-combos-bedroom.csv",
    "living_room": "working-combos-livingroom.csv",
    "kitchen": "working-combos-kitchen.csv",
    "bathroom": "working-combos-bathroom.csv",
}


class ModelGenerationError(RuntimeError):
    """The SOILIE-3D pipeline could not construct the requested scene."""


@contextlib.contextmanager
def _model_working_directory(runtime_root: Path) -> Iterator[None]:
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
    with _model_working_directory(runtime_root):
        prepare_data = importlib.import_module("modules.prepare_data")
        working_combos = importlib.import_module("modules.working_combos")
        png2gif = importlib.import_module("modules.png2gif")
    return prepare_data, working_combos, png2gif


def seed_model(seed: int) -> None:
    """Seed only the random generators already consumed by the model."""

    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed % (2**32))
    except ImportError as error:  # pragma: no cover - the renderer image installs model requirements.
        raise ModelGenerationError("The SOILIE-3D numerical runtime is unavailable.") from error


def select_model_objects(runtime_root: Path, request: dict[str, Any], scene_index: int) -> list[str]:
    """Map a public generation mode to the model's terminal selection path."""

    if request["mode"] == "objects":
        return list(request["objects"])

    _prepare_data, working_combos, _png2gif = _load_original_modules(runtime_root)
    selection_seed = request["seed"]
    if not request.get("sameObjectsAcrossScenes", True):
        selection_seed += scene_index * SCENE_SEED_STEP
    seed_model(selection_seed)

    filename = "working-combos-refined.csv"
    if request["mode"] == "room_type":
        filename = ROOM_COMBINATION_FILES[request["roomType"]]

    requested_count = request["objectCount"]
    # There is deliberately no substitute catalog or fallback selection path.
    # The model loader retains repeated labels as instances when requested and
    # otherwise filters them before one finite exact-count selection.
    with _model_working_directory(runtime_root):
        try:
            objects = working_combos.select_exact(
                requested_count,
                filepath=str(runtime_root / "data" / filename),
                triplet_filepath=str(runtime_root / "data" / "triplets.csv"),
                allow_duplicates=request.get("allowDuplicates", False),
            )
        except working_combos.WorkingCombinationError as error:
            raise ModelGenerationError(
                f"SOILIE-3D could not select the exact requested object count: {error}"
            ) from error
    if len(objects) != requested_count or any(
        not isinstance(label, str) or not label for label in objects
    ):
        raise ModelGenerationError(
            "SOILIE-3D returned a selection that differs from the requested object count."
        )
    return objects


def _generate_model_inputs(
    runtime_root: Path,
    objects: list[str],
    seed: int,
    *,
    return_alignment_diagnostics: bool,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Run coordinate construction and retain diagnostics for the accepted try."""

    prepare_data, _working_combos, _png2gif = _load_original_modules(runtime_root)
    seed_model(seed)
    attempt = 1
    with _model_working_directory(runtime_root):
        while True:
            try:
                arguments = {
                    "prev_text_len": 0,
                    "progbar_prefix": f"SOILIE-3D coordinate pass {attempt}",
                }
                if return_alignment_diagnostics:
                    arguments["return_alignment_diagnostics"] = True
                    coordinates, _progress, diagnostics = prepare_data.calculateCoords(
                        objects, **arguments,
                    )
                else:
                    coordinates, _progress = prepare_data.calculateCoords(objects, **arguments)
                    diagnostics = []
            except SystemExit as error:
                raise ModelGenerationError(
                    "SOILIE-3D could not find the ordered triplets required for this scene."
                ) from error
            except prepare_data.TripletGeometryError as error:
                raise ModelGenerationError(
                    f"SOILIE-3D rejected invalid triplet geometry: {error}"
                ) from error
            if coordinates is not None and all(
                all(abs(component) < 5 for component in value["coords"])
                for value in coordinates.values()
            ):
                return coordinates, diagnostics
            attempt += 1


def generate_model_inputs(
    runtime_root: Path,
    objects: list[str],
    seed: int,
) -> dict[str, dict[str, Any]]:
    """Run the model's triplet sampler with its established coordinate-only API."""

    coordinates, _diagnostics = _generate_model_inputs(
        runtime_root,
        objects,
        seed,
        return_alignment_diagnostics=False,
    )
    return coordinates


def generate_model_inputs_with_diagnostics(
    runtime_root: Path,
    objects: list[str],
    seed: int,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return model inputs plus rigid-alignment residuals for scientific audit."""

    return _generate_model_inputs(
        runtime_root,
        objects,
        seed,
        return_alignment_diagnostics=True,
    )


def animate_model_output(runtime_root: Path, filename: str, output_path: Path) -> Path:
    """Call the model's five-interpolation-frame GIF builder unchanged."""

    _prepare_data, _working_combos, png2gif = _load_original_modules(runtime_root)
    with _model_working_directory(runtime_root):
        png2gif.animate_output(filename, str(output_path))
    return output_path / f"{Path(filename).stem}.gif"


def load_model_provenance(runtime_root: Path) -> dict[str, Any]:
    path = runtime_root / "model-provenance.json"
    if not path.exists():
        path = runtime_root / ".codex" / "runtime" / "model-provenance.json"
    return json.loads(path.read_text(encoding="utf-8"))
