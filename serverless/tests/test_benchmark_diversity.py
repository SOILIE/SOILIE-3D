import json
from pathlib import Path
import tempfile
import unittest

from serverless.benchmark.diversity import coverage_sample, freeze
from serverless.benchmark.run_batch import load_request_plan
from serverless.common.v4_runtime import ROOM_COMBINATION_FILES


class DiversityTests(unittest.TestCase):
    def test_sampling_covers_rare_labels_without_seeing_quality(self):
        candidates = [('bed','chair','desk'),('bed','chair','lamp'),('sink','toilet','towel')]
        chosen = coverage_sample(candidates,3,42)
        self.assertEqual(chosen,coverage_sample(candidates,3,42))
        self.assertEqual(set(candidates),set(chosen))
        self.assertIn(('sink','toilet','towel'),chosen[:2])

    def test_frozen_inputs_cover_modes_counts_duplicates_and_empty_preset(self):
        base = Path(__file__).parents[2]/'.codex/tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            root = Path(folder)
            (root/'data').mkdir()
            (root/'assets').mkdir()
            (root/'assets/asset_rotations.csv').write_text('object_name,asset_name,x,y,z\nsink,sink_0001.obj,0,0,0\ntoilet,toilet_0001.obj,0,0,0\ntowel,towel_0001.obj,0,0,0\n')
            for label in ('sink','toilet','towel'):
                (root/f'assets/{label}_0001.obj').touch()
            (root/'data/object_sizes_manual.csv').write_text('object,diameter,count,sizecat\nsink,1,1,large\ntoilet,1,1,large\ntowel,1,1,small\n')
            (root/'data/triplets.csv').write_text('objectA,objectB,objectC\nsink,toilet,towel\nsink,toilet,unavailable_asset\n')
            for filename in [*ROOM_COMBINATION_FILES.values(),'working-combos-refined.csv']:
                content = 'obj0,obj1,obj2,obj3,obj4,obj5\n'
                if 'bathroom' not in filename:
                    content += 'bed,chair,desk,lamp,table,chair\n'
                (root/'data'/filename).write_text(content)
            plan = freeze(root,repetitions=1,explicit_limit=4)
            self.assertEqual(plan,freeze(root,repetitions=1,explicit_limit=4))
            self.assertEqual(['bathroom'],plan['emptyPresets'])
            self.assertEqual(1,plan['registeredAssetTriplets'])
            self.assertIn('sink',plan['explicitClasses'])
            self.assertNotIn('unavailable_asset',plan['explicitClasses'])
            (root/'plan.json').write_text(json.dumps(plan))
            requests = load_request_plan(root/'plan.json')
            self.assertEqual({'objects','room_type','random'},{v['mode'] for v in requests})
            self.assertEqual({3,4,5,6},{v['objectCount'] for v in requests if 'objectCount' in v})
            self.assertEqual({False,True},{v['allowDuplicates'] for v in requests if 'allowDuplicates' in v})
            self.assertEqual(len(requests),len({v['seed'] for v in requests}))
            plan['requests'][0]['room'] = {'mode':'custom'}
            (root/'plan.json').write_text(json.dumps(plan))
            with self.assertRaises(ValueError):
                load_request_plan(root/'plan.json')
