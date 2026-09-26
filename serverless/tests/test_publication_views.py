from copy import deepcopy
import unittest
import hashlib
import json
from pathlib import Path
import tempfile

from serverless.cloud_benchmark.publication_views import (completed_calls, measured_cost, native_timing,
                                                         room_models, merge_geometry, verified_reviews, mesh_check_coverage)
from serverless.benchmark.cost import AWS_URL, GPT4_URL


class PublicationViewsTests(unittest.TestCase):
    def test_surface_checks_are_not_reported_as_closed_solid_volumes(self):
        rows = [{'scene': {'model': 'soilie', 'solidMeshOverlap': {
            'pairCount': 6, 'broadPhaseDisjointPairs': 5, 'surfaceDisjointPairs': 1}}}]
        coverage = mesh_check_coverage(rows)['soilie']
        self.assertEqual(6, coverage['pairCount'])
        self.assertEqual(1, coverage['roomsUsingSurfaceTests'])
        self.assertEqual(0, coverage['booleanPairs'])

    def test_open_surface_crossing_counts_as_detection_not_missing_volume(self):
        checks = [
            {'complete': True, 'overlapPairs': []},
            {'complete': False, 'unavailablePairs': [{'reason': 'open mesh; 12 intersecting triangle pair(s)'}]},
            {'complete': False, 'unavailablePairs': [{'reason': 'Boolean failed'}]},
            {'complete': True, 'overlapPairs': [{'intersectionM3': .1}]}]
        result = mesh_check_coverage([{'scene': {'model': 'infinigen', 'solidMeshOverlap': check}} for check in checks])['infinigen']
        self.assertEqual(4, result['checkedRooms'])
        self.assertEqual(2, result['detectedIntersectionRooms'])
        self.assertEqual(1, result['noDetectedIntersectionRooms'])
        self.assertEqual(1, result['unresolvedRooms'])

    def test_identical_geometry_is_not_counted_twice_and_changes_are_rejected(self):
        row = {'scene': {'id': 'one'}, 'metrics': {'gap': 0}}
        self.assertEqual([row], merge_geometry([row], [deepcopy(row)]))
        with self.assertRaises(ValueError):
            merge_geometry([row], [{'scene': {'id': 'one'}, 'metrics': {'gap': 1}}])
        with self.assertRaises(ValueError):
            merge_geometry([row, row], [])

    def test_review_gate_requires_all_strata_and_unchanged_files(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            manifest = {'releaseEligible': True, 'cohortSha256': 'cohort', 'comparisons': {}}
            for baseline, stem in (('layoutgpt', 'ai-pilot'), ('infinigen_controlled', 'ai-pilot-infinigen')):
                files = {}
                for suffix in ('-summary.json', '-responses.json'):
                    path = root / (stem + suffix)
                    path.write_text(json.dumps({'releaseEligible': True, 'cohortSha256': 'cohort', 'reviewersCompleted': 10}))
                    files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
                manifest['comparisons'][baseline] = {'releaseEligible': True,
                    'pairs': {'bedroom': 120, 'living_room': 120}, 'files': files}
            (root / 'review-manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(4, len(verified_reviews(root, 'cohort')))
            with self.assertRaises(ValueError): verified_reviews(root, 'other')
            (root / 'ai-pilot-summary.json').write_text('{}')
            with self.assertRaises(ValueError): verified_reviews(root, 'cohort')

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

    def test_original_bedroom_pilot_keeps_no_count_constraint_and_separate_timing(self):
        data = self.export()
        data['variant'] = 'bedroom-original-prompt-timing'
        data['rows'][0]['scene']['roomType'] = 'bedroom'
        data['attempts'][0].update(requestedObjects=None, countSatisfied=None, finishReason='stop')
        calls = completed_calls([data], 'bedroom')
        self.assertEqual(1, len(calls))
        self.assertEqual(4, calls[0]['seconds'])
        with self.assertRaises(ValueError): completed_calls([data])
        for field, value in [('finishReason', 'length'), ('geometryStatus', 'invalid'), ('requestedObjects', 3)]:
            changed = deepcopy(data)
            changed['attempts'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): completed_calls([changed], 'bedroom')
        rates = {'currency': 'USD', 'lambda': {'source': AWS_URL,
            'computeUsdPerGbSecond': .0000166667, 'storageUsdPerGbSecond': .000000034, 'requestUsd': .0000002},
            'gpt4': {'source': GPT4_URL, 'inputUsdPerMillion': 30, 'outputUsdPerMillion': 60}}
        sources = [{'platform': 'AWS Lambda', 'roomType': 'living_room', 'generationSeconds': 10}] * 2500
        result = measured_cost(sources, completed_calls([self.export()]), rates, bedroom_calls=calls)
        self.assertEqual(1, result['byRoomType']['bedroom']['layoutgpt']['n'])
        self.assertAlmostEqual(.072, result['byRoomType']['bedroom']['layoutgpt']['mean'])
        self.assertNotIn('layoutgptBedroom', result['missing'])
        self.assertIn('Separate timing/usage pilot', result['bedroomPilot']['basis'])

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

    def test_cost_distributions_price_individual_records_and_label_transfer(self):
        rates = {'currency': 'USD', 'lambda': {'source': AWS_URL,
            'computeUsdPerGbSecond': .0000166667, 'storageUsdPerGbSecond': .000000034, 'requestUsd': .0000002},
            'gpt4': {'source': GPT4_URL, 'inputUsdPerMillion': 30, 'outputUsdPerMillion': 60}}
        sources = [{'platform': 'AWS Lambda', 'roomType': 'living_room', 'generationSeconds': 10}] * 2500
        sources += [{'platform': 'AWS Lambda', 'roomType': 'bedroom', 'generationSeconds': 20}]
        native = {'attempts': [
            {'id': 'native-bedroom', 'roomType': 'bedroom', 'status': 'complete', 'generationSeconds': 100},
            {'id': 'native-living', 'roomType': 'living_room', 'status': 'complete', 'generationSeconds': 200},
            {'id': 'shared', 'roomType': 'living_room', 'status': 'complete', 'generationSeconds': 500, 'timingEligible': False}]}
        result = measured_cost(sources, completed_calls([self.export()]), rates, native)
        self.assertEqual(2504, len(result['observations']))
        self.assertEqual(0, result['byRoomType']['bedroom']['layoutgpt']['n'])
        self.assertIsNone(result['byRoomType']['bedroom']['layoutgpt']['mean'])
        self.assertNotIn('shared', [row['id'] for row in result['observations']])
        self.assertIn('not run on AWS Lambda', result['infinigenBasis'])
        for room in ('bedroom', 'living_room'):
            for model, summary in result['byRoomType'][room].items():
                rows = [row for row in result['observations'] if row['roomType'] == room and row['model'] == model]
                self.assertEqual(sorted(row['usd'] for row in rows), summary['values'])
        for row in result['observations']:
            if row['model'] == 'layoutgpt':
                self.assertAlmostEqual(.072, row['usd'])
            else:
                self.assertAlmostEqual(row['seconds'] * (4 * .0000166667 + 9.5 * .000000034) + .0000002, row['usd'])
        self.assertTrue(all(row['basis'] == 'hypothetical-runtime-transfer' for row in result['observations'] if row['model'] == 'infinigen'))


if __name__ == '__main__': unittest.main()
