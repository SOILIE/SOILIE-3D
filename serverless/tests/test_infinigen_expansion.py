import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from serverless.benchmark.expand_infinigen import (compress_blend, wait_for_disk, matching_capacity,
                                                  bedroom_schedule, attempt_task, ordered_attempts, run_attempt)


class InfinigenExpansionTests(unittest.TestCase):
    def test_parallel_attempts_commit_in_seed_order_not_finish_order(self):
        barrier = threading.Barrier(3)
        completed = threading.Event()
        def fake(room, index, args):
            barrier.wait(timeout=5)
            if index == 100:
                self.assertTrue(completed.wait(timeout=5))
            if index == 102:
                completed.set()
            return index
        with patch('serverless.benchmark.expand_infinigen.run_attempt', side_effect=fake):
            self.assertEqual([100, 101, 102], list(ordered_attempts('bedroom', 100, 3, None)))

    def test_completed_attempt_receipt_resumes_without_regeneration(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            folder = root / 'bedroom/attempt-100'
            folder.mkdir(parents=True)
            entry = {'seed': 4100, 'status': 'complete'}
            (folder / 'expansion-entry.json').write_text(json.dumps(entry))
            with patch('serverless.benchmark.expand_infinigen.run_command') as native:
                self.assertEqual(entry, run_attempt('bedroom', 100, SimpleNamespace(output=root)))
                native.assert_not_called()

    def test_capacity_counts_distinct_unused_inventory_peers_not_generations(self):
        def scene(identity, labels, room='bedroom'):
            return {'scene': {'id': identity, 'model': 'soilie', 'roomType': room,
                             'objects': [{'label': label} for label in labels]}}
        labels = ['bed', 'chair', 'lamp', 'desk', 'rug', 'cabinet']
        pool = [scene('frozen', labels), scene('available', labels), scene('available', labels),
                scene('two-beds', ['bed', 'bed', 'lamp', 'desk', 'rug', 'cabinet']),
                scene('architecture-not-furniture', ['bed', 'window', 'lamp', 'desk', 'rug', 'cabinet']),
                scene('other-room', labels, 'living_room')]
        protocol = {'cases': [{'id': 'case', 'comparisonCondition': 'infinigen_controlled'}],
                    'stimulusEvidence': [{'caseId': 'case', 'soilieScene': 'frozen', 'matchingStratum': ['bedroom']}]}
        report = matching_capacity(pool, [protocol], 'bedroom')
        self.assertEqual(1, report['remainingInventoryPeers'])
        self.assertEqual(2, report['maximumTotalPairs'])
        self.assertEqual(1, report['frozenPairs'])

        pool.extend([scene('three', ['bed', 'lamp', 'desk']),
                     scene('four', ['bed', 'lamp', 'desk', 'chair'])])
        varied = matching_capacity(pool, [protocol], 'bedroom', (3, 4, 5, 6))
        self.assertEqual({'3': 1, '4': 1, '5': 1, '6': 1}, varied['availableByCount'])
        self.assertEqual(5, varied['maximumTotalPairs'])

    def test_count_schedule_is_frozen_and_preserves_started_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'bedroom/attempt-001').mkdir(parents=True)
            (root / 'campaign.json').write_text('{}')
            (root / 'bedroom/checkpoint.json').write_text('{"attempts":[{}]}')
            (root / 'bedroom/attempt-001/run.json').write_text('{}')
            schedule = bedroom_schedule(root, True, [], [])
            self.assertEqual(2, schedule['startAttempt'])
            self.assertEqual(('controlled-six-fast', 6), attempt_task('bedroom', 1, schedule))
            self.assertEqual([3, 4, 5, 6, 3], [attempt_task('bedroom', n, schedule)[1] for n in range(2, 7)])
            self.assertEqual(('controlled-six-fast', 6), attempt_task('living_room', 10, schedule))
            # Advancing a checkpoint must not move the schedule or recycle a seed.
            (root / 'bedroom/checkpoint.json').write_text('{"attempts":[{},{},{}]}')
            self.assertEqual(schedule, bedroom_schedule(root, True, [], []))
            with self.assertRaises(ValueError): bedroom_schedule(root, False, [], [])
            (root / 'campaign.json').write_text('{"changed":true}')
            with self.assertRaises(ValueError): bedroom_schedule(root, True, [], [])

    def test_disk_wait_needs_recovery_margin_and_does_not_terminate_campaign(self):
        with patch('serverless.benchmark.expand_infinigen.shutil.disk_usage',
                   side_effect=[SimpleNamespace(free=n * 1024**3) for n in (14, 16, 19, 20)]), \
                patch('serverless.benchmark.expand_infinigen.time.sleep') as sleep:
            wait_for_disk(Path('.'))
            self.assertEqual(2, sleep.call_count)
        with patch('serverless.benchmark.expand_infinigen.shutil.disk_usage',
                   return_value=SimpleNamespace(free=25 * 1024**3)), \
                patch('serverless.benchmark.expand_infinigen.time.sleep') as sleep:
            wait_for_disk(Path('.'))
            sleep.assert_not_called()

    def test_lossless_compression_is_recoverable_and_resumable(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            source = root / 'scene.blend'
            content = b'Blender evidence bytes' * 1000
            source.write_bytes(content)
            result = compress_blend(source, root)
            self.assertFalse(source.exists())
            self.assertEqual(hashlib.sha256(content).hexdigest(), result['originalSha256'])
            with gzip.open(source.with_suffix('.blend.gz'), 'rb') as stream:
                self.assertEqual(content, stream.read())
            self.assertEqual(result, compress_blend(source, root))
            self.assertLess(result['archiveBytes'], result['originalBytes'])
            with self.assertRaises(ValueError): compress_blend(root / 'other.blend', root)
            with self.assertRaises(ValueError): compress_blend(root.parent / 'scene.blend', root)


if __name__ == '__main__': unittest.main()
