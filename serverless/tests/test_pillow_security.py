from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class PillowSecurityTests(unittest.TestCase):
    """Keep every shipped Pillow dependency above the audited security floor."""

    def test_pillow_security_floor_is_consistent_across_runtime_surfaces(self):
        expected = {
            "requirements.txt": "pillow>=13.0.0",
            "serverless/benchmark/requirements.txt": "Pillow==13.0.0",
            "serverless/renderer/Dockerfile": "Pillow==13.0.0",
        }

        for relative_path, requirement in expected.items():
            with self.subTest(path=relative_path):
                contents = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(requirement, contents)
                self.assertNotIn("Pillow==10.4.0", contents)
                self.assertNotIn("pillow>=10.4.0", contents)


if __name__ == "__main__":
    unittest.main()
