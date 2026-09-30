import base64
import hashlib
import io
from pathlib import Path
import tarfile
import unittest
from unittest.mock import Mock

from serverless.benchmark.private_archive import archive_path, contained, evict, inventory
from serverless.tests.test_arbutus_inventory import temporary_directory


class PrivateArchiveTests(unittest.TestCase):
    def client(self, wrong=False):
        client = Mock()
        stored = {}
        client.get_public_access_block.return_value = {'PublicAccessBlockConfiguration': {
            k: True for k in ('BlockPublicAcls', 'IgnorePublicAcls',
                             'BlockPublicPolicy', 'RestrictPublicBuckets')}}
        client.get_bucket_versioning.return_value = {'Status': 'Enabled'}

        def put(**kw):
            body = kw['Body']
            stored[kw['Key']] = body if isinstance(body, bytes) else body.read()
            self.assertEqual('AES256', kw['ServerSideEncryption'])
            self.assertEqual('private,no-store', kw['CacheControl'])
            self.assertNotIn('ACL', kw)

        def head(**kw):
            body = stored[kw['Key']]
            return {'VersionId': 'fixture-version', 'ContentLength': len(body) + int(wrong),
                    'ChecksumSHA256': base64.b64encode(hashlib.sha256(body).digest()).decode()}

        client.put_object.side_effect = put
        client.head_object.side_effect = head
        return client, stored

    def fixture(self, root):
        source = root / 'inactive-run'
        source.mkdir()
        (source / 'scene.blend').write_bytes(b'mesh evidence')
        (source / 'observations.json').write_text('{"measurement": 1}')
        return source

    def test_verified_private_archive_and_idempotent_file_eviction(self):
        with temporary_directory() as folder:
            root = Path(folder).resolve()
            source = self.fixture(root)
            sibling = root / 'active.json'
            sibling.write_text('preserve')
            client, stored = self.client()
            receipt = archive_path(client, 'private-fixture', source, root / 'receipts', root, True)
            self.assertEqual(2, len(receipt['members']))
            self.assertFalse((source / 'scene.blend').exists())
            self.assertTrue(sibling.exists())
            self.assertEqual('fixture-version', receipt['versionId'])
            with tarfile.open(fileobj=io.BytesIO(stored[receipt['key']]), mode='r:gz') as archive:
                for row in receipt['members']:
                    data = archive.extractfile(row['path']).read()
                    self.assertEqual(row['sha256'], hashlib.sha256(data).hexdigest())
            count = client.put_object.call_count
            again = archive_path(client, 'private-fixture', source, root / 'receipts', root, True)
            self.assertEqual(count, client.put_object.call_count)
            self.assertEqual(receipt['evictedBytes'], again['evictedBytes'])

    def test_remote_verification_failure_preserves_sources(self):
        with temporary_directory() as folder:
            root = Path(folder).resolve()
            source = self.fixture(root)
            client, _ = self.client(wrong=True)
            with self.assertRaisesRegex(ValueError, 'size'):
                archive_path(client, 'private-fixture', source, root / 'receipts', root, True)
            self.assertTrue((source / 'scene.blend').exists())
            self.assertEqual([], list((root / 'receipts').glob('*.json')))

    def test_changed_source_not_evicted(self):
        with temporary_directory() as folder:
            root = Path(folder).resolve()
            source = self.fixture(root)
            rows = inventory(source, root)
            (source / 'scene.blend').write_bytes(b'new data')
            with self.assertRaisesRegex(ValueError, 'changed'):
                evict(root, rows)
            self.assertEqual(2, len(list(source.iterdir())))

    def test_new_files_after_snapshot_are_not_evicted(self):
        with temporary_directory() as folder:
            root = Path(folder).resolve()
            source = self.fixture(root)
            client, _ = self.client()
            archive_path(client, 'private-fixture', source, root / 'receipts', root)
            new_file = source / 'later.json'
            new_file.write_text('preserve newer evidence')
            archive_path(client, 'private-fixture', source, root / 'receipts', root, True)
            self.assertEqual('preserve newer evidence', new_file.read_text())

    def test_public_bucket_and_nonversioned_bucket_rejected(self):
        with temporary_directory() as folder:
            root = Path(folder).resolve()
            source = self.fixture(root)
            client, _ = self.client()
            client.get_public_access_block.return_value['PublicAccessBlockConfiguration']['BlockPublicPolicy'] = False
            with self.assertRaisesRegex(ValueError, 'public'):
                archive_path(client, 'bad', source, root / 'receipts', root, True)
            client.get_public_access_block.return_value['PublicAccessBlockConfiguration']['BlockPublicPolicy'] = True
            client.get_bucket_versioning.return_value = {}
            with self.assertRaisesRegex(ValueError, 'versioned'):
                archive_path(client, 'bad', source, root / 'receipts', root, True)
            client.put_object.assert_not_called()

    def test_credentials_active_lock_and_broad_paths_rejected(self):
        with temporary_directory() as folder:
            root = Path(folder).resolve()
            source = self.fixture(root)
            for name in ('auth.json', 'api-token.txt', '.env', 'id_ed25519', 'collection.lock'):
                path = source / name
                path.write_text('fixture')
                with self.assertRaises(ValueError):
                    inventory(source, root)
                path.unlink()
            for path in (root, root / '..' / 'outside'):
                with self.assertRaises(ValueError):
                    contained(path, root)
            client, _ = self.client()
            with self.assertRaisesRegex(ValueError, 'inside its source'):
                archive_path(client, 'bad', source, source / 'receipts', root, True)


if __name__ == '__main__':
    unittest.main()
