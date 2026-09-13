"""Machine-only audit of the unmodified SOILIE-3D V4 execution path.

The benchmark calls V4's own working-combination loader and calculateCoords
function. It deliberately does not ablate, replace, or approximate the model.
Room-fit output is reported separately because it is a web-interface extension.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from serverless.common.v4_runtime import (
    ROOM_COMBINATION_FILES,
    V4GenerationError,
    generate_v4_inputs,
    load_v4_provenance,
    select_v4_objects,
)


RUN_SEED = 20260913
CASES_PER_MODE = 8
MODES = ("random", "bedroom", "living_room", "kitchen")


def _interior(inputs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {name: value for name, value in inputs.items() if not name.isupper()}


def _base_name(name: str) -> str:
    return name.split(".")[0]


def _relation_label(name: str) -> str:
    label = _base_name(name)
    return "window_treatment" if label in {"window", "blinds", "curtain"} else label


def _raw_overlap_pairs(inputs: dict[str, dict[str, Any]]) -> int:
    objects = list(_interior(inputs).items())
    overlaps = 0
    for index, (_first_name, first) in enumerate(objects):
        for _second_name, second in objects[index + 1 :]:
            distance = math.dist(first["coords"], second["coords"])
            if distance < (first["size"]["diameter"] + second["size"]["diameter"]) / 2:
                overlaps += 1
    return overlaps


def _recency_frame_zero(object_count: int) -> dict[str, Any]:
    """Evaluate V4's literal change_imagination_focus equations."""

    current_index = object_count - 1
    depths = [
        index + 8 - current_index if index < current_index else 0 if index == current_index else -1
        for index in range(object_count)
    ]
    alpha_map = {2: 0.03, 3: 0.05, 4: 0.1, 5: 0.2, 6: 0.35, 7: 0.5, 0: 1, -1: 0, 1: 0.03}
    alphas = [alpha_map[depth] for depth in depths]
    return {
        "objectCount": object_count,
        "octreeDepths": depths,
        "alphas": alphas,
        "visibleAtAlphaGreaterThanZero": sum(alpha > 0 for alpha in alphas),
    }


def evaluate(runtime_root: Path) -> dict[str, Any]:
    rows = []
    selections = Counter()
    mode_success = defaultdict(int)
    timing = []
    deterministic = []
    failures = Counter()

    for mode_index, mode in enumerate(MODES):
        for case_index in range(CASES_PER_MODE):
            seed = RUN_SEED + mode_index * 100_003 + case_index * 997
            request: dict[str, Any] = {
                "mode": "random" if mode == "random" else "room_type",
                "objectCount": 3 + case_index % 4,
                "seed": seed,
                "allowDuplicates": False,
                "sameObjectsAcrossScenes": True,
            }
            if mode != "random":
                request["roomType"] = mode

            objects = select_v4_objects(runtime_root, request, 0)
            started = time.perf_counter()
            try:
                inputs = generate_v4_inputs(runtime_root, objects, seed)
            except V4GenerationError as error:
                duration = (time.perf_counter() - started) * 1000
                timing.append(duration)
                failures["V4_ORDERED_TRIPLET_MISSING"] += 1
                rows.append(
                    {
                        "mode": mode,
                        "seed": seed,
                        "requestedObjectCount": request["objectCount"],
                        "selectedObjectCount": len(objects),
                        "status": "failed",
                        "errorCode": "V4_ORDERED_TRIPLET_MISSING",
                        "error": str(error),
                    }
                )
                continue

            duration = (time.perf_counter() - started) * 1000
            replay = generate_v4_inputs(runtime_root, objects, seed)
            timing.append(duration)
            deterministic.append(inputs == replay)
            mode_success[mode] += 1
            selections.update(_base_name(name) for name in objects)
            interior = _interior(inputs)
            selected_labels = [_base_name(name) for name in objects]
            returned_labels = [_base_name(name) for name in interior]
            rows.append(
                {
                    "mode": mode,
                    "seed": seed,
                    "requestedObjectCount": request["objectCount"],
                    "selectedObjectCount": len(objects),
                    "status": "complete",
                    "returnedInteriorCount": len(interior),
                    "selectedObjects": selected_labels,
                    "returnedObjects": returned_labels,
                    "finiteCoordinates": all(
                        math.isfinite(component)
                        for value in inputs.values()
                        for component in value["coords"]
                    ),
                    "withinV4FiveMetreRetryBoundary": all(
                        abs(component) < 5
                        for value in inputs.values()
                        for component in value["coords"]
                    ),
                    "inputLabelsPreserved": Counter(_base_name(name) for name in objects)
                    <= Counter(_base_name(name) for name in interior),
                    "inputRelationsPreservedAfterWindowNormalization": Counter(
                        _relation_label(name) for name in objects
                    ) <= Counter(_relation_label(name) for name in interior),
                    "rawPreBlenderOverlapPairs": _raw_overlap_pairs(inputs),
                }
            )

    provenance = load_v4_provenance(runtime_root)
    sorted_timing = sorted(timing)
    successful = [row for row in rows if row["status"] == "complete"]
    return {
        "schemaVersion": 2,
        "benchmarkId": "soilie-exact-v4-machine-audit",
        "runDate": "2026-09-13",
        "model": {
            "name": provenance["model"],
            "version": provenance["version"],
            "baselineCommit": provenance["baselineCommit"],
            "assetCount": provenance["assetCount"],
            "sourceFilesVerified": len(provenance["files"]),
            "implementation": "Original V4 functions; no fallback or substitute layout solver.",
        },
        "design": {
            "scenes": len(rows),
            "modes": list(MODES),
            "casesPerMode": CASES_PER_MODE,
            "objectCounts": [3, 4, 5, 6],
            "seed": RUN_SEED,
            "stage": "Object selection through calculateCoords, before Blender's original placement corrections.",
            "roomFitIncluded": False,
        },
        "results": {
            "coordinateGenerationSuccessRate": len(successful) / len(rows),
            "coordinateGenerationFailures": dict(failures),
            "fiveMetreRetryBoundaryPassRateAmongSuccessful": sum(
                row["withinV4FiveMetreRetryBoundary"] for row in successful
            ) / len(successful),
            "inputLabelPreservationRateAmongSuccessful": sum(
                row["inputLabelsPreserved"] for row in successful
            ) / len(successful),
            "inputRelationPreservationRateAfterWindowNormalization": sum(
                row["inputRelationsPreservedAfterWindowNormalization"] for row in successful
            ) / len(successful),
            "requestedObjectCountMatchRate": sum(
                row["requestedObjectCount"] == row["selectedObjectCount"] for row in rows
            ) / len(rows),
            "rawPreBlenderScenesWithOverlapRate": sum(
                row["rawPreBlenderOverlapPairs"] > 0 for row in successful
            ) / len(successful),
            "meanRawPreBlenderOverlapPairs": statistics.fmean(
                row["rawPreBlenderOverlapPairs"] for row in successful
            ),
            "exactSeedReplayRate": sum(deterministic) / len(deterministic),
            "exactSeedReplayScenes": len(deterministic),
            "medianCoordinateGenerationMs": statistics.median(timing),
            "p95CoordinateGenerationMs": sorted_timing[math.ceil(len(sorted_timing) * 0.95) - 1],
            "modeSuccessCounts": dict(mode_success),
            "mostFrequentlySelectedObjects": selections.most_common(12),
        },
        "recencyImplementationAudit": {
            "sourceFunction": "modules.render.change_imagination_focus",
            "finalFocusFrames": [_recency_frame_zero(count) for count in range(3, 9)],
            "interpretation": (
                "These are the literal V4 alpha values. They show graded recency, but do not enforce a strict "
                "five-visible-object cap for six-to-eight-object inputs. This implementation result must not be "
                "rewritten to match the manuscript's stronger prose claim."
            ),
        },
        "presetAvailability": {
            "random": "working-combos-refined.csv",
            **ROOM_COMBINATION_FILES,
            "bathroom": "Unavailable: the original working-combos-bathroom.csv contains no data rows.",
        },
        "metricNotes": {
            "rawPreBlenderOverlap": (
                "A conservative sphere-overlap diagnostic on calculateCoords output. V4 subsequently applies its "
                "own grounding, wall orientation, pair rotation, and overlap/stacking operations in Blender."
            ),
            "requestedObjectCountMatch": (
                "V4 removes repeated labels with set semantics when duplicates are disabled; an exact count can "
                "therefore fall below the requested count while remaining at least three."
            ),
            "inputLabelPreservation": (
                "Exact label matching counts V4's intentional random substitution among window, blinds, and curtain "
                "as a change. The normalized relation score treats those three architectural variants as one role."
            ),
            "roomFit": "The optional boundary wrapper is excluded from every model score.",
        },
        "rows": rows,
    }


def markdown(document: dict[str, Any]) -> str:
    result = document["results"]
    return f"""# Exact SOILIE-3D V4 machine audit

Run date: {document['runDate']}
Model: {document['model']['name']} {document['model']['version']} at `{document['model']['baselineCommit']}`
Design: {document['design']['scenes']} seeded scenes through the original V4 selection and `calculateCoords` path.

| Metric | Result | Meaning |
| --- | ---: | --- |
| Coordinate-generation success | {result['coordinateGenerationSuccessRate']:.1%} | Returned finite V4 coordinates. |
| Five-metre retry-boundary pass | {result['fiveMetreRetryBoundaryPassRateAmongSuccessful']:.1%} | Successful samples met V4's own retry condition. |
| Input-label preservation | {result['inputLabelPreservationRateAmongSuccessful']:.1%} | Exact selected labels remained; a V4 window/blind/curtain rewrite counts as a change. |
| Relation preservation after window normalization | {result['inputRelationPreservationRateAfterWindowNormalization']:.1%} | Treats V4's window/blind/curtain substitution as one architectural role. |
| Requested-count match | {result['requestedObjectCountMatchRate']:.1%} | Duplicate removal did not reduce the requested count. |
| Raw scenes with a sphere overlap | {result['rawPreBlenderScenesWithOverlapRate']:.1%} | Pre-Blender diagnostic, before V4's own stacking/separation pass. |
| Exact seeded replay | {result['exactSeedReplayRate']:.1%} | Repeated runs returned byte-equivalent data. |
| Median coordinate time | {result['medianCoordinateGenerationMs'] / 1000:.2f} s | V4 selection excluded; render time excluded. |
| p95 coordinate time | {result['p95CoordinateGenerationMs'] / 1000:.2f} s | Slowest 5% threshold in this fixed sample. |

The room-sizing wrapper is excluded. The recency table in the JSON records V4's literal alpha schedule and its discrepancy with a strict five-visible-object claim. Published LayoutGPT, GRAINS, and Infinigen Indoors metrics remain separate because their datasets and tasks are not matched.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = evaluate(args.runtime.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "soilie-exact-v4.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    (args.output / "soilie-exact-v4.md").write_text(markdown(document), encoding="utf-8")


if __name__ == "__main__":
    main()
