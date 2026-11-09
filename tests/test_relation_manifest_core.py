import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from modules import relation_manifest as m

ROOT = Path(__file__).resolve().parents[1] / '.codex/tests/catalogs/manifest'

class RelationManifestCoreTests(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / 'relation-inputs.json'

    def test_marker_blocks_loading_and_failed_completion_preserves_marker(self):
        m.begin_relation_run(self.path, collection_id='00000000-0000-4000-8000-000000000001')
        before = self.path.read_bytes()
        with self.assertRaises(m.RelationManifestError): m.load_verified_relation_manifest(self.path, data_root=self.root)
        with self.assertRaises(m.RelationManifestError): m.complete_relation_run(self.path, collection_id='00000000-0000-4000-8000-000000000001', frames=[], data_root=self.root)
        self.assertEqual(self.path.read_bytes(), before)

    def test_atomic_write_failure_keeps_previous_file_and_cleans_stage(self):
        m.begin_relation_run(self.path, collection_id='00000000-0000-4000-8000-000000000001')
        before = self.path.read_bytes()
        with patch.object(m.os, 'replace', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError): m.begin_relation_run(self.path, collection_id='00000000-0000-4000-8000-000000000002')
        self.assertEqual(self.path.read_bytes(), before)
        stage = m._manifest_staging_root(self.path)
        self.assertFalse(stage.exists() and list(stage.glob('*.tmp')))

    def test_duplicate_json_keys_and_unsafe_paths_rejected(self):
        self.path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding='utf-8')
        with self.assertRaises(m.RelationManifestError): m.load_verified_relation_manifest(self.path, data_root=self.root)
        for value in ('../escape.csv', '/absolute.csv', 'a/../../b.csv', 'a\\b.csv', 'CON.csv'):
            with self.subTest(value=value), self.assertRaises(m.RelationManifestError): m._relative_posix(value, field='test')

    def test_digest_and_pose_are_stable(self):
        self.path.write_bytes(b'abc')
        self.assertEqual(m.sha256_file(self.path), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
        pose = [[1,0,0,0],[0,1,0,0],[0,0,1,0]]
        self.assertEqual(m.camera_pose_sha256(pose), m.camera_pose_sha256([[float(x) for x in row] for row in pose]))
