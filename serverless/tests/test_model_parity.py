from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
BASELINE = {
    "modules/progress_bar.py": "6422be7d2d9636f108b4b49d51ef1f72b81ccd0e4a9fc4a4ae4526e4e66e694a",
    "modules/timer.py": "eae6e389f0859d25a33cd8d04429bcde6310003f9e2a7c4b1d160c2cf3c46f9d",
    "suggested_setup.blend": "46285b7334eb11cbeaae213a7ae37bca890cfd79a23cd5825125051efcc7df0b",
    "assets/asset_rotations.csv": "c83fd551111f853c02a022d5469434d2f2cc0615661f41fa78d3a21538169842",
    "requirements.txt": "c14202519e1dbbae9b6e44863d16f69b6f6e827b55c16da3cddc6d03955a16aa",
}

# These files intentionally differ from publication-2025. Pinning their patched
# bytes keeps the reviewed maintenance surface explicit instead of weakening the
# original-file parity check whenever a regression repair touches model code.
REVIEWED_MAINTENANCE = {
    "imagine.py": "1907be65a8883276004eb7b776015eee431771b20a0d0cc6dd8cabc536aa6ba9",
    "modules/collect_data.py": "4718becd9331bb29f58e5db887e492c8d37de15c0c58603cd6fb601ada0f6f85",
    "modules/import_tools.py": "ee6379943d29d16f144902c13364e460b96021b8f5b72a87802080750c123d17",
    "modules/png2gif.py": "ec1ff753dbe970f303f7c8c5668cd7d9715773b54cc73613a01f17ffdf2ccc32",
    "modules/prepare_data.py": "d7d24ec2f5b16b9536fea264c9a2bc5c5ec7e24086b5c79943008590804fb0be",
    "modules/recency.py": "6f82ff6c76c87f88197b5a460284669982466a505970fda5f97ee2d83d82a60a",
    "modules/render.py": "07fbb228ffed339085a73534c54a384d3a2d1ccfb94568cd27d38dbf2adf1040",
    "modules/source_geometry.py": "a8915def7ba82ecd72992db43c1f73752c844a45ee556e3d5821aba219cdbbb6",
    # Mounted window assemblies remain fixed, but cannot support furniture.
    "modules/support_settlement.py": "cd14f0573c0dd41592757c8375b16cb21b4e7c074b9e4cfd8d3d22160ad5e65b",
    "modules/working_combo_schema.py": "7af878e0f023c930010d11fa48a441d7e70e8d610a5896f5056682cb837d626b",
    "modules/working_combos.py": "e37fc3aa97ba39c57bdbb743db056bed899dfa0019b4c824da9eb03ead5a49a3",
}


class ModelParityTests(unittest.TestCase):
    def test_unchanged_model_files_match_publication_branch_bytes(self):
        observed = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in BASELINE
        }
        self.assertEqual(BASELINE, observed)

    def test_maintenance_files_match_reviewed_patch_bytes(self):
        observed = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in REVIEWED_MAINTENANCE
        }
        self.assertEqual(REVIEWED_MAINTENANCE, observed)

    def test_runtime_records_the_exact_repository_source(self):
        provenance_path = ROOT / ".codex" / "runtime" / "model-provenance.json"
        if not provenance_path.exists():
            self.skipTest("Compile the SOILIE-3D runtime metadata before checking provenance.")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        current_render = hashlib.sha256((ROOT / "modules/render.py").read_bytes()).hexdigest()
        self.assertEqual(current_render, provenance["files"]["modules/render.py"]["sha256"])
        self.assertNotIn("stagedSha256", provenance["files"]["modules/render.py"])
        self.assertFalse(provenance["extensions"]["optionalRoomFitChangesInteriorPlacement"])
        self.assertTrue(provenance["extensions"]["collisionRecoveryChangesOnlyRepeatedStates"])
        self.assertIn("repeated pairwise", provenance["extensions"]["collisionRecovery"])


if __name__ == "__main__":
    unittest.main()
