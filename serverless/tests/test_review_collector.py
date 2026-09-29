"""Operational fixtures only: no model calls or research judgments."""
from copy import deepcopy
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from serverless.cloud_benchmark import run_full_review as collector
from serverless.cloud_benchmark.staged_pilot import digest, read, write_new


class CollectorTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_locked_progress_is_nonfatal_and_later_recovers(self):
        state = {'completed': 18}
        with patch.object(collector.os, 'replace', side_effect=PermissionError('reader lock')), \
             patch.object(collector.time, 'sleep'), patch('sys.stderr'):
            self.assertFalse(collector.progress(self.root, state))
        self.assertEqual(1, state['heartbeatWriteFailures'])
        self.assertTrue(collector.progress(self.root, state))
        self.assertEqual(state, read(self.root/'progress.json'))

    def test_pending_write_error_cannot_stop_collection(self):
        with patch.object(Path, 'write_text', side_effect=PermissionError('pending lock')), \
             patch.object(collector.time, 'sleep'), patch('sys.stderr'):
            self.assertFalse(collector.progress(self.root, {}))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows sharing semantics')
    def test_real_windows_reader_lock_preserves_snapshot_and_recovers(self):
        import ctypes
        from ctypes import wintypes
        native = ctypes.WinDLL('kernel32', use_last_error=True)
        native.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        native.CreateFileW.restype = wintypes.HANDLE
        native.CloseHandle.argtypes = [wintypes.HANDLE]
        collector.progress(self.root, {'completed':18})
        # Reproduce a reader that permits reads/writes but not file replacement.
        handle = native.CreateFileW(str(self.root/'progress.json'), 0x80000000, 3, None, 3, 0, None)
        self.assertNotEqual(ctypes.c_void_p(-1).value, handle)
        try:
            with patch('sys.stderr'):
                self.assertFalse(collector.progress(self.root, {'completed':19}))
            self.assertEqual(18, read(self.root/'progress.json')['completed'])
        finally:
            native.CloseHandle(handle)
        self.assertTrue(collector.progress(self.root, {'completed':19}))
        self.assertEqual(19, read(self.root/'progress.json')['completed'])

    def test_dimension_counts_resume_without_counting_paused_time(self):
        rows = [{'assignmentId': str(i), 'profile': key}
                for i, key in enumerate(collector.DIMENSIONS)]
        state = {'completed': 1, 'expected': 5,
                 'dimensions': collector.dimension_progress({'assignments': rows}, {'1','2','3','4'})}
        collector.refresh_progress(state, rows[1:], 120)
        self.assertEqual(1, state['dimensions'][0]['completed'])
        self.assertEqual(0, state['dimensions'][0]['etaSeconds'])
        self.assertIsNone(state['etaSeconds'])  # Prior session must not seed the rate.
        state['dimensions'][1].update(expected=10, completed=3, sessionCompleted=3)
        state.update(expected=14, completed=4)
        collector.refresh_progress(state, rows[1:3], 60)
        self.assertEqual(200, state['etaSeconds'])
        self.assertEqual(140, state['dimensions'][1]['etaSeconds'])
        self.assertEqual(2, state['active'])

    def launch(self):
        return {'protocolSha256':'protocol', 'model':'gpt-5.6-sol', 'reasoningEffort':'xhigh',
                'sourceCommit':'original', 'codeSha256':{collector.RUNNER_PATH:'old',
                 'delivery.py':'frozen', 'validation.py':'frozen'}}

    def test_explicit_operational_upgrade_keeps_original_provenance(self):
        original = self.launch()
        collector.validate_launch(self.root, original)
        raw = (self.root/'collector-provenance.json').read_bytes()
        changed = deepcopy(original)
        changed.update(sourceCommit='after-attribution-migration')
        changed['codeSha256'][collector.RUNNER_PATH] = 'heartbeat-and-workers'
        with self.assertRaisesRegex(ValueError, '--resume-note'):
            collector.validate_launch(self.root, changed)
        collector.validate_launch(self.root, changed, 'Approved heartbeat repair and five workers')
        collector.validate_launch(self.root, changed)  # Reuses exact immutable approval.
        self.assertEqual(raw, (self.root/'collector-provenance.json').read_bytes())
        self.assertEqual(1, len(list((self.root/'collector-upgrades').glob('*.json'))))

    def test_resume_note_cannot_authorize_method_or_delivery_changes(self):
        original = self.launch()
        collector.validate_launch(self.root, original)
        for field in ('protocolSha256','model','reasoningEffort'):
            bad = deepcopy(original)
            bad[field] = 'changed'
            with self.assertRaises(ValueError):
                collector.validate_launch(self.root, bad, 'Not permission to change method')
        bad = deepcopy(original)
        bad['codeSha256']['delivery.py'] = 'changed'
        with self.assertRaises(ValueError):
            collector.validate_launch(self.root, bad, 'Not permission to change delivery')

    def exercise_collection(self, fail=False):
        keys = list(collector.DIMENSIONS)
        rows = [{'assignmentId':str(i), 'profile':keys[i % 5]} for i in range(11)]
        protocol = {'assignments':rows, 'stage':'full_counterbalanced', 'fullCampaignAuthorized':True,
                    'model':'gpt-5.6-sol','reasoningEffort':'xhigh'}
        write_new(self.root/'diagram-audit.json', {'passed':True, 'protocolSha256':digest(protocol)})
        # Saved answer zero must not be scheduled again. Ignore preferences.
        saved, invoked = {'0'}, []
        mutex = threading.Lock()
        occupied = 0
        peak = 0
        first_wave = threading.Barrier(5)
        def report(_):
            return {'responses':len(saved), 'expected':len(rows), 'complete':len(saved)==len(rows),
                    'remainingAssignmentIds':[r['assignmentId'] for r in rows if r['assignmentId'] not in saved]}
        def fake_review(root, manifest, row, executable, stop):
            nonlocal occupied, peak
            with mutex:
                invoked.append(row['assignmentId'])
                occupied += 1
                peak = max(peak, occupied)
                wave_one = len(invoked) <= 5
            if wave_one:
                first_wave.wait(timeout=10)
            try:
                if fail and row['assignmentId'] == '1':
                    raise ValueError('Synthetic invalid response')
                time.sleep(.03)
                with mutex:
                    saved.add(row['assignmentId'])
                return row['assignmentId']
            finally:
                with mutex:
                    occupied -= 1
        with patch.object(collector,'load_frozen',return_value=protocol), \
             patch.object(collector,'preflight'), patch.object(collector,'results',side_effect=report), \
             patch.object(collector,'run_one',side_effect=fake_review):
            if fail:
                with self.assertRaises(RuntimeError):
                    collector.run(self.root,sys.executable,workers=5)
            else:
                collector.run(self.root,sys.executable,workers=5)
        self.assertEqual(5, peak)
        self.assertNotIn('0', invoked)
        self.assertEqual(len(invoked),len(set(invoked)))
        self.assertFalse((self.root/'collection.lock').exists())
        state = read(self.root/'progress.json')
        self.assertEqual(len(saved),state['completed'])
        self.assertEqual(len(saved),sum(g['completed'] for g in state['dimensions']))
        self.assertEqual(0,state['active'])
        self.assertEqual('needs_attention' if fail else 'complete',state['state'])
        if fail:
            self.assertEqual(5,len(invoked))  # Drain in-flight answers; launch no replacements.
            self.assertFalse((self.root/'full-results.json').exists())
        return state

    def test_five_workers_skip_existing_answers_and_finish(self):
        self.exercise_collection()

    def test_failed_answer_drains_workers_without_resubmitting(self):
        self.exercise_collection(fail=True)

    def test_invalid_worker_count_rejected_before_lock(self):
        for count in (0,6):
            with self.assertRaises(ValueError):
                collector.run(self.root,sys.executable,workers=count)
        self.assertFalse((self.root/'collection.lock').exists())

    @unittest.skipUnless(shutil.which('powershell'), 'Windows monitor test')
    def test_monitor_draws_six_real_progress_bars_and_scales_fill(self):
        state = self.exercise_collection()
        state['state'] = 'running'
        state.update(completed=2400, expected=4800)
        for group in state['dimensions']:
            group.update(completed=480, expected=960)
        collector.progress(self.root,state)
        script = Path(__file__).parents[2]/'scripts/watch-ai-reviews.ps1'
        command = ['powershell','-NoProfile','-File',str(script),'-Root',str(self.root),'-Once']
        for width in (48,80,120):
            output = subprocess.check_output(command+['-Color','Never','-Width',str(width)],encoding='utf-8')
            self.assertNotIn('\x1b',output)
            bars = re.findall(r'\[([\u2588-\u2591]+)\]',output)
            self.assertEqual(6,len(bars))
            self.assertEqual(1,len({len(bar) for bar in bars[1:]}))
            for bar in bars:
                self.assertEqual(len(bar)//2,bar.count('\u2588'))
            self.assertEqual(6,output.count('50.0%'))
            self.assertLessEqual(max(map(len,output.splitlines())),width)
            if width >= 80:
                for label in collector.DIMENSIONS.values():
                    self.assertIn(label,output)
        colored = subprocess.check_output(command+['-Color','Always'],encoding='utf-8')
        self.assertIn('\x1b[1;38;5;',colored)
        self.assertEqual(6,len(re.findall(r'\[([\u2588-\u2591]+)\]', re.sub(r'\x1b\[[0-9;]*m','',colored))))
        compact_command = ". '%s' -Root '%s' -Once -Color Never > $null; New-ReviewDashboard $state 76 '' 12" % (script,self.root)
        compact = subprocess.check_output(['powershell','-NoProfile','-Command',compact_command],encoding='utf-8')
        # Dot-sourcing emits the initial Console frame too; inspect the final 8 lines.
        compact_lines = compact.splitlines()[-8:]
        self.assertEqual(6,len(re.findall(r'\[([\u2588-\u2591]+)\]', '\n'.join(compact_lines))))
        self.assertLessEqual(max(map(len,compact_lines)),76)

    @unittest.skipUnless(shutil.which('powershell'), 'Windows monitor test')
    def test_monitor_handles_missing_status_and_zero_progress(self):
        script = Path(__file__).parents[2]/'scripts/watch-ai-reviews.ps1'
        output = subprocess.check_output(['powershell','-NoProfile','-File',str(script),
            '-Root',str(self.root),'-Once','-Color','Never'],encoding='utf-8')
        self.assertIn('WAITING FOR COLLECTOR',output)
        bars = re.findall(r'\[([\u2588-\u2591]+)\]',output)
        self.assertEqual(6,len(bars))
        self.assertTrue(all(set(bar)=={'\u2591'} for bar in bars))


if __name__ == '__main__':
    unittest.main()
