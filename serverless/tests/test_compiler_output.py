"""Compiler output must never become a second, stale model checkout."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.compiler.build_runtime import build


class CompilerOutputTests(unittest.TestCase):
    def test_legacy_nested_runtime_is_rejected_before_compilation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "v4").mkdir()
            with patch("serverless.compiler.build_runtime.record_v4_provenance") as provenance:
                with self.assertRaisesRegex(RuntimeError, "Legacy copied model source"):
                    build(Path.cwd(), output)
            provenance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
