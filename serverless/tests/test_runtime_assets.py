from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.compiler.runtime_assets import verify


class RuntimeAssetTests(unittest.TestCase):
    def test_changed_runtime_input_is_rejected(self):
        base = Path(__file__).parents[2] / ".codex" / "tests"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            root = Path(temporary)
            asset = root / "assets" / "chair.obj"
            asset.parent.mkdir()
            asset.write_bytes(b"known V4 input")
            manifest = {
                "schemaVersion": 1,
                "files": [{
                    "path": "assets/chair.obj",
                    "sourceKey": "files/assets/chair.obj",
                    "bytes": asset.stat().st_size,
                    "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                }],
            }
            with patch("serverless.compiler.runtime_assets.expected_paths", return_value=[asset]):
                verify(root, manifest)
                asset.write_bytes(b"changed V4 input")
                with self.assertRaisesRegex(ValueError, "checksum differs"):
                    verify(root, manifest)


if __name__ == "__main__":
    unittest.main()
