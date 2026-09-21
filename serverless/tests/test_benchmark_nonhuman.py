import unittest

from serverless.benchmark.geometry import box_corners, measure
from serverless.benchmark.nonhuman import (cooccurrence_fidelity, matched_validity_rates,
                                           relation_drift, soilie_diagnostics, validity_rates)
from serverless.benchmark.publish_comparison import analysis_cohort


def scene(scene_id, centres, room=(-2, -2, 2, 2), solid=None):
    objects = [{"id": key, "label": key.split("-")[0], "kind": "furniture",
                "corners": box_corners(value, (1, 1, 1))} for key, value in centres.items()]
    result = {"id": scene_id, "model": "soilie", "roomType": "bedroom", "units": "m",
              "objects": objects, "room": {"polygon": [[room[0], room[1]], [room[2], room[1]],
                                                          [room[2], room[3]], [room[0], room[3]]], "floorZ": 0}}
    if solid is not None:
        result["solidMeshOverlap"] = {"method": "evaluated-solid-mesh-boolean-v1", "objectCount": len(objects),
                                      "complete": True, "meanWorstOverlapPct": solid,
                                      "maxOverlapPct": solid, "overlapPairs": [], "unavailablePairs": []}
    return result


class NonHumanDiagnosticsTests(unittest.TestCase):
    def test_category_cooccurrence_fidelity_has_human_readable_percentage_points(self):
        catalog = [
            ("bed", "lamp", "chair", "desk", "book", "plant"),
            ("bed", "lamp", "desk", "chair", "plant", "book"),
        ]
        attempts = []
        for count in (3, 4, 5, 6):
            for index, row in enumerate(catalog):
                attempts.append({"status": "complete", "selection": list(row[:count]),
                                 "request": {"objectCount": count}, "id": f"{count}-{index}"})
        result = cooccurrence_fidelity(attempts, catalog)
        self.assertTrue(result["available"])
        self.assertEqual(result["equalCountMeanAbsoluteDifferencePercentagePoints"], 0)
        self.assertEqual([row["completedScenes"] for row in result["strata"]], [2, 2, 2, 2])

    def test_category_cooccurrence_treats_duplicates_as_category_presence(self):
        catalog = [("bed", "lamp", "chair", "desk", "book", "plant")]
        attempts = [{"status": "complete", "selection": ["bed", "bed", "lamp"],
                     "request": {"objectCount": 3}, "id": "duplicate"}]
        result = cooccurrence_fidelity(attempts, catalog)
        first = result["strata"][0]
        self.assertEqual(first["generatedAnchorCategories"], 2)
        self.assertGreater(first["meanAbsoluteConditionalDifferencePercentagePoints"], 0)

    def test_analysis_cohort_keeps_validated_successes_and_only_active_failures(self):
        config = {"provenance": {"version": "4.0.2"}, "provenanceSegments": [
            {"firstAttempt": 0, "provenance": {"version": "4.0.2"}},
            {"firstAttempt": 3, "provenance": {"version": "4.0.2"}},
        ]}
        rows = [
            {"id": "retained-success", "status": "complete"},
            {"id": "replaced-failure", "status": "failed"},
            {"id": "active-success", "status": "complete", "provenanceSegment": 1},
            {"id": "active-failure", "status": "failed", "provenanceSegment": 1},
        ]
        self.assertEqual([row["id"] for row in analysis_cohort(rows, config)],
                         ["retained-success", "active-success", "active-failure"])

    def test_validity_rates_distinguish_envelopes_boundaries_and_meshes(self):
        clean = scene("clean", {"bed": (-1, 0, .5), "desk": (1, 0, .5)}, solid=0)
        overlap = scene("overlap", {"bed": (0, 0, .5), "desk": (.5, 0, .5)}, solid=10)
        outside = scene("outside", {"bed": (1.8, 0, .5)}, solid=0)
        rows = [{"scene": value, "metrics": measure(value)} for value in (clean, overlap, outside)]
        rates = validity_rates(rows)
        expected_tolerance = 0.0001
        self.assertEqual(rates["envelopeCollisionFree"],
                         {"n": 3, "passed": 2, "pct": 200 / 3,
                          "numericalTolerancePct": expected_tolerance})
        self.assertEqual(rates["fullyContained"],
                         {"n": 3, "passed": 2, "pct": 200 / 3,
                          "numericalTolerancePct": expected_tolerance})
        self.assertEqual(rates["envelopeCollisionFreeAndContained"],
                         {"n": 3, "passed": 1, "pct": 100 / 3,
                          "numericalTolerancePct": expected_tolerance})
        self.assertEqual(rates["occupiedMeshCollisionFree"],
                         {"n": 3, "passed": 2, "pct": 200 / 3,
                          "numericalTolerancePct": expected_tolerance})

    def test_relation_drift_has_human_scale_units(self):
        before = scene("before", {"bed": (0, 0, .5), "desk": (1, 0, .5)})
        final = scene("final", {"bed": (0, 0, .5), "desk": (0, 2, .5)})
        result = relation_drift({"id": "attempt", "stages": {"beforeSeparation": before, "final": final}})
        self.assertAlmostEqual(result["meanObjectDisplacementCm"], 50 * 5 ** .5)
        self.assertAlmostEqual(result["meanPairDistanceChangeCm"], 100)
        self.assertAlmostEqual(result["meanPairBearingChangeDeg"], 90)
        self.assertTrue(result["changed"])

    def test_matched_validity_gives_each_shared_stratum_equal_weight(self):
        passing = {"maxEnvelopeOverlapPct": 0, "maxOutsideFootprintPct": 0, "maxSolidOverlapPct": 0}
        failing = {"maxEnvelopeOverlapPct": 1, "maxOutsideFootprintPct": 1, "maxSolidOverlapPct": 1}
        groups = {
            "soilie": {("bedroom", 3, 0): [{"metrics": passing}] * 9,
                       ("bedroom", 6, 1): [{"metrics": failing}]},
            "layoutgpt": {("bedroom", 3, 0): [{"metrics": failing}],
                          ("bedroom", 6, 1): [{"metrics": passing}] * 9},
        }
        result = matched_validity_rates(groups, ("soilie", "layoutgpt"), list(groups["soilie"]))
        self.assertEqual(result["envelopeCollisionFree"]["soilie"]["equalStratumPct"], 50)
        self.assertEqual(result["envelopeCollisionFree"]["layoutgpt"]["equalStratumPct"], 50)
        self.assertEqual(result["envelopeCollisionFree"]["soilie"]["sceneN"], 10)

    def test_selection_breadth_is_duplicate_aware(self):
        base = scene("base", {"bed": (0, 0, .5), "lamp": (1, 0, .5)})
        attempts = [
            {"id": "a", "status": "complete", "selection": ["bed", "lamp"],
             "stages": {"beforeSeparation": base, "final": base}},
            {"id": "b", "status": "complete", "selection": ["lamp", "bed"],
             "stages": {"beforeSeparation": base, "final": base}},
            {"id": "c", "status": "complete", "selection": ["bed", "bed"],
             "stages": {"beforeSeparation": base, "final": base}},
        ]
        result = soilie_diagnostics(attempts)["selectionBreadth"]
        self.assertEqual(result["distinctObjectClasses"], 2)
        self.assertEqual(result["distinctObjectCombinations"], 2)
        self.assertEqual(result["mostFrequentCombinationScenes"], 2)

    def test_overlap_resolution_reports_work_done_not_only_final_passes(self):
        clean = scene("clean", {"bed": (-1, 0, .5), "desk": (1, 0, .5)})
        overlapping = scene("overlapping", {"bed": (0, 0, .5), "desk": (.5, 0, .5)})
        resolved = scene("resolved", {"bed": (-1, 0, .5), "desk": (1, 0, .5)})
        attempts = [
            {"id": "clean", "status": "complete", "selection": ["bed", "desk"],
             "stages": {"beforeSeparation": clean, "final": clean}},
            {"id": "corrected", "status": "complete", "selection": ["bed", "desk"],
             "stages": {"beforeSeparation": overlapping, "final": resolved}},
        ]
        result = soilie_diagnostics(attempts)["overlapResolution"]
        self.assertEqual(result["completedScenes"], 2)
        self.assertEqual(result["scenesWithInitialEnvelopeOverlap"], 1)
        self.assertEqual(result["scenesWithInitialEnvelopeOverlapPct"], 50)
        self.assertEqual(result["scenesResolvedToNoEnvelopeOverlap"], 1)
        self.assertEqual(result["resolutionPct"], 100)
        self.assertEqual(result["numericalTolerancePct"], 0.0001)
        self.assertGreater(result["initialMeanWorstEnvelopeOverlapPct"]["median"], 0)
        self.assertEqual(result["finalMeanWorstEnvelopeOverlapPct"]["median"], 0)

    def test_single_furniture_scene_has_no_invented_pair_drift(self):
        only = scene("only", {"bed": (0, 0, .5)})
        result = relation_drift({"id": "attempt", "stages": {"beforeSeparation": only, "final": only}})
        self.assertEqual(result["pairCount"], 0)
        self.assertIsNone(result["meanPairDistanceChangeCm"])
        self.assertIsNone(result["meanPairBearingChangeDeg"])


if __name__ == "__main__":
    unittest.main()
