import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from serverless.infinigen_cloud.handler import request_parameters, run_construction
from serverless.infinigen_cloud.campaign import requests, reserve_usd, CAP_USD, accounted_cost
from serverless.infinigen_cloud.publication import measured_rows, completion_coverage, index_outputs
from serverless.benchmark.infinigen_task import COMMIT
from serverless.infinigen_cloud.receipts import attach_billing, valid_result
from botocore.exceptions import ClientError


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

    def test_failed_initialization_and_prior_pilots_retain_budget(self):
        state = {'priorAccountedUsd': .2, 'entries': {
            'a': {'status': 'failed', 'usagePricedUsd': .00001, 'reservedUsd': .1},
            'b': {'status': 'complete', 'usagePricedUsd': .02, 'reservedUsd': .1},
            'c': {'status': 'reserved', 'reservedUsd': .1},
        }}
        self.assertAlmostEqual(accounted_cost(state), .42)

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
        self.assertEqual(set(resources), {'Function', 'Role', 'LogGroup', 'InvocationPolicy'})
        self.assertEqual(resources['InvocationPolicy']['Properties']['MaximumRetryAttempts'], 0)
        self.assertEqual(resources['Function']['Properties']['Timeout'], 900)
        self.assertEqual(resources['Function']['Properties']['ReservedConcurrentExecutions'], 20)
        statement = resources['Role']['Properties']['Policies'][0]['PolicyDocument']['Statement'][0]
        self.assertEqual(statement['Resource']['Fn::Sub'], 'arn:aws:s3:::${OutputBucket}/${OutputPrefix}/*')
        self.assertNotIn('s3:DeleteObject', statement['Action'])

    def test_billing_does_not_cross_streams_or_invocations(self):
        rows = {'a': {}, 'b': {}}
        events = [
            {'logStreamName': 'one', 'timestamp': 1, 'message': '{"stage":"complete","id":"a"}'},
            {'logStreamName': 'two', 'timestamp': 2, 'message': '{"stage":"complete","id":"b"}'},
            {'logStreamName': 'one', 'timestamp': 3, 'message': 'REPORT RequestId: a\tBilled Duration: 2000 ms\tMax Memory Used: 500 MB'},
            {'logStreamName': 'two', 'timestamp': 4, 'message': 'REPORT RequestId: b\tBilled Duration: 4000 ms\tMax Memory Used: 600 MB'},
        ]
        attach_billing(rows, events, {'computeUsdPerGbSecond': .1, 'storageUsdPerGbSecond': 0, 'requestUsd': 0})
        self.assertAlmostEqual(rows['a']['usagePricedUsd'], 1.2)
        self.assertAlmostEqual(rows['b']['usagePricedUsd'], 2.4)

    def test_duplicate_delivery_cannot_launch_blender(self):
        from serverless.infinigen_cloud.handler import lambda_handler
        client = Mock()
        client.put_object.side_effect = ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        with patch('boto3.client', return_value=client), patch.dict('os.environ', {'OUTPUT_PREFIX': 'test', 'OUTPUT_BUCKET': 'test'}), patch('subprocess.Popen') as launch:
            result = lambda_handler({'condition': 'controlled', 'roomType': 'bedroom', 'index': 0}, None)
        self.assertEqual(result['status'], 'already_claimed')
        launch.assert_not_called()

    def test_data_index_exposes_only_completed_scene_artifacts(self):
        identity = 'controlled-bedroom-00'
        folder = 'files/outputs/runtime-pilot-2026-09-25/soilie-infinigen-timing-test/' + identity
        state = {'entries': {'a': {'status': 'complete', 'result': {'id': identity, 'artifacts': [
            {'file': name, 'key': folder + '/' + name, 'bytes': 42}
            for name in ('construction.log.gz', 'scene.blend.gz', 'solve_state.json.gz')]}},
            'b': {'status': 'failed'}}}
        client = Mock()
        client.head_object.return_value = {'ContentLength': 42}
        with patch('serverless.infinigen_cloud.publication.measured_rows'), patch('serverless.infinigen_cloud.publication.completed_campaign', return_value=state), patch('serverless.benchmark.archive.merge_index', return_value=3) as index:
            self.assertEqual(index_outputs(Path('fixture'), client)['sceneFiles'], 3)
            self.assertEqual(set(index.call_args.args[2]), {folder + '/' + name for name in ('scene.blend.gz', 'solve_state.json.gz', 'result.json')})
            client.head_object.return_value = {'ContentLength': 1}
            with self.assertRaises(ValueError):
                index_outputs(Path('fixture'), client)

    def test_public_measurements_require_complete_cohort_and_strip_private_fields(self):
        state = {'status': 'complete', 'cleanupComplete': True, 'name': 'private-resource', 'entries': {}}
        for event in requests():
            room, condition, index, seed, count, profile = request_parameters(event)
            identity = f'{condition}-{room}-{index:02d}'
            state['entries'][identity] = {'request': event, 'status': 'complete', 'logTail': 'private operational log',
                'result': {'id': identity, 'status': 'complete', 'sourceCommit': COMMIT, 'seed': seed,
                           'profile': profile, 'memoryMb': 6144, 'blenderThreads': 4, 'objectCount': count,
                           'generationSeconds': 30.0, 'privateExtra': 'never publish'}}
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            path = Path(temporary) / 'campaign.json'
            path.write_text(json.dumps(state))
            rows = measured_rows(path)
            self.assertEqual(len(rows), 80)
            self.assertNotIn('private', json.dumps(rows))
            first = next(iter(state['entries'].values()))
            first['status'] = first['result']['status'] = 'failed'
            first['result']['errorCode'] = 'CONSTRUCTION_FAILED'
            state['status'] = 'needs_review'
            path.write_text(json.dumps(state))
            self.assertEqual(len(measured_rows(path)), 79)
            self.assertEqual(completion_coverage(path)['bedroom']['infinigen'], {'attempted': 20, 'completed': 19, 'notCompleted': 1})
            first['status'] = 'uncertain'
            path.write_text(json.dumps(state))
            with self.assertRaises(ValueError):
                measured_rows(path)
            state['entries'].pop(next(iter(state['entries'])))
            path.write_text(json.dumps(state))
            with self.assertRaises(ValueError):
                measured_rows(path)


if __name__ == '__main__':
    unittest.main()
