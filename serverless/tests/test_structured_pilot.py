"""Synthetic v2 checks; no manufactured responses enter research directories."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from serverless.benchmark.geometry import box_corners
from serverless.benchmark.numbered_evidence import inventory, task_evidence
from serverless.benchmark.stimuli import diagram
from serverless.cloud_benchmark.staged_pilot import (SCHEMA, PROFILES, STRATA, assignments, select_pairs,
    digest, write_new, record, results, delivery_prompt, validate_answer, load_frozen)
from serverless.study.clarified_rubric import prompt, VERSION as V1
from serverless.study import structured_rubric as v2
from serverless.tests.test_staged_pilot import cases
from serverless.study.service import review_instructions


def scene():
    return {'id': 'fixture', 'model': 'fixture', 'fixture': True, 'roomType': 'bedroom',
            'room': {'polygon': [[0, 0], [6, 0], [6, 5], [0, 5]], 'floorZ': 0},
            'objects': [{'id': label, 'label': label, 'corners': box_corners(center, dimensions),
                         'frontDirection': [1, 0], 'frontConvention': 'fixture functional front'}
                        for label, center, dimensions in [('bed', [1, 1, .3], [2, 1, .6]),
                                                          ('chair', [3, 2, .5], [.5, .5, 1]),
                                                          ('desk', [4, 3, .4], [1, .8, .8])]]}


class StructuredPilotTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def frozen(self):
        selected = select_pairs(cases(120), set(), v2.SEED)
        rows, reviewers = [], []
        for index, profile in enumerate(p for p in PROFILES for _ in range(2)):
            reviewer = f'reviewer-{index+1:02}'
            rows.extend(assignments(selected, reviewer, profile, v2.VERSION, v2.SEED))
            reviewers.append({'reviewerId': reviewer, 'profile': profile, 'reviewPrompt': prompt(profile, v2.VERSION),
                              'promptHash': 'fixture-prompt', 'systemPromptHash': 'fixture-system'})
        protocol = {'rubricVersion': v2.VERSION, 'assignments': rows, 'reviewers': reviewers,
                    'outputSchema': v2.schema(SCHEMA), 'model': 'gpt-5.6-sol', 'reasoningEffort': 'xhigh',
                    'evidenceInstruction': v2.EVIDENCE_INSTRUCTION, 'retainedRoomFunctionSha256': digest([])}
        for row in rows:
            row['evidence'] = {side: task_evidence(inventory(scene()), row['profile']) for side in ('left', 'right')}
            row['evidenceSha256'] = digest(row['evidence'])
            row['deliveryPromptHash'] = digest(delivery_prompt(protocol, row).encode())
        write_new(self.root / 'protocol.json', protocol)
        write_new(self.root / 'protocol-sha256.json', {'sha256': digest(protocol)})
        write_new(self.root / 'private/retained-room-function.json', [])
        write_new(self.root / 'packets/index.json', {row['assignmentId']: {'imageSha256': 'fixture-image'} for row in rows})
        return protocol

    def answer(self, row, preference='tie'):
        return {'observations': [{'side': side, 'objects': list(ids), 'severity': 'none', 'evidence': 'Synthetic test only.'}
                                 for side, ids in v2.checklist_keys(row['evidence'], row['profile'])],
                'judgement': preference, 'errorChoice': 'neither', 'confidence': 3, 'note': 'Synthetic test only.'}

    def save(self, row, preference='tie'):
        receipt = {'model': 'gpt-5.6-sol', 'reasoningEffort': 'xhigh', 'promptHash': 'fixture-prompt',
                   'systemPromptHash': 'fixture-system', 'imageSha256': 'fixture-image',
                   'threadId': row['assignmentId'], 'isolatedContext': True,
                   **{key: row[key] for key in ('deliveryPromptHash', 'evidenceSha256')}}
        record(self.root, row['assignmentId'], self.answer(row, preference), receipt)

    def test_rules_versions_and_room_function_are_separate(self):
        doc = {'decisionScope': 'focus_only', 'evidenceMode': 'visual_only'}
        original = review_instructions(doc, 'room_function')
        expected = {'orientation': ['independently of the available gap', 'adjacent wall'],
                    'proportions': ['EVERY supplied unordered pair', 'ordinary armchairs', 'miniature', 'specialist'],
                    'relationships': ['nightstand serves', '3D views', 'not_applicable'],
                    'access': ['entire working edge need not', 'route and usable', 'record uncertain', 'Foot-end-only']}
        for profile, fragments in expected.items():
            old, new = prompt(profile), prompt(profile, v2.VERSION)
            self.assertNotEqual(old, new)
            self.assertEqual(new, review_instructions({**doc, 'rubricVersion': v2.VERSION}, profile))
            for fragment in fragments:
                self.assertIn(fragment, new)
            self.assertIn('clear loss of functional use', new)
        self.assertEqual(original, review_instructions(doc, 'room_function'))
        with self.assertRaises(ValueError):
            prompt('access', 'unknown')

    def test_numbers_ratios_scale_and_source_id_invariance(self):
        source = scene()
        expected = inventory(source)
        self.assertEqual(3, len(expected['volumeRatios']))
        self.assertAlmostEqual(4.8, expected['volumeRatios'][0]['ratio'])
        for scale in (.01, 4, 25):
            other = deepcopy(source)
            for index, item in enumerate(reversed(other['objects'])):
                item['id'] = f'unrelated-source-name-{index}'
                item['corners'] = [[v * scale for v in point] for point in item['corners']]
            other['objects'].reverse()
            self.assertEqual(expected, inventory(other))
        for profile in ('access', 'orientation', 'relationships'):
            self.assertEqual({'objects'}, set(task_evidence(expected, profile)))

    def test_diagram_preserves_every_polygon_and_front(self):
        source = scene()
        before = deepcopy(source)
        plain = ET.fromstring(diagram(source))
        numbered = ET.fromstring(diagram(source, numbered=True))
        shape = lambda root: [node.attrib for node in root.iter() if node.tag.endswith('polygon') or node.attrib.get('class') == 'front']
        self.assertEqual(shape(plain), shape(numbered))
        keys = [node.attrib['data-key'] for node in numbered.iter() if 'data-key' in node.attrib]
        refs = [node.attrib['data-object-ref'] for node in numbered.iter() if 'data-object-ref' in node.attrib]
        self.assertEqual(['1', '2', '3'], keys)
        self.assertEqual(Counter({'1': 3, '2': 3, '3': 3}), Counter(refs))
        self.assertEqual(before, source)

    def test_fresh_selection_excludes_seventy_and_remains_balanced(self):
        source = cases(120)
        earlier = {row['pairId'] for row in source[::12][:38]}
        old = select_pairs(source, earlier)
        excluded = earlier | {row['pairId'] for row in old}
        self.assertEqual(70, len(excluded))
        chosen = select_pairs(source, excluded, v2.SEED)
        self.assertFalse(excluded & {row['pairId'] for row in chosen})
        self.assertEqual({stratum: 8 for stratum in STRATA}, Counter((row['baseline'], row['roomType']) for row in chosen))
        self.assertEqual(chosen, select_pairs(list(reversed(source)), excluded, v2.SEED))

    def test_schema_rejects_missing_duplicate_and_unknown_observations(self):
        protocol = self.frozen()
        for profile in PROFILES:
            row = next(r for r in protocol['assignments'] if r['profile'] == profile)
            good = self.answer(row)
            validate_answer(good, v2.VERSION, row['evidence'], profile)
            for change in ('missing', 'duplicate', 'unknown', 'too_long'):
                bad = deepcopy(good)
                if change == 'missing': bad['observations'].pop()
                if change == 'duplicate': bad['observations'].append(bad['observations'][0])
                if change == 'unknown': bad['observations'][0]['objects'] = ['99']
                if change == 'too_long': bad['observations'][0]['evidence'] = 'x' * 181
                with self.assertRaises(ValueError):
                    validate_answer(bad, v2.VERSION, row['evidence'], profile)
        with self.assertRaises(ValueError):
            validate_answer(good, V1)

    def test_case_prompt_freeze_and_missing_only_resume(self):
        protocol = self.frozen()
        row = protocol['assignments'][0]
        delivered = delivery_prompt(protocol, row)
        self.assertNotIn(row['pairId'], delivered)
        for model in ('soilie', 'layoutgpt', 'infinigen'):
            self.assertNotIn(model, delivered.lower())
        self.save(row)
        with self.assertRaises(FileExistsError):
            self.save(row)
        self.assertEqual(319, len(results(self.root)['remainingAssignmentIds']))
        path = self.root / 'protocol.json'
        mutated = json.loads(path.read_bytes())
        mutated['assignments'][0]['evidence']['left']['objects'][0]['category'] = 'changed'
        path.write_text(json.dumps(mutated))
        with self.assertRaisesRegex(ValueError, 'protocol changed'):
            load_frozen(self.root)

    def test_exact_agreement_gate_does_not_ignore_tie_changes(self):
        protocol = self.frozen()
        count = 0
        for row in protocol['assignments']:
            preference = 'tie'
            if row['profile'] == 'orientation' and row['repeatOf'] and count < 2:
                preference = 'left'
                count += 1
            self.save(row, preference)
        report = results(self.root)
        self.assertFalse(report['pilotAccepted'])
        self.assertEqual(14, report['byDimension']['orientation']['agreements'])
        transition = report['repeatTransitions']['overall']
        self.assertEqual(2, transition['tie_preference_change'])
        self.assertEqual(0, transition['winner_reversal'])
        self.assertIsNone(transition['decisiveAgreementPct'])

    def test_swapped_physical_winner_not_button_label(self):
        protocol = self.frozen()
        repeat = next(row for row in protocol['assignments'] if row['repeatOf'])
        original = next(row for row in protocol['assignments'] if row['assignmentId'] == repeat['repeatOf'])
        self.save(original, 'left')
        self.save(repeat, 'right')
        report = results(self.root)
        self.assertEqual(1, report['agreements'])
        self.assertEqual(1, report['repeatTransitions']['overall']['same_winner'])


if __name__ == '__main__':
    unittest.main()
