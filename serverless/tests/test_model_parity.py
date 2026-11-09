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
}

# Identity pins for the generation core at authored commit 91bf332 and the local
# cohort at 2f2db1c, plus b109f59's human-authored terminal input fixes. The menu
# is not part of benchmark execution. These checks detect source drift; the
# behavioral tests establish correctness separately.
REVIEWED_MAINTENANCE = {
    "requirements.txt": "ee98c11e16e13a7e5672b53c48772c49d94d8bfb6cf8d98a382c3c25c3418dd2",
    "imagine.py": "87ecad6fdfc78630d0c486b5f7c60b250d3ef6fdf27f57011b6e97204ef453bf",
    "modules/collect_data.py": "0c2034f6ce9dc4e736c341d51a4bf63517f19bfa018aa0ee87075960e1eda40a",
    "modules/import_tools.py": "bdd3d41461b1a24bb1502d94c137e3446f5276483a83d30bf38bf46d92ec8185",
    "modules/png2gif.py": "ec1ff753dbe970f303f7c8c5668cd7d9715773b54cc73613a01f17ffdf2ccc32",
    "modules/prepare_data.py": "88ba4660ee6374adc61c243cbfb89667b8441947051d6be0bfedb40e82919eab",
    "modules/recency.py": "93aa5e7fa3149edd3bb2fd06fec1e9a801f45e61cdaff9dad5bb9540f40c75d9",
    "modules/render.py": "361430a0520182981f3bdcd29e815710188444cde7ca6221336e2bc51cfca929",
    "modules/source_geometry.py": "f3e95aae25e83574a8a5c565d98c551a3eba3fbffc6a23f27893f61cb403aba8",
    # Mounted window assemblies remain fixed, but cannot support furniture.
    "modules/support_settlement.py": "cd14f0573c0dd41592757c8375b16cb21b4e7c074b9e4cfd8d3d22160ad5e65b",
    "modules/working_combo_schema.py": "d8e36880d317a0c0589f6700e1a5f8a7212c1bdb676b4170cbf222a11ad9cdb5",
    "modules/working_combos.py": "1b6598cd7efa2d59651b4a5e5e1c3acd92f0ef0ee63f2d09c1e4d8933b4a1510",
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
