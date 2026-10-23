"""Frozen requests must never silently shrink or lose their cost provenance."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from serverless.benchmark.run_batch import (
    load_request_plan, retain_execution_log, verify_execution_log, worker,
)
from serverless.cloud_benchmark.rate_card import extract, USAGE


class AuthoritativeWorkerTests(unittest.TestCase):
    def temporary(self):
        base = Path(__file__).resolve().parents[2] / '.codex/tests'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def request(self, count=8):
        return {'mode': 'room_type', 'roomType': 'bedroom', 'seed': 71,
                'objectCount': count, 'allowDuplicates': True,
                'sameObjectsAcrossScenes': True}

    def test_worker_rejects_six_selected_instances_for_an_eight_item_request(self):
        work = self.temporary()
        (work / 'request.json').write_text(json.dumps(self.request()))
        with patch('serverless.common.model_runtime.select_model_objects',
                   return_value=['chair'] * 6), patch(
                   'serverless.common.model_runtime.generate_model_inputs_with_diagnostics') as generate:
            with self.assertRaisesRegex(RuntimeError, 'instance count differs'):
                worker(SimpleNamespace(work=work, runtime=work))
        generate.assert_not_called()

    def test_raw_worker_log_survives_temporary_cleanup_and_detects_mutation(self):
        path = self.temporary() / 'process.log'
        raw = b'Blender output\r\nTraceback\nnon-UTF-8: \xff\xfe\n' * 200
        path.write_bytes(raw)
        record = retain_execution_log(path)
        self.assertEqual(record, retain_execution_log(path))
        path.unlink()
        self.assertEqual(raw, verify_execution_log(json.loads(json.dumps(record))))
        for changed in ({'bytes': len(raw) + 1}, {'sha256': 'wrong'},
                        {'data': 'not base64'}, {'encoding': 'plain-text'}):
            with self.assertRaises(ValueError):
                verify_execution_log({**record, **changed})

    def test_frozen_authoritative_plan_accepts_eight_and_rejects_repeated_seeds(self):
        path = self.temporary() / 'requests.json'
        document = {'schemaVersion': 1, 'cohort': 'authoritative',
                    'requests': [self.request()]}
        path.write_text(json.dumps(document))
        self.assertEqual(document, load_request_plan(path))
        document['requests'].append(self.request())
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'repeat a seed'):
            load_request_plan(path)

    def test_authoritative_plan_rejects_changed_count_or_duplicate_policy(self):
        path = self.temporary() / 'requests.json'
        for change in ({'objectCount': 9}, {'objectCount': True}, {'allowDuplicates': False}):
            request = {**self.request(), **change}
            path.write_text(json.dumps({'schemaVersion': 1, 'cohort': 'authoritative',
                                        'requests': [request]}))
            with self.assertRaises(ValueError):
                load_request_plan(path)

    def test_tariff_snapshot_rejects_a_missing_or_ambiguous_regional_rate(self):
        offer = {'publicationDate': '2026-10-22T00:00:00Z',
                 'products': {}, 'terms': {'OnDemand': {}}}
        for index, usage in enumerate(USAGE):
            sku = str(index)
            offer['products'][sku] = {'attributes': {'usagetype': usage}}
            offer['terms']['OnDemand'][sku] = {'term': {'priceDimensions': {
                'rate': {'beginRange': '0', 'rateCode': 'example',
                         'pricePerUnit': {'USD': '0.0000002'}}}}}
        raw = json.dumps(offer).encode()
        result = extract(raw)
        self.assertEqual({'computeGBSecondUSD', 'requestUSD', 'ephemeralGBSecondUSD'},
                         set(result) & set(USAGE.values()))
        self.assertEqual('ca-central-1', result['region'])
        del offer['products']['0']
        with self.assertRaisesRegex(ValueError, 'omitted'):
            extract(json.dumps(offer).encode())


if __name__ == '__main__':
    unittest.main()
