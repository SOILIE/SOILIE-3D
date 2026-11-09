"""A failed or incomplete Blender check must never look like a passing gate."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from tests import __main__ as runner


class TestRunnerTests(unittest.TestCase):
    def test_timeout_preserves_partial_diagnostics_and_fails(self):
        error = subprocess.TimeoutExpired(["blender"], 5, output=b"asset 42\n", stderr=b"detail\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(runner.subprocess, "run", side_effect=error), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            result, raw = runner.blender_command(["blender"], "assets", 5)
        self.assertEqual(1, result["returnCode"])
        self.assertIn("Timed out", result["error"])
        self.assertEqual("asset 42\n", raw)
        self.assertEqual(raw, result["stdoutTail"])
        self.assertEqual("detail\n", stderr.getvalue())

    def test_launch_failure_is_an_explicit_fixture_failure(self):
        with patch.object(runner.subprocess, "run", side_effect=FileNotFoundError("missing Blender")):
            result, raw = runner.blender_command(["blender"], "collision", 5)
        self.assertEqual("collision", result["fixture"])
        self.assertEqual(1, result["returnCode"])
        self.assertEqual("", raw)

    def test_asset_success_requires_one_complete_receipt(self):
        manifest = json.loads((runner.ROOT / "serverless/runtime-assets.json").read_text())
        count = sum(item["path"].startswith("assets/") and item["path"].endswith(".obj")
                    for item in manifest["files"])
        receipt = json.dumps({"assetSmoke": "passed", "count": count})
        self.assertEqual(count, runner.asset_smoke_receipt("import log\n" + receipt)["count"])
        for output in ("", json.dumps({"assetSmoke": "passed", "count": 0}),
                       json.dumps({"assetSmoke": "passed", "count": count - 1}), receipt + "\n" + receipt):
            with self.subTest(output=output), self.assertRaises(ValueError):
                runner.asset_smoke_receipt(output)
