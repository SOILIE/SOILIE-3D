from copy import deepcopy
import unittest

from serverless.benchmark.geometry import box_corners, measure
from serverless.benchmark.stimuli import diagram, review_metrics, select_pairs, semantic_signature, semantic_similarity, symmetric_review_metrics
from serverless.study.export_pilot import aggregate, condition_result
from serverless.tests import test_study_service as study_tests


def fixture(model,index):
    scene = {"id":f"{model}-{index}","model":model,"roomType":"bedroom","units":"m",
             "room":{"polygon":[[0,0],[4,0],[4,4],[0,4]],"floorZ":0},
             "objects":[{"id":"a","label":"bed","corners":box_corners([1,1,.5],[1,1,1])},
                        {"id":"b","label":"desk","corners":box_corners([3,3,.5],[1,1,1],30)}]}
    return {"scene":scene,"metrics":measure(scene)}


class StimulusTests(unittest.TestCase):
    def test_sampling_is_stable_and_cannot_see_quality(self):
        rows = [fixture(model,i) for model in ("soilie","layoutgpt") for i in range(20)]
        selected = select_pairs(rows)
        changed = deepcopy(rows)
        for row in changed:
            row["metrics"]["meanWorstEnvelopeOverlapPct"] = 99
        self.assertEqual(selected,select_pairs(changed))
        self.assertEqual(12,len(selected))
        self.assertEqual(12,len({pair[2]["id"] for pair in selected}))

    def test_semantic_aliases_are_duplicate_aware(self):
        left = fixture("soilie",1)["scene"]
        left["objects"] = [
            {"id":"a","label":"bed","corners":box_corners([1,1,.5],[1,1,1])},
            {"id":"b","label":"night_stand","corners":box_corners([3,3,.5],[1,1,1])},
            {"id":"c","label":"night_stand","corners":box_corners([2,3,.5],[1,1,1])},
        ]
        right = deepcopy(left)
        right["objects"][0]["label"] = "double_bed"
        right["objects"][1]["label"] = "nightstand"
        right["objects"][2]["label"] = "round_end_table"
        self.assertEqual(semantic_signature(left), semantic_signature(right))
        self.assertEqual(1, semantic_similarity(left, right))

    def test_matching_rejects_a_bedroom_with_different_bed_count(self):
        left = fixture("soilie",1)
        right = fixture("layoutgpt",1)
        right["scene"]["objects"][0]["label"] = "table"
        rows = [left, right]
        self.assertEqual([], select_pairs(rows))

    def test_matching_keeps_at_least_two_thirds_of_roles_aligned(self):
        rows = [fixture(model,i) for model in ("soilie","layoutgpt") for i in range(20)]
        for row in rows:
            if row["scene"]["model"] == "layoutgpt":
                row["scene"]["objects"][0]["label"] = "double_bed"
                row["scene"]["objects"][1]["label"] = "desk"
                row["metrics"] = measure(row["scene"])
        selected = select_pairs(rows)
        self.assertEqual(12, len(selected))
        self.assertTrue(all(semantic_similarity(pair[2], pair[3]) == 1 for pair in selected))

    def test_matching_maximizes_pair_count_before_edge_preference(self):
        left_flexible = fixture("soilie", 1)
        left_needed = fixture("soilie", 2)
        right_flexible = fixture("layoutgpt", 1)
        right_restricted = fixture("layoutgpt", 2)
        for row in (left_flexible, right_flexible, right_restricted):
            row["scene"]["objects"].append(
                {"id":"c","label":"chair","corners":box_corners([2,1,.5],[1,1,1])})
        left_needed["scene"]["objects"].append(
            {"id":"c","label":"lamp","corners":box_corners([2,1,.5],[1,1,1])})
        for row in (left_flexible, left_needed, right_flexible, right_restricted):
            row["metrics"] = measure(row["scene"])
        # The preferred first edge is left_flexible/right_flexible. Taking it
        # greedily strands left_needed, whereas reassignment yields two pairs.
        left_flexible["metrics"]["furnitureDensity"] = .10
        left_needed["metrics"]["furnitureDensity"] = .35
        right_flexible["metrics"]["furnitureDensity"] = .10
        right_restricted["metrics"]["furnitureDensity"] = .05
        selected = select_pairs(
            [left_flexible, left_needed, right_flexible, right_restricted], limit=2)
        self.assertEqual(2, len(selected))
        self.assertEqual({"layoutgpt-1", "layoutgpt-2"}, {pair[3]["id"] for pair in selected})

    def test_frozen_threshold_can_define_a_larger_geometry_cohort(self):
        left = fixture("soilie", 1)
        right = fixture("layoutgpt", 1)
        left["scene"]["objects"].extend([
            {"id":"c","label":"lamp","corners":box_corners([1,3,.5],[1,1,1])},
            {"id":"d","label":"pillow","corners":box_corners([3,1,.5],[1,1,1])},
        ])
        right["scene"]["objects"].extend([
            {"id":"c","label":"wardrobe","corners":box_corners([1,3,.5],[1,1,1])},
            {"id":"d","label":"nightstand","corners":box_corners([3,1,.5],[1,1,1])},
        ])
        left["metrics"], right["metrics"] = measure(left["scene"]), measure(right["scene"])
        rows = [left, right]
        self.assertEqual(select_pairs(rows, limit=1), [])
        selected = select_pairs(rows, limit=1, minimum_semantic_similarity=.5)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][1][2], .5)

    def test_views_do_not_mutate_geometry_or_expose_model(self):
        scene = fixture("soilie",1)["scene"]
        before = deepcopy(scene)
        svg = diagram(scene)
        self.assertEqual(before,scene)
        self.assertIn("Plan view",svg)
        self.assertIn("Oblique view",svg)
        self.assertIn("3D bird’s-eye view",svg)
        self.assertIn('viewBox="0 0 720 1040"',svg)
        self.assertNotIn("soilie",svg)

    def test_review_metrics_keep_missing_values_unavailable(self):
        row = fixture("soilie",1)
        values = {item["id"]:item for item in review_metrics(row)}
        self.assertEqual("measured",values["meanWorstEnvelopeOverlapPct"]["availability"])
        self.assertEqual("unavailable",values["supportGapCm"]["availability"])
        self.assertIsNone(values["supportGapCm"]["value"])

    def test_reviewer_only_sees_measurements_available_for_both_rooms(self):
        left, right = fixture("soilie", 1), fixture("layoutgpt", 1)
        left["metrics"]["connectedClearancePct"] = 42
        right["metrics"]["connectedClearancePct"] = None
        left_metrics, right_metrics = symmetric_review_metrics(left, right)
        self.assertEqual([row["id"] for row in left_metrics], [row["id"] for row in right_metrics])
        self.assertNotIn("connectedClearancePct", {row["id"] for row in left_metrics})
        self.assertNotIn("supportGapCm", {row["id"] for row in left_metrics})

    def test_new_wave_never_reuses_prior_scenes(self):
        rows = [fixture(model,i) for model in ('soilie','layoutgpt') for i in range(20)]
        first = select_pairs(rows)
        prior = {'cases':[{'id':str(i),'comparisonCondition':pair[0]} for i,pair in enumerate(first)],
                 'stimulusEvidence':[{'caseId':str(i),'soilieScene':pair[2]['id'],'baselineScene':pair[3]['id']}
                                     for i,pair in enumerate(first)]}
        second = select_pairs(rows,limit=50,previous_protocols=[prior])
        self.assertEqual(8,len(second))
        self.assertFalse({row[2]['id'] for row in first} & {row[2]['id'] for row in second})
        self.assertFalse({row[3]['id'] for row in first} & {row[3]['id'] for row in second})


class ExportTests(unittest.TestCase):
    setUp = study_tests.StudyServiceTests.setUp
    tearDown = study_tests.StudyServiceTests.tearDown

    def test_pair_majority_sign_test_uses_distinct_decisive_pairs(self):
        from collections import Counter
        from serverless.study.export_pilot import _pair_majority_sign_test

        result = _pair_majority_sign_test(Counter(soilie=8, layoutgpt=2, tie=4), "layoutgpt")
        self.assertEqual(10,result["decisivePairs"])
        self.assertEqual(80,result["soiliePairSharePct"])
        self.assertAlmostEqual(.109375,result["twoSidedExactP"])

    def test_combined_export_separates_numeric_dominance_from_overall_preference(self):
        protocol = {
            "evidenceMode":"combined",
            "cases":[{"id":"case","comparisonCondition":"layoutgpt",
                      "relationMetrics":[{"id":"overlap","value":0,"availability":"measured","direction":"lower"}],
                      "comparisonMetrics":[{"id":"overlap","value":10,"availability":"measured","direction":"lower"}]}],
            "stimulusEvidence":[{"caseId":"case","semanticSimilarity":1}],
        }
        rows = [
            {"caseId":"case","comparisonCondition":"layoutgpt","repeatOf":None,
             "judgement":choice,"leftCondition":"soilie","rightCondition":"layoutgpt","promptProfile":"overall","note":""}
            for choice in ("left", "right", "tie")
        ]
        result = condition_result("layoutgpt", protocol, rows)
        self.assertEqual(1, result["metricAlignment"]["pairDominance"]["soilie"])
        self.assertEqual((1, 1, 1), (result["metricAlignment"]["aligned"],
                                    result["metricAlignment"]["opposed"],
                                    result["metricAlignment"]["tie"]))
        relation = result["metricAlignment"]["preferencesByNumericRelation"][0]
        self.assertEqual({"soilie":1,"tie":1,"baseline":1},
                         {key:relation[key] for key in ("soilie","tie","baseline")})

    def test_export_separates_ai_and_excludes_controls(self):
        from serverless.tests.test_study_service import protocol
        self.service.document = protocol()
        self.service.document["cases"] = [dict(row,comparisonCondition="layoutgpt") for row in self.service.document["cases"]]
        # New version has its own immutable session; ignore the setUp fixture.
        self.service.document["studyVersion"] = "export-fixture"
        invite = self.service.invite("exporter","overall","test-model")
        session = self.service.start({"invitation":invite})
        for case in session["cases"]:
            self.service.respond(session["sessionId"],{"sessionToken":session["sessionToken"],"caseId":case["caseId"],
                "judgement":"tie","errorChoice":"uncertain","confidence":1,"note":"Test only"})
        self.store.create("fake-human",{"sessionId":"fake-human","respondentType":"human","studyVersion":"export-fixture"})
        result = aggregate(self.store,self.service.document)
        self.assertEqual(1,result["reviewersCompleted"])
        self.assertEqual(12,result["conditions"][0]["responses"])
        self.assertEqual(14,len(result["responses"]))
        self.assertEqual(0,result["humanParticipants"])
        self.assertEqual(2,result["reviewers"][0]["agreements"])
        self.assertEqual(12,len(result["conditions"][0]["pairResults"]))
        self.assertIn("pairClustered95PctInterval", result["conditions"][0])
        self.assertEqual(0,result["conditions"][0]["pairMajoritySignTest"]["decisivePairs"])
        self.assertIsNone(result["conditions"][0]["pairMajoritySignTest"]["twoSidedExactP"])
        self.assertNotIn("sessionToken",str(result))
        self.assertNotIn("recordedAt",str(result))
        self.assertTrue(all(row["respondentType"]=="ai_pilot" for row in result["responses"]))


if __name__ == "__main__":
    unittest.main()
