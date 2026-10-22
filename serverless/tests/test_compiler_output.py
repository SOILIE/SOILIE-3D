"""Compiler output must never become a second, stale model checkout."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.compiler.build_runtime import build


TEST_TEMP_ROOT = Path(__file__).parents[2] / ".codex" / "test-compiler-output"


@contextmanager
def temporary_output():
    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
            yield Path(directory)
    finally:
        try:
            TEST_TEMP_ROOT.rmdir()
        except OSError:
            # Another concurrent test may still own a child directory.
            pass


class CompilerOutputTests(unittest.TestCase):
    def test_nested_model_runtime_is_rejected_before_compilation(self):
        with temporary_output() as output:
            (output / "soilie-runtime").mkdir()
            with patch("serverless.compiler.build_runtime.record_model_provenance") as provenance:
                with self.assertRaisesRegex(RuntimeError, "Copied model source"):
                    build(Path.cwd(), output)
            provenance.assert_not_called()

    def test_checksums_keep_model_identity_and_version_separate(self):
        with temporary_output() as output:
            with patch(
                "serverless.compiler.build_runtime.record_model_provenance",
                return_value={"assetCount": 191},
            ), patch(
                "serverless.compiler.build_runtime.compile_api_index",
                return_value={"objects": 64, "triplets": 6126, "combinations": 68732},
            ), patch(
                "serverless.compiler.build_runtime.model_version",
                return_value="4.0.3",
            ), patch(
                "serverless.compiler.build_runtime.sha256",
                return_value="test-digest",
            ):
                build(Path.cwd(), output)

            checksums = json.loads((output / "checksums.json").read_text(encoding="utf-8"))
            self.assertEqual("SOILIE-3D", checksums["model"])
            self.assertEqual("4.0.3", checksums["modelVersion"])


if __name__ == "__main__":
    unittest.main()
