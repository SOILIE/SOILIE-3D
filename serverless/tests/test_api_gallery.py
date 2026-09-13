from __future__ import annotations

import json
import os
import time
import unittest
import uuid
from unittest.mock import patch


# The Lambda module reads deployment configuration while importing. Harmless
# test values keep these unit tests independent of credentials and AWS state.
os.environ.setdefault("AWS_DEFAULT_REGION", "ca-central-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("JOB_TABLE", "test-jobs")
os.environ.setdefault("QUEUE_URL", "https://sqs.ca-central-1.amazonaws.com/000000000000/test")
os.environ.setdefault("VISITOR_HMAC_SECRET", "unit-test-secret")
os.environ.setdefault("GALLERY_AUTH_BUCKET", "test-gallery-auth")

from serverless.api import handler as api  # noqa: E402


class GalleryApiTests(unittest.TestCase):
    def test_management_token_is_stable_and_job_scoped(self):
        first_job = str(uuid.uuid4())
        second_job = str(uuid.uuid4())
        self.assertEqual(api._management_token(first_job), api._management_token(first_job))
        self.assertNotEqual(api._management_token(first_job), api._management_token(second_job))
        self.assertEqual(64, len(api._management_token(first_job)))

    def test_password_verifier_is_salted_and_never_contains_plaintext(self):
        password = "a distinct removal password"
        first = api._password_verifier(password)
        second = api._password_verifier(password)

        self.assertEqual("pbkdf2-sha256", first["algorithm"])
        self.assertEqual(310_000, first["iterations"])
        self.assertNotEqual(first["salt"], second["salt"])
        self.assertNotEqual(first["passwordHash"], second["passwordHash"])
        self.assertNotIn(password, json.dumps(first))
        self.assertTrue(api._password_matches(password, first))
        self.assertFalse(api._password_matches("the wrong password", first))

    def test_password_and_display_name_validation(self):
        with self.assertRaises(ValueError):
            api._password_verifier("too-short")
        with self.assertRaises(ValueError):
            api._display_name("x" * 41)
        self.assertEqual("Anonymous", api._display_name("  "))
        self.assertEqual("A Scene Maker", api._display_name("  A   Scene Maker  "))

    def test_scene_retention_path_rejects_zero_and_malformed_values(self):
        job_id = str(uuid.uuid4())
        self.assertEqual((job_id, 0), api._retention_path(f"/generations/{job_id}/scenes/1/retention"))
        self.assertIsNone(api._retention_path(f"/generations/{job_id}/scenes/0/retention"))
        self.assertIsNone(api._retention_path(f"/generations/{job_id}/scenes/nope/retention"))
        self.assertIsNone(api._retention_path("/generations/not-a-uuid/scenes/1/retention"))

    def test_public_gallery_record_excludes_private_auth_and_job_fields(self):
        gallery_id = str(uuid.uuid4())
        item = {
            "galleryId": {"S": gallery_id},
            "createdAt": {"N": "1760000000"},
            "publishedAt": {"N": "1760000100"},
            "displayName": {"S": "Anonymous"},
            "objectsJson": {"S": '["bed","lamp","desk"]'},
            "roomJson": {"S": '{"actual":{"widthM":4.2,"depthM":3.5}}'},
            "seed": {"N": "19"},
            "mode": {"S": "objects"},
            "artifactsJson": {"S": '{"ordinaryImage":"https://example.test/ordinary.png"}'},
            "modelImplementation": {"S": "original"},
            "passwordHash": {"S": "must-not-leak"},
            "jobId": {"S": str(uuid.uuid4())},
        }
        record = api._gallery_record(item)
        self.assertEqual(gallery_id, record["galleryId"])
        self.assertEqual(["bed", "lamp", "desk"], record["objects"])
        self.assertNotIn("passwordHash", record)
        self.assertNotIn("jobId", record)
        self.assertEqual("original", record["model"]["implementation"])

    def test_gallery_excludes_retired_approximate_records(self):
        exact = {
            "galleryId": {"S": str(uuid.uuid4())},
            "createdAt": {"N": "1760000000"},
            "publishedAt": {"N": "1760000100"},
            "objectsJson": {"S": '["bed","lamp","desk"]'},
            "roomJson": {"S": '{"actual":{"widthM":4.2,"depthM":3.5}}'},
            "seed": {"N": "19"},
            "mode": {"S": "objects"},
            "artifactsJson": {"S": '{}'},
            "modelImplementation": {"S": "original"},
        }
        retired = {**exact, "galleryId": {"S": str(uuid.uuid4())}}
        retired.pop("modelImplementation")
        with patch.object(api.dynamodb, "query", return_value={"Items": [retired, exact]}):
            response = api._gallery({"rawQueryString": ""})
        items = json.loads(response["body"])["items"]
        self.assertEqual([exact["galleryId"]["S"]], [item["galleryId"] for item in items])

    def test_gallery_cursor_round_trip_and_invalid_cursor(self):
        sk = f"1760000000#{uuid.uuid4()}"
        with patch.object(api.dynamodb, "query", return_value={"Items": [], "LastEvaluatedKey": api._item_key("gallery", sk)}):
            first = api._gallery({"rawQueryString": "limit=1"})
        cursor = json.loads(first["body"])["nextCursor"]

        with patch.object(api.dynamodb, "query", return_value={"Items": []}) as query:
            second = api._gallery({"rawQueryString": f"limit=1&cursor={cursor}"})
        self.assertEqual(200, second["statusCode"])
        self.assertEqual(api._item_key("gallery", sk), query.call_args.kwargs["ExclusiveStartKey"])

        invalid = api._gallery({"rawQueryString": "cursor=%"})
        self.assertEqual(400, invalid["statusCode"])
        self.assertEqual("INVALID_CURSOR", json.loads(invalid["body"])["error"]["code"])

    def test_gallery_removal_restores_unexpired_source_to_temporary(self):
        job_id = str(uuid.uuid4())
        gallery_id = str(uuid.uuid4())
        expiry = int(time.time()) + 3600
        pointer = {"jobId": {"S": job_id}, "sceneIndex": {"N": "1"}}
        scene = {
            "expiresAt": {"N": str(expiry + 86400)},
            "resultExpiresAt": {"N": str(expiry)},
            "retentionJson": {"S": json.dumps({"mode": "public", "galleryId": gallery_id})},
        }
        with patch.object(api, "_get", return_value=scene), patch.object(api.dynamodb, "update_item") as update:
            api._restore_scene_after_gallery_removal(pointer, gallery_id)
        saved = json.loads(update.call_args.kwargs["ExpressionAttributeValues"][":retention"]["S"])
        self.assertEqual("temporary", saved["mode"])
        self.assertIn("expiresAt", saved)


if __name__ == "__main__":
    unittest.main()
