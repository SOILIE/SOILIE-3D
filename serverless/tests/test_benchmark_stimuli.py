from copy import deepcopy
import unittest

from serverless.benchmark.geometry import box_corners, measure
from serverless.benchmark.stimuli import diagram, select_pairs
from serverless.study.export_pilot import aggregate
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
            row["metrics"]["meanWorstOverlapPct"] = 99
        self.assertEqual(selected,select_pairs(changed))
        self.assertEqual(12,len(selected))
        self.assertEqual(12,len({pair[2]["id"] for pair in selected}))

    def test_views_do_not_mutate_geometry_or_expose_model(self):
        scene = fixture("soilie",1)["scene"]
        before = deepcopy(scene)
        svg = diagram(scene)
        self.assertEqual(before,scene)
        self.assertIn("Plan view",svg)
        self.assertIn("Oblique view",svg)
        self.assertNotIn("soilie",svg)


class ExportTests(unittest.TestCase):
    setUp = study_tests.StudyServiceTests.setUp
    tearDown = study_tests.StudyServiceTests.tearDown
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
        self.assertNotIn("sessionToken",str(result))
        self.assertTrue(all(row["respondentType"]=="ai_pilot" for row in result["responses"]))


if __name__ == "__main__":
    unittest.main()
