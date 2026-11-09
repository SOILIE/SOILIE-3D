import math
import random
import unittest
import itertools
from unittest import mock

import numpy as np
import pandas as pd

from modules import prepare_data as prep


class TripletGeometryTests(unittest.TestCase):
    def test_reconstruction_recovers_measured_distances_and_angles(self):
        # Derive measurements from independently selected Cartesian vectors.
        rng = random.Random(314159)
        for _ in range(40):
            c = np.array([rng.uniform(-2, 2), rng.uniform(.2, 3), 0.0])
            camera = np.array([rng.uniform(-2, 2), rng.uniform(-2, 2), rng.uniform(.2, 3)])
            angle = lambda u, v: math.degrees(math.acos(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v))))
            a, b, actual_c, actual_camera = prep.reconstruct_triplet_coordinates(
                2, np.linalg.norm(c), np.linalg.norm(camera),
                angle([1, 0, 0], c), angle(camera, [1, 0, 0]), angle(camera, c),
            )
            np.testing.assert_allclose(a, [0, 0, 0], atol=1e-10)
            np.testing.assert_allclose(b, [2, 0, 0], atol=1e-10)
            np.testing.assert_allclose(actual_c, c, atol=1e-10)
            np.testing.assert_allclose(actual_camera, camera, atol=1e-10)

    def test_invalid_and_impossible_geometry_is_rejected(self):
        for args in [(0, 1, 1, 90, 90, 90), (1, 1, 1, 0, 90, 90),
                     (1, 1, 1, 90, 0, 0), (1, 1, float('nan'), 90, 90, 90)]:
            with self.subTest(args=args), self.assertRaises(prep.TripletGeometryError):
                prep.reconstruct_triplet_coordinates(*args)

    def test_alignment_rotates_rigidly_and_reports_irreducible_residual(self):
        a, b = [1, 2, 3], [3, 2, 3]
        c, camera, diag = prep.align_triplet_about_axis(
            a, b, [2, 3, 3], a, b, [2.5, 2, 5], [1, 2, 4],
            object_name='c', base_object_name='d', anchor_names=['a', 'b'])
        np.testing.assert_allclose(c, [1, 3, 3], atol=1e-12)
        np.testing.assert_allclose(camera, [2.5, 4, 3], atol=1e-12)
        self.assertAlmostEqual(diag['rotationDegrees'], -90)
        self.assertAlmostEqual(diag['postRotationCameraPositionResidual'], math.sqrt(1.25))
        self.assertAlmostEqual(diag['sourceObjectCameraDistance'], diag['rotatedSourceObjectCameraDistance'])

    def test_axis_degeneracy_and_anchor_validation(self):
        kw = dict(object_name='c', base_object_name='d', anchor_names=['a', 'b'])
        c, camera, diag = prep.align_triplet_about_axis(
            [0, 0, 0], [1, 0, 0], [1, 0, 0], [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], **kw)
        self.assertEqual(diag['status'], 'underdetermined_camera_axis')
        np.testing.assert_allclose(c, [0, 0, 1])
        np.testing.assert_allclose(camera, [0, 1, 0])
        with self.assertRaises(prep.TripletGeometryError):
            prep.align_triplet_about_axis([0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0], [2, 0, 0], [0, 1, 0], [0, 0, 1], **kw)

    def test_window_companions_preserve_instances_and_copy_values(self):
        coords = {'curtain': [1, 2, 3], 'blinds.1': [4, 5, 6], 'window.1': [7, 8, 9], 'chair': [0, 0, 0]}
        sizes = {key: [i, 'medium'] for i, key in enumerate(coords)}
        prep.ensure_window_assemblies(coords, sizes)
        self.assertEqual(set(coords), {'curtain', 'blinds.1', 'window', 'window.1', 'chair'})
        self.assertEqual(coords['window'], [1, 2, 3])
        self.assertEqual(coords['blinds.1'], [7, 8, 9])
        self.assertEqual(sizes['blinds.1'], sizes['window.1'])
        coords['window'][0] = 99
        self.assertEqual(coords['curtain'][0], 1)
        self.assertEqual(prep.suffix_duplicates(['chair', 'table', 'chair', 'chair']), ['chair', 'table', 'chair.1', 'chair.2'])

    def test_count_boundary_rejects_before_reading_files(self):
        for count in (0, 2, 10):
            with self.subTest(count=count), self.assertRaises(prep.TripletCoverageError):
                prep.calculateCoords(['chair'] * count)

    def test_calculate_coords_single_sampling_pass_for_three_through_nine(self):
        cases = [['window'] * 3]
        cases += [['chair', 'table', 'lamp', 'sofa', 'desk', 'bed', 'curtain', 'blinds', 'window'][:n]
                  for n in range(4, 10)]
        for objects in cases:
            with self.subTest(objects=objects):
                original = objects.copy()
                rows = [list(names) + [1., 1., 1., 90., 90., 90.]
                        for names in itertools.product(sorted(set(objects)), repeat=3)]
                frame = pd.DataFrame(rows, columns=prep.TRIPLET_COLUMNS)
                rng = random.Random(421)
                choices = []
                def choose(options):
                    result = rng.choice(options)
                    choices.append(result)
                    return result
                sizes = {name: [1., 'medium'] for name in prep.suffix_duplicates(objects)}
                with mock.patch.object(prep.pd, 'read_csv', side_effect=[frame, pd.DataFrame()]) as reads, \
                     mock.patch.object(prep, 'loadObjectSizes', return_value=sizes), \
                     mock.patch.object(prep, 'retrieve_realistic_object_color', return_value=(1, 2, 3)), \
                     mock.patch.object(prep.random, 'choice', side_effect=choose), \
                     mock.patch.object(prep.random, 'seed', side_effect=AssertionError('unexpected reseed')), \
                     mock.patch.object(prep.np.random, 'seed', side_effect=AssertionError('unexpected reseed')):
                    result, length, diagnostics = prep.calculateCoords(objects, prev_text_len=42, return_alignment_diagnostics=True)
                self.assertEqual(objects, original)
                self.assertEqual(reads.call_count, 2)
                self.assertEqual(length, 42)
                anchor_a, anchor_b, partners = choices[0]
                base = choices[1] if len(partners) > 1 else partners[0]
                if len(objects) == 9:
                    self.assertEqual((anchor_a, anchor_b, partners, base),
                                     (4, 0, (1, 2, 3, 5, 6, 7, 8), 8))
                names = prep.suffix_duplicates(objects)
                expected = {names[anchor_a]: [0, 0, 0], names[anchor_b]: [1, 0, 0]}
                expected.update({names[i]: [0, 1, 0] for i in partners})
                # Assembly normalization gives coverings the window anchor.
                for name in list(expected):
                    if name.split('.')[0] in {'curtain', 'blinds'}:
                        suffix = name.partition('.')[2]
                        window = 'window' + ('.' + suffix if suffix else '')
                        if window in expected:
                            expected[name] = expected[window].copy()
                        else:
                            expected[window] = expected[name].copy()
                self.assertEqual(set(result), set(expected) | {'CAMERA', 'WALL', 'FLOOR'})
                for name, point in expected.items():
                    np.testing.assert_allclose(result[name]['coords'], point, atol=1e-12)
                    self.assertEqual(result[name]['size'], {'diameter': 1., 'category': 'medium'})
                    self.assertEqual(result[name]['color'], (1, 2, 3))
                np.testing.assert_allclose(result['CAMERA']['coords'], [0, 0, 1], atol=1e-12)
                self.assertEqual(len(diagnostics), len(objects) - 3)
                for diagnostic in diagnostics:
                    self.assertEqual(diagnostic['anchorNames'], [names[anchor_a], names[anchor_b]])
                    self.assertEqual(diagnostic['baseObjectName'], names[base])
                    self.assertAlmostEqual(diagnostic['postRotationCameraPositionResidual'], 0., places=12)
