import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import Mock

from serverless.arbutus.archive import archive_room, bundle, owned
from serverless.arbutus.worker import sha
from serverless.tests.test_arbutus_inventory import temporary_directory


class ArchiveTests(unittest.TestCase):
    def fixture(self,root):
        task=root/'local/infinigen-bedroom-000'
        work=task/'attempt-00'
        work.mkdir(parents=True)
        scene=b'{"id":"fixture","objects":[]}'
        (work/'scene.json').write_bytes(scene)
        members={'scene/scene.blend':b'native fixture bytes','scene/solve_state.json':b'{"objs":{}}',
                 'scene/assets/info.pickle':b'private metadata','started.json':b'{"command":"private"}',
                 'generation.log':b'private execution log','scene.json':scene}
        source=work/'artifacts.tar.gz'
        with tarfile.open(source,'w:gz') as archive:
            for name,raw in members.items():
                member=tarfile.TarInfo(name);member.size=len(raw)
                archive.addfile(member,io.BytesIO(raw))
                target=work/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(raw)
        record={'id':task.name,'planSha256':'a'*64,'task':{'roomType':'bedroom'},
            'artifactsSha256':sha(source),'sceneSha256':sha(work/'scene.json'),
            'artifactPath':'attempt-00/artifacts.tar.gz'}
        return task,source,record

    def client(self,wrong=False):
        client=Mock()
        stored={}
        def put(**kw):
            body=kw['Body'];stored[kw['Key']]=body if isinstance(body,bytes) else body.read()
            return {}
        def head(**kw):
            body=stored[kw['Key']]
            return {'ContentLength':len(body)+(1 if wrong else 0),
                'ChecksumSHA256':base64.b64encode(hashlib.sha256(body).digest()).decode()}
        client.put_object.side_effect=put
        client.head_object.side_effect=head
        return client,stored

    def test_public_bundle_excludes_execution_logs_and_retains_them_locally(self):
        with temporary_directory() as folder:
            task,source,record=self.fixture(Path(folder))
            staged,members=bundle(source,task)
            with tarfile.open(staged) as archive:
                self.assertEqual({'scene/scene.blend','scene/solve_state.json'},set(archive.getnames()))
            self.assertEqual(b'private execution log',(task/'private-evidence/generation.log').read_bytes())
            self.assertEqual(6,len(members))
            self.assertTrue(source.exists())
            first=sha(staged)
            staged,members=bundle(source,task)
            self.assertEqual(first,sha(staged))

    def test_verification_failure_never_deletes_local_data(self):
        with temporary_directory() as folder:
            root=Path(folder);task,source,record=self.fixture(root)
            client,_=self.client(wrong=True)
            with self.assertRaisesRegex(ValueError,'size'):
                archive_room(client,root,'local',record,'files/outputs/test/')
            self.assertTrue(source.exists())
            self.assertTrue((source.parent/'scene/scene.blend').exists())
            self.assertFalse((task/'s3-receipt.json').exists())

    def test_verified_upload_evicts_only_recoverable_completed_files(self):
        with temporary_directory() as folder:
            root=Path(folder);task,source,record=self.fixture(root)
            sibling=task/'attempt-01';sibling.mkdir();(sibling/'active.blend').write_bytes(b'active')
            client,stored=self.client()
            result=archive_room(client,root,'local',record,'files/outputs/test/')
            self.assertTrue(result['evictionComplete'])
            self.assertFalse(source.exists())
            self.assertFalse((source.parent/'scene/scene.blend').exists())
            self.assertTrue((sibling/'active.blend').exists())
            self.assertTrue((source.parent/'scene.json').exists())
            self.assertEqual(2,len(stored))
            count=client.put_object.call_count
            archive_room(client,root,'local',record,'files/outputs/test/')
            self.assertEqual(count,client.put_object.call_count)

    def test_owned_targets_reject_siblings(self):
        with temporary_directory() as folder:
            root=Path(folder)
            with self.assertRaises(ValueError):owned(root/'../outside',root)


if __name__=='__main__': unittest.main()
