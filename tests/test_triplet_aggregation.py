import csv
import json
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest import mock

from modules import prepare_data as prep
from modules import relation_manifest as manifest
from modules.object_identity import encode_collector_object_label
from modules.triplet_schema import TRIPLET_COLUMNS


WORK = Path(__file__).resolve().parents[1] / '.codex/tests/core-geometry'


class TripletAggregationTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=WORK)
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'data'
        self.data.mkdir()
        self.source = self.data / 'triplets.csv'
        self.output = self.data / 'average.csv'
        self.pickled = self.data / 'average.pickle'

    def write_rows(self, rows):
        with self.source.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(TRIPLET_COLUMNS)
            writer.writerows(rows)

    def test_averaging_preserves_ordered_groups_and_weights_every_observation(self):
        self.write_rows([
            ['chair', 'table', 'lamp', 1, 2, 3, 10, 20, 30],
            ['table', 'chair', 'lamp', 9, 8, 7, 60, 70, 80],
            ['chair', 'table', 'lamp', 3, 4, 5, 30, 40, 50],
            ['chair', 'table', 'lamp', 5, 6, 7, 50, 60, 70],
        ])
        prep.averageTriplets(self.source, self.output, self.pickled)
        with self.output.open(newline='', encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, list(TRIPLET_COLUMNS) + ['count'])
            rows = list(reader)
        self.assertEqual([(r['objectA'], r['objectB'], r['objectC']) for r in rows], [('chair', 'table', 'lamp'), ('table', 'chair', 'lamp')])
        fields = ('distanceAB', 'distanceAC', 'distanceAO', 'angleBAC', 'angleOAB', 'angleOAC')
        self.assertEqual([float(rows[0][field]) for field in fields], [3, 4, 5, 30, 40, 50])
        self.assertEqual([float(rows[1][field]) for field in fields], [9, 8, 7, 60, 70, 80])
        self.assertEqual([int(row['count']) for row in rows], [3, 1])
        with self.pickled.open('rb') as handle:
            result = pickle.load(handle)
        self.assertEqual(result['count'], [3, 1])
        self.assertEqual(result['angleOAC'], [50, 80])

    def test_invalid_input_and_promotion_failure_preserve_both_previous_outputs(self):
        self.output.write_bytes(b'prior csv')
        self.pickled.write_bytes(b'prior pickle')
        for value in ('nan', '-1'):
            self.write_rows([['a', 'b', 'c', value, 2, 3, 10, 20, 30]])
            with self.assertRaises(prep.TripletGeometryError):
                prep.averageTriplets(self.source, self.output, self.pickled)
            self.assertEqual(self.output.read_bytes(), b'prior csv')
            self.assertEqual(self.pickled.read_bytes(), b'prior pickle')
        self.write_rows([['a', 'b', 'c', 1, 2, 3, 10, 20, 30]])
        # Simulate a failed replacement of the second candidate after the first
        # has already been installed; promotion must roll both outputs back.
        original_replace = prep.os.replace
        def fail_second(src, dst):
            if Path(dst) == self.pickled and 'triplets-avg.' in Path(src).name:
                raise OSError('injected replacement failure')
            return original_replace(src, dst)
        with mock.patch.object(prep.os, 'replace', side_effect=fail_second), self.assertRaises(OSError):
            prep.averageTriplets(self.source, self.output, self.pickled)
        self.assertEqual(self.output.read_bytes(), b'prior csv')
        self.assertEqual(self.pickled.read_bytes(), b'prior pickle')

    def test_incomplete_manifest_blocks_gather_without_touching_outputs(self):
        path = self.data / 'relation-inputs.json'
        manifest.begin_relation_run(path)
        self.output.write_bytes(b'prior csv')
        self.pickled.write_bytes(b'prior receipt')
        marker = path.read_bytes()
        with self.assertRaises(manifest.RelationManifestError):
            prep.gatherTriplets(path, self.output, self.pickled, data_root=self.data)
        self.assertEqual(path.read_bytes(), marker)
        self.assertEqual(self.output.read_bytes(), b'prior csv')
        self.assertEqual(self.pickled.read_bytes(), b'prior receipt')

    def test_gather_preserves_distinct_frame_measurements_and_seals(self):
        project = self.data.parent
        annotation = project / 'json' / 'princeton' / 'scene.json'
        annotation.parent.mkdir(parents=True)
        annotation.write_text(json.dumps({'name': 'scene', 'extrinsics': 'poses.txt',
            'fileList': ['1-100.jpg', '2-200.jpg'],
            'frames': [{'polygon': [1]}, {'polygon': [1]}]}), encoding='utf-8')
        folder = self.data / 'princeton' / 'scene'
        folder.mkdir(parents=True)
        geometry = {'depthEncoding': 'sun3d_circular_uint16_mm',
                    'pairingStrategy': 'sun3d_nearest_timestamp',
                    'poseIndexStrategy': 'one_based_image_frame', 'pixelOrigin': 1}
        labels = [encode_collector_object_label('chair', 'left'),
                  encode_collector_object_label('chair', 'right'),
                  encode_collector_object_label('table', 'one')]
        records = []
        for index, distance in enumerate((3, 7)):
            number = index + 1
            relation = folder / f'{number}.csv'
            with relation.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow(TRIPLET_COLUMNS)
                writer.writerow(labels + [1, 2, distance, 90, 60, 45])
            records.append(manifest.build_relation_frame_record(
                dataset='princeton', scene='scene', annotation_path='json/princeton/scene.json',
                annotation_sha256=manifest.sha256_file(annotation), source_geometry=geometry,
                json_frame_index=index, image={'name': f'{number}-{number * 100}.jpg', 'frameId': number, 'timestamp': number * 100},
                depth={'name': f'{number}-{number * 100}.png', 'frameId': number, 'timestamp': number * 100},
                extrinsics_file='poses.txt', pose_index=index,
                camera_to_world=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, distance]],
                relation_path=f'princeton/scene/{number}.csv', relation_sha256=manifest.sha256_file(relation),
                relation_row_count_value=1))
        path = self.data / 'relation-inputs.json'
        collection = manifest.begin_relation_run(path)
        manifest.complete_relation_run(path, collection_id=collection, frames=records, data_root=self.data)
        receipt = prep.gatherTriplets(path, self.output, self.pickled, data_root=self.data)
        with self.output.open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([(r['objectA'], r['objectB'], r['objectC'], float(r['distanceAO'])) for r in rows],
                         [('chair', 'chair', 'table', 3), ('chair', 'chair', 'table', 7)])
        self.assertEqual(receipt['frames'], records)
        self.assertEqual(receipt['output']['sha256'], manifest.sha256_file(self.output))
        old_output, old_receipt = self.output.read_bytes(), self.pickled.read_bytes()
        (folder / '2.csv').write_text('tampered', encoding='utf-8')
        with self.assertRaises(manifest.RelationManifestError):
            prep.gatherTriplets(path, self.output, self.pickled, data_root=self.data)
        self.assertEqual(self.output.read_bytes(), old_output)
        self.assertEqual(self.pickled.read_bytes(), old_receipt)
