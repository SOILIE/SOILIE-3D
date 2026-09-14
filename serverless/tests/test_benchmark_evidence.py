from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.benchmark.run_batch import write_json
from serverless.benchmark.support_replays import merge_support
from serverless.benchmark.timing import record_session, session_summary, generation_breakdown
from serverless.benchmark.verify_parity import digest
from serverless.tests import test_benchmark_parity as parity_fixtures


class EvidenceTests(unittest.TestCase):
    def test_generation_timing_excludes_gaps_and_arbitrary_failure_cutoffs(self):
        rows = [{'status':'complete','startedAt':'2026-09-14T00:00:00Z','generationSeconds':10,'observationSeconds':3,
                 'collisionRecoveryActivated':True,'collisionRecoveryMoves':2},
                {'status':'failed','startedAt':'2026-09-14T12:00:00Z','generationSeconds':900,'errorCode':'TIMEOUT'},
                {'status':'failed','startedAt':'2026-09-14T15:00:00Z','generationSeconds':5}]
        result = generation_breakdown(rows)
        self.assertEqual(10,result['totalSeconds'])
        self.assertEqual(10,result['completedSeconds'])
        self.assertEqual(2,result['failedAttempts'])
        self.assertEqual({'TIMEOUT':1,'UNCLASSIFIED_FAILURE':1},result['failureCounts'])
        self.assertEqual(6,result['successfulLayoutsPerMinute'])
        self.assertEqual(3,result['excludedObservationSeconds'])
        self.assertEqual(1,result['collisionCyclesDetected'])
        self.assertEqual(2,result['collisionRecoveryMoves'])
        self.assertEqual(0,result['boundaryContainmentRepairs'])
        with self.assertRaises(ValueError):
            generation_breakdown([{'status':'running','generationSeconds':30}])

    def setUp(self):
        base = Path(__file__).parents[2]/'.codex/tests'
        base.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_support_adds_no_samples_or_timing_and_cannot_move_objects(self):
        row = dict(parity_fixtures.ParityTests().row(),id='example',generationSeconds=20)
        replay = deepcopy(row)
        replay['generationSeconds'] = 100
        replay['stages']['final']['objects'][0]['support'] = {'samplingVersion':2,'gapM':.01}
        write_json(self.root/'run.json',{'support':True,'roomFitIncluded':False,'provenance':{}})
        write_json(self.root/'attempt-00000.json',replay)
        result,evidence = merge_support([row],[self.root],{digest({})})
        self.assertEqual(1,len(result))
        self.assertEqual(20,result[0]['generationSeconds'])
        self.assertEqual(1,evidence['attemptsChecked'])
        self.assertEqual(.01,result[0]['stages']['final']['objects'][0]['support']['gapM'])
        self.assertNotIn('support',row['stages']['final']['objects'][0])
        replay['stages']['final']['objects'][0]['corners'][0][0] += .01
        write_json(self.root/'attempt-00000.json',replay)
        with self.assertRaises(ValueError):
            merge_support([row],[self.root],{digest({})})

    def test_runtime_or_duplicate_replay_is_rejected(self):
        row = dict(parity_fixtures.ParityTests().row(),id='example')
        write_json(self.root/'run.json',{'support':True,'roomFitIncluded':False,'provenance':{}})
        write_json(self.root/'attempt-00000.json',row)
        with self.assertRaises(ValueError):
            merge_support([row],[self.root],{'different'})
        with self.assertRaises(ValueError):
            merge_support([row],[self.root,self.root],{digest({})})

    def test_legacy_attempts_do_not_gain_invented_active_session_time(self):
        first = {'startedAt':'2026-09-13T00:00:00+00:00','generationSeconds':10,'wallSeconds':11}
        second = dict(first,startedAt='2026-09-13T01:00:00+00:00')
        write_json(self.root/'attempt-00000.json',first)
        with record_session(self.root,'test'):
            write_json(self.root/'attempt-00001.json',second)
        result = session_summary(self.root,[first,second])
        self.assertEqual(3611,result['calendarAttemptSpanSeconds'])
        self.assertFalse(result['completeSessionCoverage'])
        self.assertIsNone(result['activeSessionWallSeconds'])
        self.assertEqual(1,result['attemptsWithoutSessionTiming'])

    def test_complete_session_records_overhead_separately(self):
        row = {'startedAt':'2026-09-13T00:00:00+00:00','generationSeconds':10}
        with record_session(self.root,'test'):
            write_json(self.root/'attempt-00000.json',row)
        result = session_summary(self.root,[row])
        self.assertTrue(result['completeSessionCoverage'])
        self.assertGreater(result['activeSessionWallSeconds'],0)
        self.assertNotIn('hostname',result['hardwareSnapshots'][0])

    def test_missing_requested_run_cannot_disappear_from_publication(self):
        from serverless.benchmark.publish_comparison import main
        argv = ['publish_comparison', '--runs', str(self.root/'missing'),
                '--layoutgpt', 'unused.json', '--rates', 'unused.json',
                '--selection', 'unused.json', '--output', str(self.root/'output')]
        with patch('sys.argv', argv), self.assertRaises(FileNotFoundError):
            main()
