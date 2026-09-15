from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
BASELINE = {
    "modules/png2gif.py": "e90d6273047540d7964a6210cb4cdd24bfb2d47fc8e26eb24b54a4372fc5e6c2",
    "modules/progress_bar.py": "6422be7d2d9636f108b4b49d51ef1f72b81ccd0e4a9fc4a4ae4526e4e66e694a",
    "modules/timer.py": "eae6e389f0859d25a33cd8d04429bcde6310003f9e2a7c4b1d160c2cf3c46f9d",
    "suggested_setup.blend": "46285b7334eb11cbeaae213a7ae37bca890cfd79a23cd5825125051efcc7df0b",
    "assets/asset_rotations.csv": "c83fd551111f853c02a022d5469434d2f2cc0615661f41fa78d3a21538169842",
    "requirements.txt": "c14202519e1dbbae9b6e44863d16f69b6f6e827b55c16da3cddc6d03955a16aa",
}

# These files intentionally differ from publication-2025. Pinning their patched
# bytes keeps the V4.0.2 maintenance surface explicit instead of weakening the
# original-file parity check whenever a regression repair touches V4 code.
V4_0_2_MAINTENANCE = {
    "imagine.py": "5cab4b362b9532485b9bdf389ab258f0cd15e479bc142b455621e4f5f0c44112",
    "modules/working_combos.py": "8dff9d87bd9c26334c48afe07510fa9325692b82cc9602d734b0bdbd4e61b956",
    "modules/prepare_data.py": "98c4fa9509ced827b97173710d7a27d4bfb28956832649d42dcaf495de57c517",
    "modules/render.py": "64b3f156b6c9dfb2c95c248ab5706808c08b98988f61aff8f2576826dc4aecf5",
}


class V4ParityTests(unittest.TestCase):
    def test_unchanged_v4_files_match_publication_branch_bytes(self):
        observed = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in BASELINE
        }
        self.assertEqual(BASELINE, observed)

    def test_v4_0_2_maintenance_files_match_reviewed_patch_bytes(self):
        observed = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in V4_0_2_MAINTENANCE
        }
        self.assertEqual(V4_0_2_MAINTENANCE, observed)

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
