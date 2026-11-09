import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from serverless.cloud_benchmark import authoritative as a, continuation as c

VERIFY_PARITY = c.verify_parity
VERIFY_GIT_BRIDGE = c.validate_git_bridge

class LambdaContinuationTests(unittest.TestCase):
    def setUp(self):
        base = Path('.codex/tests/continuation')
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.parent_root = self.root / 'parent'; self.parent_root.mkdir()
        self.old_prov = self.root / 'old.json'; self.new_prov = self.root / 'new.json'
        self.old_prov.write_text(json.dumps({'files': {'model': {'sha256': 'model-sha'}}, 'baselineCommit': 'old'}))
        self.new_prov.write_text(json.dumps({'files': {'model': {'sha256': 'model-sha'}}, 'baselineCommit': 'new'}))
        self.old = {'provenanceSha256': a.file_digest(self.old_prov), 'sourceCommit': 'old',
                    'files': {'model': 'model-sha'}, 'version': '4', 'blenderSha256': 'blender',
                    'dependencies': {'numpy':'1'}, 'executionFiles': {'serverless/cloud_benchmark/authoritative.py':'old-controller', 'serverless/common/engine.py':'same'}}
        self.current = {**self.old, 'provenanceSha256': a.file_digest(self.new_prov), 'sourceCommit':'new',
                        'executionFiles': {'serverless/cloud_benchmark/authoritative.py':'new-controller',
                                           'serverless/cloud_benchmark/continuation.py':'new-module', 'serverless/common/engine.py':'same'}}
        self.parent = {'schemaVersion':1, 'platforms':['local'], 'phase':'authoritative', 'maxObjectCount':8,
                       'requests':a.allocation(platforms=('local',)), 'cloudParity':{'status':'deferred'},
                       'configuration':{'publicRunNamespace':'plan-sha256-v1', 'provenance':str(self.old_prov), 'runtimeSnapshot':self.old, 'sourceAuditSha256':'source-audit'},
                       'timingPolicy':'original timing', 'reviewPolicy':'original review'}
        self.parent['planSha256'] = a.digest(self.parent)
        (self.parent_root / 'plan.json').write_text(json.dumps(self.parent))
        self.rows = [{'requestId': t['id'], 'status':'complete', 'request': t['request']} for t in self.parent['requests']]
        self.completed = {t['id'] for t in self.parent['requests']}
        (self.parent_root / 'local-ledger.json').write_text(json.dumps({'planSha256':self.parent['planSha256'], 'attempts':self.rows}))
        self.bridge = self.root / 'bridge.json'
        self.bridge.write_text(json.dumps({'passed':True, 'parentPlanSha256':self.parent['planSha256'],
            'parentProvenanceSha256':self.old['provenanceSha256'], 'runtimeProvenanceSha256':self.current['provenanceSha256'],
            'parentSourceCommit':'old', 'sourceCommit':'new', 'executionChanges':{
              'serverless/cloud_benchmark/authoritative.py':{'from':'old-controller','to':'new-controller'},
              'serverless/cloud_benchmark/continuation.py':{'from':None,'to':'new-module'}}}))
        self.gates = {}
        for name, checks in c.GATES.items():
            path = self.root / (name + '.json'); path.write_text(json.dumps({'passed':True,
                'runtimeProvenanceSha256':self.current['provenanceSha256'], 'runtimeSnapshotSha256':a.digest(self.current),
                'sourceCommit':'new', 'function':'function:42', 'image':'sha256:test',
                'scenes':[{'roomType':r,'objectCount':n} for r in ('bedroom','living_room') for n in range(3,9)], 'checks':{k:True for k in checks}})); self.gates[name] = path
        rates = {'schemaVersion':1,'currency':'USD','region':'ca-central-1','architecture':'x86_64',
                 'sourceUrl':'https://aws.amazon.com/lambda/pricing/', 'retrievedAt':'2026-10-22T00:00:00Z',
                 'computeGBSecondUSD':.0000166667,'ephemeralGBSecondUSD':.0000000309,'requestUSD':.0000002}
        self.rates = self.root / 'rates.json'; self.rates.write_text(json.dumps(rates))
        self.budget = self.root / 'budget'; self.budget.mkdir()
        (self.budget / 'budget.json').write_text(json.dumps({'schemaVersion':1,'budgetUSD':25.0,'region':'ca-central-1','rateCard':rates,'attempts':[]}))
        self.args = SimpleNamespace(root=self.root/'continuation', parent=self.parent_root, runtime=self.root,
            provenance=self.new_prov, parent_provenance=self.old_prov, blender=self.root/'blender', bridge=self.bridge,
            unit=self.gates['unit'], blender_gate=self.gates['blender'], parity=self.gates['parity'], rates=self.rates,
            budget_root=self.budget, budget_anchor=self.root/'prior', source=self.gates['source'], function='function:42', image='sha256:test', bucket='bucket',
            archive_date='2026-11-09', region='ca-central-1', profile='test')
        prior = {'schemaVersion':1, 'phase':'authoritative', 'maxObjectCount':8,
                 'platforms':['local','lambda'], 'requests':a.allocation(),
                 'configuration':{'budgetRoot':str(self.budget), 'rateCard':rates, 'budgetUSD':25.0}}
        prior['planSha256'] = a.digest(prior)
        self.args.budget_anchor.mkdir()
        (self.args.budget_anchor / 'plan.json').write_text(json.dumps(prior))
        pin = patch.object(c, 'BUDGET_ANCHOR_SHA256', prior['planSha256']); pin.start(); self.addCleanup(pin.stop)
        count_pin = patch.object(c, 'HISTORICAL_ATTEMPT_COUNT', 0); count_pin.start(); self.addCleanup(count_pin.stop)
        history_pin = patch.object(c, 'HISTORICAL_ATTEMPTS_SHA256', a.digest([])); history_pin.start(); self.addCleanup(history_pin.stop)
        from serverless.cloud_benchmark.evidence_store import archive_configuration
        cfg = {'runtime':str(self.root), 'provenance':str(self.new_prov), 'blender':str(self.root/'blender'),
               'runtimeSnapshot':self.current, 'region':'ca-central-1', 'profile':'test', 'budgetRoot':str(self.budget),
               'rateCard':rates, 'budgetUSD':25.0, 'function':'function:42', 'image':'sha256:test', 'bucket':'bucket',
               'lambdaConcurrency':16, 'memoryMB':4096, 'ephemeralMB':1024, 'objectCountDefinition':a.OBJECT_COUNT_DEFINITION,
               'objectCountPolicy':a.OBJECT_COUNT_POLICY, 'outputArchive':archive_configuration('2026-11-09'),
               'publicRunNamespace':'plan-sha256-v1'}
        ledger = self.budget/'budget.json'
        parent_ref = {'root':str(self.parent_root), 'planSha256':self.parent['planSha256'],
                      'requestsSha256':a.digest(self.parent['requests']), 'ledgerSha256':a.file_digest(self.parent_root/'local-ledger.json')}
        anchor = {'root':str(self.args.budget_anchor), 'planSha256':prior['planSha256'],
                  'ledgerSnapshotSha256':a.file_digest(ledger), 'attemptCount':0, 'attemptsSha256':a.digest([])}
        binding = a.digest({'parent':parent_ref, 'budgetAnchor':anchor, 'configuration':cfg,
                            'timingPolicy':self.parent['timingPolicy'], 'reviewPolicy':self.parent['reviewPolicy']})
        for name, path in self.gates.items():
            doc = json.loads(path.read_text()); doc.update(continuationBindingSha256=binding, parentSourceAuditSha256='source-audit')
            path.write_text(json.dumps(doc))
        git_bridge = patch.object(c, 'validate_git_bridge'); git_bridge.start(); self.addCleanup(git_bridge.stop)
        parity_check = patch.object(c, 'verify_parity'); parity_check.start(); self.addCleanup(parity_check.stop)
        self.rows_patch = patch.object(a, 'attempt_rows', return_value=self.rows); self.rows_patch.start(); self.addCleanup(self.rows_patch.stop)
        self.verifier = patch.object(a, 'verify_completed', return_value=self.completed); self.verified = self.verifier.start(); self.addCleanup(self.verifier.stop)
        self.snapshot = patch.object(a, 'runtime_snapshot', return_value=self.current); self.snapshot.start(); self.addCleanup(self.snapshot.stop)

    def test_schedule_preserves_parent_and_uses_new_ordinals(self):
        tasks = c.allocation(self.parent)
        self.assertEqual(5000,len(tasks))
        self.assertFalse({t['request']['seed'] for t in tasks} & {t['request']['seed'] for t in self.parent['requests']})
        self.assertFalse({t['id'] for t in tasks} & self.completed)
        for i, task in enumerate(tasks):
            self.assertEqual(2_000_000_000 + 997 * (5000+i), task['request']['seed'])
            self.assertEqual(self.parent['requests'][i]['request']['objectCount'], task['request']['objectCount'])
        for room in ('bedroom','living_room'):
            self.assertEqual({3:417,4:417,5:417,6:417,7:416,8:416}, dict(Counter(t['request']['objectCount'] for t in tasks if t['request']['roomType']==room)))

    def test_freeze_and_dispatch_load_validate_parent_and_pins(self):
        original = (self.parent_root / 'plan.json').read_bytes()
        plan = c.freeze(self.args)
        self.assertEqual(plan, a.load_plan(self.args.root))
        self.assertEqual(original, (self.parent_root / 'plan.json').read_bytes())
        self.assertGreaterEqual(self.verified.call_count, 2)
        self.assertEqual(('lambda',), a.plan_platforms(plan))
        self.assertEqual(plan, c.freeze(self.args))
        self.assertEqual(str(self.budget), plan['configuration']['budgetRoot'])

    def test_bad_parent_qualification_bridge_or_budget_block_freeze(self):
        self.verified.return_value = set()
        with self.assertRaisesRegex(ValueError, '5000'): c.freeze(self.args)
        self.verified.return_value = self.completed
        parity_original = self.gates['parity'].read_text()
        self.gates['parity'].write_text('{}')
        with self.assertRaisesRegex(ValueError, 'parity'): c.freeze(self.args)
        self.gates['parity'].write_text(parity_original)
        self.old_prov.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Original provenance'): c.freeze(self.args)
        self.assertFalse((self.args.root/'plan.json').exists())

    def test_incomplete_unknown_and_missing_shared_budget_block_freeze(self):
        self.rows_patch.stop()
        try:
            with patch.object(a, 'attempt_rows', return_value=[{**self.rows[0], 'status':'unknown'}]):
                with self.assertRaisesRegex(ValueError, 'only completed'):
                    c.freeze(self.args)
        finally:
            self.rows_patch.start()
        (self.budget / 'budget.json').unlink()
        with self.assertRaisesRegex(ValueError, 'existing cumulative budget'):
            c.freeze(self.args)
        self.assertFalse((self.args.root / 'plan.json').exists())

    def test_unreviewed_execution_and_changed_model_block_bridge(self):
        bridge = a.read(self.bridge)
        bad = {**self.current, 'executionFiles':{**self.current['executionFiles'], 'serverless/common/engine.py':'changed'}}
        with self.assertRaisesRegex(ValueError, 'Execution changes'):
            c.validate_bridge(self.parent, bad, self.old_prov, self.new_prov, bridge, self.root)
        bad = {**self.current, 'files':{'model':'different'}}
        with self.assertRaisesRegex(ValueError, 'Model source'):
            c.validate_bridge(self.parent, bad, self.old_prov, self.new_prov, bridge, self.root)

    def test_only_the_observed_hardware_memory_change_is_allowed(self):
        parent = dict(self.parent)
        parent['configuration'] = dict(self.parent['configuration'])
        old = {**self.old, 'localHardware':{'cpu':'same','memoryBytes':12543291392}}
        parent['configuration']['runtimeSnapshot'] = old
        current = {**self.current, 'localHardware':{'cpu':'same','memoryBytes':12543283200}}
        bridge = a.read(self.bridge)
        c.validate_bridge(parent, current, self.old_prov, self.new_prov, bridge, self.root)
        for hardware in ({'cpu':'changed','memoryBytes':12543283200},
                         {'cpu':'same','memoryBytes':12543283199}):
            with self.subTest(hardware=hardware), self.assertRaisesRegex(ValueError, 'hardware differs'):
                c.validate_bridge(parent, {**current, 'localHardware':hardware},
                                  self.old_prov, self.new_prov, bridge, self.root)

    def test_git_bridge_accepts_only_exact_reviewed_menu_and_helper_blobs(self):
        outputs = [SimpleNamespace(stdout=b'imagine.py\0serverless/renderer/asset_smoke.py\0'),
                   SimpleNamespace(stdout=c.ORIGINAL_IMAGINE_BLOB),
                   SimpleNamespace(stdout=c.MENU_FIX_IMAGINE_BLOB),
                   SimpleNamespace(stdout='dcf573d83b136b98c00dc5879f3beabbfb96673f'),
                   SimpleNamespace(stdout='475be9bbf4230fe7930070c8ab8da8b8d62147b5')]
        with patch.object(c.subprocess, 'run', side_effect=outputs), \
             patch.object(c, 'git_blob_sha', side_effect=[c.MENU_FIX_IMAGINE_BLOB,
                                                          '475be9bbf4230fe7930070c8ab8da8b8d62147b5']):
            VERIFY_GIT_BRIDGE(self.root, 'old', 'new')
        with patch.object(c.subprocess, 'run', return_value=SimpleNamespace(stdout=b'imagine.py\0modules/render.py\0')):
            with self.assertRaisesRegex(ValueError, 'outside the reviewed'):
                VERIFY_GIT_BRIDGE(self.root, 'old', 'new')

    def test_budget_history_and_parent_ledger_tampering_block_load(self):
        c.freeze(self.args)
        (self.parent_root / 'local-ledger.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'parent or continuation allocation'):
            a.load_plan(self.args.root)

    def test_parity_verifies_each_original_and_retained_remote_artifact(self):
        chosen = {}
        for task in self.parent['requests']:
            key = (task['request']['roomType'], task['request']['objectCount'])
            chosen.setdefault(key, task)
        scenes, attempts = [], []
        config = {'function':'function:42', 'image':'sha256:test', 'runtimeSnapshot':self.current}
        parity_sha = c.parity_plan(self.parent, config, self.parent_root, a.file_digest(self.bridge))['planSha256']
        verification = self.root/'verification.json'
        verification.write_text(json.dumps({'function':'function:42','image':'sha256:test',
            'runtimeSnapshotSha256':a.digest(self.current),'parentPlanSha256':self.parent['planSha256'],
            'parityPlanSha256':parity_sha,'verifiedAt':'2026-11-09T00:00:00Z'}))
        (self.root/'receipt.json').write_text(json.dumps({'awsRequestId':'invocation'}))
        for task in chosen.values():
            request = task['request']
            scenes.append({'roomType':request['roomType'], 'objectCount':request['objectCount'],
                           'requestId':task['id'], 'seed':request['seed'], 'sha256':'artifact', 'maxGeometryErrorM':0.0})
            attempts.append({'role':'parity','status':'complete','request':request,'sha256':'artifact',
                             'requestId':'parity-'+task['id'], 'attempt':1,
                             'campaignRoot':str(self.root), 'function':'function:42', 'image':'sha256:test',
                             'planSha256':parity_sha, 'verification':str(verification),
                             'parentPlanSha256':self.parent['planSha256'],
                             'verificationSha256':a.file_digest(verification), 'receipt':'receipt.json', 'receiptSha256':'receipt-sha',
                             'invocationIdentity':'identity.json', 'invocationIdentitySha256':'identity-sha'})
        document = {'scenes':scenes, 'parityPlanSha256':parity_sha}
        plan = {'parent':{'root':str(self.parent_root)}, 'configuration':config,
                'bridge':{'sha256':a.file_digest(self.bridge)}}
        artifact = {'implementation':{'sourceCommit':'old'}}
        with patch.object(a, 'load_artifact', return_value=artifact) as load, \
             patch.object(a, 'validate_artifact') as validate, \
             patch.object(a, 'verify_attempt_evidence'), \
             patch.object(a, 'saved_invocation_identity', return_value={'awsRequestId':'invocation'}), \
             patch('serverless.cloud_benchmark.pilot.compare_placements', return_value=0.0):
            VERIFY_PARITY(document, self.parent, plan, {'attempts':attempts})
            self.assertEqual(24, load.call_count)
            self.assertEqual(12, validate.call_count)
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                VERIFY_PARITY({'scenes':scenes+[scenes[0]]}, self.parent, plan, {'attempts':attempts})
            with self.assertRaisesRegex(ValueError, 'retained completed'):
                VERIFY_PARITY(document, self.parent, plan, {'attempts':attempts[:-1]})
            wrong = [{**row} for row in attempts]
            wrong[0]['requestId'] = 'invented'
            with self.assertRaisesRegex(ValueError, 'retained completed'):
                VERIFY_PARITY(document, self.parent, plan, {'attempts':wrong})

    def test_continuation_never_retries_or_reseeds(self):
        plan = c.freeze(self.args)
        task = plan['requests'][0]
        failed = {'requestId':task['id'], 'status':'failed', 'request':task['request']}
        with self.assertRaises(ValueError):
            a.pending_tasks(plan, 'lambda', [failed])
        with self.assertRaisesRegex(ValueError, 'never retries'):
            a.pending_tasks(plan, 'lambda', [failed], [task['id']])
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            a.pending_tasks(plan, 'lambda', [{**failed, 'status':'unknown'}])

    def test_prefreeze_parity_reserves_once_and_uses_all_original_seeds(self):
        artifact = {'implementation': {'sourceCommit': 'old'}}
        def finish(_root, _plan, attempt):
            attempt.update(status='complete', sha256='fixture', costUSD=.01)
        with patch('boto3.Session') as session, \
             patch.object(a, 'verify_lambda') as verify, \
             patch('serverless.cloud_benchmark.pilot.invoke_and_download') as invoke, \
             patch('serverless.cloud_benchmark.pilot.compare_placements', return_value=0.0), \
             patch.object(a, 'finish_cloud', side_effect=finish), \
             patch.object(a, 'saved_invocation_identity', return_value={'awsRequestId':'id'}), \
             patch.object(a, 'load_artifact', return_value=artifact):
            first = c.prefreeze_parity(self.args)
            self.assertEqual(12, invoke.call_count)
            self.assertEqual(12, len(first['scenes']))
            self.assertEqual({task['request']['seed'] for task in c.parity_tasks(self.parent)},
                             {scene['seed'] for scene in first['scenes']})
            self.assertEqual(first, c.prefreeze_parity(self.args))
            self.assertEqual(12, invoke.call_count)
            self.assertEqual(2, verify.call_count)
            self.assertEqual(2, session.call_count)
            self.args.root = self.root/'different-parity-root'
            self.args.function = 'different-function:43'
            with self.assertRaisesRegex(ValueError, 'another plan'):
                c.prefreeze_parity(self.args)
            self.assertEqual(12, invoke.call_count)
        ledger = json.loads((self.budget/'budget.json').read_text())
        self.assertEqual(12, len(ledger['attempts']))
        self.assertTrue(all(row['role'] == 'parity' and row['status'] == 'complete'
                            for row in ledger['attempts']))

    def test_prefreeze_parity_refuses_uncertain_history_before_cloud(self):
        path = self.budget/'budget.json'
        budget = json.loads(path.read_text())
        budget['attempts'].append({'status':'unknown', 'reservedUSD':.1})
        path.write_text(json.dumps(budget))
        with patch('boto3.Session') as session, self.assertRaisesRegex(ValueError, 'uncertain'):
            c.prefreeze_parity(self.args)
        session.assert_not_called()

    def test_uncertain_parity_is_reconciled_without_a_second_invocation(self):
        with patch('boto3.Session'), patch.object(a, 'verify_lambda'), \
             patch('serverless.cloud_benchmark.pilot.invoke_and_download', side_effect=OSError('interrupted')) as invoke:
            with self.assertRaisesRegex(ValueError, 'uncertain invocation'):
                c.prefreeze_parity(self.args)
            self.assertEqual(1, invoke.call_count)
        def finish(_root, _plan, attempt):
            attempt.update(status='complete', costUSD=.01)
        with patch.object(a, 'finish_cloud', side_effect=finish), \
             patch('serverless.cloud_benchmark.pilot.invoke_and_download') as invoke, \
             patch('boto3.Session') as session:
            c.reconcile_parity(self.args)
            invoke.assert_not_called()
            session.assert_not_called()
        budget = json.loads((self.budget/'budget.json').read_text())
        self.assertEqual(1, len(budget['attempts']))
        self.assertEqual('complete', budget['attempts'][0]['status'])

    def test_qualification_derives_receipts_from_verified_inputs(self):
        raw = self.root/'parity-raw.json'
        raw.write_text(json.dumps({'passed':True, 'parentPlanSha256':self.parent['planSha256'],
                                   'function':'function:42', 'image':'sha256:test',
                                   'scenes':[{'roomType':r, 'objectCount':n}
                                             for r in ('bedroom','living_room') for n in range(3,9)]}))
        self.args.parity = raw
        self.args.qualification_dir = self.root/'qualification'
        with patch('boto3.Session') as session:
            paths = c.qualify(self.args)
        session.assert_not_called()
        self.assertEqual({'unit','blender','source','parity'}, set(paths))
        self.assertTrue(json.loads(Path(paths['parity']).read_text())['checks']['localLambdaParity'])
        self.args.unit = Path(paths['unit'])
        self.args.blender_gate = Path(paths['blender'])
        self.args.source = Path(paths['source'])
        self.args.parity = Path(paths['parity'])
        self.assertEqual('lambda-continuation-v1', c.freeze(self.args)['kind'])

    def test_interrupted_publication_never_exposes_partial_root(self):
        with patch('os.rename', side_effect=OSError('interrupted')):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                c.freeze(self.args)
        self.assertFalse(self.args.root.exists())
        self.assertEqual([], list(self.root.glob('.continuation-stage-*')))

    def test_export_keeps_two_plan_and_source_identities(self):
        local_task = self.parent['requests'][0]
        lambda_task = c.allocation(self.parent)[0]
        parent = {**self.parent, 'requests':[local_task]}
        plan = {'phase':'authoritative', 'planSha256':'lambda-plan', 'requests':[lambda_task],
                'parent':{'root':str(self.parent_root)}, 'configuration':{'runtimeSnapshot':self.current},
                'qualification':{}, 'bridge':{}, 'budgetAnchor':{}, 'timingPolicy':'timing'}
        local_row = {'requestId':local_task['id'], 'status':'complete', 'sha256':'one', 'artifact':'one.json'}
        lambda_row = {'requestId':lambda_task['id'], 'status':'complete', 'sha256':'two', 'artifact':'two.json'}
        output = self.root/'export.json'
        with patch.object(c, 'verified_parent', return_value=parent), \
             patch.object(a, 'campaign_status', return_value={'complete':True}), \
             patch.object(a, 'attempt_rows', side_effect=[[local_row],[lambda_row]]), \
             patch.object(a, 'verify_completed', side_effect=[{local_task['id']},{lambda_task['id']}]), \
             patch.object(a, 'load_artifact', return_value={'scene':'fixture'}), \
             patch.object(a, 'budget_document', return_value={'sessions':[]}), \
             patch.object(a, 'generation_date', return_value='2026-11-09'):
            result = c.export_evidence(self.root, plan, output)
        self.assertEqual(2, result['scenes'])
        document = json.loads(output.read_text())
        self.assertEqual([self.parent['planSha256'], 'lambda-plan'], [x['planSha256'] for x in document['sources']])
        self.assertEqual(['old','new'], [x['runtimeSnapshot']['sourceCommit'] for x in document['sources']])
        self.assertEqual(['local','lambda'], [x['platform'] for x in document['scenes']])

    def test_tamper_is_detected_even_after_rehash(self):
        plan = c.freeze(self.args)
        plan['requests'][0]['request']['seed'] += 1
        plan['planSha256'] = a.digest({k:v for k,v in plan.items() if k != 'planSha256'})
        (self.args.root/'plan.json').write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, 'allocation changed'): a.load_plan(self.args.root)
