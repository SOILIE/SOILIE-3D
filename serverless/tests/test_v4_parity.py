from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
BASELINE = {
    "imagine.py": "251681fbf568c250075d6a725e6d2c0c6d2ccfe0d51a4b481d47dcc5649f98ba",
    "modules/prepare_data.py": "4bd3b2f53843df85c29da91d40ec2447f579691dae439c20bb7c2a7cb46f660c",
    "modules/working_combos.py": "2f682d3d6c17b8c9592f2cbc5490cad9571b6e794f755333b02e5273e8548eba",
    "modules/png2gif.py": "e90d6273047540d7964a6210cb4cdd24bfb2d47fc8e26eb24b54a4372fc5e6c2",
    "modules/progress_bar.py": "6422be7d2d9636f108b4b49d51ef1f72b81ccd0e4a9fc4a4ae4526e4e66e694a",
    "modules/timer.py": "eae6e389f0859d25a33cd8d04429bcde6310003f9e2a7c4b1d160c2cf3c46f9d",
    "suggested_setup.blend": "46285b7334eb11cbeaae213a7ae37bca890cfd79a23cd5825125051efcc7df0b",
    "assets/asset_rotations.csv": "c83fd551111f853c02a022d5469434d2f2cc0615661f41fa78d3a21538169842",
    "requirements.txt": "c14202519e1dbbae9b6e44863d16f69b6f6e827b55c16da3cddc6d03955a16aa",
}


class V4ParityTests(unittest.TestCase):
    def test_unchanged_v4_files_match_publication_branch_bytes(self):
        observed = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in BASELINE
        }
        self.assertEqual(BASELINE, observed)

    def test_runtime_records_the_exact_repository_source(self):
        provenance_path = ROOT / ".codex" / "runtime" / "v4-provenance.json"
        if not provenance_path.exists():
            self.skipTest("Compile the V4 runtime metadata before checking provenance.")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        current_render = hashlib.sha256((ROOT / "modules/render.py").read_bytes()).hexdigest()
        self.assertEqual(current_render, provenance["files"]["modules/render.py"]["sha256"])
        self.assertNotIn("stagedSha256", provenance["files"]["modules/render.py"])
        self.assertFalse(provenance["extensions"]["optionalRoomFitChangesInteriorPlacement"])
        self.assertTrue(provenance["extensions"]["collisionRecoveryChangesOnlyRepeatedStates"])
        self.assertIn("repeated pairwise", provenance["extensions"]["collisionRecovery"])


if __name__ == "__main__":
    unittest.main()
