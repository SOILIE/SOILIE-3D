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

# These files intentionally differ from publication-2025. Pinning their patched
# bytes keeps the reviewed maintenance surface explicit instead of weakening the
# original-file parity check whenever a regression repair touches model code.
REVIEWED_MAINTENANCE = {
    "requirements.txt": "ee98c11e16e13a7e5672b53c48772c49d94d8bfb6cf8d98a382c3c25c3418dd2",
    "imagine.py": "1907be65a8883276004eb7b776015eee431771b20a0d0cc6dd8cabc536aa6ba9",
    "modules/collect_data.py": "0c2034f6ce9dc4e736c341d51a4bf63517f19bfa018aa0ee87075960e1eda40a",
    "modules/import_tools.py": "878b14cb64e8230c9da6c63ca257af304bc0a4143df87a1777f2bb5ae33ef752",
    "modules/object_identity.py": "e52161e7aa9466f5eaee019a75a271dd9a38468bc401c0928a8a071ee5520d0f",
    "modules/png2gif.py": "ec1ff753dbe970f303f7c8c5668cd7d9715773b54cc73613a01f17ffdf2ccc32",
    "modules/prepare_data.py": "45e18963b902dfeed5e6a496815a22049de5336f4e4d3b3f1183877471b155f2",
    "modules/recency.py": "93aa5e7fa3149edd3bb2fd06fec1e9a801f45e61cdaff9dad5bb9540f40c75d9",
    "modules/relation_manifest.py": "56b3d5ea2d157679056932206ab4fffa32f9216a17bc154b9b8541ff0b4a82d4",
    "modules/render.py": "c4148085f11afba8d689f5137bb2ce957093c49edfda64f0985e1ee5eeddc3dd",
    "modules/source_geometry.py": "cf06faee364c8a807b18cec45b6a3b562ab710f9ffb89fb90a2a86e69ea447cc",
    # Mounted window assemblies remain fixed, but cannot support furniture.
    "modules/support_settlement.py": "cd14f0573c0dd41592757c8375b16cb21b4e7c074b9e4cfd8d3d22160ad5e65b",
    "modules/working_combo_schema.py": "7af878e0f023c930010d11fa48a441d7e70e8d610a5896f5056682cb837d626b",
    "modules/utils.py": "225ad84c4ce0b03fdadd0f89d7db49ebae50d373b2ae98d1983272ed713eb590",
    "modules/working_combos.py": "6262f734677593599f3e2587ac715933e451c7c38454dc435404ba64ea279a0f",
    "serverless/benchmark/requirements.txt": "97317ba8c4a56ca9b64c7393f3b43d1d3397fc64938ddd9928d85cb5404c1033",
    "serverless/renderer/Dockerfile": "72a26431d5bd477f0f3ab13e54871655663dd8ccad52c045b9d21e16571f18f0",
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
