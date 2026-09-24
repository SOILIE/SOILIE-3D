from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.cloud_benchmark.handler import request_document
from serverless.cloud_benchmark.pilot import compare_placements
from serverless.cloud_benchmark.campaign import compute_cost, prepare_retries, estimated_spend
from serverless.cloud_benchmark.design import allocation, freeze
from serverless.cloud_benchmark.local import missing_ranges
from serverless.cloud_benchmark.cleanup import validate_downloads
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.benchmark.balanced_campaign import living_shards


class CloudBenchmarkTests(unittest.TestCase):
    def test_contact_remeasurement_cannot_change_geometry_or_timing(self):
        from serverless.benchmark.reobserve_contacts import audit_observation
        original={'generationSeconds':5,'stages':{'final':{'objects':[{'id':'chair',
            'corners':[[1,2,3]],'support':{'gapM':.08}}]}}}
        measured=deepcopy(original)
        measured['stages']['final']['objects'][0]['support']={'gapM':.000005}
        measured['contactObservation']={'sourceSha256':'fixed'}
        audit_observation(original,measured)
        measured['stages']['final']['objects'][0]['corners'][0][2]=2.92
        with self.assertRaisesRegex(ValueError,'Non-observation'):
            audit_observation(original,measured)

    def test_checkpoint_retries_rename_without_rewriting_or_dispatching(self):
        base=Path(__file__).resolve().parents[2]/'.codex/tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            path=Path(folder)/'ledger.json'
            actual=Path.replace
            calls=[]
            def replace(source,target):
                calls.append(source)
                if len(calls)==1:
                    raise PermissionError('reader temporarily holds Windows file')
                return actual(source,target)
            with patch.object(Path,'replace',replace), patch('serverless.cloud_benchmark.checkpoint.time.sleep'):
                write_json(path,{'safe':True})
            self.assertEqual({'safe':True},json.loads(path.read_text()))
            self.assertEqual(2,len(calls))
    def test_budget_reserves_full_watchdog_not_only_expected_duration(self):
        reserve=compute_cost(915,4096)
        self.assertGreater(reserve,.06)
        slots=int(24/reserve)
        self.assertLess(slots,500)
        self.assertLessEqual(slots*reserve+1,25)
        self.assertGreater((slots+1)*reserve+1,25)
        self.assertAlmostEqual(compute_cost(30,4096),.002030467,places=8)

    def test_four_condition_allocation_is_exact_balanced_disjoint_and_fixed(self):
        from collections import Counter
        document={'livingShards':living_shards(), 'livingRoomCount':5000, 'bedroomCount':5000}
        local, cloud=allocation(document)
        self.assertEqual(2500,sum(row['target'] for row in local))
        counts=Counter((row['roomType'],row['objectCount']) for row in cloud)
        self.assertEqual({(room,count):625 for room in ('bedroom','living_room') for count in range(3,7)},dict(counts))
        local_seeds={row['seed']+index*row['seedStep'] for row in local for index in range(row['target'])}
        self.assertFalse(local_seeds & {row['seed'] for row in cloud})
        self.assertEqual((local,cloud),allocation(document))
        shard={'seed':100,'seedStep':10,'target':6}
        self.assertEqual([(1,2),(4,6)],missing_ranges(shard,{100,120,130}))
        self.assertEqual([],missing_ranges(shard,{100,110,120,130,140,150}))

    def test_retry_archives_failed_evidence_and_never_drops_paid_cost(self):
        import hashlib
        base=Path(__file__).resolve().parents[2]/'.codex/tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            output=Path(folder); (output/'downloads').mkdir()
            raw=b'{"status":"failed"}'
            (output/'downloads/1.json').write_bytes(raw)
            (output/'downloads/1-receipt.json').write_text('{}')
            ledger={'setupAllowanceUSD':1,'pilotEstimatedUSD':.1,'entries':{
                '1':{'status':'failed','costUSD':.06,'sha256':hashlib.sha256(raw).hexdigest(),'task':{'seed':1}},
                '2':{'status':'complete','costUSD':.01,'task':{'seed':2}}}}
            before=estimated_spend(ledger,.061)
            for seed in (2,3):
                with self.assertRaisesRegex(ValueError,'reconciled failed'):
                    prepare_retries(ledger,output,[seed])
            prepare_retries(ledger,output,[1])
            self.assertEqual('retry_ready',ledger['entries']['1']['status'])
            self.assertEqual(before,estimated_spend(ledger,.061))
            self.assertEqual(raw,next((output/'retry-history').glob('1/*/1.json')).read_bytes())
            ledger['entries']['1'].update(status='complete',costUSD=.002)
            self.assertAlmostEqual(before+.002,estimated_spend(ledger,.061))
            ledger['entries']['3']={'status':'unknown','task':{'seed':3}}
            with self.assertRaisesRegex(ValueError,'ambiguous'):
                prepare_retries(ledger,output,[])

    def test_frozen_allocation_pins_sources_and_rejects_local_cloud_conflicts(self):
        base=Path(__file__).resolve().parents[2]/'.codex/tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            campaign=Path(folder)/'campaign'; output=Path(folder)/'cloud'
            campaign.mkdir(); output.mkdir()
            shards=living_shards()
            source=campaign/'bedroom-source'; source.mkdir()
            import hashlib
            selections=[]
            for index in range(2500):
                name=f'attempt-{index:05d}.json'
                (source/name).write_text('{}')
                selections.append({'file':name,'sha256':hashlib.sha256(b'{}').hexdigest()})
            (campaign/'campaign.json').write_text(json.dumps({'livingShards':shards,'livingRoomCount':5000,
                'bedroomCount':5000, 'bedroomSelection':selections}))
            for shard in shards[:2]:
                target=campaign/f"living-{shard['index']:02d}"
                target.mkdir()
                row={'status':'complete','attempt':0,'id':str(shard['seed']),
                     'request':{'seed':shard['seed'],'objectCount':shard['objectCount']}}
                (target/'attempt-00000.json').write_text(json.dumps(row))
            plan=freeze(campaign,output)
            self.assertEqual(5000,len(plan['requests']))
            self.assertEqual(5000,len({row['seed'] for row in plan['requests']}))
            self.assertEqual(plan,freeze(campaign,output))
            conflict=campaign/'living-12'; conflict.mkdir()
            (conflict/'attempt-00000.json').write_text(json.dumps({'status':'complete','attempt':0}))
            with self.assertRaisesRegex(ValueError,'allocation review'):
                freeze(campaign,output)
            (conflict/'attempt-00000.json').unlink()
            (source/'attempt-00000.json').write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'changed'):
                freeze(campaign,output)

    def test_cleanup_cannot_delete_partial_evidence(self):
        base=Path(__file__).resolve().parents[2]/'.codex/tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            output=Path(folder)
            for name,document in [('completion',{'complete':False}),('ledger',{'entries':{}}),('plan',{'requests':[]})]:
                (output/(name+'.json')).write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError,'complete'):
                validate_downloads(output)
    def test_only_bounded_bedroom_and_living_requests_can_run(self):
        for room in ('bedroom','living_room'):
            self.assertTrue(request_document({'seed': 4, 'objectCount': 6, 'roomType':room})['allowDuplicates'])
        for row in ({'seed': True, 'objectCount': 3}, {'seed': -1, 'objectCount': 4},
                    {'seed': 1, 'objectCount': 7}, {'seed': 1, 'objectCount': 3, 'mode': 'objects'}):
            with self.assertRaises(ValueError):
                request_document({**row, 'roomType':'living_room'})
        with self.assertRaises(ValueError):
            request_document({'seed':4,'objectCount':3,'roomType':'kitchen'})

    def test_parity_never_compares_timings_or_ignores_changed_placements(self):
        obj = {'id': 'chair', 'label': 'chair', 'asset': 'chair_1', 'kind': 'furniture',
               'corners': [[0, 0, 0], [1, 1, 1]], 'transform': [[1,0,0,0]], 'frontDirection': [1,0,0]}
        stage = {'room': {'polygon': [[0,0],[2,0],[2,2],[0,2]], 'floorZ': 0}, 'objects': [obj]}
        local = {'status': 'complete', 'request': {'seed': 1}, 'selection': ['chair'],
                 'implementation': {'sourceCommit': 'fixed'}, 'generationSeconds': 3,
                 'stages': {name: deepcopy(stage) for name in ('beforeSeparation','afterSeparation','final')}}
        remote = deepcopy(local)
        remote['generationSeconds'] = 9
        self.assertEqual(0, compare_placements(local, remote))
        for change in ('geometry', 'selection', 'implementation'):
            different = deepcopy(remote)
            if change == 'geometry':
                different['stages']['final']['objects'][0]['corners'][0][0] = .01
            else:
                different[change] = 'different'
            with self.assertRaises(ValueError):
                compare_placements(local, different)


if __name__ == '__main__':
    unittest.main()
