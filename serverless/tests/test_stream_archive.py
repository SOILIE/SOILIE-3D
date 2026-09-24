import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from serverless.benchmark.stream_archive import verify_remote, archive_binary


class StreamArchiveTests(unittest.TestCase):
    def test_remote_checksum_is_required_not_etag_or_user_metadata(self):
        content = b'completed scientific artifact'
        sha = hashlib.sha256(content).hexdigest()
        client = Mock()
        client.head_object.return_value = {'ContentLength': len(content),
            'ChecksumSHA256': base64.b64encode(bytes.fromhex(sha)).decode()}
        result = verify_remote(client, 'bucket', 'object', sha, len(content))
        self.assertEqual(sha, result['sha256'])
        client.get_object.assert_not_called()
        client.head_object.return_value = {'ContentLength': len(content), 'Metadata': {'sha256': sha}}
        client.get_object.return_value = {'Body': io.BytesIO(content)}
        verify_remote(client, 'bucket', 'object', sha, len(content))
        client.get_object.return_value = {'Body': io.BytesIO(b'incorrect bytes')}
        with self.assertRaisesRegex(ValueError, 'checksum'):
            verify_remote(client, 'bucket', 'object', sha, len(content))
        with self.assertRaisesRegex(ValueError, 'size'):
            verify_remote(client, 'bucket', 'object', sha, 1)

    def test_local_binary_survives_bad_remote_then_evicts_with_recovery_receipt(self):
        scratch = Path(__file__).parents[2] / '.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            campaign = Path(temporary)
            work = campaign / 'bedroom/attempt-000'
            directory = work / 'scene-bedroom-000'
            directory.mkdir(parents=True)
            path = directory / 'scene.blend.gz'
            content = b'verified generated binary'
            path.write_bytes(content)
            checksum = hashlib.sha256(content).hexdigest()
            attempt = {'export': 'bedroom/attempt-000/export.json', 'archive': {
                'archiveSha256': checksum, 'archiveBytes': len(content), 'originalSha256': 'original-hash'}}
            record = {'id': 'test-scene', 'model': 'infinigen_controlled', 'roomType': 'bedroom', 'artifacts': {}}
            client = Mock()
            client.head_object.return_value = {'ContentLength': 1}
            with self.assertRaises(ValueError):
                archive_binary(client, 'bucket', 'files/outputs/test/', campaign, attempt, record)
            self.assertTrue(path.exists())
            client.head_object.return_value = {'ContentLength': len(content),
                'ChecksumSHA256': base64.b64encode(bytes.fromhex(checksum)).decode()}
            self.assertEqual(len(content), archive_binary(client, 'bucket', 'files/outputs/test/', campaign, attempt, record))
            self.assertFalse(path.exists())
            receipt = json.loads((work / 's3-binary-receipt.json').read_bytes())
            self.assertEqual('original-hash', receipt['originalBlendSha256'])
            self.assertEqual(checksum, receipt['sha256'])
            self.assertEqual(0, archive_binary(client, 'bucket', 'files/outputs/test/', campaign, attempt, record))


if __name__ == '__main__': unittest.main()
