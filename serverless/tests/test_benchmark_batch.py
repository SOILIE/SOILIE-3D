"""Checkpoint and watchdog tests use tiny child processes, not alternative solvers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from serverless.benchmark.run_batch import checkpoint_rows, prepare_manifest, run_lock, runtime_implementation, terminate_tree, write_json


class BatchTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).parents[2] / ".codex/tests"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_resume_keeps_failed_attempts_and_timing(self):
        for i, status in enumerate(["failed", "complete", "failed"]):
            write_json(self.root / f"attempt-{i:05d}.json", {"attempt": i, "status": status, "wallSeconds": i+1})
        rows = checkpoint_rows(self.root)
        self.assertEqual(3, len(rows))
        self.assertEqual(1, sum(row["status"] == "complete" for row in rows))
        self.assertEqual(6, sum(row["wallSeconds"] for row in rows))
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_missing_checkpoint_cannot_silently_overwrite(self):
        write_json(self.root / "attempt-00001.json", {"attempt": 1})
        with self.assertRaises(RuntimeError):
            checkpoint_rows(self.root)

    def test_runtime_identity_records_version_commit_and_render_bytes(self):
        (self.root / "modules").mkdir()
        (self.root / "modules" / "render.py").write_text("model = 'fixture'\n", encoding="utf-8")
        (self.root / "package.json").write_text('{"version":"4.0.2"}', encoding="utf-8")
        identity = runtime_implementation(self.root)
        self.assertEqual("4.0.2", identity["modelVersion"])
        self.assertRegex(identity["sourceCommit"], r"^(?:local|[0-9a-f]{40})$")
        self.assertEqual(64, len(identity["renderSha256"]))

    def test_explicit_render_maintenance_resume_records_a_provenance_boundary(self):
        def provenance(render_digest):
            return {
                "model": "SOILIE-3D V4", "version": "4.0.2", "channel": "main",
                "baselineCommit": render_digest, "totalBytes": 10,
                "files": {
                    "modules/render.py": {"sha256": render_digest, "bytes": 10},
                    "data/triplets.csv": {"sha256": "fixed", "bytes": 20},
                },
            }
        manifest = self.root / "run.json"
        base = {"schemaVersion": 1, "targetCompletions": 10, "provenance": provenance("old")}
        first, segment = prepare_manifest(manifest, base, [])
        self.assertEqual(0, segment)
        current = {**base, "provenance": provenance("new")}
        resumed, segment = prepare_manifest(manifest, current, [{"attempt": 0}], True)
        self.assertEqual(1, segment)
        self.assertEqual([0, 1], [value["firstAttempt"] for value in resumed["provenanceSegments"]])
        with self.assertRaises(RuntimeError):
            changed_data = {**current, "provenance": provenance("new")}
            changed_data["provenance"] = json.loads(json.dumps(changed_data["provenance"]))
            changed_data["provenance"]["files"]["data/triplets.csv"]["sha256"] = "different"
            prepare_manifest(manifest, changed_data, [{"attempt": 0}], True)

    @unittest.skipUnless(os.name == "posix", "Linux measurement worker")
    def test_single_writer_and_release(self):
        with run_lock(self.root):
            with self.assertRaises(BlockingIOError):
                with run_lock(self.root):
                    pass
        with run_lock(self.root):
            pass

    def test_watchdog_terminates_owned_worker(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 start_new_session=os.name == "posix")
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                child.wait(timeout=.05)
            terminate_tree(child)
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:
                terminate_tree(child)


if __name__ == "__main__":
    unittest.main()
