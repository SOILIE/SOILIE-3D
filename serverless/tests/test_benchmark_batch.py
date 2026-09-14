"""Checkpoint and watchdog tests use tiny child processes, not alternative solvers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from serverless.benchmark.run_batch import checkpoint_rows, run_lock, terminate_tree, write_json


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
