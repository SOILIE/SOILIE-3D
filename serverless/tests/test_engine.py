from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

from serverless.common.engine import RequestError, open_runtime, public_catalog_document, validate_request
from serverless.common.model_version import MODEL_VERSION


RUNTIME = Path(os.environ.get("SOILIE_RUNTIME_DB", ".codex/runtime/relations.sqlite3"))


@unittest.skipUnless(RUNTIME.exists(), "Compile the runtime database before running integration tests.")
class EngineBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = open_runtime(RUNTIME)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def request(self, **overrides):
        payload = {
            "clientRequestId": str(uuid.uuid4()),
            "mode": "random",
            "sceneCount": 1,
            "objectCount": 6,
            "sameObjectsAcrossScenes": True,
            "allowDuplicates": False,
            "room": {"mode": "auto"},
            "seed": 19,
        }
        payload.update(overrides)
        return validate_request(self.db, payload)

    def test_catalog_identifies_original_v4(self):
        document = public_catalog_document(self.db)
        self.assertEqual(
            {
                "name": "SOILIE-3D V4", "version": MODEL_VERSION, "implementation": "original",
                "sourceCommit": "local", "assetManifestSha256": "local", "channel": "publication-2026",
            },
            document["model"],
        )
        self.assertTrue(document["objects"])
        self.assertNotIn("window", {item["name"] for item in document["objects"]})

    def test_exact_working_combo_is_accepted(self):
        encoded = self.db.execute(
            "SELECT objects_json FROM working_combinations WHERE preset = 'random' ORDER BY row_index LIMIT 1"
        ).fetchone()[0]
        import json

        objects = json.loads(encoded)[:3]
        request = self.request(mode="objects", objects=objects)
        self.assertEqual(objects, request["objects"])

    def test_duplicate_instances_remain_valid_input(self):
        row = self.db.execute(
            "SELECT object_a, object_b, object_c FROM triplets WHERE object_a = object_b LIMIT 1"
        ).fetchone()
        if row is None:
            self.skipTest("The current V4 triplet data has no repeated-label triplet.")
        request = self.request(mode="objects", objects=list(row))
        self.assertEqual(list(row), request["objects"])

    def test_unsupported_set_returns_stable_error_and_real_suggestions(self):
        with self.assertRaises(RequestError) as raised:
            self.request(mode="objects", objects=["bathtub", "helmet", "flute"])
        self.assertEqual("UNSUPPORTED_OBJECT_COMBINATION", raised.exception.code)
        self.assertTrue(raised.exception.details["suggestions"])

    def test_empty_original_preset_is_not_synthesized(self):
        members = self.db.execute(
            "SELECT COUNT(*) FROM working_combinations WHERE preset = 'bathroom'"
        ).fetchone()[0]
        self.assertEqual(0, members)
        with self.assertRaises(RequestError) as raised:
            self.request(mode="room_type", roomType="bathroom")
        self.assertEqual("UNSUPPORTED_ROOM_PRESET", raised.exception.code)

    def test_custom_room_validation_is_boundary_only_metadata(self):
        import json

        encoded = self.db.execute(
            "SELECT objects_json FROM working_combinations WHERE preset = 'random' ORDER BY row_index LIMIT 1"
        ).fetchone()[0]
        request = self.request(
            mode="objects",
            objects=json.loads(encoded)[:3],
            room={"mode": "custom", "widthM": 4.5, "depthM": 3.8},
        )
        self.assertEqual({"mode": "custom", "widthM": 4.5, "depthM": 3.8}, request["room"])

    def test_scene_and_random_object_limits_match_public_contract(self):
        with self.assertRaises(RequestError):
            self.request(sceneCount=4)
        with self.assertRaises(RequestError):
            self.request(objectCount=7)


if __name__ == "__main__":
    unittest.main()
