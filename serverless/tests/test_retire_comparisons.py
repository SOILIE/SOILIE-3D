"""Recovery and deletion guards for the deliberately destructive retirement CLI."""
import base64
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from botocore.exceptions import ClientError

from serverless.benchmark.retire_comparisons import (
    PRIVATE_ROOT, PRESERVED, PUBLIC_ROOT, ROOT, archive_local, baseline_recovery, copy_one, inventory,
    local_inventory, packed, private_policy, rebuild_index, require_private,
    retire, save, upload_local, verify)

SCRATCH = Path(__file__).resolve().parents[2] / '.codex' / 'test-retire-comparisons'


class RetirementTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.context = {'bucket': 'fixture', 'account': '123456789012',
                        'prefix': PRIVATE_ROOT + '20261022T000000Z/'}

    def storage(self):
        client = Mock()
        objects = {}
        policy = private_policy({'Version': '2012-10-17', 'Statement': []},
                                self.context['bucket'], self.context['account'])
        client.get_bucket_policy.return_value = {'Policy': json.dumps(policy)}

        def head(**kwargs):
            if kwargs['Key'] not in objects:
                raise ClientError({'Error': {'Code': 'NoSuchKey'},
                                   'ResponseMetadata': {'HTTPStatusCode': 404}}, 'HeadObject')
            body = objects[kwargs['Key']]
            return {'ContentLength': len(body), 'ETag': '"hash"',
                    'ChecksumType': 'FULL_OBJECT',
                    'ChecksumSHA256': base64.b64encode(hashlib.sha256(body).digest()).decode()}

        def put(**kwargs):
            body = kwargs['Body']
            objects[kwargs['Key']] = body if isinstance(body, bytes) else body.read()
            self.assertEqual('private,no-store', kwargs['CacheControl'])
            self.assertEqual('AES256', kwargs['ServerSideEncryption'])

        client.head_object.side_effect = head
        client.put_object.side_effect = put
        client.get_object.side_effect = lambda **kwargs: dict(head(**kwargs), Body=io.BytesIO(objects[kwargs['Key']]))
        client.copy_object.side_effect = lambda **kwargs: objects.update({kwargs['Key']: objects[kwargs['CopySource']['Key']]})
        client.upload_fileobj.side_effect = lambda stream, bucket, key, **kwargs: objects.update({key: stream.read()})
        return client, objects

    def test_policy_preserves_public_policy_but_explicitly_denies_external_and_cloudfront(self):
        original = {'Version': '2012-10-17', 'Statement': [
            {'Sid': 'Existing', 'Effect': 'Allow', 'Principal': '*',
             'Action': 's3:GetObject', 'Resource': 'arn:aws:s3:::fixture/*'}]}
        desired = private_policy(original, 'fixture', '123456789012')
        self.assertEqual(1, len(original['Statement']))
        self.assertEqual(original['Statement'][0], desired['Statement'][0])
        owner, cloudfront = desired['Statement'][1:]
        self.assertEqual({'StringNotEquals': {'aws:PrincipalAccount': '123456789012'}}, owner['Condition'])
        self.assertEqual('arn:aws:s3:::fixture/private/*', owner['Resource'])
        self.assertEqual('Deny', cloudfront['Effect'])
        self.assertEqual({'Service': 'cloudfront.amazonaws.com'}, cloudfront['Principal'])
        self.assertEqual(desired, private_policy(desired, 'fixture', '123456789012'))

    def test_private_policy_required_before_copy_work(self):
        client, _ = self.storage()
        require_private(client, 'fixture', '123456789012')
        client.get_bucket_policy.return_value = {'Policy': '{"Statement":[]}'}
        with self.assertRaisesRegex(ValueError, 'denies'):
            require_private(client, 'fixture', '123456789012')

    def test_composite_or_missing_checksum_streams_actual_recovery_bytes(self):
        client, objects = self.storage()
        objects['recovery'] = b'actual bytes'
        checksum = hashlib.sha256(objects['recovery']).hexdigest()
        verify(client, 'fixture', 'recovery', checksum, len(objects['recovery']))
        client.get_object.assert_not_called()
        client.head_object.side_effect = None
        client.head_object.return_value = {'ContentLength': len(objects['recovery']),
            'ETag': '"hash"', 'ChecksumType': 'COMPOSITE',
            'ChecksumSHA256': base64.b64encode(bytes.fromhex(checksum)).decode()}
        verify(client, 'fixture', 'recovery', checksum, len(objects['recovery']))
        client.get_object.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            verify(client, 'fixture', 'recovery', '0' * 64, len(objects['recovery']))

    def test_copy_pins_source_and_keeps_original_content_without_local_binary(self):
        client, objects = self.storage()
        key = PUBLIC_ROOT + 'old/infinigen/scene.blend'
        objects[key] = b'baseline geometry'
        row = copy_one(client, self.context, {'key': key, 'etag': '"source"', 'bytes': len(objects[key])})
        self.assertEqual(hashlib.sha256(objects[key]).hexdigest(), row['recovery']['sha256'])
        self.assertEqual(objects[key], objects[row['recovery']['key']])
        self.assertEqual('"source"', client.copy_object.call_args.kwargs['CopySourceIfMatch'])
        self.assertEqual('"source"', client.get_object.call_args.kwargs['IfMatch'])
        client.delete_object.assert_not_called()
        self.assertEqual([], list(self.state.iterdir()))

    def test_preserved_or_unrelated_keys_can_never_be_copied_for_deletion(self):
        client, _ = self.storage()
        for key in (PRESERVED + 'scene.blend', PUBLIC_ROOT, 'other/object'):
            with self.assertRaisesRegex(ValueError, 'protected|scope'):
                copy_one(client, self.context, {'key': key, 'etag': '"x"', 'bytes': 1})
        client.copy_object.assert_not_called()

    def test_inventory_uses_all_pages_and_preserves_historical_objects(self):
        client, objects = self.storage()
        objects['catalog/files-index.json'] = b'{"schemaVersion":1,"keys":[]}'
        def obj(key):
            return {'Key': key, 'ETag': '"old"', 'Size': 2,
                    'LastModified': datetime.now(UTC)}
        client.get_paginator.return_value.paginate.return_value = [
            {'Contents': [obj(PRESERVED + 'a')]},
            {'Contents': [obj(PUBLIC_ROOT + 'old/a'), obj(PUBLIC_ROOT + 'new-unindexed/b')]}]
        result = inventory(client, self.context, self.state)
        self.assertEqual(2, result['objects'])
        document = json.loads((self.state / 'inventory.json').read_bytes())
        self.assertEqual([PRESERVED + 'a'], [row['key'] for row in document['preserved']])
        self.assertIn(self.context['prefix'] + 'manifests/inventory.json', objects)

    def test_local_snapshot_is_recoverable_before_originals_are_changed(self):
        client, objects = self.storage()
        source = self.state / 'packet'
        source.mkdir()
        (source / 'comparison.json').write_bytes(b'{"model":"infinigen"}')
        result = upload_local(client, self.context, source, 'website-benchmarks', self.state)
        self.assertEqual(1, result['files'])
        manifest = json.loads(objects[result['receipt']['key']])
        self.assertEqual('comparison.json', manifest['files'][0]['path'])
        self.assertEqual((source / 'comparison.json').read_bytes(), objects[manifest['files'][0]['key']])
        self.assertTrue((source / 'comparison.json').exists())

    def test_linked_snapshot_root_is_rejected_before_resolving(self):
        client, _ = self.storage()
        with patch.object(Path, 'is_symlink', return_value=True), patch.object(Path, 'resolve') as resolve:
            with self.assertRaisesRegex(ValueError, 'regular directory'):
                upload_local(client, self.context, self.state, 'linked', self.state)
            resolve.assert_not_called()
        with patch.object(Path, 'is_symlink', return_value=True), patch.object(
                Path, 'lstat', return_value=SimpleNamespace(st_file_attributes=1024)):
            with self.assertRaisesRegex(ValueError, 'must not be a link'):
                local_inventory(self.state.parent, [self.state.name])

    def test_completed_local_label_cannot_be_overwritten_by_pending_website(self):
        client, objects = self.storage()
        source = self.state / 'packet'
        source.mkdir()
        (source / 'status.json').write_bytes(b'original publication')
        first = upload_local(client, self.context, source, 'website-benchmarks', self.state)
        frozen = dict(objects)
        (source / 'status.json').write_bytes(b'pending results')
        again = upload_local(client, self.context, source, 'website-benchmarks', self.state)
        self.assertEqual(first, again)
        self.assertEqual(frozen, objects)
        # Remote manifest alone can also reconstruct lost local receipt state.
        (self.state / 'local-website-benchmarks.json').unlink()
        self.assertEqual(first, upload_local(client, self.context, source, 'website-benchmarks', self.state))
        self.assertEqual(frozen, objects)

    def test_incomplete_recovery_inventory_blocks_all_deletes(self):
        client, _ = self.storage()
        save(self.state / 'inventory.json', {'objects': [{'key': PUBLIC_ROOT + 'old/a'}]})
        (self.state / 'copy-receipts').mkdir()
        with self.assertRaisesRegex(ValueError, 'entire frozen inventory'):
            retire(client, self.context, self.state)
        client.delete_object.assert_not_called()

    def test_parallel_deletion_requires_remote_receipts_and_resumes(self):
        client, objects = self.storage()
        key = PUBLIC_ROOT + 'old/scene.json'
        body = b'original evidence'
        objects[key] = body
        row = copy_one(client, self.context, {'key': key, 'etag': '"source"', 'bytes': len(body)})
        save(self.state / 'inventory.json', {'objects': [row]})
        receipt_key = self.context['prefix'] + 'manifests/copy-00000.json'
        receipt_body = packed({'rows': [row]})
        objects[receipt_key] = receipt_body
        save(self.state / 'copy-receipts/00000.json', {'rows': [row], 'remote': {
            'key': receipt_key, 'bytes': len(receipt_body),
            'sha256': hashlib.sha256(receipt_body).hexdigest()}})
        result = retire(client, self.context, self.state, workers=2)
        self.assertEqual(1, result['deleted'])
        client.delete_object.assert_called_once_with(Bucket='fixture', Key=key, IfMatch='"source"')
        self.assertIn(self.context['prefix'] + 'manifests/deleted.jsonl', objects)
        retire(client, self.context, self.state, workers=2)
        self.assertEqual(1, client.delete_object.call_count)

    def test_changed_recovery_content_blocks_deletion(self):
        client, objects = self.storage()
        key = PUBLIC_ROOT + 'old/a'
        objects[key] = b'old'
        row = copy_one(client, self.context, {'key': key, 'etag': '"old"', 'bytes': 3})
        save(self.state / 'inventory.json', {'objects': [row]})
        receipt_key = self.context['prefix'] + 'manifests/copy-00000.json'
        body = packed({'rows': [row]})
        objects[receipt_key] = body
        save(self.state / 'copy-receipts/00000.json', {'rows': [row], 'remote': {
            'key': receipt_key, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}})
        objects[row['recovery']['key']] = b'BAD'
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            retire(client, self.context, self.state, workers=2)
        client.delete_object.assert_not_called()

    def test_forged_receipt_cannot_authorize_deleting_root_folder_marker(self):
        client, objects = self.storage()
        row = {'key': PUBLIC_ROOT, 'etag': '"source"', 'bytes': 0,
               'recovery': {'key': self.context['prefix'] + 's3/' + PUBLIC_ROOT,
                            'sha256': hashlib.sha256(b'').hexdigest(), 'bytes': 0}}
        save(self.state / 'inventory.json', {'objects': [row]})
        receipt_key = self.context['prefix'] + 'manifests/copy-00000.json'
        body = packed({'rows': [row]})
        objects[receipt_key] = body
        save(self.state / 'copy-receipts/00000.json', {'rows': [row], 'remote': {
            'key': receipt_key, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}})
        with self.assertRaisesRegex(ValueError, 'Protected'):
            retire(client, self.context, self.state)
        client.delete_object.assert_not_called()

    def test_changed_catalog_is_never_overwritten(self):
        client, objects = self.storage()
        objects['catalog/files-index.json'] = b'{"schemaVersion":1,"keys":[]}'
        save(self.state / 'catalog-before.json', {'etag': '"old"'})
        with self.assertRaisesRegex(ValueError, 'changed since retirement'):
            rebuild_index(client, self.context, self.state)
        client.put_object.assert_not_called()

    def test_local_manifest_excludes_caches_credentials_and_source_code(self):
        root = self.state / 'evidence'
        root.mkdir()
        for name in ('scene.json', 'scene.blend', 'review.log', 'driver.py', 'auth.json', 'id_ed25519'):
            (root / name).write_bytes(b'fixture')
        (root / 'assets').mkdir()
        (root / 'assets' / 'original.blend').write_bytes(b'preserve source')
        (root / 'work-00001').mkdir()
        (root / 'work-00001' / 'data.json').write_bytes(b'cache')
        result = local_inventory(self.state, ['evidence'])
        self.assertEqual({'evidence/scene.json', 'evidence/scene.blend', 'evidence/review.log'},
                         {row['path'] for row in result['members']})
        self.assertEqual(5, len(result['excluded']))

    def test_browser_profiles_and_nested_source_checkouts_are_excluded_as_trees(self):
        root = self.state / 'evidence'
        root.mkdir()
        (root / 'result.json').write_bytes(b'scientific evidence')
        for name in ('chrome-profile', 'chrome-profile-1', 'playwright-user-data',
                     'BrowserContext', 'Local Storage', 'Session Storage',
                     'Service Worker', 'Sync Data', 'IndexedDB', 'venv', 'lib'):
            folder = root / name
            folder.mkdir()
            (folder / 'browser-database.log').write_bytes(b'excluded fixture')
        checkout = root / 'copied-repo'
        checkout.mkdir()
        (checkout / 'package.json').write_bytes(b'{}')
        (checkout / 'fixture.json').write_bytes(b'exclude source metadata')
        result = local_inventory(self.state, ['evidence'])
        self.assertEqual(['evidence/result.json'], [row['path'] for row in result['members']])
        self.assertEqual(12, len(result['excluded']))

    def test_frozen_inventory_phase_resumes_after_missing_remote_backup(self):
        client, objects = self.storage()
        objects['catalog/files-index.json'] = b'{"schemaVersion":1,"keys":[]}'
        frozen = {'context': self.context, 'objects': [{'key': PUBLIC_ROOT + 'old/a', 'bytes': 1}], 'preserved': []}
        save(self.state / 'inventory.json', frozen)
        inventory(client, self.context, self.state)
        client.get_paginator.assert_not_called()
        self.assertEqual(frozen, json.loads((self.state / 'inventory.json').read_bytes()))
        before = (self.state / 'catalog-before.json').read_bytes()
        inventory(client, self.context, self.state)
        self.assertEqual(before, (self.state / 'catalog-before.json').read_bytes())

    def test_arbitrarily_named_browser_profiles_are_excluded_at_every_depth(self):
        root = self.state / 'evidence'
        root.mkdir()
        (root / 'result.json').write_bytes(b'scientific evidence')
        for name, markers in (('Default', ('Preferences', 'History')),
                              ('unrelated-name', ('Local State',))):
            folder = root / name
            folder.mkdir()
            for marker in markers:
                (folder / marker).write_bytes(b'private browser state')
            (folder / 'profile.json').write_bytes(b'exclude browser data')
        result = local_inventory(self.state, ['evidence'])
        self.assertEqual(['evidence/result.json'], [row['path'] for row in result['members']])
        self.assertEqual(2, len(result['excluded']))
        result = local_inventory(self.state, ['evidence/Default'])
        self.assertEqual([], result['members'])
        self.assertEqual(1, len(result['excluded']))

    def test_catalog_phase_recognizes_own_applied_update_after_interruption(self):
        client, objects = self.storage()
        objects['catalog/files-index.json'] = packed({'schemaVersion': 1, 'keys': [PUBLIC_ROOT + 'old/a', 'files/input/data.json']})
        save(self.state / 'catalog-before.json', {'etag': '"hash"'})
        client.get_paginator.return_value.paginate.return_value = [{'Contents': [{'Key': PRESERVED + 'a'}]}]
        # This test bucket only stores catalog writes in this phase.
        def put(**kwargs):
            objects[kwargs['Key']] = kwargs['Body']
        client.put_object.side_effect = put
        first = rebuild_index(client, self.context, self.state)
        count = client.put_object.call_count
        (self.state / 'catalog-after.json').unlink()
        again = rebuild_index(client, self.context, self.state)
        self.assertEqual(first, again)
        self.assertEqual(count, client.put_object.call_count)
        self.assertTrue((self.state / 'catalog-after.json').exists())

    def test_local_shards_recover_baselines_then_evict_only_unchanged_members(self):
        client, objects = self.storage()
        source = self.state / 'old-evidence'
        source.mkdir()
        (source / 'infinigen-scene.json').write_bytes(b'baseline geometry')
        (source / 'soilie-scene.json').write_bytes(b'old geometry')
        (source / 'driver.py').write_bytes(b'preserve implementation')
        roots = [source.relative_to(ROOT / '.codex').as_posix()]
        state = self.state / 'receipts'
        state.mkdir()
        first = archive_local(client, self.context, roots, state, shard_bytes=16)
        self.assertEqual(2, first['files'])
        self.assertEqual(1, first['baselineFiles'])
        self.assertTrue((source / 'infinigen-scene.json').exists())
        self.assertEqual(2, len(list((state / 'local-receipts').glob('*.json'))))
        self.assertFalse(list(state.glob('*.tar.gz')))
        (source / 'new.json').write_bytes(b'preserve newer work')
        removed = archive_local(client, self.context, roots, state, evict=True, shard_bytes=16)
        self.assertGreater(removed['evictedBytes'], 0)
        self.assertTrue((source / 'driver.py').exists())
        self.assertTrue((source / 'new.json').exists())
        self.assertFalse((source / 'infinigen-scene.json').exists())
        self.assertIn(self.context['prefix'] + 'manifests/local-baseline-index.json', objects)

    def test_lost_local_inventory_restores_remote_frozen_manifest(self):
        client, objects = self.storage()
        roots = ['old-evidence']
        frozen = {'roots': roots, 'members': [{'path': 'old-evidence/gone.json',
                   'bytes': 3, 'sha256': hashlib.sha256(b'old').hexdigest()}], 'excluded': []}
        objects[self.context['prefix'] + 'manifests/local-evidence-inventory.json'] = packed(frozen)
        with patch('serverless.benchmark.retire_comparisons.local_inventory') as scan:
            result = archive_local(client, self.context, roots, self.state, inventory_only=True)
        scan.assert_not_called()
        self.assertEqual(1, result['files'])
        self.assertEqual(frozen, json.loads((self.state / 'local-evidence-inventory.json').read_bytes()))
        client.put_object.assert_not_called()

    def test_reusable_baseline_index_preserves_identity_and_excludes_soilie(self):
        client, objects = self.storage()
        key = PUBLIC_ROOT + 'website-comparisons/layoutgpt/geometry.json'
        objects[key] = b'baseline source geometry'
        artifact = {'key': key, 'bytes': len(objects[key]),
                    'sha256': hashlib.sha256(objects[key]).hexdigest()}
        manifest_key = PUBLIC_ROOT + 'website-comparisons/manifest.json'
        objects[manifest_key] = packed({'quantitativeRooms': [
            {'id': 'baseline-01', 'model': 'layoutgpt', 'artifacts': {'geometry': artifact}},
            {'id': 'retired-01', 'model': 'soilie'}]})
        rows = [copy_one(client, self.context, {'key': name, 'etag': '"source"', 'bytes': len(objects[name])})
                for name in (key, manifest_key)]
        receipt_key = self.context['prefix'] + 'manifests/copy-00000.json'
        body = packed({'rows': rows})
        objects[receipt_key] = body
        save(self.state / 'copy-receipts/00000.json', {'rows': rows, 'remote': {
            'key': receipt_key, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}})
        result = baseline_recovery(client, self.context, self.state)
        self.assertEqual(1, result['collections']['quantitativeRooms'])
        baseline = json.loads(objects[result['receipt']['key']])['collections']['quantitativeRooms'][0]
        self.assertEqual('baseline-01', baseline['id'])
        self.assertEqual(artifact, {k: baseline['artifacts']['geometry'][k] for k in artifact})
        self.assertEqual(self.context['prefix'] + 's3/' + key,
                         baseline['artifacts']['geometry']['privateRecovery']['key'])


if __name__ == '__main__':
    unittest.main()
