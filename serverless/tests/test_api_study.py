"""API serialization uses the same service as the browser pilot."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("AWS_DEFAULT_REGION", "ca-central-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("JOB_TABLE", "test-jobs")
os.environ.setdefault("QUEUE_URL", "https://sqs.ca-central-1.amazonaws.com/000000000000/test")
os.environ.setdefault("VISITOR_HMAC_SECRET", "unit-test-secret")
os.environ.setdefault("GALLERY_AUTH_BUCKET", "test-gallery-auth")

from serverless.api import handler as api
from serverless.study.service import StudyService
from serverless.study.store import SQLiteStudyStore
from serverless.tests.test_study_service import protocol


class StudyApiTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).parents[2]/".codex/tests"
        base.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.store = SQLiteStudyStore(Path(self.temp.name)/"api.sqlite3")
        self.service = StudyService(protocol(),self.store,b"unit-only",enabled=True)
        self.adapter = patch.object(api,"_study_service",return_value=self.service)
        self.adapter.start()

    def tearDown(self):
        self.adapter.stop()
        self.temp.cleanup()

    def test_default_closed_and_no_human_enrollment(self):
        self.service.enabled = False
        self.assertEqual(503,api._study_start({"body":"{}"})["statusCode"])
        self.service.enabled = True
        self.assertEqual(403,api._study_start({"body":json.dumps({"participantLabel":"Person"})})["statusCode"])

    def test_start_submit_resume_immutable_over_api(self):
        invite = self.service.invite("r1","overlap","test-model")
        result = api._study_start({"body":json.dumps({"invitation":invite,"respondentType":"human"})})
        self.assertEqual(201,result["statusCode"])
        session = json.loads(result["body"])
        self.assertEqual("ai_pilot",session["respondentType"])
        self.assertNotIn("leftCondition",session["cases"][0])
        row = {"sessionToken":session["sessionToken"],"caseId":session["cases"][0]["caseId"],
               "judgement":"tie","errorChoice":"uncertain","confidence":1,"note":"Test fixture only"}
        event = {"body":json.dumps(row)}
        for _ in range(2):
            self.assertEqual(200,api._study_response(event,session["sessionId"])["statusCode"])
        resumed = api._study_session(event,session["sessionId"])
        self.assertEqual([row["caseId"]],json.loads(resumed["body"])["completedCaseIds"])
        self.assertEqual(409,api._study_response({"body":json.dumps(dict(row,judgement="left"))},session["sessionId"])["statusCode"])

    def test_json_base64_and_size_validation(self):
        for body in ("not json", "[]"):
            self.assertEqual(400,api._study_start({"body":body})["statusCode"])
        event = {"body":base64.b64encode(b'{}').decode(),"isBase64Encoded":True}
        self.assertEqual(403,api._study_start(event)["statusCode"])
        self.assertEqual(413,api._study_start({"body":"x"*12001})["statusCode"])


if __name__ == "__main__":
    unittest.main()
