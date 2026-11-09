"""Contracts for continuing a frozen authoritative campaign without changing its cohort."""
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from serverless.cloud_benchmark import authoritative as campaign


class CampaignInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.local = campaign.allocation(platforms=('local',))
        cls.mixed = campaign.allocation()

    def test_every_frozen_request_has_expected_id_count_and_original_seed(self):
        counts = (3, 4, 5, 6, 7, 8)
        for platforms, tasks in ((('local',), self.local), (('local', 'lambda'), self.mixed)):
            self.assertEqual(5000 * len(platforms), len(tasks))
            self.assertEqual(len(tasks), len({task['id'] for task in tasks}))
            self.assertEqual(len(tasks), len({task['request']['seed'] for task in tasks}))
            for ordinal, task in enumerate(tasks):
                index, offset = divmod(ordinal, 2 * len(platforms))
                platform = platforms[offset // 2]
                room = ('bedroom', 'living_room')[offset % 2]
                self.assertEqual(f'{platform}-{room}-{index:04d}', task['id'])
                self.assertEqual({'mode': 'room_type', 'roomType': room,
                                  'objectCount': counts[index % 6],
                                  'seed': 2_000_000_000 + 997 * ordinal,
                                  'allowDuplicates': True, 'sameObjectsAcrossScenes': True}, task['request'])
            for platform in platforms:
                for room in ('bedroom', 'living_room'):
                    histogram = Counter(task['request']['objectCount'] for task in tasks
                                        if task['platform'] == platform and task['request']['roomType'] == room)
                    self.assertEqual({3:417, 4:417, 5:417, 6:417, 7:416, 8:416}, histogram)

    def test_mixed_schedule_is_not_a_local_only_continuation(self):
        local_seeds = {task['request']['seed'] for task in self.local}
        mixed_lambda = {task['request']['seed'] for task in self.mixed if task['platform'] == 'lambda'}
        self.assertEqual(2500, len(local_seeds & mixed_lambda))
        mixed_local = {task['id']: task for task in self.mixed if task['platform'] == 'local'}
        self.assertEqual(4998, sum(task != mixed_local[task['id']] for task in self.local))

    def test_scope_checksum_and_exact_schedule_are_enforced(self):
        base = Path(__file__).resolve().parents[1] / '.codex/tests/campaign-invariants'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            plan = {'schemaVersion': 1, 'platforms': ['local'], 'phase': 'authoritative',
                    'maxObjectCount': 8, 'requests': self.local,
                    'configuration': {'publicRunNamespace': 'plan-sha256-v1'},
                    'cloudParity': {'status': 'deferred'}}
            plan['planSha256'] = campaign.digest(plan)
            path = root / 'plan.json'
            path.write_text(json.dumps(plan), encoding='utf-8')
            self.assertEqual(plan, campaign.load_plan(root, verify_runtime=False))
            with self.assertRaisesRegex(ValueError, 'not part'):
                campaign.require_platform(plan, 'lambda')
            altered = dict(plan, platforms=['local','lambda'])
            altered['planSha256'] = campaign.digest({k:v for k,v in altered.items() if k != 'planSha256'})
            path.write_text(json.dumps(altered), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'fixed allocation'):
                campaign.load_plan(root, verify_runtime=False)
            plan['requests'] = list(plan['requests'])
            plan['requests'][0] = {**plan['requests'][0], 'request': {**plan['requests'][0]['request'], 'seed': 42}}
            plan['planSha256'] = campaign.digest({k:v for k,v in plan.items() if k != 'planSha256'})
            path.write_text(json.dumps(plan), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'fixed allocation'):
                campaign.load_plan(root, verify_runtime=False)

    def test_local_never_retries_failed_or_uncertain_originals(self):
        plan = {'platforms': ['local'], 'requests': self.local}
        task = self.local[0]
        for status in ('unknown', 'dispatched'):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'uncertain'):
                campaign.pending_tasks(plan, 'local', [{'requestId': task['id'], 'status': status}])
        failed = {'requestId': task['id'], 'status': 'failed', 'request': task['request']}
        with self.assertRaises(ValueError): campaign.pending_tasks(plan, 'local', [failed])
        with self.assertRaisesRegex(ValueError, 'never retries'):
            campaign.pending_tasks(plan, 'local', [failed], [task['id']])
        eligible = [self.local[2]['id'], self.local[4]['id']]
        self.assertEqual(eligible, [t['id'] for t in campaign.eligible_pending_tasks(plan, 'local', [], eligible)])
        with self.assertRaises(ValueError): campaign.eligible_pending_tasks(plan, 'local', [], eligible[::-1])

    def test_reservations_and_settlements_accumulate_across_plans(self):
        budget = {'attempts': [
            {'planSha256':'pilot', 'reservedUSD': .08, 'costUSD': .03},
            {'planSha256':'previous', 'reservedUSD': .08, 'costUSD': .04},
            {'planSha256':'current', 'reservedUSD': .08, 'status':'unknown'},
            {'planSha256':'current', 'reservedUSD': .08, 'status':'dispatched'},
        ]}
        self.assertAlmostEqual(.23, campaign.spent(budget))
        budget['attempts'][2]['costUSD'] = .05
        self.assertAlmostEqual(.20, campaign.spent(budget))
