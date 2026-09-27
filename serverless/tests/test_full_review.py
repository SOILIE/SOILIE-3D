"""Synthetic judgments only; tests never write to real campaign directories."""
from collections import Counter
from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from shapely.geometry import LineString, Point
from serverless.benchmark.geometry import box_corners
from serverless.benchmark.functional_evidence import spatial_facts, task_evidence, reference_catalog
from serverless.benchmark.stimuli import diagram
from serverless.cloud_benchmark.full_review import bucket, make_assignments, results
from serverless.cloud_benchmark.staged_pilot import SCHEMA, digest, write_new, record, delivery_prompt
from serverless.study import functional_rubric_v3 as v3
from serverless.study import structured_rubric as v2
from serverless.tests.test_staged_pilot import cases
from serverless.tests.test_structured_pilot import scene


class FullReviewTests(unittest.TestCase):
    def test_all_nine_vote_combinations(self):
        expected = {('model_a','model_a'):('model_a','win_win'), ('model_b','model_b'):('model_b','win_win'),
                    ('tie','tie'):('tie','same_tie'), ('model_a','model_b'):('disagreement','opposite_winner')}
        for winner in ('model_a','model_b'):
            expected[winner,'tie'] = winner,'win_tie'
        expected.update({(b,a):v for (a,b),v in list(expected.items())})
        self.assertEqual(9, len(expected))
        for inputs, result in expected.items():
            self.assertEqual(result, bucket(*inputs))
        with self.assertRaises(ValueError): bucket('left','right')

    def test_full_assignment_balance_opposite_sides_and_unique_context_slots(self):
        source = cases(120)
        rows = make_assignments(source)
        self.assertEqual(rows, make_assignments(list(reversed(source))))
        self.assertEqual(4800, len(rows))
        lookup = {r['assignmentId']:r for r in rows}
        self.assertEqual(4800, len(lookup))
        for row in rows:
            other = lookup[row['pairedWith']]
            self.assertEqual(row['leftSource'], other['rightSource'])
            self.assertEqual(row['leftCondition'], other['rightCondition'])
            self.assertEqual(row['profile'], other['profile'])
            self.assertEqual(row['pairId'], other['pairId'])
            self.assertNotEqual(row['reviewerId'], other['reviewerId'])
        counts = Counter((r['reviewerId'], r['baseline'], r['roomType'], r['leftCondition']=='soilie') for r in rows)
        self.assertEqual({60}, set(counts.values()))

    def test_geometry_scale_invariance_and_no_volume_leakage(self):
        original = scene()
        baseline = spatial_facts(original)
        for scale in (.01,100):
            scaled = deepcopy(original)
            scaled['room']['polygon'] = [[v*scale for v in p] for p in scaled['room']['polygon']]
            for o in scaled['objects']:
                o['corners'] = [[v*scale for v in p] for p in o['corners']]
            self.assertEqual(baseline, spatial_facts(scaled))
        for profile in ('orientation','relationships','access','room_function'):
            evidence = task_evidence(original, profile)
            self.assertNotIn('volumeRatios', evidence)
            self.assertIn('spatialFacts', evidence)
        size = task_evidence(original, 'proportions')
        self.assertNotIn('spatialFacts', size)
        self.assertEqual(3, len(size['catalogPairExamples']))
        self.assertTrue(all(all(v > 0 for v in p['dimensionsIn']) for p in reference_catalog()['products']))

    def test_front_gap_and_overlap_not_conflated(self):
        source = scene()
        source['objects'] = [
            {'id':'sofa','label':'sofa','corners':box_corners([1,2,.5],[1,2,1]),
             'frontDirection':[1,0],'frontConvention':'fixture functional front'},
            {'id':'table','label':'coffee table','corners':box_corners([2.01,2,.25],[1,2,.5])}]
        facts = spatial_facts(source)
        self.assertEqual([0,0], facts['pairs'][0]['boxIntersectionFractions'])
        neighbor = facts['fronts'][0]['neighborsAcrossFront'][0]
        self.assertEqual(.01, neighbor['signedGapInObjectDepths'])
        self.assertEqual(1, neighbor['frontageFraction'])

    def test_clear_chair_candidate_and_unknown_are_not_movement_verdicts(self):
        source = scene()
        source['objects'] = [source['objects'][1]]
        candidate = spatial_facts(source)['chairCandidates'][0]
        self.assertEqual('box_clear_candidate', candidate['status'])
        source['room']['polygon'] = [[2.75,1.75],[3.25,1.75],[3.25,2.25],[2.75,2.25]]
        candidate = spatial_facts(source)['chairCandidates'][0]
        self.assertEqual('not_established', candidate['status'])

    def test_adjacent_chairs_are_not_reported_as_overlapping_or_immovable(self):
        source=scene()
        source['room']['polygon']=[[-.68,-2.1],[2.12,-2.1],[2.12,3.17],[-.68,3.17]]
        specs=[('chair-a','chair',[.3565,1.476,.6],[.6,.48,1.2],[1,0]),
               ('chair-b','chair',[.36,2.016,.6],[.593,.58,1.2],[0,-1]),
               ('table','table',[-.005, -.018,.3],[1.323,2.49,.6],None),
               ('storage','storage',[.829,1.655,.58],[.325,.695,1.16],[1,0])]
        source['objects']=[]
        for key,label,center,size,front in specs:
            item={'id':key,'label':label,'corners':box_corners(center,size)}
            if front: item.update(frontDirection=front,frontConvention='fixture functional front')
            source['objects'].append(item)
        facts=spatial_facts(source)
        chairs=[r for r in facts['pairs'] if r['objects']==['1','2']][0]
        self.assertEqual([0,0],chairs['boxIntersectionFractions'])
        self.assertEqual(['box_clear_candidate']*2,[r['status'] for r in facts['chairCandidates']])

    def test_front_layers_minimum_length_labels_clear_and_geometry_unchanged(self):
        original = scene()
        before = deepcopy(original)
        old = ET.fromstring(diagram(original, numbered=True))
        new = ET.fromstring(diagram(original, numbered=True, clear_fronts=True))
        polygons = lambda r:[n.attrib for n in r.iter() if n.tag.endswith('polygon')]
        self.assertEqual(polygons(old), polygons(new))
        self.assertEqual(before, original)
        arrows = [n for n in new.iter() if 'data-front-object' in n.attrib]
        self.assertEqual(9,len(arrows))
        reserved=[]
        for n in arrows:
            a,b = [(float(n.get('x'+k)),float(n.get('y'+k))) for k in ('1','2')]
            self.assertGreaterEqual(math.dist(a,b),37.98)
            reserved.append(LineString([a,b]).buffer(10))
        circles = [n for n in new.iter() if n.tag.endswith('circle') and n.get('r')=='11']
        for n in circles:
            circle=Point(float(n.get('cx')),float(n.get('cy'))).buffer(11)
            self.assertFalse(any(circle.intersects(line) for line in reserved))
        children = list(new)
        layers = [i for i,n in enumerate(children) if 'data-front-layer' in n.attrib]
        self.assertEqual(3,len(layers))
        for i in layers:
            self.assertTrue(all(n.tag.endswith('line') for n in children[i]))

    def test_frozen_furniture_rules_and_no_desired_outcome(self):
        common = v3.prompt('access')
        for fragment in ('Foot-end-only', 'full front', 'TV stand', 'working position', 'not proof', 'not failure'):
            self.assertIn(fragment, common)
        self.assertIn('reference points on BOTH sides', v3.prompt('proportions'))
        self.assertIn('Name the actual interfering object', v3.prompt('relationships'))
        for profile in v3.PROFILES:
            text = v3.prompt(profile).lower()
            for name in ('soilie','layoutgpt','infinigen','90%','95%'):
                self.assertNotIn(name,text)

    def test_records_bucket_physical_room_not_screen_side_and_keep_partial_pending(self):
        scratch = Path(__file__).parents[2]/'.codex/tests'
        scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            root=Path(temp)
            pair=make_assignments(cases(1))
            row=pair[0]
            rows=[row,next(r for r in pair if r['assignmentId']==row['pairedWith'])]
            reviewers=[{'reviewerId':r['reviewerId'],'promptHash':'p','systemPromptHash':'s','reviewPrompt':'fixture'} for r in rows]
            protocol={'rubricVersion':v3.VERSION,'stage':'full_counterbalanced','assignments':rows,'reviewers':reviewers,
                      'model':'gpt-5.6-sol','reasoningEffort':'xhigh','outputSchema':v2.schema(SCHEMA),
                      'retainedRoomFunctionSha256':digest([]),'developmentPairIds':[],
                      'evidenceInstruction':'fixture','aggregation':{'interpretation':'fixture'}}
            for r in rows:
                r['evidence']={side:task_evidence(scene(),r['profile']) for side in ('left','right')}
                r['evidenceSha256']=digest(r['evidence'])
                r['deliveryPromptHash']=digest(delivery_prompt(protocol,r).encode())
            write_new(root/'protocol.json',protocol)
            write_new(root/'protocol-sha256.json',{'sha256':digest(protocol)})
            write_new(root/'private/retained-room-function.json',[])
            write_new(root/'packets/index.json',{r['assignmentId']:{'imageSha256':'img'} for r in rows})
            for i,r in enumerate(rows):
                choice=next(side for side in ('left','right') if r[side+'Condition']=='soilie')
                answer={'observations':[{'side':side,'objects':list(ids),'severity':'none','evidence':'Synthetic only.'}
                     for side,ids in v2.checklist_keys(r['evidence'],r['profile'])],
                    'judgement':choice,'errorChoice':'neither','confidence':3,'note':'Synthetic only.'}
                receipt={'model':'gpt-5.6-sol','reasoningEffort':'xhigh','promptHash':'p','systemPromptHash':'s',
                         'imageSha256':'img','threadId':str(i),'isolatedContext':True,
                         'evidenceSha256':r['evidenceSha256'],'deliveryPromptHash':r['deliveryPromptHash']}
                record(root,r['assignmentId'],answer,receipt)
                report=results(root)
                self.assertEqual(bool(i),report['complete'])
                self.assertEqual(i,report['overall']['buckets']['model_a'])
                with self.assertRaises(FileExistsError): record(root,r['assignmentId'],answer,receipt)


if __name__ == '__main__':
    unittest.main()
