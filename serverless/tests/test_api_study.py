from __future__ import annotations

import json
import os
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("AWS_DEFAULT_REGION", "ca-central-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("JOB_TABLE", "test-jobs")
os.environ.setdefault("QUEUE_URL", "https://sqs.ca-central-1.amazonaws.com/000000000000/test")
os.environ.setdefault("VISITOR_HMAC_SECRET", "unit-test-secret")
os.environ.setdefault("GALLERY_AUTH_BUCKET", "test-gallery-auth")

from serverless.api import handler as api  # noqa: E402


class StudyApiTests(unittest.TestCase):
    def test_protocol_is_prepared_without_legacy_approximation_cases(self):
        self.assertEqual("prepared-v3", api._study_document["studyVersion"])
        self.assertFalse(api._study_document["collectionEnabled"])
        self.assertEqual([], api._study_document["cases"])
        conditions = {item["id"] for item in api._study_document["plannedConditions"]}
        self.assertEqual(
            {"soilie-v4-exact", "layoutgpt-official", "grains-official", "infinigen-indoors-official"},
            conditions,
        )

    def test_future_assignments_are_stable_and_opaque(self):
        session_id = str(uuid.uuid4())
        case = {
            "id": "future-01",
            "objects": ["bed", "desk", "lamp"],
            "relationImage": "https://example.test/v4.png",
            "comparisonImage": "https://example.test/baseline.png",
            "comparisonCondition": "baseline",
        }
        first = api._study_assignment(session_id, case)
        self.assertEqual(first, api._study_assignment(session_id, case))
        self.assertEqual({"caseId", "objects", "leftImage", "rightImage"}, set(first))
        self.assertNotIn("comparisonCondition", first)
        self.assertNotEqual(first["leftImage"], first["rightImage"])

    def test_start_is_closed_before_validation_or_storage(self):
        with patch.object(api.dynamodb, "put_item") as put_item:
            result = api._study_start({"body": json.dumps({"participantLabel": "P-101"})})
        self.assertEqual(503, result["statusCode"])
        self.assertEqual("STUDY_NOT_COLLECTING", json.loads(result["body"])["error"]["code"])
        put_item.assert_not_called()

    def test_resume_and_response_are_closed(self):
        session_id = str(uuid.uuid4())
        self.assertEqual(503, api._study_session({"body": "{}"}, session_id)["statusCode"])
        self.assertEqual(503, api._study_response({"body": "{}"}, session_id)["statusCode"])


if __name__ == "__main__":
    unittest.main()
