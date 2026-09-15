"""Duplicate imports must select Blender's new object, not the mesh asset ID."""

from __future__ import annotations

import unittest

from modules.blender_names import blender_copy_index, blender_source_name


class BlenderNameTests(unittest.TestCase):
    def test_copy_suffix_does_not_confuse_asset_sequence_number(self):
        names = ["cupboard_0002", "cupboard_0002.001", "cupboard_0002.002"]
        self.assertEqual("cupboard_0002.002", max(names, key=blender_copy_index))
        self.assertEqual(0, blender_copy_index("cupboard_0002"))
        self.assertEqual("cupboard_0002", blender_source_name("cupboard_0002.002"))


if __name__ == "__main__":
    unittest.main()
