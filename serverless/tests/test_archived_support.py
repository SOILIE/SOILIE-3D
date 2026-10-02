from copy import deepcopy
import unittest

from serverless.cloud_benchmark.refresh_archived_support import validate_replay


class ArchivedSupportTests(unittest.TestCase):
    def fixture(self):
        obj = dict(id='bag', label='bag', asset='bag.obj', frontDirection=[0,1,0],
                   corners=[[0,0,1]], transform=[[1,0,0,0],[0,1,0,0],[0,0,1,1],[0,0,0,1]])
        original = {'source': {'sha256': 'pinned'}, 'record': {'scene': {'room': {}, 'objects': [obj]}}}
        replay = dict(source=deepcopy(original['source']), scene=deepcopy(original['record']['scene']),
                      control={'moves': []}, correction={'moves': []})
        return replay, original

    def test_unchanged_and_vertical_only_replays(self):
        replay, original = self.fixture()
        self.assertEqual(validate_replay(replay, original), set())
        replay['scene']['objects'][0]['transform'][2][3] = 0
        replay['scene']['objects'][0]['corners'][0][2] = 0
        replay['correction']['moves'] = [{'id': 'bag', 'dropM': 1}]
        self.assertEqual(validate_replay(replay, original), {'bag'})

    def test_rejects_incomplete_or_incidental_changes(self):
        for mutation in ('inventory','room','control','horizontal','identity','unreported'):
            with self.subTest(mutation=mutation):
                replay, original = self.fixture()
                if mutation == 'inventory': replay['scene']['objects'] = []
                if mutation == 'room': replay['scene']['room']['height'] = 2
                if mutation == 'control': replay['control']['moves'] = [{'id':'bag'}]
                if mutation == 'horizontal': replay['scene']['objects'][0]['transform'][0][3] = 1
                if mutation == 'identity': replay['scene']['objects'][0]['label'] = 'lamp'
                if mutation == 'unreported': replay['scene']['objects'][0]['transform'][2][3] = 0
                with self.assertRaises(ValueError): validate_replay(replay, original)
