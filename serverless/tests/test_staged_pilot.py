"""Small synthetic fixtures only; never manufacture research judgments."""
from collections import Counter
from copy import deepcopy
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.cloud_benchmark.staged_pilot import (
    PROFILES, STRATA, VERSION, assignments, digest, load_frozen, record, results,
    select_pairs, validate_answer, write_new, finalize, continuation_inventory, checkpoint)
from serverless.cloud_benchmark.run_staged_pilot import command, parse_events, verify_panel_pixels, recover_completed
from serverless.study.clarified_rubric import prompt
from serverless.benchmark.geometry import box_corners
from serverless.benchmark.stimuli import diagram
from serverless.study.service import review_instructions, StudyService
from serverless.study.store import SQLiteStudyStore


def cases(count=12):
    return [{'pairId': f'{baseline}:{room}:{i}', 'baseline': baseline, 'roomType': room,
             'title': room, 'relationImage': '/a.svg', 'comparisonImage': '/b.svg',
             'profileImages': {'proportions': {'relationImage': '/va.svg', 'comparisonImage': '/vb.svg'}}}
            for baseline, room in STRATA for i in range(count)]


class StagedPilotTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_selection_is_balanced_deterministic_and_excludes_all_calibration_cases(self):
        source = cases()
        excluded = {row['pairId'] for row in source[::12]}
        selected = select_pairs(source, excluded)
        self.assertEqual(selected, select_pairs(list(reversed(source)), excluded))
        self.assertEqual({stratum: 8 for stratum in STRATA}, Counter((r['baseline'], r['roomType']) for r in selected))
        self.assertFalse(excluded.intersection(row['pairId'] for row in selected))
        with self.assertRaises(ValueError):
            select_pairs(source[:4], set())

    def test_every_stream_has_balanced_sides_and_eight_correct_reversals(self):
        selected = select_pairs(cases(), set())
        for i, profile in enumerate(PROFILES * 2):
            rows = assignments(selected, f'r{i}', profile)
            self.assertEqual(40, len(rows))
            originals = {r['assignmentId']: r for r in rows if not r['repeatOf']}
            self.assertEqual(32, len(originals))
            for stratum in STRATA:
                group = [r for r in originals.values() if (r['baseline'], r['roomType']) == stratum]
                self.assertEqual(4, sum(r['leftCondition'] == 'soilie' for r in group))
                self.assertEqual(2, sum(bool(r['repeatOf']) and (r['baseline'], r['roomType']) == stratum for r in rows))
            for row in rows:
                self.assertEqual('/v' in row['leftSource'], profile == 'proportions')
                if row['repeatOf']:
                    first = originals[row['repeatOf']]
                    self.assertEqual(first['leftSource'], row['rightSource'])
                    self.assertEqual(first['rightCondition'], row['leftCondition'])

    def test_agreed_furniture_rules_and_immutable_original_room_function(self):
        document = {'decisionScope': 'focus_only', 'evidenceMode': 'visual_only'}
        old = review_instructions(document, 'room_function')
        for profile in PROFILES:
            new = review_instructions({**document, 'rubricVersion': VERSION}, profile)
            self.assertEqual(new, prompt(profile))
            self.assertIn('clear, meaningful advantage', new)
            self.assertIn('tucked', new)
        access = prompt('access')
        for text in ('Foot-end-only access is acceptable', 'complete accessible long side', 'without moving other furniture',
                     'full front', 'nightstands need front access', 'TV stand alone', 'Decorative items'):
            self.assertIn(text, access)
        self.assertIn('coffee table between a correctly aligned sofa and TV is normal', prompt('relationships'))
        self.assertIn('not automatically implausible', prompt('proportions'))
        self.assertEqual(old, review_instructions(document, 'room_function'))
        with self.assertRaises(ValueError):
            review_instructions({**document, 'rubricVersion': VERSION}, 'room_function')

    def test_actual_volume_table_is_scale_invariant(self):
        # Exercise the actual evidence renderer, not an unused ratio helper.
        # This verifies numerical evidence, not empirical LLM invariance.
        scene = {'model': 'fixture', 'room': {'polygon': [[0, 0], [6, 0], [6, 6], [0, 6]], 'floorZ': 0},
                 'objects': [{'id': label, 'label': label, 'corners': box_corners(center, size)}
                             for label, center, size in [('chair', [1, 1, 1], [1, 1, 2]),
                                                         ('desk', [3, 3, 1], [2, 1, 2])]]}
        table = lambda value: re.findall(r'<text x="24" y="10(?:22|40)">(.*?)</text>', diagram(value, show_volumes=True, show_fronts=False))
        expected = table(scene)
        self.assertTrue(expected)
        for factor in (.01, 4, 25):
            other = deepcopy(scene)
            other['room']['polygon'] = [[v * factor for v in p] for p in other['room']['polygon']]
            for item in other['objects']:
                item['corners'] = [[v * factor for v in p] for p in item['corners']]
            self.assertEqual(expected, table(other))

    def test_versioned_service_session_survives_reload(self):
        document = {'studyVersion': 'fixture', 'evidenceMode': 'visual_only', 'decisionScope': 'focus_only',
                    'rubricVersion': VERSION, 'pilotCollectionEnabled': True, 'reviewerPlan': list(PROFILES),
                    'cases': [{'id': str(i), 'title': 'Room', 'comparisonCondition': 'baseline',
                               'relationImage': '/a.svg', 'comparisonImage': '/b.svg'} for i in range(4)]}
        store = SQLiteStudyStore(self.root / 'study.sqlite3')
        service = StudyService(document, store, b'fixture', enabled=True)
        session = service.start({'invitation': service.invite('test', 'access', 'test-model')})
        self.assertEqual(prompt('access'), session['rubric'])
        self.assertEqual(VERSION, store.get(session['sessionId'])['rubricVersion'])
        self.assertEqual(session['rubric'], service.resume(session['sessionId'], session)['rubric'])

    def frozen(self):
        full = cases(120)
        selected = select_pairs(full, set())
        rows, reviewers = [], []
        for i, profile in enumerate(p for p in PROFILES for _ in range(2)):
            reviewer = f'reviewer-{i + 1:02}'
            rows += assignments(selected, reviewer, profile)
            reviewers.append({'reviewerId': reviewer, 'profile': profile, 'promptHash': 'prompt', 'systemPromptHash': 'system'})
        value = {'assignments': rows, 'reviewers': reviewers, 'retainedRoomFunctionSha256': digest([]),
                 'model': 'gpt-5.6-sol', 'reasoningEffort': 'xhigh', 'seed': 'fixture',
                 'sourceProtocolHashes': {'fixture': 'fixture-only'},
                 'fullPairIds': [row['pairId'] for row in full], 'selectedPairs': [row['pairId'] for row in selected],
                 'fullPairSetSha256': digest(sorted(row['pairId'] for row in full)), 'excludedCalibrationPairs': []}
        write_new(self.root / 'protocol.json', value)
        write_new(self.root / 'protocol-sha256.json', {'sha256': digest(value)})
        write_new(self.root / 'private/retained-room-function.json', [])
        write_new(self.root / 'packets/index.json', {r['assignmentId']: {'imageSha256': 'image'} for r in rows})
        return rows

    def save(self, row, choice='tie'):
        record(self.root, row['assignmentId'], {'judgement': choice, 'errorChoice': 'neither', 'confidence': 3, 'note': 'Test fixture.'},
               {'model': 'gpt-5.6-sol', 'reasoningEffort': 'xhigh', 'promptHash': 'prompt', 'systemPromptHash': 'system',
                'imageSha256': 'image', 'isolatedContext': True, 'threadId': row['assignmentId']})

    def test_complete_controls_threshold_and_missing_only_resume(self):
        rows = self.frozen()
        for row in rows[:3]:
            self.save(row)
        summary = results(self.root)
        self.assertFalse(summary['pilotAccepted'])
        self.assertEqual(317, len(summary['remainingAssignmentIds']))
        failures = Counter()
        for row in rows[3:]:
            # One inconsistent control per dimension still yields 15/16.
            choice = 'tie'
            if row['repeatOf'] and failures[row['profile']] == 0:
                choice = 'left'
                failures[row['profile']] += 1
            self.save(row, choice)
        summary = results(self.root)
        self.assertTrue(summary['pilotAccepted'])
        self.assertFalse(summary['releaseEligible'])
        self.assertEqual(256, summary['mainJudgements'])
        self.assertEqual(60, summary['agreements'])
        self.assertEqual(64, summary['comparisons'])
        with self.assertRaises(FileExistsError):
            self.save(rows[0])

    def test_low_dimension_cannot_be_hidden_by_other_dimensions(self):
        rows = self.frozen()
        failures = 0
        for row in rows:
            choice = 'tie'
            if row['repeatOf'] and row['profile'] == 'relationships' and failures < 2:
                choice = 'left'
                failures += 1
            self.save(row, choice)
        summary = results(self.root)
        self.assertEqual(62, summary['agreements'])
        self.assertFalse(summary['pilotAccepted'])

    def test_prompt_or_retained_evidence_tampering_is_rejected(self):
        self.frozen()
        path = self.root / 'private/retained-room-function.json'
        path.write_text('["changed"]')
        with self.assertRaisesRegex(ValueError, 'Retained'):
            load_frozen(self.root)

    def test_executor_pins_model_new_context_and_rejects_tools(self):
        args = command('codex', self.root, {'model': 'gpt-5.6-sol', 'reasoningEffort': 'xhigh'})
        self.assertIn('--ephemeral', args)
        self.assertIn('model_reasoning_effort=xhigh', args)
        self.assertNotIn('resume', args)
        events = [{'type': 'thread.started', 'thread_id': 'test'}, {'type': 'turn.completed', 'usage': {}}]
        self.assertEqual('test', parse_events('\n'.join(map(json.dumps, events)))[0])
        events.insert(1, {'type': 'item.completed', 'item': {'type': 'command_execution'}})
        with self.assertRaisesRegex(ValueError, 'tool'):
            parse_events('\n'.join(map(json.dumps, events)))

    def test_pixel_audit_rejects_even_one_changed_edge_pixel(self):
        from PIL import Image
        panel = Image.new('RGB', (720, 1080), 'white')
        pair = Image.new('RGB', (1480, 1146), 'grey')
        pair.paste(panel, (10, 66))
        verify_panel_pixels(pair, panel, 10)
        pair.putpixel((10, 66), (254, 254, 254))
        with self.assertRaisesRegex(ValueError, 'pixels'):
            verify_panel_pixels(pair, panel, 10)

    def test_answer_schema_does_not_accept_extra_fields_or_boolean_confidence(self):
        answer = {'judgement': 'tie', 'errorChoice': 'neither', 'confidence': 3, 'note': 'Fixture.'}
        validate_answer(answer)
        for invalid in ({**answer, 'confidence': True}, {**answer, 'model': 'inferred'},
                        {**answer, 'note': ''}, {**answer, 'judgement': 'unknown'}):
            with self.assertRaises(ValueError):
                validate_answer(invalid)

    def test_passing_checkpoint_retains_all_votes_and_never_authorizes_continuation(self):
        rows = self.frozen()
        for row in rows:
            self.save(row)
        # Pixel checking is separately covered with actual PIL images. These
        # synthetic answers never leave this disposable unit-test directory.
        with patch('serverless.cloud_benchmark.run_staged_pilot.preflight'):
            self.assertTrue(finalize(self.root)['pilotAccepted'])
        checkpoint_document = json.loads((self.root / 'pilot-results.json').read_bytes())
        self.assertEqual(320, len(checkpoint_document['judgements']))
        inventory = continuation_inventory(self.root)
        self.assertEqual(256, inventory['reusableMainJudgements'])
        self.assertFalse(inventory['authorized'])
        self.assertTrue(all(len(value) == 448 for value in inventory['remainingPairsByReviewer'].values()))
        write_new(self.root / 'preflight.json', {'fixtureOnly': True})
        exported = self.root / 'checkpoint.json'
        self.assertEqual(320, checkpoint(self.root, exported)['judgements'])
        public = json.loads(exported.read_bytes())
        self.assertEqual(320, len(public['judgements']))
        self.assertFalse(public['releaseEligible'])
        self.assertTrue(all('threadId' not in row['receipt'] and 'contextSha256' in row['receipt'] for row in public['judgements']))
        altered_report = json.loads((self.root / 'pilot-results.json').read_bytes())
        altered_report['judgements'][0]['note'] = 'Changed report, not a model answer.'
        (self.root / 'pilot-results.json').write_text(json.dumps(altered_report))
        with self.assertRaisesRegex(ValueError, 'Checkpoint evidence differs'):
            checkpoint(self.root, self.root / 'tampered-export.json')
        self.assertFalse((self.root / 'tampered-export.json').exists())
        path = self.root / 'responses' / (rows[0]['assignmentId'] + '.json')
        value = json.loads(path.read_bytes())
        value['note'] = 'Changed fixture.'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'response changed'):
            continuation_inventory(self.root)

    def test_unknown_and_reused_context_receipts_are_rejected(self):
        rows = self.frozen()
        self.save(rows[0])
        self.save(rows[1])
        path = self.root / 'responses' / (rows[1]['assignmentId'] + '.json')
        value = json.loads(path.read_bytes())
        value['receipt']['threadId'] = rows[0]['assignmentId']
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'contexts were reused'):
            results(self.root)

    def test_recovery_submits_existing_model_output_without_a_call(self):
        row = self.frozen()[0]
        workspace = self.root / 'execution' / row['assignmentId']
        workspace.mkdir(parents=True)
        answer = {'judgement': 'tie', 'errorChoice': 'neither', 'confidence': 3, 'note': 'Recovery fixture.'}
        events = [{'type': 'thread.started', 'thread_id': 'recovery-fixture'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(answer)}},
                  {'type': 'turn.completed', 'usage': {}}]
        (workspace / 'events.jsonl').write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
        write_new(workspace / 'answer.json', answer)
        write_new(workspace / 'invocation.json', {'model': 'gpt-5.6-sol', 'reasoningEffort': 'xhigh',
                  'promptHash': 'prompt', 'systemPromptHash': 'system', 'imageSha256': 'image'})
        with patch('serverless.cloud_benchmark.run_staged_pilot.preflight'), patch('subprocess.run') as call:
            self.assertEqual({'recovered': 1, 'modelCalls': 0}, recover_completed(self.root))
            self.assertEqual({'recovered': 0, 'modelCalls': 0}, recover_completed(self.root))
            call.assert_not_called()
        self.assertEqual(1, results(self.root)['responses'])


if __name__ == '__main__':
    unittest.main()
