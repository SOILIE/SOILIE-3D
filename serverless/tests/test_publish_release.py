import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.cloud_benchmark.publish_release import PUBLIC_FILES, publish, release_files


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        image = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
        self.image_name = hashlib.sha256(image).hexdigest()[:24] + '.svg'
        (self.root / 'stimuli').mkdir()
        (self.root / 'stimuli' / self.image_name).write_bytes(image)
        for name in PUBLIC_FILES: self.write(name, {})
        reports = {'releaseEligible': True, 'cohortSha256': 'cohort', 'reviewersCompleted': 10,
                   'stimuli': [{'soilieImage': '/benchmarks/stimuli/' + self.image_name,
                                'baselineImage': '/benchmarks/stimuli/' + self.image_name}]}
        manifest = {'releaseEligible': True, 'cohortSha256': 'cohort', 'comparisons': {}}
        for baseline, stem in (('layoutgpt', 'ai-pilot'), ('infinigen_controlled', 'ai-pilot-infinigen')):
            files = {}
            for suffix in ('-summary.json', '-responses.json'):
                name = stem + suffix
                self.write(name, reports)
                files[name] = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
            manifest['comparisons'][baseline] = {'releaseEligible': True, 'pairs': {'bedroom': 120, 'living_room': 120}, 'files': files}
        self.write('review-manifest.json', manifest)
        comparison = {'schemaVersion': 4, 'aiReview': {'ready': True, 'cohortSha256': 'cohort',
            'manifestSha256': hashlib.sha256((self.root / 'review-manifest.json').read_bytes()).hexdigest()}, 'models': {'soilie': {'n': 10000}}}
        comparison['evidenceDigest'] = hashlib.sha256(json.dumps(comparison, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.write('comparison.json', comparison)
        self.write('status.json', {'analysis': {'state': 'complete'}, 'corpus': {'completedLayouts': 10000}})
        self.write('room-measurements.json', {'cohortSha256': 'cohort', 'rows': [
            {'sceneId': str(i), 'model': 'soilie', 'roomType': 'bedroom' if i < 5000 else 'living_room'} for i in range(10000)]})

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data), encoding='utf-8')

    def test_only_public_allowlist_and_hash_matched_images_are_published(self):
        self.write('private.json', {'sessionToken': 'private'})
        files, cohort, counts = release_files(self.root)
        self.assertEqual(len(PUBLIC_FILES) + 1, len(files))
        self.assertNotIn('private.json', files)
        self.assertEqual('cohort', cohort)
        self.assertEqual({'soilie': 10000}, counts)
        (self.root / 'stimuli' / self.image_name).write_text('changed')
        with self.assertRaises(ValueError): release_files(self.root)

    def test_private_metadata_and_chart_count_drift_are_rejected(self):
        self.write('publication-inputs.json', {'sessionToken': 'private'})
        with self.assertRaises(ValueError): release_files(self.root)
        self.write('publication-inputs.json', {})
        self.write('room-measurements.json', {'cohortSha256': 'cohort', 'rows': []})
        with self.assertRaises(ValueError): release_files(self.root)

    def test_manifest_and_index_publish_after_verified_objects(self):
        events = []
        def uploaded(_client, _bucket, key, body, _mime, checksum, size):
            events.append(key)
            self.assertEqual(hashlib.sha256(body).hexdigest(), checksum)
            return {'sha256': checksum, 'bytes': size}
        def indexed(_client, _bucket, keys):
            self.assertEqual(set(events), set(keys))
            events.append('index')
            return len(keys)
        with patch('serverless.cloud_benchmark.publish_release.upload', side_effect=uploaded), \
             patch('serverless.cloud_benchmark.publish_release.merge_index', side_effect=indexed):
            result = publish(None, 'test', self.root, '2026-09-24', '0.2.1')
        self.assertEqual('index', events[-1])
        self.assertTrue(events[-2].endswith('/manifest.json'))
        self.assertEqual('files/outputs/benchmark-2026-09-24/analysis-v0.2.1/', result['prefix'])
        with self.assertRaises(ValueError): publish(None, 'test', self.root, '2026-09-24', '../bad')
        with patch('serverless.cloud_benchmark.publish_release.upload', side_effect=uploaded), \
             patch('serverless.cloud_benchmark.publish_release.merge_index', return_value=12):
            amended = publish(None, 'test', self.root, '2026-09-24', '0.2.1', 'abcdef012345')
        self.assertEqual('files/outputs/benchmark-2026-09-24/analysis-v0.2.1-abcdef012345/', amended['prefix'])
        with self.assertRaises(ValueError): publish(None, 'test', self.root, '2026-09-24', '0.2.1', '../bad')

    def test_cost_download_requires_matching_digest_and_count(self):
        self.write('cost-measurements.json', {'rows': [{'id': 'public', 'usd': .01}]})
        comparison = json.loads((self.root / 'comparison.json').read_bytes())
        comparison.pop('evidenceDigest')
        comparison['cost'] = {'measurements': {'file': 'cost-measurements.json', 'rows': 1,
            'sha256': hashlib.sha256((self.root / 'cost-measurements.json').read_bytes()).hexdigest()}}
        comparison['evidenceDigest'] = hashlib.sha256(json.dumps(comparison, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.write('comparison.json', comparison)
        files, _, _ = release_files(self.root)
        self.assertIn('cost-measurements.json', files)
        self.write('cost-measurements.json', {'rows': []})
        with self.assertRaisesRegex(ValueError, 'cost evidence differs'): release_files(self.root)


if __name__ == '__main__': unittest.main()
