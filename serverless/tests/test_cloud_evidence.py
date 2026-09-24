from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.cloud_benchmark.evidence import corrected_source, sha, validate_final
from serverless.cloud_benchmark.reviews import prepare
from serverless.cloud_benchmark.submit_reviews import submit


class EvidenceTests(unittest.TestCase):
    def fixture(self):
        return {'id':'fixture','status':'complete','request':{'seed':1,'roomType':'bedroom','objectCount':3},
                'generationSeconds':2,'implementation':{'modelVersion':'4.0.2'},
                'stages':{'final':{'solidMeshOverlap':{'complete':True,'maxOverlapPct':0},'objects':[
                    {'id':'chair','label':'chair','support':{'gapM':0,'belowFloorM':0,
                        'source':'mesh-vertical-contact','samplingVersion':3,'supportKind':'floor','supportId':'Floor'}}]}}}

    def temporary(self):
        directory=Path(__file__).resolve().parents[2]/'.codex/tests'
        directory.mkdir(exist_ok=True,parents=True)
        return tempfile.TemporaryDirectory(dir=directory)

    def test_missing_invalid_or_unsettled_geometry_cannot_pass(self):
        source=self.fixture()
        validate_final(source)
        for value in (None,float('nan'),float('inf'),-1,.1):
            row=deepcopy(source); row['stages']['final']['solidMeshOverlap']['maxOverlapPct']=value
            with self.assertRaises(ValueError): validate_final(row)
        for field,value in [('gapM',.01),('belowFloorM',.01),('gapM',float('nan')),('belowFloorM',-1)]:
            row=deepcopy(source); row['stages']['final']['objects'][0]['support'][field]=value
            with self.assertRaises(ValueError): validate_final(row)
        row=deepcopy(source); row['stages']['final']['objects'][0].pop('support')
        with self.assertRaises(ValueError): validate_final(row)
        row=deepcopy(source); row['generationSeconds']=0
        with self.assertRaises(ValueError): validate_final(row)

    def test_source_hash_and_original_request_are_required(self):
        with self.temporary() as folder:
            grid=Path(folder); (grid/'downloads').mkdir()
            source=self.fixture(); raw=json.dumps(source).encode()
            path=grid/'downloads/1.json'; path.write_bytes(raw)
            entry={'status':'complete','sha256':sha(raw)}
            result, provenance=corrected_source(grid,source['request'],entry)
            self.assertEqual(source,result)
            self.assertEqual(2,provenance['generationSeconds'])
            self.assertEqual(raw,path.read_bytes())
            with self.assertRaisesRegex(ValueError,'fixed allocation'):
                corrected_source(grid,{**source['request'],'objectCount':4},entry)
            with self.assertRaisesRegex(ValueError,'modified'):
                corrected_source(grid,source['request'],{**entry,'sha256':'bad'})

    def test_only_full_cohort_can_start_review_preparation(self):
        with self.temporary() as folder:
            root=Path(folder)
            (root/'cohort.json').write_text(json.dumps({'complete':True,'soilieScenes':5000}))
            with self.assertRaisesRegex(ValueError,'10,000'):
                prepare(root,root/'review')
            self.assertFalse((root/'review').exists())

    def test_declared_complete_count_cannot_replace_actual_scene_count(self):
        with self.temporary() as folder:
            root=Path(folder); raw=b'{"rows":[]}'
            (root/'measured-scenes.json').write_bytes(raw)
            (root/'cohort.json').write_text(json.dumps({'complete':True,'soilieScenes':10000,
                                                      'measurementsSha256':sha(raw)}))
            with self.assertRaisesRegex(ValueError,'5,000 distinct'):
                prepare(root,root/'review')

    def test_missing_or_duplicate_judgement_is_rejected_before_persistence(self):
        with self.temporary() as folder:
            root=Path(folder); packet=root/'packets/reviewer-01'; packet.mkdir(parents=True)
            case={'set':'set-a','caseId':'test'}
            (packet/'cases.json').write_text(json.dumps([case]))
            for responses in ([],[case,case],[{**case,'caseId':'wrong'}]):
                (packet/'answers.json').write_text(json.dumps(responses))
                with self.assertRaisesRegex(ValueError,'Exactly one'):
                    submit(root,'reviewer-01')

    def test_failed_final_audit_cannot_prepare_or_start_reviewers(self):
        from serverless.cloud_benchmark.finalize import main
        with self.temporary() as folder:
            root=Path(folder); (root/'local-completion.json').write_text('{}')
            output=root/'final'
            arguments=['finalize','--grid',str(root),'--campaign',str(root),
                       '--cloud-evidence',str(root),'--output',str(output)]
            with patch('sys.argv',arguments), patch('serverless.cloud_benchmark.finalize.compile_full',
                    side_effect=ValueError('invalid geometry')), patch('serverless.cloud_benchmark.finalize.prepare') as prepare_review:
                with self.assertRaisesRegex(ValueError,'invalid geometry'): main()
                prepare_review.assert_not_called()
            state=json.loads((output/'readiness.json').read_bytes())
            self.assertEqual('failed',state['stage'])
            self.assertEqual(0,state['reviewersStarted'])


if __name__=='__main__':
    unittest.main()
