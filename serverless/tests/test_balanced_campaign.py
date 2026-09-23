"""Independent shards and frozen selection must not change the research model."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from serverless.benchmark.balanced_campaign import living_shards, prepare, jobs_for, task_complete
from serverless.benchmark.run_batch import write_json
from serverless.tests.test_support_correction_audit import fixture


class BalancedCampaignTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[2]/'.codex/tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_all_attempt_seeds_are_disjoint_and_requested_counts_balanced(self):
        shards = living_shards()
        attempts = [row['seed']+i*row['seedStep'] for row in shards for i in range(row['target']*2)]
        self.assertEqual(10000, len(set(attempts)))
        self.assertEqual({3: 1250, 4: 1250, 5: 1250, 6: 1250},
                         {count: sum(row['target'] for row in shards if row['objectCount'] == count) for count in range(3,7)})
        self.assertEqual(5000, sum(row['target'] for row in shards))
        for total, count in [(19,20), (5001,20), (5000,5)]:
            with self.assertRaises(ValueError):
                living_shards(total, count)

    def test_freeze_preserves_source_and_validated_repairs_on_resume(self):
        source, repaired, output = [self.root/name for name in ('source','repaired','balanced')]
        source.mkdir(); repaired.mkdir()
        write_json(source/'run.json', {'targetCompletions': 10000})
        for i in range(22):
            row, derived, _ = fixture()
            row['request']['roomType'] = 'bedroom'
            row['id'] = derived['id'] = 'scene-'+str(i)
            row['attempt'] = derived['attempt'] = i
            derived['request'] = dict(row['request'])
            path = source/f'attempt-{i:05d}.json'
            write_json(path, row)
            derived['supportCorrection']['sourceSha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            if i < 2:
                write_json(repaired/path.name, derived)
        document = prepare(source, repaired, output, 20)
        self.assertEqual(20, len(document['bedroomSelection']))
        self.assertEqual('attempt-00019.json', document['bedroomSelection'][-1]['file'])
        self.assertEqual(22, len(list(source.glob('attempt-*.json'))))
        self.assertEqual(2, len(list((output/'bedroom-repaired').glob('attempt-*.json'))))
        self.assertEqual((source/'attempt-00000.json').read_bytes(), (output/'bedroom-source/attempt-00000.json').read_bytes())
        self.assertEqual(document, prepare(source, repaired, output, 20))
        (output/'bedroom-source/attempt-00000.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            prepare(source, repaired, output, 20)

    def test_worker_commands_and_repair_ranges_cover_the_plan_once(self):
        jobs = jobs_for({'bedroomCount': 5000, 'livingShards': living_shards()}, self.root, Path('/repo'), Path('/blender'), 6)
        repairs = [task for task in jobs if task['kind'] == 'repair']
        self.assertEqual(list(range(5000)), [i for task in repairs for i in range(task['start'], task['start']+task['target'])])
        generations = [task for task in jobs if task['kind'] == 'generation']
        self.assertEqual(20, len(generations))
        for task in generations:
            command = task['command']
            for option, value in [('--room-type','living_room'),('--blender-threads','1'),('--parallel-workers','6'),('--seed-step','19940')]:
                self.assertEqual(value, command[command.index(option)+1])
            self.assertIn('--solid-mesh-overlap', command)
            self.assertIn('--support', command)
            self.assertNotIn('--no-duplicates', command)
            self.assertEqual(250, task['target'])

    def test_attempt_limit_is_not_mistaken_for_completed_work(self):
        directory = self.root/'living-00'
        directory.mkdir()
        task = {'id': 'living-00', 'kind': 'generation', 'target': 2}
        write_json(directory/'attempt-00000.json', {'status': 'complete'})
        write_json(directory/'attempt-00001.json', {'status': 'failed'})
        self.assertFalse(task_complete(task, self.root))
        write_json(directory/'attempt-00002.json', {'status': 'complete'})
        self.assertTrue(task_complete(task, self.root))


if __name__ == '__main__':
    unittest.main()
