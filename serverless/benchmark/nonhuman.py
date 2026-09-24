"""Interpretable model diagnostics that do not require human judgements.

Cross-source validity rates use the common oriented-envelope representation.
SOILIE-only relation diagnostics compare its measured relational proposal with
the final placement, so they quantify how much collision handling changed the
proposal without inventing target relations for another model.
"""
from collections import Counter, defaultdict
from itertools import combinations
import math

import numpy as np

from serverless.benchmark.geometry import canonical_label, furniture, measure, summarize


# Pass/fail summaries treat at most one part per million of an object's volume
# or footprint as numerical contact. Continuous distributions retain the raw
# value, so this tolerance cannot erase the amount-of-overlap evidence.
NUMERICAL_TOLERANCE_PCT = 1e-4
MIN_COOCCURRENCE_CONDITIONAL_PCT = 5.0


def _rate(flags):
    flags = list(flags)
    return {"n": len(flags), "passed": sum(flags),
            "pct": 100 * sum(flags) / len(flags) if flags else None,
            "numericalTolerancePct": NUMERICAL_TOLERANCE_PCT}


def validity_rates(rows):
    """Return pass rates whose denominators and geometric meaning are explicit."""
    envelope = [row["metrics"]["maxEnvelopeOverlapPct"] <= NUMERICAL_TOLERANCE_PCT for row in rows]
    contained = [row["metrics"]["maxOutsideFootprintPct"] <= NUMERICAL_TOLERANCE_PCT for row in rows]
    joint = [first and second for first, second in zip(envelope, contained)]
    solid_rows = [row for row in rows if row["metrics"]["maxSolidOverlapPct"] is not None]
    solid = [row["metrics"]["maxSolidOverlapPct"] <= NUMERICAL_TOLERANCE_PCT for row in solid_rows]
    return {
        "envelopeCollisionFree": _rate(envelope),
        "fullyContained": _rate(contained),
        "envelopeCollisionFreeAndContained": _rate(joint),
        "occupiedMeshCollisionFree": _rate(solid),
    }


def matched_validity_rates(groups, models, shared):
    """Equal-stratum validity rates for a declared shared comparison subset."""
    predicates = {
        "envelopeCollisionFree": lambda metric: metric["maxEnvelopeOverlapPct"] <= NUMERICAL_TOLERANCE_PCT,
        "fullyContained": lambda metric: metric["maxOutsideFootprintPct"] <= NUMERICAL_TOLERANCE_PCT,
        "envelopeCollisionFreeAndContained": lambda metric: (
            metric["maxEnvelopeOverlapPct"] <= NUMERICAL_TOLERANCE_PCT
            and metric["maxOutsideFootprintPct"] <= NUMERICAL_TOLERANCE_PCT
        ),
        "occupiedMeshCollisionFree": lambda metric: (
            None if metric["maxSolidOverlapPct"] is None
            else metric["maxSolidOverlapPct"] <= NUMERICAL_TOLERANCE_PCT
        ),
    }
    result = {}
    for name, predicate in predicates.items():
        result[name] = {}
        for model in models:
            strata, scene_n, passed = [], 0, 0
            for key in shared:
                flags = [predicate(row["metrics"]) for row in groups[model][key]]
                flags = [flag for flag in flags if flag is not None]
                if not flags:
                    strata = []
                    break
                strata.append(100 * sum(flags) / len(flags))
                scene_n += len(flags)
                passed += sum(flags)
            result[name][model] = {
                "available": len(strata) == len(shared) and bool(shared),
                "sharedStrata": len(shared),
                "sceneN": scene_n,
                "passed": passed,
                "equalStratumPct": sum(strata) / len(strata) if strata else None,
                "numericalTolerancePct": NUMERICAL_TOLERANCE_PCT,
            }
    return result


def _centre(item):
    return np.asarray(item["corners"], dtype=float).mean(axis=0)


def _bearing_delta(first, second):
    delta = abs(first - second) % 360
    return min(delta, 360 - delta)


def relation_drift(attempt):
    """Measure horizontal changes from SOILIE's proposal to final placement.

    Distances are absolute centimetres rather than percentages, avoiding an
    unstable denominator when the proposal deliberately starts objects close
    together. Pair directions are deterministic because instance IDs are
    sorted before vectors are calculated.
    """
    before = {item["id"]: item for item in furniture(attempt["stages"]["beforeSeparation"])}
    final = {item["id"]: item for item in furniture(attempt["stages"]["final"])}
    if before.keys() != final.keys():
        raise ValueError("Relation-drift stages must contain the same furniture instances")
    ids = sorted(before)
    displacements = [float(np.linalg.norm(_centre(final[key])[:2] - _centre(before[key])[:2]) * 100)
                     for key in ids]
    distance_changes, bearing_changes = [], []
    for first, second in combinations(ids, 2):
        initial = _centre(before[second])[:2] - _centre(before[first])[:2]
        resolved = _centre(final[second])[:2] - _centre(final[first])[:2]
        distance_changes.append(abs(float(np.linalg.norm(resolved) - np.linalg.norm(initial))) * 100)
        if np.linalg.norm(initial) > 1e-9 and np.linalg.norm(resolved) > 1e-9:
            bearing_changes.append(_bearing_delta(
                math.degrees(math.atan2(initial[1], initial[0])),
                math.degrees(math.atan2(resolved[1], resolved[0])),
            ))
    return {
        "sceneId": attempt["id"],
        "objectCount": len(ids),
        "pairCount": len(distance_changes),
        "meanObjectDisplacementCm": sum(displacements) / len(displacements),
        "maxObjectDisplacementCm": max(displacements),
        "meanPairDistanceChangeCm": sum(distance_changes) / len(distance_changes) if distance_changes else None,
        "meanPairBearingChangeDeg": sum(bearing_changes) / len(bearing_changes) if bearing_changes else None,
        "changed": max(displacements) > 1e-5,
    }


def overlap_resolution(attempts, stage_metrics=None):
    """Summarize how often V4's own separation stage removes envelope overlap.

    This is deliberately paired with the relation-drift report: eliminating an
    intersection is useful only when readers can also see how far the proposed
    arrangement had to move. The metric uses the same oriented envelopes at
    both stages and does not claim exact occupied-mesh volume before separation.
    """
    affected = []
    for attempt in attempts:
        before = (stage_metrics[attempt['id']]['beforeSeparation'] if stage_metrics is not None
                  else measure(attempt["stages"]["beforeSeparation"]))
        if before["maxEnvelopeOverlapPct"] <= NUMERICAL_TOLERANCE_PCT:
            continue
        final = (stage_metrics[attempt['id']]['final'] if stage_metrics is not None
                 else measure(attempt["stages"]["final"]))
        affected.append({
            "beforeMeanWorstOverlapPct": before["meanWorstEnvelopeOverlapPct"],
            "finalMeanWorstOverlapPct": final["meanWorstEnvelopeOverlapPct"],
            "resolved": final["maxEnvelopeOverlapPct"] <= NUMERICAL_TOLERANCE_PCT,
        })
    total = len(attempts)
    resolved = sum(row["resolved"] for row in affected)
    return {
        "completedScenes": total,
        "scenesWithInitialEnvelopeOverlap": len(affected),
        "scenesWithInitialEnvelopeOverlapPct": 100 * len(affected) / total if total else None,
        "scenesResolvedToNoEnvelopeOverlap": resolved,
        "resolutionPct": 100 * resolved / len(affected) if affected else None,
        "numericalTolerancePct": NUMERICAL_TOLERANCE_PCT,
        "initialMeanWorstEnvelopeOverlapPct": summarize(
            row["beforeMeanWorstOverlapPct"] for row in affected
        ),
        "finalMeanWorstEnvelopeOverlapPct": summarize(
            row["finalMeanWorstOverlapPct"] for row in affected
        ),
        "interpretation": (
            "Among completed scenes whose relational proposal contained intersecting oriented furniture "
            "envelopes, this reports how often the ordinary V4 separation stage removed every envelope "
            "intersection. Read it with relation-preservation distances; envelope clearance alone does not "
            "establish semantic plausibility or exact pre-correction mesh collision."
        ),
    }


def _conditional_occurrence(rows):
    """Return P(second present | first present) for category-presence rows.

    Repeated instances count once because this diagnostic asks which object
    categories co-occur, matching the category-level question used by GRAINS.
    """
    anchors, pairs = Counter(), Counter()
    for row, weight in rows:
        labels = set(row)
        for first in labels:
            anchors[first] += weight
            for second in labels - {first}:
                pairs[first, second] += weight
    return anchors, {pair: value / anchors[pair[0]] for pair, value in pairs.items()}


def cooccurrence_fidelity(attempts, source_combinations):
    """Compare generated selections with V4's bedroom combination catalog.

    The controlled campaign requests 3, 4, 5 and 6 objects in rotation. Every
    source row has six labels, so V4's published weighting expression assigns
    every row the same weight; a request takes the corresponding row prefix.
    We compare each requested count separately and give those four strata equal
    weight. This is a sampler-fidelity check, not an independent quality score.
    """
    completed = [row for row in attempts if row.get("status") == "complete"]
    counts = (3, 4, 5, 6)
    source_by_count = {
        count: [(tuple(canonical_label(label) for label in row[:count]), 1.0)
                for row in source_combinations]
        for count in counts
    }
    generated_by_count = {
        count: [(tuple(canonical_label(label) for label in attempt.get("selection", [])), 1.0)
                for attempt in completed if attempt.get("request", {}).get("objectCount") == count]
        for count in counts
    }
    strata = []
    for count in counts:
        source_anchors, source_pairs = _conditional_occurrence(source_by_count[count])
        generated_anchors, generated_pairs = _conditional_occurrence(generated_by_count[count])
        labels = sorted(source_anchors)
        weighted_error = weight_total = 0.0
        pair_count = 0
        for first in labels:
            # Weight a conditional by how often its anchor category appears in
            # the source catalog. This keeps very rare anchors from dominating.
            anchor_weight = source_anchors[first] / len(source_by_count[count])
            for second in labels:
                if first == second:
                    continue
                expected = source_pairs.get((first, second), 0.0)
                observed = generated_pairs.get((first, second), 0.0)
                if max(expected, observed) * 100 < MIN_COOCCURRENCE_CONDITIONAL_PCT:
                    continue
                error_pp = abs(observed - expected) * 100
                weighted_error += error_pp * anchor_weight
                weight_total += anchor_weight
                pair_count += 1
        strata.append({
            "requestedObjects": count,
            "sourceRows": len(source_by_count[count]),
            "completedScenes": len(generated_by_count[count]),
            "sourceCategories": len(labels),
            "generatedSourceCategoryCoveragePct": 100 * len(set(labels) & set(generated_anchors)) / len(labels),
            "qualifyingDirectedCategoryPairs": pair_count,
            "meanAbsoluteConditionalDifferencePercentagePoints": (
                weighted_error / weight_total if weight_total else None
            ),
            "generatedAnchorCategories": len(generated_anchors),
        })
    values = [row["meanAbsoluteConditionalDifferencePercentagePoints"] for row in strata
              if row["meanAbsoluteConditionalDifferencePercentagePoints"] is not None]
    return {
        "available": bool(source_combinations) and len(values) == len(counts),
        "completedScenes": len(completed),
        "strata": strata,
        "equalCountMeanAbsoluteDifferencePercentagePoints": (
            sum(values) / len(values) if values else None
        ),
        "direction": "lower",
        "minimumConditionalProbabilityPct": MIN_COOCCURRENCE_CONDITIONAL_PCT,
        "definition": (
            "For every ordered category pair, calculate how often the second category is present "
            "when the first is present. Compare generated selections with the corresponding prefix "
            "of the published bedroom combination catalog, then average absolute differences in "
            "percentage points with equal weight for requested counts 3 through 6. Category pairs "
            "below 5% in both distributions are omitted so thousands of zero-versus-zero pairs do not "
            "make the result look artificially accurate."
        ),
        "interpretation": (
            "This measures whether the implemented sampler reproduces the object-category associations "
            "encoded by its own source catalog. It is analogous to the category co-occurrence check "
            "reported by GRAINS, but the datasets and category vocabularies differ, so it is not a "
            "head-to-head score and does not measure placement quality or generalization."
        ),
    }


def soilie_diagnostics(attempts, source_combinations=None, stage_metrics=None):
    completed = [attempt for attempt in attempts if attempt.get("status") == "complete"]
    drift = [relation_drift(attempt) for attempt in completed]
    combinations_seen = Counter(tuple(sorted(canonical_label(label) for label in attempt.get("selection", [])))
                                for attempt in completed)
    labels = {label for combination in combinations_seen for label in combination}
    return {
        "overlapResolution": overlap_resolution(completed, stage_metrics),
        "relationPreservation": {
            "scenes": len(drift),
            "scenesWithFurniturePairs": sum(row["pairCount"] > 0 for row in drift),
            "changedScenes": sum(row["changed"] for row in drift),
            "meanObjectDisplacementCm": summarize(row["meanObjectDisplacementCm"] for row in drift),
            "maxObjectDisplacementCm": summarize(row["maxObjectDisplacementCm"] for row in drift),
            "meanPairDistanceChangeCm": summarize(row["meanPairDistanceChangeCm"] for row in drift),
            "meanPairBearingChangeDeg": summarize(row["meanPairBearingChangeDeg"] for row in drift),
            "interpretation": "Horizontal centre displacement and pair-relation drift between the measured relational proposal and final placement. Lower values mean collision handling preserved more of the proposed arrangement.",
        },
        "selectionBreadth": {
            "completedScenes": len(completed),
            "distinctObjectClasses": len(labels),
            "distinctObjectCombinations": len(combinations_seen),
            "mostFrequentCombinationScenes": max(combinations_seen.values(), default=0),
            "mostFrequentCombinationPct": (100 * max(combinations_seen.values()) / len(completed)) if completed else None,
            "interpretation": "Distinct duplicate-aware object combinations observed in the controlled room-preset campaign. This describes sampler breadth, not spatial quality.",
        },
        "catalogCooccurrenceFidelity": (
            cooccurrence_fidelity(completed, source_combinations)
            if source_combinations is not None
            else {"available": False, "reason": "No source combination catalog was supplied."}
        ),
    }


def attach_validity(rows_by_model):
    """Convenience wrapper used by publication and unit tests."""
    return {model: validity_rates(rows) for model, rows in rows_by_model.items()}
