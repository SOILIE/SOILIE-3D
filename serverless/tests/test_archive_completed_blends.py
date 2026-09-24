import base64
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from serverless.benchmark.archive_completed_blends import archive_one


class CompletedArchiveTests(unittest.TestCase):
    def test_delete_only_after_verified_remote_and_durable_recovery_receipt(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            folder = Path(directory)
            source = folder / 'scene.blend'
            original = b'completed procedural scene' * 1000
            source.write_bytes(original)
            client = Mock()
            client.head_object.return_value = {'ContentLength': 1}
            with self.assertRaises(ValueError): archive_one(client, 'bucket', folder, 'outputs/')
            self.assertEqual(original, source.read_bytes())
            self.assertFalse((folder / 'scene.blend.archive-upload.tmp').exists())
            body = []
            def receive(**kwargs): body.append(kwargs['Body'].read())
            client.put_object.side_effect = receive
            client.head_object.side_effect = lambda **_: {'ContentLength': len(body[-1]),
                'ChecksumSHA256': base64.b64encode(hashlib.sha256(body[-1]).digest()).decode()}
            receipt, freed = archive_one(client, 'bucket', folder, 'outputs/')
            self.assertEqual(original, gzip.decompress(body[-1]))
            self.assertEqual(hashlib.sha256(original).hexdigest(), receipt['originalBlendSha256'])
            self.assertEqual(len(original), freed)
            self.assertFalse(source.exists())
            self.assertEqual(receipt, json.loads((folder / 's3-scene-archive.json').read_bytes()))
            self.assertEqual(0, archive_one(client, 'bucket', folder, 'outputs/')[1])

    def test_concurrently_changed_original_is_preserved(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            folder = Path(directory)
            source = folder / 'scene.blend'
            source.write_bytes(b'original')
            client = Mock()
            body = []
            def receive(**kwargs):
                body.append(kwargs['Body'].read())
                source.write_bytes(b'changed by another process')
            client.put_object.side_effect = receive
            client.head_object.side_effect = lambda **_: {'ContentLength': len(body[-1]),
                'ChecksumSHA256': base64.b64encode(hashlib.sha256(body[-1]).digest()).decode()}
            with self.assertRaisesRegex(ValueError, 'changed'): archive_one(client, 'bucket', folder, 'outputs/')
            self.assertEqual(b'changed by another process', source.read_bytes())


if __name__ == '__main__': unittest.main()
