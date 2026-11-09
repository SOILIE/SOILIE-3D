"""The smoke inventory is tied to the tracked asset manifest, not just CSV rows."""
import csv
import json
from pathlib import Path
import unittest

from serverless.renderer.asset_smoke import registered_assets

ROOT = Path(__file__).resolve().parents[1]


class AssetRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / 'assets/asset_rotations.csv').open(newline='', encoding='utf-8-sig') as source:
            cls.rows = list(csv.DictReader(source))
        cls.manifest = json.loads((ROOT / 'serverless/runtime-assets.json').read_text(encoding='utf-8'))

    def test_all_manifest_meshes_are_selectable(self):
        actual = registered_assets(self.rows, self.manifest)
        self.assertEqual(191, len(actual))
        self.assertEqual(len(set(actual)), len(actual))

    def test_missing_and_unexpected_rotation_entries_fail(self):
        target = self.rows[0]['asset_name']
        missing = [row for row in self.rows if row['asset_name'] != target]
        extra = self.rows + [{'asset_name': 'untracked_9999.obj'}]
        for rows in (missing, extra, []):
            with self.subTest(rows=len(rows)), self.assertRaisesRegex(ValueError, 'differs'):
                registered_assets(rows, self.manifest)

    def test_incomplete_or_duplicate_manifest_fails(self):
        files = self.manifest['files']
        mesh = next(row for row in files if row['path'].endswith('.obj'))
        for changed in ([row for row in files if row is not mesh], files + [mesh]):
            with self.subTest(count=len(changed)), self.assertRaisesRegex(ValueError, '191 distinct'):
                registered_assets(self.rows, {'schemaVersion': 1, 'files': changed})


if __name__ == '__main__':
    unittest.main()
