import gzip
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from serverless.benchmark.archive import build, decode_index, merge_index, prefix, public_only, publish
from serverless.tests.test_benchmark_stimuli import fixture


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).parents[2]/'.codex/tests'
        base.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.output = Path(self.temp.name)
        self.stimuli_temp = tempfile.TemporaryDirectory(dir=base)
        self.stimuli = Path(self.stimuli_temp.name)

    def tearDown(self):
        self.temp.cleanup()
        self.stimuli_temp.cleanup()

    def test_public_allowlist_rejects_private_values_not_web_sources(self):
        public_only({'source':'https://github.com/soilie/SOILIE-3D'})
        for value in ({'sessionToken':'x'},{'note':'C:\\Users\\private'}, {'note':'/mnt/c/private'}, {'accountId':'123'}):
            with self.assertRaises(ValueError):
                public_only(value)

    def test_archive_is_outside_temporary_results_and_has_real_geometry(self):
        document = build({'rows':[fixture('soilie',0)]},{'runs':[]},[],'2026-09-14',self.output)
        self.assertEqual('files/outputs/benchmark-2026-09-14/',prefix('2026-09-14'))
        self.assertEqual({'soilie':1},document['counts'])
        self.assertEqual(4,len(document['files']))
        geometry = json.loads((self.output/document['scenes'][0]['geometry']).read_text())
        self.assertEqual(fixture('soilie',0),geometry)
        self.assertIn('Plan view',(self.output/document['scenes'][0]['diagram']).read_text())

    def test_archive_can_publish_source_geometry_without_a_front_axis(self):
        row = fixture('infinigen',0)
        for item in row['scene']['objects']:
            item.pop('frontDirection',None)
        document = build({'rows':[row]},{'runs':[]},[],'2026-09-14',self.output)
        svg = (self.output/document['scenes'][0]['diagram']).read_text()
        self.assertNotIn('class="front"',svg)

    def test_focused_reviews_are_not_collapsed_into_an_overall_score(self):
        review = {'decisionScope':'focus_only','stimuli':[]}
        document = build({'rows':[fixture('soilie',0)]},{'runs':[]},[review],
                         '2026-09-14',self.output)
        self.assertEqual(1,len(document['aiReviews']))
        self.assertIsNone(document['combinedAiPilot'])

    def test_frozen_review_stimulus_is_archived_by_verified_content_hash(self):
        body = b'<svg xmlns="http://www.w3.org/2000/svg"><title>Frozen review view</title></svg>'
        name = hashlib.sha256(body).hexdigest()[:24]+'.svg'
        (self.stimuli/name).write_bytes(body)
        source = '/benchmarks/stimuli/'+name
        review = {'decisionScope':'focus_only','stimuli':[
            {'caseId':'case-1','soilieImage':source,'baselineImage':source},
        ]}

        document = build({'rows':[fixture('soilie',0)]},{'runs':[]},[review],
                         '2026-09-14',self.output,self.stimuli)

        archived = document['reviewStimulusPaths'][source]
        self.assertTrue(archived.startswith('review-stimuli/'))
        self.assertEqual(body,(self.output/archived).read_bytes())

    def test_frozen_review_stimulus_rejects_a_false_content_hash(self):
        name = '0'*24+'.svg'
        (self.stimuli/name).write_text('<svg/>')
        source = '/benchmarks/stimuli/'+name
        review = {'decisionScope':'focus_only','stimuli':[
            {'caseId':'case-1','soilieImage':source,'baselineImage':source},
        ]}
        with self.assertRaisesRegex(ValueError,'checksum does not match'):
            build({'rows':[fixture('soilie',0)]},{'runs':[]},[review],
                  '2026-09-14',self.output,self.stimuli)

    def test_gzipped_index_preserves_other_keys_and_updates_conditionally(self):
        client = MagicMock()
        client.get_object.return_value = {'Body':BytesIO(gzip.compress(b'{"schemaVersion":1,"keys":["files/old.csv"]}')),
                                          'ContentEncoding':'gzip','ETag':'"original"'}
        self.assertEqual(2,merge_index(client,'bucket',['files/new.json']))
        request = client.put_object.call_args.kwargs
        self.assertEqual('"original"',request['IfMatch'])
        document = decode_index({'Body':BytesIO(request['Body']),'ContentEncoding':request['ContentEncoding']})
        self.assertEqual(['files/new.json','files/old.csv'],document['keys'])

    def test_a_stale_checkpoint_cannot_hide_previously_published_scenes(self):
        build({'rows':[fixture('soilie',0)]},{'runs':[]},[],'2026-09-14',self.output)
        client = MagicMock()
        client.get_object.return_value = {'Body':BytesIO(b'{"scenes":[{"id":"soilie-newer"}]}'),'ETag':'"original"'}
        with self.assertRaises(ValueError):
            publish(client,'bucket',self.output)
        client.put_object.assert_not_called()

    def test_unchanged_immutable_files_reuse_the_published_receipt(self):
        from unittest.mock import patch
        document = build({'rows':[fixture('soilie',0)]},{'runs':[]},[],'2026-09-14',self.output)
        client = MagicMock()
        client.get_object.return_value = {'Body':BytesIO(json.dumps(document).encode()),'ETag':'"original"'}
        with patch('serverless.benchmark.archive.merge_index',return_value=5):
            publish(client,'bucket',self.output)
        uploaded = [call.kwargs['Key'] for call in client.put_object.call_args_list]
        self.assertFalse(any(document['prefix']+key in uploaded for key in document['files']))
        self.assertIn(document['prefix']+'manifest.json',uploaded)
