from collections import Counter
from copy import deepcopy
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.cloud_benchmark.authoritative import (
    allocation, budget_document, campaign_status, digest, export_evidence, file_digest,
    finish_cloud, invocation_cost, load_plan, next_attempt, pending_tasks, runtime_snapshot,
    generation_date, passed_gate, recovered_evidence, SOURCE_CHECKS, spent, validate_artifact, validate_rates,
    verify_completed, verify_lambda, versioned_function,
)
from serverless.cloud_benchmark.progress import render
from serverless.cloud_benchmark.pilot import invoke_and_download


RATES = {'schemaVersion': 1, 'currency': 'USD', 'region': 'ca-central-1',
         'architecture': 'x86_64', 'sourceUrl': 'https://aws.amazon.com/lambda/pricing/',
         'retrievedAt': '2026-10-22T00:00:00Z', 'computeGBSecondUSD': .0000166667,
         'ephemeralGBSecondUSD': .0000000309, 'requestUSD': .0000002}


class AuthoritativeCampaignTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parents[2] / '.codex/fresh-campaign-20261022/tests'
        self.base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.base)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def plan(self, phase='pilot'):
        return {'phase': phase, 'planSha256': 'frozen', 'maxObjectCount': 8,
                'configuration': {'budgetRoot': str(self.root / 'shared-budget'),
                                  'rateCard': deepcopy(RATES), 'region': 'ca-central-1',
                                  'runtimeSnapshot': {'version': '4.0.4', 'sourceCommit': 'source',
                                                      'files': {'modules/render.py': 'render'}}},
                'gates': {}, 'requests': allocation(8, phase),
                'timingPolicy': 'generation separate from observation'}

    def artifact(self, task):
        log = b'original Blender diagnostics\n'
        return {'request': task['request'], 'status': 'complete',
                'implementation': {'modelVersion': '4.0.4', 'sourceCommit': 'source', 'renderSha256': 'render'},
                'selection': ['chair'] * task['request']['objectCount'],
                'wallSeconds': 11, 'observationSeconds': 1, 'generationSeconds': 10,
                'executionLog': {'encoding': 'gzip+base64', 'bytes': len(log),
                                 'sha256': hashlib.sha256(log).hexdigest(),
                                 'data': base64.b64encode(gzip.compress(log, mtime=0)).decode()},
                'stages': {'final': {'objects': [{'kind': 'furniture'}] * 12}}}

    def test_fresh_four_cells_have_exact_histogram_and_all_seeds_are_disjoint(self):
        final = allocation()
        pilot = allocation(phase='pilot')
        self.assertEqual(10_000, len(final))
        self.assertEqual(240, len(pilot))
        expected = {3: 417, 4: 417, 5: 417, 6: 417, 7: 416, 8: 416}
        for platform_name in ('local', 'lambda'):
            for room in ('bedroom', 'living_room'):
                selected = [row for row in final if row['platform'] == platform_name
                            and row['request']['roomType'] == room]
                self.assertEqual(expected, dict(Counter(row['request']['objectCount'] for row in selected)))
        self.assertEqual(len(final), len({row['request']['seed'] for row in final}))
        self.assertFalse({row['request']['seed'] for row in final} & {row['request']['seed'] for row in pilot})
        self.assertEqual(final, allocation())
        self.assertTrue(all(row['request']['allowDuplicates'] for row in final))
        self.assertTrue(all('source' not in row for row in final))

    def test_five_count_fallback_is_chosen_before_freeze(self):
        rows = allocation(7)
        self.assertEqual(10_000, len(rows))
        self.assertEqual({count: 500 for count in range(3, 8)},
                         dict(Counter(row['request']['objectCount'] for row in rows
                                      if row['platform'] == 'local' and row['request']['roomType'] == 'bedroom')))
        self.assertEqual(200, len(allocation(7, 'pilot')))
        with self.assertRaises(ValueError):
            allocation(6)

    def test_reservations_use_frozen_regional_compute_request_and_extra_storage(self):
        rates = validate_rates(deepcopy(RATES), 'ca-central-1')
        self.assertAlmostEqual(30 * (4 * .0000166667 + .5 * .0000000309) + .0000002,
                               invocation_cost(30, rates))
        self.assertGreater(invocation_cost(915, rates), .061)
        other = deepcopy(rates)
        other['computeGBSecondUSD'] *= 2
        self.assertGreater(invocation_cost(30, other), invocation_cost(30, rates))
        for value in (float('nan'), -1, None, True):
            with self.assertRaises(ValueError):
                invocation_cost(value, rates)
        for field, value in (('region', 'other'), ('requestUSD', 0), ('sourceUrl', 'not a source')):
            bad = {**rates, field: value}
            with self.assertRaises(ValueError):
                validate_rates(bad, 'ca-central-1')

    def test_pilots_and_retries_cannot_reset_shared_budget(self):
        plan = self.plan()
        budget = budget_document(plan)
        budget['attempts'] = [{'planSha256': 'old-pilot', 'status': 'failed', 'costUSD': 2, 'reservedUSD': 3},
                              {'planSha256': 'frozen', 'status': 'unknown', 'reservedUSD': .1}]
        directory = self.root / 'shared-budget'
        directory.mkdir()
        (directory / 'budget.json').write_text(json.dumps(budget))
        self.assertAlmostEqual(2.1, spent(budget_document(plan)))
        final = self.plan('authoritative')
        self.assertAlmostEqual(2.1, spent(budget_document(final)))
        final['configuration']['rateCard']['requestUSD'] *= 2
        with self.assertRaisesRegex(ValueError, 'never reset'):
            budget_document(final)

    def test_failed_request_requires_explicit_same_seed_retry_and_unknown_blocks(self):
        plan = self.plan()
        task = next(row for row in plan['requests'] if row['platform'] == 'local')
        failed = {'requestId': task['id'], 'status': 'failed', 'request': task['request']}
        with self.assertRaisesRegex(ValueError, 'Explicitly name'):
            pending_tasks(plan, 'local', [failed])
        tasks = pending_tasks(plan, 'local', [failed], [task['id']])
        self.assertEqual(task, tasks[0])
        self.assertEqual(task['request']['seed'], tasks[0]['request']['seed'])
        with self.assertRaisesRegex(ValueError, 'Reconcile every'):
            pending_tasks(plan, 'local', [{**failed, 'status': 'unknown'}], [task['id']])
        with self.assertRaises(ValueError):
            pending_tasks(plan, 'local', [], ['invented'])

    def test_attempt_directories_never_overwrite_and_completion_hashes_are_verified(self):
        plan = self.plan()
        task = next(row for row in plan['requests'] if row['platform'] == 'local')
        attempt = next_attempt(self.root, plan, task, [])
        folder = self.root / attempt['directory']
        path = folder / 'attempt-00000.json'
        path.write_text(json.dumps(self.artifact(task)))
        attempt.update(status='complete', artifact=path.relative_to(self.root).as_posix(), sha256=file_digest(path))
        self.assertEqual({task['id']}, verify_completed(self.root, plan, 'local', [attempt]))
        with self.assertRaisesRegex(ValueError, 'Unledgered'):
            next_attempt(self.root, plan, task, [])
        second = next_attempt(self.root, plan, task, [attempt])
        self.assertEqual(2, second['attempt'])
        self.assertTrue(path.exists())
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            verify_completed(self.root, plan, 'local', [attempt])

    def test_requested_count_is_distinct_from_normalized_geometry_count(self):
        plan = self.plan()
        task = plan['requests'][0]
        row = self.artifact(task)
        # A window assembly may produce more meshes than requested instances.
        self.assertEqual(row, validate_artifact(row, task, plan))
        row['selection'].append('chair')
        with self.assertRaisesRegex(ValueError, 'exact requested'):
            validate_artifact(row, task, plan)
        row = self.artifact(task)
        row['generationSeconds'] = 11
        with self.assertRaisesRegex(ValueError, 'timers'):
            validate_artifact(row, task, plan)

    def test_bad_cloud_geometry_still_preserves_billed_cost_and_failed_service_calls_are_terminal(self):
        plan = self.plan()
        task = next(row for row in plan['requests'] if row['platform'] == 'lambda')
        attempt = next_attempt(self.root, plan, task, [])
        folder = self.root / attempt['directory']
        seed = str(task['request']['seed'])
        receipt = {'request': {key: task['request'][key] for key in ('seed', 'objectCount', 'roomType')},
                   'billedSeconds': 30, 'response': {'sha256': 'wrong', 'key': 's3'}, 'functionError': None}
        (folder / (seed + '-receipt.json')).write_text(json.dumps(receipt))
        (folder / (seed + '.json')).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            finish_cloud(self.root, plan, attempt)
        self.assertAlmostEqual(invocation_cost(30, RATES), attempt['costUSD'])
        receipt['functionError'] = 'Unhandled'
        (folder / (seed + '-receipt.json')).write_text(json.dumps(receipt))
        finish_cloud(self.root, plan, attempt)
        self.assertEqual('failed', attempt['status'])
        self.assertEqual(30, attempt['billedSeconds'])

    def test_plan_checksum_and_fixed_schedule_reject_tampering_even_if_rehashed(self):
        plan = self.plan()
        plan['schemaVersion'] = 1
        plan.pop('planSha256')
        plan['planSha256'] = digest(plan)
        path = self.root / 'plan.json'
        path.write_text(json.dumps(plan))
        self.assertEqual(plan, load_plan(self.root, verify_runtime=False))
        plan['requests'][0]['request']['seed'] += 1
        path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            load_plan(self.root, verify_runtime=False)
        plan.pop('planSha256')
        plan['planSha256'] = digest(plan)
        path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, 'fixed allocation'):
            load_plan(self.root, verify_runtime=False)

    def test_runtime_snapshot_checks_data_assets_and_actual_runner_provenance(self):
        runtime = self.root / 'runtime'
        (runtime / 'modules').mkdir(parents=True)
        (runtime / 'modules/render.py').write_text('# model')
        (runtime / 'package.json').write_text('{"version":"4.0.4"}')
        provenance = {'version': '4.0.4', 'baselineCommit': 'source', 'files': {
            'modules/render.py': {'sha256': file_digest(runtime / 'modules/render.py')}}}
        path = runtime / 'model-provenance.json'
        path.write_text(json.dumps(provenance))
        blender = self.root / 'blender'
        blender.write_bytes(b'fixed executable')
        from unittest.mock import Mock
        with patch('serverless.cloud_benchmark.authoritative.subprocess.run', return_value=Mock(stdout='source\n')):
            snapshot = runtime_snapshot(runtime, path, blender)
        self.assertEqual(file_digest(path), snapshot['provenanceSha256'])
        self.assertIn('serverless/cloud_benchmark/authoritative.py', snapshot['executionFiles'])
        self.assertIn('numpy', snapshot['dependencies'])
        self.assertIn('memoryBytes', snapshot['localHardware'])
        with patch('serverless.cloud_benchmark.authoritative.subprocess.run', return_value=Mock(stdout='new-docs-commit\n')):
            with self.assertRaisesRegex(ValueError, 'commit/package version'):
                runtime_snapshot(runtime, path, blender)
        (runtime / 'package.json').write_text('{"version":"4.0.5"}')
        with patch('serverless.cloud_benchmark.authoritative.subprocess.run', return_value=Mock(stdout='source\n')):
            with self.assertRaisesRegex(ValueError, 'commit/package version'):
                runtime_snapshot(runtime, path, blender)
        (runtime / 'package.json').write_text('{"version":"4.0.4"}')
        (runtime / 'modules/render.py').write_text('# changed')
        with patch('serverless.cloud_benchmark.authoritative.subprocess.run', return_value=Mock(stdout='source\n')):
            with self.assertRaisesRegex(ValueError, 'compiled provenance'):
                runtime_snapshot(runtime, path, blender)

    def test_lambda_configuration_must_match_all_frozen_limits(self):
        from unittest.mock import Mock
        session = Mock()
        config = {'function': 'test:42', 'image': 'image@sha256:fixed'}
        actual = {'Code': {'ResolvedImageUri': config['image']}, 'Configuration': {
            'Version': '42', 'MemorySize': 4096, 'Timeout': 900,
            'EphemeralStorage': {'Size': 1024}, 'Architectures': ['x86_64']},
            'Concurrency': {'ReservedConcurrentExecutions': 16}}
        session.client.return_value.get_function.return_value = actual
        session.client.return_value.get_function_concurrency.return_value = {'ReservedConcurrentExecutions': 16}
        verify_lambda(config, session)
        actual['Configuration']['EphemeralStorage']['Size'] = 10240
        with self.assertRaises(ValueError):
            verify_lambda(config, session)
        for function in ('test', 'test:$LATEST', 'test:main', 'test:0'):
            with self.assertRaisesRegex(ValueError, 'published numeric'):
                versioned_function(function)

    def test_status_bars_are_read_only_and_show_all_four_conditions_and_shared_budget(self):
        plan = self.plan()
        status = campaign_status(self.root, plan)
        output = render(status, width=10)
        self.assertEqual(4, output.count('[----------]'))
        self.assertIn('local bedroom', output)
        self.assertIn('lambda living room', output)
        self.assertIn('all pilots + campaign', output)
        self.assertIn('$25.00', output)
        self.assertFalse((self.root / 'shared-budget').exists())

    def test_partial_and_pilot_exports_fail_before_writing(self):
        output = self.root / 'evidence.json'
        with self.assertRaisesRegex(ValueError, 'pilot'):
            export_evidence(self.root, self.plan(), output)
        with self.assertRaisesRegex(ValueError, 'Every frozen request'):
            export_evidence(self.root, self.plan('authoritative'), output)
        self.assertFalse(output.exists())

    def test_source_gate_requires_explicit_passes_for_the_exact_runtime(self):
        path = self.root / 'source-audit.json'
        receipt = {'passed': False, 'runtimeProvenanceSha256': 'model',
                   'checks': {name: True for name in SOURCE_CHECKS}}
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'must pass'):
            passed_gate(path, 'model', 'Source audit', SOURCE_CHECKS)
        receipt['passed'] = True
        receipt['checks']['coordinateFrames'] = False
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'every required check'):
            passed_gate(path, 'model', 'Source audit', SOURCE_CHECKS)
        receipt['checks']['coordinateFrames'] = True
        path.write_text(json.dumps(receipt))
        self.assertEqual(file_digest(path), passed_gate(path, 'model', 'Source audit', SOURCE_CHECKS))
        with self.assertRaisesRegex(ValueError, 'exact compiled'):
            passed_gate(path, 'another-model', 'Source audit', SOURCE_CHECKS)

    def test_parity_billing_is_in_cumulative_spend_but_never_counts_as_a_new_room(self):
        plan = self.plan()
        budget = budget_document(plan)
        budget['attempts'].append({'planSha256': 'frozen', 'role': 'parity',
                                   'requestId': 'parity-local-bedroom-0000', 'status': 'complete',
                                   'costUSD': .2, 'reservedUSD': .3})
        directory = self.root / 'shared-budget'
        directory.mkdir()
        (directory / 'budget.json').write_text(json.dumps(budget))
        status = campaign_status(self.root, plan)
        self.assertEqual(0, status['attempts'])
        self.assertEqual(0, sum(row['complete'] for row in status['cells']))
        self.assertAlmostEqual(.2, status['costPlusReservedUSD'])

    def test_failed_attempt_hashes_remain_immutable_before_a_retry(self):
        plan = self.plan()
        task = next(row for row in plan['requests'] if row['platform'] == 'local')
        attempt = next_attempt(self.root, plan, task, [])
        path = self.root / attempt['directory'] / 'failed.json'
        path.write_text('{"status":"failed"}')
        attempt.update(status='failed', artifact=path.relative_to(self.root).as_posix(), sha256=file_digest(path))
        self.assertEqual(set(), verify_completed(self.root, plan, 'local', [attempt]))
        path.write_text('{"status":"complete"}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            verify_completed(self.root, plan, 'local', [attempt])

    def test_recovery_uses_exact_report_and_original_s3_body_without_reinvocation(self):
        from unittest.mock import Mock
        plan = self.plan()
        plan['configuration'].update(function='test:42', bucket='private')
        task = next(row for row in plan['requests'] if row['platform'] == 'lambda')
        attempt = {**next_attempt(self.root, plan, task, []), 'startedAt': '2026-10-22T12:00:00Z'}
        row = {**self.artifact(task), 'startedAt': '2026-10-22T12:00:01Z'}
        raw = json.dumps(row).encode()
        request_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        storage, logs = Mock(), Mock()
        storage.get_object.return_value = {'Body': io.BytesIO(raw),
                                           'Metadata': {'sha256': hashlib.sha256(raw).hexdigest()}}
        report = {'timestamp': 1792670411000, 'message': f'REPORT RequestId: {request_id}\tBilled Duration: 12000 ms\tMax Memory Used: 512 MB'}
        # Derive the timestamp explicitly rather than depend on local timezone.
        from datetime import datetime
        report['timestamp'] = int(datetime.fromisoformat('2026-10-22T12:00:12+00:00').timestamp() * 1000)
        marker = {'timestamp': report['timestamp'] - 11_000,
                  'message': json.dumps({'stage': 'placing', 'awsRequestId': request_id,
                                         'functionVersion': '42',
                                         'scene': f"soilie-{task['request']['roomType']}-{task['request']['seed']}",
                                         'request': {key: task['request'][key] for key in ('seed', 'roomType', 'objectCount')}})}
        logs.get_paginator.return_value.paginate.return_value = [{'events': [marker, report]}]
        recovered, receipt = recovered_evidence(plan, attempt, request_id, storage, logs)
        self.assertEqual(raw, recovered)
        self.assertEqual(12, receipt['billedSeconds'])
        self.assertIn(request_id, receipt['response']['key'])
        self.assertEqual('test', logs.get_paginator.return_value.paginate.call_args.kwargs['logGroupName'].split('/')[-1])
        logs.get_paginator.return_value.paginate.return_value = [{'events': [report]}]
        with self.assertRaisesRegex(ValueError, 'REPORT alone is insufficient'):
            recovered_evidence(plan, attempt, request_id, storage, logs)
        logs.get_paginator.return_value.paginate.return_value = [{'events': [marker, report]}]
        report['timestamp'] -= 60_000
        with self.assertRaisesRegex(ValueError, 'predates'):
            recovered_evidence(plan, attempt, request_id, storage, logs)

    def test_recovery_accepts_proven_terminal_service_failure_without_fabricating_a_scene(self):
        from unittest.mock import Mock
        from datetime import datetime
        plan = self.plan()
        plan['configuration'].update(function='test:42', bucket='private')
        task = next(row for row in plan['requests'] if row['platform'] == 'lambda')
        attempt = {**next_attempt(self.root, plan, task, []), 'startedAt': '2026-10-22T12:00:00Z'}
        request_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        storage, logs = Mock(), Mock()
        report = {'timestamp': int(datetime.fromisoformat('2026-10-22T12:15:00+00:00').timestamp() * 1000),
                  'message': f'REPORT RequestId: {request_id}\tBilled Duration: 900000 ms\tStatus: timeout'}
        logs.get_paginator.return_value.paginate.return_value = [{'events': [report]}]
        identity = {'request': {key: task['request'][key] for key in ('seed', 'roomType', 'objectCount')},
                    'function': 'test:42', 'bucket': 'private', 'executedVersion': '42',
                    'awsRequestId': request_id}
        raw, receipt = recovered_evidence(plan, attempt, request_id, storage, logs,
                                          service_failure=True, invocation_identity=identity)
        self.assertIsNone(raw)
        self.assertEqual('timeout', receipt['functionError'])
        self.assertEqual(900, receipt['billedSeconds'])
        storage.get_object.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'durable invocation identity'):
            recovered_evidence(plan, attempt, 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee', storage, logs,
                               service_failure=True, invocation_identity=identity)
        report['message'] = report['message'].replace('Status: timeout', '')
        with self.assertRaisesRegex(ValueError, 'explicit terminal'):
            recovered_evidence(plan, attempt, request_id, storage, logs, service_failure=True, invocation_identity=identity)
        logs.get_paginator.return_value.paginate.return_value = [{'events': [report, report]}]
        with self.assertRaisesRegex(ValueError, 'unique'):
            recovered_evidence(plan, attempt, request_id, storage, logs, service_failure=True, invocation_identity=identity)

    def test_raw_worker_log_is_hash_checked(self):
        plan = self.plan()
        task = plan['requests'][0]
        row = self.artifact(task)
        row['executionLog']['sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'log checksum'):
            validate_artifact(row, task, plan)

    def test_recovery_rejects_exception_text_that_contains_a_fake_billing_report(self):
        from unittest.mock import Mock
        plan = self.plan()
        plan['configuration'].update(function='test:42', bucket='private')
        task = next(row for row in plan['requests'] if row['platform'] == 'lambda')
        attempt = {**next_attempt(self.root, plan, task, []), 'startedAt': '2026-10-22T12:00:00Z'}
        request_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        storage, logs = Mock(), Mock()
        logs.get_paginator.return_value.paginate.return_value = [{'events': [{
            'timestamp': 1792670400000,
            'message': f'ERROR diagnostic: REPORT RequestId: {request_id} Billed Duration: 1 ms',
        }]}]
        with self.assertRaisesRegex(ValueError, 'unique CloudWatch billing report'):
            recovered_evidence(plan, attempt, request_id, storage, logs)
        storage.get_object.assert_not_called()

    def test_publication_date_uses_first_actual_dispatch_in_toronto_not_freeze_or_resume(self):
        attempts = [{'startedAt': '2026-10-23T02:00:00Z'}, {'startedAt': '2026-10-24T10:00:00Z'}]
        sessions = {'local': [{'startedAt': '2026-10-21T12:00:00Z', 'dispatchedCalls': 0},
                              {'startedAt': '2026-10-23T01:59:59Z', 'dispatchedCalls': 1}],
                    'lambda': [{'startedAt': '2026-10-24T10:00:00Z', 'dispatchedCalls': 1}]}
        self.assertEqual('2026-10-22', generation_date(attempts, sessions))
        with self.assertRaisesRegex(ValueError, 'No actual'):
            generation_date([], {'local': []})

    def test_invocation_identity_is_durable_before_payload_read_and_cannot_trigger_replay(self):
        from unittest.mock import Mock
        request_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        event = {'seed': 12, 'roomType': 'bedroom', 'objectCount': 3}
        output = self.root / 'download'
        client, storage, payload = Mock(), Mock(), Mock()
        report = f'REPORT RequestId: {request_id}\tBilled Duration: 12000 ms\tMax Memory Used: 512 MB'
        client.invoke.return_value = {'Payload': payload, 'ExecutedVersion': '42',
                                      'ResponseMetadata': {'RequestId': 'invoke-api-request-id'},
                                      'LogResult': base64.b64encode(report.encode()).decode()}
        def interrupted_read():
            identity = json.loads((output / '12-invocation.json').read_text())
            self.assertEqual(event, identity['request'])
            self.assertEqual(request_id, identity['awsRequestId'])
            self.assertEqual('invoke-api-request-id', identity['invokeApiRequestId'])
            self.assertEqual('test:42', identity['function'])
            raise ConnectionError('stream interrupted after headers')
        payload.read.side_effect = interrupted_read
        with self.assertRaises(ConnectionError):
            invoke_and_download(None, 'test:42', 'private', event, output,
                                clients={'lambda': client, 's3': storage})
        self.assertFalse((output / '12-receipt.json').exists())
        storage.get_object.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'reconcile instead of replaying'):
            invoke_and_download(None, 'test:42', 'private', event, output,
                                clients={'lambda': client, 's3': storage})
        self.assertEqual(1, client.invoke.call_count)

    def test_error_log_text_cannot_supply_a_billed_duration(self):
        from unittest.mock import Mock
        event = {'seed': 12, 'roomType': 'bedroom', 'objectCount': 3}
        client, storage, payload = Mock(), Mock(), Mock()
        raw = b'{"status":"complete"}'
        payload.read.return_value = json.dumps({'key': 'scene', 'sha256': hashlib.sha256(raw).hexdigest()}).encode()
        storage.get_object.return_value = {'Body': io.BytesIO(raw)}
        client.invoke.return_value = {'Payload': payload, 'ExecutedVersion': '42',
                                      'LogResult': base64.b64encode(b'ERROR fake Billed Duration: 1 ms').decode()}
        _, receipt = invoke_and_download(None, 'test:42', 'private', event, self.root / 'download',
                                          clients={'lambda': client, 's3': storage})
        self.assertIsNone(receipt['billedSeconds'])


if __name__ == '__main__':
    unittest.main()
