from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from serverless.study.service import StudyError, StudyService, PROFILES
from serverless.study.store import SQLiteStudyStore


def protocol():
    return {"studyVersion":"unit-v1", "pilotCollectionEnabled":True,
            "cases":[{"id":f"c{i}","title":"Bedroom","comparisonCondition":"baseline",
                      "relationImage":f"/{i}a.svg", "comparisonImage":f"/{i}b.svg"} for i in range(12)]}


class StudyServiceTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parents[2]/".codex/tests"
        scratch.mkdir(parents=True,exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=scratch)
        self.store = SQLiteStudyStore(Path(self.directory.name)/"pilot.sqlite3")
        self.service = StudyService(protocol(),self.store,b"test-secret",True,clock=lambda:1000)
        self.invitation = self.service.invite("reviewer-01","overlap","test-model")
        self.session = self.service.start({"invitation":self.invitation,"respondentType":"human"})

    def tearDown(self):
        self.directory.cleanup()

    def response(self):
        return {"sessionToken":self.session["sessionToken"], "caseId":self.session["cases"][0]["caseId"],
                "judgement":"left", "errorChoice":"uncertain", "confidence":2,"note":"Visible overlap on the right."}

    def test_all_ten_profiles_and_server_assigned_provenance(self):
        self.assertEqual(10,len(PROFILES))
        self.assertEqual("ai_pilot",self.session["respondentType"])
        stored = self.store.get(self.session["sessionId"])
        self.assertEqual("test-model",stored["model"])
        self.assertEqual("overlap",stored["promptProfile"])

    def test_balanced_opaque_stable_assignments(self):
        stored = self.store.get(self.session["sessionId"])
        main = [case for case in stored["assignments"] if not case["repeatOf"]]
        self.assertEqual(6,sum(case["leftCondition"] == "soilie" for case in main))
        self.assertEqual(14,len(self.session["cases"]))
        self.assertEqual({"caseId","title","leftImage","rightImage"},set(self.session["cases"][0]))
        self.assertEqual(self.session,self.service.start({"invitation":self.invitation}))

    def test_save_reload_and_idempotency(self):
        response = self.response()
        self.service.respond(self.session["sessionId"],response)
        self.service.respond(self.session["sessionId"],response)
        reloaded = self.service.resume(self.session["sessionId"],response)
        self.assertEqual([response["caseId"]],reloaded["completedCaseIds"])
        self.assertEqual(1,len(self.store.responses(self.session["sessionId"])))
        with self.assertRaises(StudyError) as caught:
            self.service.respond(self.session["sessionId"],dict(response,judgement="right"))
        self.assertEqual(409,caught.exception.status)

    def test_assignment_pinned_across_protocol_change(self):
        old_cases = deepcopy(self.session["cases"])
        self.service.document = dict(protocol(),studyVersion="new",cases=[])
        resumed = self.service.resume(self.session["sessionId"],self.response())
        self.assertEqual(old_cases,resumed["cases"])
        self.assertEqual("unit-v1",resumed["studyVersion"])

    def test_no_human_start_or_forged_invitation(self):
        for body in ({"participantLabel":"Human", "consent":True}, {"invitation":self.invitation+"x"}):
            with self.assertRaises(StudyError) as caught:
                self.service.start(body)
            self.assertEqual(403,caught.exception.status)

    def test_invalid_token_confidence_and_expiry(self):
        with self.assertRaises(StudyError):
            self.service.respond(self.session["sessionId"],dict(self.response(),sessionToken="wrong"))
        with self.assertRaises(StudyError):
            self.service.respond(self.session["sessionId"],dict(self.response(),confidence=True))
        self.service.clock = lambda:10000000
        with self.assertRaises(StudyError):
            self.service.resume(self.session["sessionId"],self.response())

    def test_malformed_response_fields_return_validation_errors(self):
        for key in ("judgement", "errorChoice"):
            for value in ([], {}, None):
                with self.subTest(key=key, value=value), self.assertRaises(StudyError) as caught:
                    self.service.respond(self.session["sessionId"], dict(self.response(), **{key:value}))
                self.assertEqual(400, caught.exception.status)
        with self.assertRaises(StudyError) as caught:
            self.service.resume(self.session["sessionId"], dict(self.response(), sessionToken="\u00e9"*64))
        self.assertEqual(403, caught.exception.status)

    def test_changed_instructions_cannot_mix_with_frozen_responses(self):
        with patch.dict(PROFILES, {"overlap":"A different instruction."}):
            for action in (self.service.resume, self.service.respond):
                with self.assertRaises(StudyError) as caught:
                    action(self.session["sessionId"], self.response())
                self.assertEqual("STUDY_PROTOCOL_CHANGED", caught.exception.code)
        self.assertEqual([], self.store.responses(self.session["sessionId"]))


if __name__ == "__main__":
    unittest.main()
