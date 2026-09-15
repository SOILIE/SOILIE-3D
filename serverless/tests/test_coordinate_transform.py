"""The appended object row is known and must not be rediscovered heuristically."""

from __future__ import annotations

import unittest

import numpy as np

from modules.coordinate_transform import transformed_appended_coordinate


class CoordinateTransformTests(unittest.TestCase):
    def test_final_row_is_the_appended_object_even_if_it_matches_an_anchor(self):
        anchors_and_object = np.array([
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.5, 0.5, 0.0],
            [0.0, 0.0, 0.0],
        ], dtype=np.float32)
        self.assertEqual([0.0, 0.0, 0.0], transformed_appended_coordinate(anchors_and_object))

    def test_empty_transform_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no coordinate rows"):
            transformed_appended_coordinate([])


if __name__ == "__main__":
    unittest.main()
