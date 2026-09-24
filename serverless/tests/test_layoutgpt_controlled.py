import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from serverless.benchmark.import_layoutgpt_controlled import compile_responses


class ControlledLayoutGPTTests(unittest.TestCase):
    def test_supplement_keeps_its_global_instance_id_and_receipt(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            folder = Path(temporary)
            (folder / 'responses').mkdir()
            request = {'id': 'controlled-living-120', 'requestedObjects': 1,
                       'request': {'messages': [{'content': 'max length 256px, max width 300px'}]},
                       'requestSha256': 'request-hash', 'sourceRoomId': 'held-out-room'}
            plan = {'requests': [request], 'sourceCommit': 'pinned-source',
                    'variant': 'living-room-count-conditioned', 'methods': 'fixture'}
            raw = json.dumps(plan).encode()
            (folder / 'requests.json').write_bytes(raw)
            response = {'model': 'gpt-4-0613', 'choices': [{'finish_reason': 'stop',
                         'message': {'content': 'table'}}]}
            response_raw = json.dumps(response).encode()
            (folder / 'responses/controlled-living-120.json').write_bytes(response_raw)
            ledger = {'planSha256': hashlib.sha256(raw).hexdigest(), 'entries': {
                request['id']: {'status': 'complete', 'wallSeconds': 4, 'actualUsd': .08,
                               'responseSha256': hashlib.sha256(response_raw).hexdigest()}}}
            (folder / 'inference-ledger.json').write_text(json.dumps(ledger))
            parser = folder / 'parser.py'
            parser.write_text("def parse_3D_layout(line, unit):\n"
                "    return line, dict(left=100, top=100, depth=10, length=20, width=20, height=20, orientation=0)\n")
            compile_responses(folder, parser)
            result = json.loads((folder / 'export.json').read_bytes())
            self.assertEqual('layoutgpt-controlled-living-120', result['scenes'][0]['id'])
            self.assertEqual('gpt-4-0613', result['scenes'][0]['provenance']['model'])
            self.assertTrue(result['attempts'][0]['countSatisfied'])
            self.assertEqual(.08, result['actualApiUsd'])
            # No raw response replacement may silently alter measured outputs.
            (folder / 'responses/controlled-living-120.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'checksum'): compile_responses(folder, parser)


if __name__ == '__main__': unittest.main()
