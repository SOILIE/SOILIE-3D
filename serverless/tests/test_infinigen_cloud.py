import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from serverless.infinigen_cloud.handler import request_parameters, run_construction
from serverless.infinigen_cloud.campaign import requests, reserve_usd, CAP_USD


class CloudPilotTests(unittest.TestCase):
    def test_balanced_schedule_and_unique_seeds(self):
        jobs = requests()
        self.assertEqual(len(jobs), 80)
        self.assertEqual(len({request_parameters(job)[3] for job in jobs}), 80)
        self.assertEqual(len({(job['roomType'], job['condition']) for job in jobs[:4]}), 4)
        for room in ('bedroom', 'living_room'):
            for condition in ('room-scale', 'controlled'):
                self.assertEqual(sum(job['roomType'] == room and job['condition'] == condition for job in jobs), 20)
        counts = [request_parameters(job)[4] for job in jobs if job['roomType'] == 'bedroom' and job['condition'] == 'controlled']
        self.assertEqual(counts, [3, 4, 5, 6] * 5)

    def test_invalid_requests_cannot_expand_the_paid_schedule(self):
        base = {'condition': 'controlled', 'roomType': 'bedroom', 'index': 0}
        for change in ({'index': 20}, {'index': -1}, {'index': True}, {'roomType': 'kitchen'}, {'condition': 'new'}, {'seed': 1}):
            with self.assertRaises(ValueError):
                request_parameters({**base, **change})

    def test_full_timeout_reservations_fit_budget(self):
        rates = {'computeUsdPerGbSecond': .0000166667, 'storageUsdPerGbSecond': .000000034, 'requestUsd': .0000002}
        self.assertLessEqual(80 * reserve_usd(rates), CAP_USD)
        self.assertGreater(80 * reserve_usd(rates), 7)

    def test_timeout_cleans_all_children_without_retry(self):
        process = Mock(pid=42)
        process.wait.side_effect = [subprocess.TimeoutExpired('blender', 760), -9]
        with patch('subprocess.Popen', return_value=process) as launch, patch('os.killpg', create=True) as kill, patch('signal.SIGKILL', 9, create=True):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_construction(['blender'], '/tmp', {}, None)
            self.assertEqual(launch.call_count, 1)
            self.assertTrue(launch.call_args.kwargs['start_new_session'])
            kill.assert_called_once()
            self.assertEqual(process.wait.call_count, 2)

    def test_template_has_no_public_endpoint_or_unscoped_writes(self):
        template = json.loads((Path(__file__).parents[1] / 'infinigen_cloud/template.json').read_text())
        resources = template['Resources']
        self.assertEqual(set(resources), {'Function', 'Role', 'LogGroup'})
        self.assertEqual(resources['Function']['Properties']['Timeout'], 900)
        self.assertEqual(resources['Function']['Properties']['ReservedConcurrentExecutions'], 20)
        statement = resources['Role']['Properties']['Policies'][0]['PolicyDocument']['Statement'][0]
        self.assertEqual(statement['Resource']['Fn::Sub'], 'arn:aws:s3:::${OutputBucket}/${OutputPrefix}/*')
        self.assertNotIn('s3:DeleteObject', statement['Action'])


if __name__ == '__main__':
    unittest.main()
