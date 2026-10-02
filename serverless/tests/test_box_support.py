from copy import deepcopy
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from serverless.benchmark.box_support import measure_box_support
from serverless.benchmark.geometry import box_corners
from serverless.cloud_benchmark.compile_box_support import one
from serverless.cloud_benchmark.staged_pilot import digest


def item(name, center, size, yaw=0):
    return {'id':name, 'label':'table', 'corners':box_corners(center, size, yaw)}


def scene(*objects):
    return {'units':'m', 'objects':list(objects), 'room':{'floorZ':0}}


class BoxSupportTests(unittest.TestCase):
    def test_floor_gap_and_penetration_are_separate(self):
        result = measure_box_support(scene(item('a', [0,0,.8], [1,1,1]), item('b', [3,0,.4], [1,1,1])))
        self.assertAlmostEqual(30, result['objects'][0]['gapCm'])
        self.assertEqual(0, result['objects'][1]['gapCm'])
        self.assertAlmostEqual(10, result['objects'][1]['belowFloorCm'])

    def test_stacked_touching_gapped_and_intersecting(self):
        lower = item('table', [0,0,.5], [2,2,1])
        for z, gap in ((1.1, 0), (1.3, 20), (.95, 0)):
            result = measure_box_support(scene(lower, item('cup', [0,0,z], [.2,.2,.2])))
            self.assertEqual('object', result['objects'][1]['supportKind'])
            self.assertAlmostEqual(gap, result['objectGapCm'])
        self.assertEqual(0, measure_box_support(scene(lower))['floorGapCm'])

    def test_disjoint_and_edge_only_footprints_do_not_supply_support(self):
        for x in (1.5, 5):
            result = measure_box_support(scene(item('lower',[0,0,.5],[2,2,1]),item('upper',[x,0,2],[1,1,1])))
            self.assertEqual('floor', result['objects'][1]['supportKind'])
            self.assertEqual(150, result['objects'][1]['gapCm'])

    def test_yaw_and_translation_do_not_change_gap(self):
        for yaw in (0, 25, 90):
            result = measure_box_support(scene(item('lower',[-10,3,.5],[2,2,1],yaw),item('upper',[-10,3,1.4],[1,1,.4],yaw)))
            self.assertAlmostEqual(20, result['objectGapCm'])

    def test_pitched_box_uses_surface_at_shared_horizontal_position(self):
        lower = item('lower',[0,0,.5],[2,2,1])
        upper = item('upper',[0,0,0],[.2,.2,.2])
        angle=math.pi/4
        upper['corners']=[[x*math.cos(angle)+z*math.sin(angle), y,
                           1.5-x*math.sin(angle)+z*math.cos(angle)] for x,y,z in upper['corners']]
        result=measure_box_support(scene(lower,upper))
        self.assertAlmostEqual((.5-.1*math.sqrt(2))*100, result['objectGapCm'], places=6)

    def test_other_object_above_is_not_a_support(self):
        result=measure_box_support(scene(item('low',[0,0,.8],[1,1,1]),item('high',[0,0,3],[2,2,1])))
        self.assertAlmostEqual(30, result['objects'][0]['gapCm'])

    def test_sloping_candidate_can_be_above_shared_footprint(self):
        rod=item('rod',[0,0,0],[4,.2,.1]); angle=math.pi/3
        rod['corners']=[[x*math.cos(angle)-z*math.sin(angle), y,
                         1.9+x*math.sin(angle)+z*math.cos(angle)] for x,y,z in rod['corners']]
        result=measure_box_support(scene(rod,item('target',[.9,0,.5],[.1,.1,.2])))
        self.assertEqual('floor',result['objects'][1]['supportKind'])
        self.assertAlmostEqual(40,result['objects'][1]['gapCm'])

    def test_mesh_annotations_do_not_change_box_measurement(self):
        source=scene(item('a',[0,0,1],[1,1,1])); expected=measure_box_support(source)
        source['objects'][0].update(supportEligible=False,support={'gapM':0})
        self.assertEqual(expected,measure_box_support(source))
        before=deepcopy(source); measure_box_support(source); self.assertEqual(before,source)

    def test_units_ids_wall_mounted_and_assemblies(self):
        source=scene(item('a',[0,0,1],[1,1,1])); source['units']='px'
        with self.assertRaises(ValueError):measure_box_support(source)
        source['units']='m'; source['objects'].append(deepcopy(source['objects'][0]))
        with self.assertRaises(ValueError):measure_box_support(source)
        source=scene(item('clock',[0,0,1],[1,1,1])); source['objects'][0]['label']='clock'
        self.assertIsNone(measure_box_support(source)['gapCm'])
        source=scene(item('a',[0,0,.5],[1,1,1]),item('b',[0,0,1.5],[1,1,1]))
        for obj in source['objects']:obj['assemblyId']='same'
        self.assertEqual('floor',measure_box_support(source)['objects'][1]['supportKind'])

    def test_ceiling_lights_are_not_misclassified_as_floating_furniture(self):
        for label in ('ceiling lamp','pendant_lamp','ceiling_light','wall lamp','wall_art','wall art'):
            source=scene(item('light',[0,0,2],[.2,.2,.2])); source['objects'][0]['label']=label
            result=measure_box_support(source)
            self.assertEqual(['light'],result['excludedObjects'])
            self.assertIsNone(result['gapCm'])

    def test_reclassification_preserves_distances_and_matches_fresh_measurement(self):
        from serverless.benchmark.box_support import reclassify_measurement
        source = scene(item('art',[0,0,2],[1,1,1]), item('chair',[3,0,1],[1,1,1]))
        old = measure_box_support(source)
        old['objects'][0]['label'] = 'wall_art'
        source['objects'][0]['label'] = 'wall_art'
        previous = deepcopy(old)
        corrected = reclassify_measurement(old)
        self.assertEqual(measure_box_support(source), corrected)
        self.assertEqual(corrected, reclassify_measurement(corrected))
        self.assertEqual(previous, old)
        self.assertEqual(previous['objects'][1], corrected['objects'][0])

    def test_archive_and_scale_checksums_and_centimetres(self):
        root=Path(__file__).resolve().parents[2]/'.codex/tests';root.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=root) as tmp:
            source=scene(item('a',[0,0,8],[10,10,10]))
            source.update(id='fixture',model='layoutgpt',roomType='bedroom',units='px')
            path=Path(tmp)/'scene.json';raw=json.dumps({'scene':source}).encode();path.write_bytes(raw)
            entry={'id':'fixture','model':'layoutgpt','roomType':'bedroom','artifacts':{'geometry':{'sha256':digest(raw)}}}
            scale={'sceneSha256':digest(source),'metresPerPixel':.01}
            result=one((path,entry,scale))
            self.assertAlmostEqual(3,result['gapCm'])
            self.assertEqual(raw,path.read_bytes())
            scale['sceneSha256']='changed'
            with self.assertRaises(ValueError):one((path,entry,scale))
            entry['artifacts']['geometry']['sha256']='changed'
            with self.assertRaises(ValueError):one((path,entry,scale))
