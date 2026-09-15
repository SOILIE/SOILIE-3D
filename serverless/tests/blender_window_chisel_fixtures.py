"""Verify that one architectural opening is cut for a window assembly."""

from pathlib import Path
import sys
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "modules"))
import render  # noqa: E402


def cube(name, location, scale):
    bpy.ops.mesh.primitive_cube_add(size=2, location=location)
    value = bpy.context.object
    value.name = name
    value.scale = scale
    return value


class WindowChiselFixtures(unittest.TestCase):
    def setUp(self):
        for value in list(bpy.data.objects):
            bpy.data.objects.remove(value, do_unlink=True)

    def test_window_coverings_share_one_wall_opening(self):
        cube("Back Wall", (0, 0, 1), (0.1, 2, 1))
        cube("window_0001", (0.05, 0, 1.3), (0.08, 0.5, 0.5))
        cube("curtain_0001", (0.05, 0, 1.3), (0.08, 0.55, 0.55))
        cube("blinds_0001", (0.05, 0, 1.3), (0.08, 0.5, 0.5))
        chiselled = []
        original = render.subtract_overlap
        render.subtract_overlap = lambda cutter, wall: chiselled.append((cutter.name, wall.name))
        try:
            render.adjust_windows_to_walls()
        finally:
            render.subtract_overlap = original
        self.assertEqual([("_SOILIE_Window_Opening", "Back Wall")], chiselled)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WindowChiselFixtures)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Window-chisel fixture failed")
