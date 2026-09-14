import unittest

from modules.collision_resolution import (
    Box,
    choose_recovery_move,
    intersection_volume,
    overlapping_pairs,
    total_overlap,
)


def box(name, x1, x2, y1, y2, z1=0, z2=1):
    return Box(name, x1, x2, y1, y2, z1, z2)


class CollisionResolutionTests(unittest.TestCase):
    def test_disjoint_touching_and_vertically_separated_boxes_do_not_overlap(self):
        first = box("first", 0, 1, 0, 1)
        self.assertEqual(0, intersection_volume(first, box("touching", 1, 2, 0, 1)))
        self.assertEqual(0, intersection_volume(first, box("above", 0, 1, 0, 1, 1, 2)))
        self.assertEqual([], overlapping_pairs({"first": first, "touching": box("touching", 1, 2, 0, 1)}))

    def test_recovery_accounts_for_a_third_object_instead_of_oscillating(self):
        boxes = {
            "left": box("left", -0.7, 0.3, 0, 1),
            "middle": box("middle", 0, 1, 0, 1),
            "right": box("right", 0.7, 1.7, 0, 1),
        }
        move = choose_recovery_move(
            boxes,
            ["middle", "left", "right"],
            {name: ((value.min_x + value.max_x) / 2, (value.min_y + value.max_y) / 2) for name, value in boxes.items()},
            (-1, 2, -1, 2),
        )
        proposal = dict(boxes)
        proposal[move.name] = proposal[move.name].translated(move.dx, move.dy)
        self.assertLess(total_overlap(proposal), total_overlap(boxes))
        self.assertEqual(0, total_overlap(proposal))

    def test_cluster_edge_candidate_guarantees_progress_in_a_crowded_row(self):
        boxes = {
            "a": box("a", 0, 2, 0, 1),
            "b": box("b", 1, 3, 0, 1),
            "c": box("c", 2, 4, 0, 1),
            "d": box("d", 3, 5, 0, 1),
        }
        move = choose_recovery_move(
            boxes,
            ["d", "c", "b", "a"],
            {name: ((value.min_x + value.max_x) / 2, 0.5) for name, value in boxes.items()},
            (0, 5, 0, 1),
        )
        proposal = dict(boxes)
        proposal[move.name] = proposal[move.name].translated(move.dx, move.dy)
        self.assertLess(total_overlap(proposal), total_overlap(boxes))

    def test_architecture_pair_is_not_treated_as_furniture_collision(self):
        boxes = {
            "window": box("window", 0, 1, 0, 0.1),
            "blinds": box("blinds", 0, 1, 0, 0.1),
        }
        fixed = frozenset(boxes)
        self.assertEqual(0, total_overlap(boxes, fixed))

    def test_architecture_is_excluded_from_the_furniture_collision_graph(self):
        boxes = {
            "window": box("window", 0, 1, 0, 0.2),
            "cabinet": box("cabinet", 0, 1, 0, 1),
        }
        self.assertEqual(0, total_overlap(boxes, frozenset({"window"})))

    def test_same_inputs_choose_same_recovery(self):
        boxes = {"a": box("a", 0, 1, 0, 1), "b": box("b", 0.5, 1.5, 0, 1)}
        arguments = (boxes, ["a", "b"], {"a": (0.5, 0.5), "b": (1, 0.5)})
        self.assertEqual(choose_recovery_move(*arguments), choose_recovery_move(*arguments))


if __name__ == "__main__":
    unittest.main()
