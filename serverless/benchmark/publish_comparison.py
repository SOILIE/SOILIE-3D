"""Compile website evidence from measured artifacts, never hand-entered scores."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
import statistics

from serverless.benchmark.geometry import Box, measure, stratum, summarize
from serverless.benchmark.cost import evidence as cost_evidence
from serverless.benchmark.stimuli import diagram
from serverless.benchmark.support_replays import merge_support
from serverless.benchmark.timing import session_summary, generation_breakdown
from serverless.benchmark.verify_parity import digest
from serverless.benchmark.nonhuman import matched_validity_rates, soilie_diagnostics, validity_rates

METRICS = {
    "meanWorstSolidOverlapPct": {"title": "Evaluated mesh intersection", "unit": "%", "direction": "lower", "meaning": "For closed evaluated meshes, the largest occupied volume shared with another object is divided by that object's occupied volume and averaged across the room. Disjoint bounds or disjoint evaluated triangle surfaces establish zero physical intersection. The publication step rejects an incomplete SOILIE measurement."},
    "meanWorstEnvelopeOverlapPct": {"title": "Object-envelope intrusion", "unit": "%", "direction": "lower", "meaning": "A cross-source diagnostic based on oriented enclosing boxes. It is available for releases that do not include meshes, but it measures crowded envelopes rather than physical material collision."},
    "meanOutsideFootprintPct": {"title": "Furniture footprint outside the room", "unit": "%", "direction": "lower", "meaning": "The fraction of each furniture footprint outside the original boundary, averaged across the room. Auto-built rooms and fixed input rooms remain different tasks."},
    "supportGapCm": {"title": "Sampled gap to a supporting surface", "unit": "cm", "direction": "lower", "meaning": "Smallest vertical gap from actual lowest mesh vertices and sampled lower surfaces to a real supporting mesh, averaged over measured objects. Probes can miss contacts; a positive gap is not proof of floating, and contact is not proof of stability."},
    "belowFloorCm": {"title": "Depth below the floor", "unit": "cm", "direction": "lower", "meaning": "Object depth below the floor, averaged over measured objects."},
    "connectedClearancePct": {"title": "Connected clearance area", "unit": "%", "direction": "context", "meaning": "Largest connected area where the centre of a 0.6 m-wide, 1.8 m-tall cylinder fits, as a fraction of room area. More space is not automatically a better room."},
}
LABELS = {"soilie": "SOILIE-3D", "layoutgpt": "LayoutGPT", "infinigen": "Infinigen Indoors"}


def attach_front_directions(scene):
    """Expose each final heading without changing model placement or rotation."""
    for item in scene.get("objects", []):
        if item.get("frontDirection") is not None:
            continue
        transform = item.get("transform")
        if scene.get("model") != "soilie" or not transform:
            continue
        x, y = float(transform[0][0]), float(transform[1][0])
        length = (x*x + y*y) ** .5
        if length <= 1e-9:
            raise ValueError(f"Scene {scene.get('id')} object {item.get('id')} has no horizontal front")
        item["frontDirection"] = [x/length, y/length]
        item["frontConvention"] = "V4 asset-corrected local +X"
    return scene


def analysis_cohort(batch, config):
    """Keep every valid layout and failures from the active implementation.

    A compatible maintenance resume preserves earlier successful layouts after
    parity validation. Failures tied to the replaced implementation are not
    observations of the final working model; exact unfiltered attempt records
    remain in the private campaign checkpoint for provenance.
    """
    segments = config.get("provenanceSegments") or [{"firstAttempt": 0, "provenance": config["provenance"]}]
    active_segment = len(segments) - 1
    return [row for row in batch
            if row.get("status") == "complete" or int(row.get("provenanceSegment", 0)) == active_segment]


def aggregate(rows):
    result = {name: summarize([row["metrics"].get(name) for row in rows]) for name in METRICS}
    result["unavailableReasons"] = dict(Counter(
        reason for row in rows for reason in row["metrics"].get("unavailable", {}).values()
    ))
    return result


def inventory_summary(rows):
    """Describe the native workload without turning object count into quality."""
    counts = sorted(row["metrics"]["objectCount"] for row in rows)
    return {
        "scenes": len(counts),
        "minimumFurnitureInstances": min(counts) if counts else None,
        "medianFurnitureInstances": statistics.median(counts) if counts else None,
        "maximumFurnitureInstances": max(counts) if counts else None,
        "roomTypes": dict(sorted(Counter(row["scene"]["roomType"] for row in rows).items())),
    }


def measured_rows(scenes):
    rows, invalid = [], []
    for scene in scenes:
        try:
            rows.append({"scene": scene, "metrics": measure(scene)})
        except (ValueError, KeyError, TypeError) as error:
            invalid.append({"id": scene["id"], "reason": str(error)})
    return rows, invalid


def compare(rows):
    """Equal stratum weighting prevents the largest source dataset dominating.

    This is an observational comparison of native outputs, not identical inputs
    or an estimate of a causal model effect. Filters are fixed before scoring.
    """
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        groups[row["scene"]["model"]][stratum(row["scene"], row["metrics"])].append(row)
    results = []
    for baseline in ("layoutgpt", "infinigen"):
        shared = sorted(set(groups["soilie"]) & set(groups[baseline]))
        sides = {model: [row for key in shared for row in groups[model][key]] for model in ("soilie", baseline)}
        metric_results = {}
        for name in METRICS:
            means = {}
            for model in sides:
                per_stratum = [statistics.fmean(row["metrics"][name] for row in groups[model][key]
                                               if row["metrics"][name] is not None)
                               for key in shared if any(row["metrics"][name] is not None for row in groups[model][key])]
                means[model] = statistics.fmean(per_stratum) if len(per_stratum) == len(shared) and shared else None
            available = all(value is not None for value in means.values())
            metric_results[name] = {"means": means, "difference": means["soilie"]-means[baseline] if available else None,
                                    "available": available}
        results.append({"baseline": baseline, "sharedStrata": [list(key) for key in shared],
                        "counts": {model: len(side) for model, side in sides.items()}, "metrics": metric_results,
                        "validityRates": matched_validity_rates(groups, ("soilie", baseline), shared),
                        "includedIds": {model: [row["scene"]["id"] for row in side] for model,side in sides.items()}})
    return results


def require_complete_soilie(groups, runs):
    """Reject a partial or incompletely observed SOILIE publication corpus."""
    expected = sum(run["target"] for run in runs)
    actual = groups.get("soilie", [])
    incomplete = [row["scene"]["id"] for row in actual
                  if row["metrics"].get("meanWorstSolidOverlapPct") is None]
    if len(actual) != expected or incomplete:
        raise RuntimeError(
            f"SOILIE publication requires {expected} complete mesh measurements; "
            f"found {len(actual)} scenes and {len(incomplete)} incomplete result(s)"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--layoutgpt", type=Path, required=True)
    parser.add_argument("--infinigen", type=Path)
    parser.add_argument("--support-replays", type=Path, nargs="*", default=[])
    parser.add_argument("--rates", type=Path, required=True)
    parser.add_argument("--layoutgpt-cost-profile", type=Path, required=True,
                        help="Reconstructed token profile for the exact released LayoutGPT configuration")
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--combination-catalog", type=Path,
                        help="Published V4 room-combination CSV used for category co-occurrence fidelity")
    parser.add_argument("--incidents", type=Path,
                        help="Optional current-cohort infrastructure incidents; omitted incidents are not historical comparison results")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    attempts, scenes, before_after, runs = [], [], [], []
    provenance_hashes = set()
    for folder in args.runs:
        # Every explicitly requested run must exist. A path typo must not
        # silently remove a workload from the comparison or failure totals.
        config = json.loads((folder/"run.json").read_text())
        if config.get('cohort') == 'diversity':
            raise ValueError('Exploration inputs cannot enter the controlled benchmark comparison')
        if config.get("full"):
            raise ValueError("Full-render parity runs cannot enter placement-only timing results")
        batch = [json.loads(path.read_text()) for path in sorted(folder.glob("attempt-*.json"))]
        segments = config.get("provenanceSegments") or [{"firstAttempt": 0, "provenance": config["provenance"]}]
        provenance_hashes.update(digest(segment["provenance"]) for segment in segments)
        batch = analysis_cohort(batch, config)
        successes = [row for row in batch if row["status"] == "complete"]
        successful_seconds = sum(row["generationSeconds"] for row in successes)
        runs.append({"roomType": config["roomType"], "target": config["targetCompletions"], "attempted": len(batch),
                     "completed": len(successes), "status": "complete" if len(successes) >= config["targetCompletions"] else "in_progress",
                     "activeWallSeconds": sum(row["wallSeconds"] for row in batch),
                     "generationSeconds": successful_seconds,
                     "generationBreakdown": generation_breakdown(batch),
                     "failures": dict(Counter(row.get("errorCode") for row in batch if row["status"] != "complete")),
                     "first10000Attempts": {"attempted": min(10000,len(batch)), "completed": sum(row["status"] == "complete" for row in batch[:10000])},
                     "configuration": {key: value for key,value in config.items() if key not in {"runtime","blender","provenance"}},
                     "baselineCommit": config["provenance"]["baselineCommit"],
                     "sessionTiming": session_summary(folder,batch)})
        attempts.extend(batch)
    attempts, support_evidence = merge_support(attempts,args.support_replays,provenance_hashes)
    for row in attempts:
        if row["status"] != "complete":
            continue
        scenes.append(attach_front_directions(row["stages"]["final"]))
        try:
            before_after.append({"id": row["id"], **{stage: measure(value)["meanWorstEnvelopeOverlapPct"] for stage,value in row["stages"].items()}})
        except ValueError:
            pass # Invalid geometry is separately recorded below, not a zero.
    release = json.loads(args.layoutgpt.read_text())
    scenes.extend(release["scenes"])
    indoors = {"scenes":[],"attempts":[],"invalidArtifacts":[],"configuration":None}
    if args.infinigen:
        indoors = json.loads(args.infinigen.read_text())
        scenes.extend(indoors["scenes"])
    rows, invalid = measured_rows(scenes)
    if len({row["scene"]["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate scene IDs would inflate sample sizes")
    groups = {model: [row for row in rows if row["scene"]["model"] == model] for model in LABELS}
    require_complete_soilie(groups, runs)
    successful_time = [row["generationSeconds"] for row in attempts if row["status"] == "complete"]
    successful_total = sum(successful_time)
    indoors_attempts = indoors["attempts"]
    indoors_times = [row["generationSeconds"] for row in indoors_attempts if row["status"] == "complete"]
    indoors_total = sum(row["generationSeconds"] for row in indoors_attempts)
    incidents = json.loads(args.incidents.read_text())["incidents"] if args.incidents else []
    indoors_incidents = [row for row in incidents if row["model"] == "infinigen"]
    indoors_timing_complete = not any(not row["generationTimingAvailable"] for row in indoors_incidents)
    source_combinations = None
    if args.combination_catalog:
        with args.combination_catalog.open(newline="", encoding="utf-8-sig") as stream:
            source_combinations = [tuple(value for value in row if value) for row in csv.reader(stream)][1:]
        if not source_combinations or any(len(row) != 6 for row in source_combinations):
            raise ValueError("The V4 bedroom combination catalog must contain six labels per row")
    document = {"schemaVersion": 2, "generatedAt": datetime.now(UTC).isoformat(), "metricDefinitions": METRICS,
                "models": {model: {"label": LABELS[model], "n": len(group), "metrics": aggregate(group),
                                   "inventory": inventory_summary(group),
                                   "validityRates": validity_rates(group)} for model,group in groups.items()},
                "comparisons": compare(rows), "runs": runs, "invalidGeometry": invalid,
                "layoutgptSources": release["sources"], "layoutgptInvalidArtifacts": release["invalidArtifacts"],
                "beforeAfter": before_after,
                "supportReplays":support_evidence,
                "infinigenInvalidArtifacts":indoors["invalidArtifacts"],
                "infinigenConfiguration":indoors["configuration"],
                "infrastructureIncidents":incidents,
                "timing": {"soilie": {"completedPerMinute": 60*len(successful_time)/successful_total if successful_total else None,
                                      "completedLatencySeconds": summarize(successful_time),
                                      "successfulGenerationSeconds": successful_total,
                                      "failedAttemptsExcludedFromTiming":sum(row["status"] != "complete" for row in attempts),
                                      "failureCounts":dict(Counter(row.get("errorCode","UNCLASSIFIED_FAILURE") for row in attempts if row["status"] != "complete"))},
                           "layoutgpt": {"available": False, "reason": "Released layouts do not include inference timings."},
                           "infinigen":{"available":bool(indoors_attempts),"attempted":len(indoors_attempts),
                                         "completed":len(indoors_times),"allAttemptSeconds":indoors_total if indoors_timing_complete else None,
                                         "recordedAttemptSeconds":indoors_total,
                                         "infrastructureInterruptedExecutions":sum(row["affectedExecutions"] for row in indoors_incidents),
                                         "completedPerMinute":60*len(indoors_times)/indoors_total if indoors_total and indoors_timing_complete else None,
                                         "completedLatencySeconds":summarize(indoors_times),
                                         "failures":dict(Counter(row["errorCode"] for row in indoors_attempts if row["status"] != "complete")),
                                         "profile":(indoors.get("configuration") or {}).get("profile"),
                                         "stage":(indoors.get("configuration") or {}).get("profileDescription") or
                                                 "Single-room coarse task: solving, procedural mesh construction, camera preparation and serialization; no image rendering"},
                           "grains": {"evidence": "paper-reported", "scenes": 10000, "seconds": 1027, "hierarchySeconds": 94, "placementSeconds": 933,
                                      "hardware": "GTX 1080 Ti and Intel i7-8700; after training", "source": "https://arxiv.org/html/1807.09193"}},
                "selectionExplanation": json.loads(args.selection.read_text()),
                "nonHumanDiagnostics": soilie_diagnostics(attempts, source_combinations),
                "humanParticipants": 0,
                "cost": cost_evidence(attempts, json.loads(args.rates.read_text()),
                                      json.loads(args.layoutgpt_cost_profile.read_text())),
                "method": "Native final outputs, matched by room type, exact furniture count and 0.25-wide summed-footprint-density bins; equal weight per shared stratum. Not identical-input experiments.",
                "grainsAvailability": "The authors removed pretrained weights; no new GRAINS geometry or inference run is claimed."}
    args.output.mkdir(parents=True, exist_ok=True)
    illustrations = []
    for model, group in groups.items():
        if not group:
            continue
        # Deliberately show a problem case, explicitly not a representative
        # random sample. This selection never feeds the blinded pilot sampler.
        sample = max(group,key=lambda row: row["metrics"]["meanWorstEnvelopeOverlapPct"])
        pairs = sample["metrics"]["envelopeOverlapPairs"]
        if not pairs:
            continue
        pair = max(pairs,key=lambda pair: max(pair["fractions"]))
        svg = diagram(sample["scene"],{pair["a"],pair["b"]})
        filename = hashlib.sha256(svg.encode()).hexdigest()[:24]+".svg"
        directory = args.output/"illustrations"
        directory.mkdir(exist_ok=True)
        (directory/filename).write_text(svg,encoding="utf-8")
        item_labels = {item["id"]: item["label"].replace("_", " ") for item in sample["scene"]["objects"]}
        first_volume = Box(next(item for item in sample["scene"]["objects"] if item["id"] == pair["a"])).volume
        second_volume = Box(next(item for item in sample["scene"]["objects"] if item["id"] == pair["b"])).volume
        highlighted_pair = {**pair,
            "labels": [item_labels[pair["a"]], item_labels[pair["b"]]],
            "equalVolume": abs(first_volume-second_volume) <= max(first_volume, second_volume)*1e-9,
        }
        illustrations.append({"model":model,"sceneId":sample["scene"]["id"],"image":"benchmarks/illustrations/"+filename,
                              "meanWorstEnvelopeOverlapPct":sample["metrics"]["meanWorstEnvelopeOverlapPct"],
                              "highlightedPair":highlighted_pair,"selection":"Largest recorded scene-average box intrusion in this native-output sample; illustrative, not typical"})
    document["illustrations"] = illustrations
    packed = json.dumps(document, separators=(",", ":"))
    document["evidenceDigest"] = hashlib.sha256(packed.encode()).hexdigest()
    (args.output/"comparison.json").write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    (args.output/"measured-scenes.json").write_text(json.dumps({"schemaVersion":2,"rows":rows}, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"measuredScenes":len(rows), "invalidGeometry":len(invalid), "output":str(args.output)}))


if __name__ == "__main__":
    main()
