"""Exercise collision recovery through the renderer inside Blender."""

from pathlib import Path
import os
import sys
import unittest

import bpy


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(os.environ.get("SOILIE_RUNTIME_DIR", ROOT))
sys.path[:0] = [str(ROOT), str(RUNTIME), str(RUNTIME / "modules")]
from modules import render


def cube(name, location, size=1):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


class CollisionRecoveryFixtures(unittest.TestCase):
    def tearDown(self):
        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    def test_recovery_clears_a_crowded_row_using_rendered_bounds(self):
        objects = {
            "left": cube("left", (-0.4, 0, 0)),
            "middle": cube("middle", (0, 0, 0)),
            "right": cube("right", (0.4, 0, 0)),
        }
        boxes = {name: render._collision_box(name, obj) for name, obj in objects.items()}
        centres = {name: (obj.location.x, obj.location.y) for name, obj in objects.items()}
        move = render.choose_recovery_move(boxes, ["middle", "left", "right"], centres)
        objects[move.name].location.x += move.dx
        objects[move.name].location.y += move.dy
        bpy.context.view_layer.update()
        moved_boxes = {name: render._collision_box(name, obj) for name, obj in objects.items()}
        self.assertLess(render.total_overlap(moved_boxes), render.total_overlap(boxes))

    def test_touching_and_vertically_separated_rendered_bounds_do_not_overlap(self):
        objects = {
            "first": cube("first", (0, 0, 0)),
            "touching": cube("touching", (1, 0, 0)),
            "above": cube("above", (0, 0, 1)),
        }
        boxes = {name: render._collision_box(name, obj) for name, obj in objects.items()}
        self.assertEqual(0, render.intersection_volume(boxes["first"], boxes["touching"]))
        self.assertEqual(0, render.intersection_volume(boxes["first"], boxes["above"]))
        self.assertEqual([], render.overlapping_pairs({
            "first": boxes["first"],
            "touching": boxes["touching"],
        }))

    def test_cluster_edge_candidate_guarantees_progress(self):
        objects = {
            name: cube(name, (x, 0, 0), size=2)
            for name, x in (("a", 0), ("b", 1), ("c", 2), ("d", 3))
        }
        boxes = {name: render._collision_box(name, obj) for name, obj in objects.items()}
        centres = {name: (obj.location.x, obj.location.y) for name, obj in objects.items()}
        move = render.choose_recovery_move(boxes, ["d", "c", "b", "a"], centres, (-1, 4, -1, 1))
        proposal = dict(boxes)
        proposal[move.name] = proposal[move.name].translated(move.dx, move.dy)
        self.assertLess(render.total_overlap(proposal), render.total_overlap(boxes))

    def test_same_rendered_bounds_choose_same_recovery(self):
        objects = {
            "a": cube("a", (0, 0, 0)),
            "b": cube("b", (0.5, 0, 0)),
        }
        boxes = {name: render._collision_box(name, obj) for name, obj in objects.items()}
        centres = {name: (obj.location.x, obj.location.y) for name, obj in objects.items()}
        arguments = (boxes, ["a", "b"], centres)
        self.assertEqual(
            render.choose_recovery_move(*arguments),
            render.choose_recovery_move(*arguments),
        )

    def test_architecture_is_excluded_from_collision_recovery(self):
        window = cube("window", (0, 0, 0))
        cabinet = cube("cabinet", (0, 0, 0))
        boxes = {
            "window": render._collision_box("window", window),
            "cabinet": render._collision_box("cabinet", cabinet),
        }
        self.assertEqual(0, render.total_overlap(boxes, frozenset({"window"})))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CollisionRecoveryFixtures)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError("Collision-recovery fixture failed")
