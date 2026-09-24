from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from serverless.cloud_benchmark.review_work import selected_assignment_ids, status, validate_answers


class ReviewWorkTests(unittest.TestCase):
    def test_batch_validation_rejects_invalid_or_reordered_answers(self):
        work = [{'set': 'group-1', 'caseId': key} for key in ('a', 'b')]
        answers = [{**case, 'judgement': 'tie', 'errorChoice': 'neither', 'confidence': 3,
                    'note': 'Visible evidence.'} for case in work]
        self.assertEqual(2, validate_answers(work, answers))
        self.assertEqual(1, validate_answers(work, answers[:1], complete=False))
        for invalid in (answers[:1], answers[::-1], answers + answers[:1],
                        [answers[0], {**answers[1], 'confidence': True}],
                        [answers[0], {**answers[1], 'note': 'x' * 501}],
                        [answers[0], {**answers[1], 'judgement': 'unknown'}]):
            with self.assertRaises(ValueError):
                validate_answers(work, invalid)

    def test_progress_distinguishes_written_saved_and_changed_answers(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            folder = root / 'packets/reviewer-01'
            folder.mkdir(parents=True)
            def write(path, value):
                path.write_text(json.dumps(value), encoding='utf-8')
            write(root / 'manifest.json', {'reviewers': {'reviewer-01': {'profile': 'access'}}, 'frozenPairs': []})
            case = {'set': 'group-1', 'caseId': 'a'}
            write(folder / 'cases.json', [case])
            def check():
                with redirect_stdout(io.StringIO()):
                    return status(root)
            self.assertEqual('no_answers_yet', check()['reviewers']['reviewer-01']['state'])
            answers = [{**case, 'judgement': 'tie', 'errorChoice': 'neither', 'confidence': 3, 'note': ''}]
            write(folder / 'answers.json', answers)
            self.assertEqual('awaiting_submission', check()['reviewers']['reviewer-01']['state'])
            self.assertFalse(check()['queueComplete'])
            write(folder / 'submitted.json', {'saved': 1, 'respondentType': 'ai_pilot',
                'answersSha256': hashlib.sha256((folder / 'answers.json').read_bytes()).hexdigest()})
            self.assertTrue(check()['queueComplete'])
            answers[0]['confidence'] = 4
            write(folder / 'answers.json', answers)
            self.assertEqual('invalid_or_being_written', check()['reviewers']['reviewer-01']['state'])
            self.assertEqual(0, check()['totals']['saved'])

    def test_filter_preserves_repeat_trials_only_for_retained_pairs(self):
        protocol = {'stimulusEvidence': [
            {'caseId': 'a', 'matchingStratum': ['bedroom']},
            {'caseId': 'b', 'matchingStratum': ['living_room']}]}
        assignments = [{'caseId': 'a', 'repeatOf': None}, {'caseId': 'b', 'repeatOf': None},
                       {'caseId': 'repeat-a', 'repeatOf': 'a'}, {'caseId': 'repeat-b', 'repeatOf': 'b'}]
        self.assertEqual({'a', 'repeat-a'}, selected_assignment_ids(protocol, assignments, 'bedroom'))
        self.assertEqual({'b', 'repeat-b'}, selected_assignment_ids(protocol, assignments, 'living_room'))
        self.assertEqual({'a', 'b', 'repeat-a', 'repeat-b'}, selected_assignment_ids(protocol, assignments))


if __name__ == '__main__': unittest.main()
