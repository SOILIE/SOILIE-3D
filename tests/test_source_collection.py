import csv
import itertools
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest

import numpy as np

try:
    import plyfile  # noqa: F401
except ModuleNotFoundError:
    stub = types.ModuleType('plyfile')
    stub.PlyData = stub.PlyElement = object
    sys.modules['plyfile'] = stub

from modules.collect_data import Frame
from modules.object_identity import decode_collector_object_label
from modules.triplet_schema import TRIPLET_COLUMNS


WORK = Path(__file__).resolve().parents[1] / '.codex/tests/core-geometry'


class SourceCollectionTests(unittest.TestCase):
    def test_all_ordered_permutations_use_world_camera_and_distinct_instances(self):
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WORK) as temporary:
            folder = Path(temporary)
            centroid_file = folder / 'centroids.csv'
            centroid_file.write_text('object,X,Y,Z\n', encoding='utf-8')
            frame = Frame('sample', 1, 1)
            identities = [('chair', 'left, 1'), ('chair', 'right_2'), ('table', '3')]
            points = [(1, 2, 3), (4, 2, 3), (1, 6, 3)]
            frame.centroids = {repr(list(key)): np.array(point, dtype=float) for key, point in zip(identities, points)}
            frame.extrinsics = np.array([[0, -1, 0, 1], [1, 0, 0, 2], [0, 0, 1, 8]], dtype=float)
            frame.getAngleDistCombos(centroid_file).export(folder)
            with (folder / 'sample.csv').open(newline='', encoding='utf-8') as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, list(TRIPLET_COLUMNS))
                rows = list(reader)

        expected_orders = list(itertools.permutations(range(3)))
        self.assertEqual(len(rows), len(expected_orders))
        camera = np.array([1, 2, 8])
        for row, order in zip(rows, expected_orders):
            self.assertEqual([decode_collector_object_label(row['object' + name]) for name in 'ABC'], [identities[i] for i in order])
            a, b, c = [np.array(points[i]) for i in order]
            ba, ca, oa = b - a, c - a, camera - a
            angle = lambda x, y: math.degrees(math.acos(float(np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y)))))
            for field, expected in [('distanceAB', np.linalg.norm(ba)), ('distanceAC', np.linalg.norm(ca)),
                                    ('distanceAO', np.linalg.norm(oa)), ('angleBAC', angle(ba, ca)),
                                    ('angleOAB', angle(oa, ba)), ('angleOAC', angle(oa, ca))]:
                self.assertAlmostEqual(float(row[field]), expected, places=10, msg=field)

    def test_zero_length_relation_rejected(self):
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WORK) as temporary:
            path = Path(temporary) / 'centroids.csv'
            path.write_text('object,X,Y,Z\n', encoding='utf-8')
            frame = Frame('bad', 1, 1)
            frame.centroids = {"['a', '1']": [0, 0, 0], "['b', '1']": [0, 0, 0], "['c', '1']": [1, 0, 0]}
            frame.extrinsics[:, 3] = [0, 1, 0]
            with self.assertRaisesRegex(ValueError, 'zero-length'):
                frame.getAngleDistCombos(path)
