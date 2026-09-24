from copy import deepcopy
import unittest

from serverless.cloud_benchmark.publication_views import completed_calls, measured_cost, native_timing, room_models
from serverless.benchmark.cost import AWS_URL, GPT4_URL


class PublicationViewsTests(unittest.TestCase):
    def export(self):
        return {'complete': True, 'reservedUncertainUsd': 0,
            'rows': [{'scene': {'id': 'layoutgpt-call-0', 'roomType': 'living_room',
                               'provenance': {'model': 'gpt-4-0613'}}}],
            'attempts': [{'id': 'call-0', 'status': 'complete', 'geometryStatus': 'complete',
                'unparsedLines': 0, 'countSatisfied': True, 'requestedObjects': 3, 'wallSeconds': 4,
                'usage': {'prompt_tokens': 2000, 'completion_tokens': 200},
                'privateReceipt': {'account': 'must-not-publish'}}]}

    def test_closed_call_export_uses_allowlist_not_private_receipts(self):
        calls = completed_calls([self.export()])
        self.assertEqual(2000, calls[0]['inputTokens'])
        self.assertNotIn('privateReceipt', calls[0])
        self.assertEqual({'id', 'requestedObjects', 'seconds', 'inputTokens', 'outputTokens', 'model'}, set(calls[0]))

    def test_rejects_uncertain_duplicate_missing_and_malformed_calls(self):
        for mutation in ('uncertain', 'missing', 'invalid', 'duration', 'tokens', 'count', 'model'):
            data = self.export()
            if mutation == 'uncertain': data['reservedUncertainUsd'] = 1
            if mutation == 'missing': data['rows'] = []
            if mutation == 'invalid': data['attempts'][0]['geometryStatus'] = 'invalid'
            if mutation == 'duration': data['attempts'][0]['wallSeconds'] = float('nan')
            if mutation == 'tokens': data['attempts'][0]['usage']['prompt_tokens'] = -1
            if mutation == 'count': data['attempts'][0]['requestedObjects'] = 8
            if mutation == 'model': data['rows'][0]['scene']['provenance']['model'] = 'different-priced-model'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): completed_calls([data])
        with self.assertRaises(ValueError): completed_calls([self.export(), self.export()])

    def test_native_timing_keeps_rooms_separate_and_excludes_shared_cpu(self):
        data = {'attempts': [
            {'roomType': 'bedroom', 'status': 'complete', 'generationSeconds': 20},
            {'roomType': 'living_room', 'status': 'complete', 'generationSeconds': 80},
            {'roomType': 'bedroom', 'status': 'complete', 'generationSeconds': 900, 'timingEligible': False},
            {'roomType': 'bedroom', 'status': 'failed', 'generationSeconds': 3600}]}
        result = native_timing(data)
        self.assertEqual([20], result['bedroom']['completedLatencySeconds']['values'])
        self.assertEqual([80], result['living_room']['completedLatencySeconds']['values'])

    def test_cost_prices_observed_living_room_usage_not_desktop_or_bedroom(self):
        rates = {'currency': 'USD', 'lambda': {'source': AWS_URL,
            'computeUsdPerGbSecond': .0000166667, 'storageUsdPerGbSecond': .000000034, 'requestUsd': .0000002},
            'gpt4': {'source': GPT4_URL, 'inputUsdPerMillion': 30, 'outputUsdPerMillion': 60}}
        sources = [{'platform': 'AWS Lambda', 'roomType': 'living_room', 'generationSeconds': 10}] * 2500
        sources += [{'platform': 'local', 'roomType': 'living_room', 'generationSeconds': 999},
                    {'platform': 'AWS Lambda', 'roomType': 'bedroom', 'generationSeconds': 999}]
        cost = measured_cost(sources, completed_calls([self.export()]), rates)
        self.assertAlmostEqual(.072, cost['layoutgpt']['usd']['mean'])
        self.assertEqual(10, cost['soilie']['seconds']['mean'])
        self.assertEqual(2500, cost['soilie']['usd']['n'])
        self.assertEqual(0, cost['freeTier']['allowanceAvailable']['computeUsd'])
        self.assertGreater(cost['freeTier']['allowanceAvailable']['extraTemporaryStorageUsd'], 0)
        self.assertGreater(cost['freeTier']['allowanceExhausted']['computeUsd'], 0)
        private_rates = deepcopy(rates)
        private_rates['accountId'] = 'do-not-publish'
        with self.assertRaises(ValueError): measured_cost(sources, completed_calls([self.export()]), private_rates)

    def test_empty_room_strata_are_missing_not_zero(self):
        models = room_models([])
        for room in models.values():
            for model in room.values():
                self.assertEqual(0, model['n'])
                self.assertIsNone(model['metrics']['floorSupportGapCm']['mean'])


if __name__ == '__main__': unittest.main()
