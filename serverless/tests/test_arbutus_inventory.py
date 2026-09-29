from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest

from serverless.arbutus.cloud import cloud_init
from serverless.arbutus.inventory_plan import prepare, target_inventory
from serverless.arbutus.worker import selected, validate_inventory
from serverless.arbutus.relay import snapshot, verify_archive

SCRATCH=Path(__file__).resolve().parents[2]/'.codex/test-arbutus'


def temporary_directory():
    SCRATCH.mkdir(parents=True,exist_ok=True)
    return tempfile.TemporaryDirectory(dir=SCRATCH)


def metadata():
    return {
        'room':{'tags':['Semantics(room)','Semantics(bedroom)'],'relations':[]},
        'bed':{'generator':{},'tags':['Semantics(object)','Semantics(bed)','FromGenerator(BedFactory)'],
               'relations':[{'target_name':'room'}]},
        'table':{'generator':{},'tags':['Semantics(object)','FromGenerator(SideTableFactory)'],
                 'relations':[{'target_name':'room'},{'target_name':'bed'}]},
    }


def row(name,room,labels):
    return {'scene':{'id':name,'model':'soilie','roomType':room,
        'objects':[{'id':str(i),'label':label} for i,label in enumerate(labels)]}}


class InventoryTests(unittest.TestCase):
    def test_plan_does_not_use_quality_or_input_order(self):
        rows=[row('a','bedroom',['bed','desk','chair']),row('b','living_room',['sofa','chair','coffee table'])]
        a=prepare(rows,per_room=1)
        changed=deepcopy(rows[::-1])
        for r in changed:r['metrics']={'quality':-100,'overlap':9999}
        self.assertEqual(a,prepare(changed,per_room=1))
        self.assertEqual(4,len(a['tasks']))
        for task in a['tasks']: self.assertEqual(task['inventory'],task['sourceInventory'])

    def test_one_substitution_not_two_and_no_deletion(self):
        source=row('a','bedroom',['bed','desk','pillow'])['scene']
        original=deepcopy(source)
        result=target_inventory(source,'layoutgpt')
        self.assertEqual({'bed':1,'desk':1,'nightstand':1},result['inventory'])
        self.assertEqual([{'from':'pillow','to':'nightstand'}],result['substitutions'])
        self.assertEqual(original,source)
        self.assertIsNone(target_inventory(row('b','bedroom',['bed','pillow','pillow'])['scene'],'layoutgpt'))

    def test_duplicate_instances_preserved(self):
        result=target_inventory(row('a','living_room',['sofa','chair','chair'])['scene'],'infinigen')
        self.assertEqual({'sofa':1,'chair':2},result['inventory'])

    def test_local_and_cloud_partitions_cover_once(self):
        tasks=[{'id':f'infinigen-bedroom-{i:03d}','baseline':'infinigen','needsGeneration':True} for i in range(120)]
        self.assertEqual(24,sum(selected(t,'local') for t in tasks))
        self.assertEqual(96,sum(selected(t,'arbutus') for t in tasks))
        for task in tasks:self.assertNotEqual(selected(task,'local'),selected(task,'arbutus'))

    def test_existing_pair_retention_is_not_regenerated(self):
        rows=[row('a','bedroom',['bed','desk','chair']),row('b','living_room',['sofa','chair','coffee table'])]
        audit={'groups':{f'{b}:{r}':{'withinOne':{'pairs':[]}}
            for b in ['layoutgpt','infinigen_controlled'] for r in ['bedroom','living_room']}}
        audit['groups']['layoutgpt:bedroom']['withinOne']['pairs']=[{
            'retainedPair':True,'soilieScene':'a','baselineScene':'old',
            'soilieInventory':{'bed':1,'desk':1,'chair':1},'baselineInventory':{'bed':1,'desk':1,'chair':1}}]
        plan=prepare(rows,per_room=1,audit=audit)
        retained=[t for t in plan['tasks'] if not t['needsGeneration']]
        self.assertEqual(1,len(retained));self.assertEqual('old',retained[0]['retainedBaselineSceneId'])

    def test_only_public_key_enters_cloud_init(self):
        text=cloud_init('ssh-ed25519 AAAABBBB fixture')
        self.assertIn('ssh_pwauth: false',text)
        self.assertNotIn('application_credential',text)
        with self.assertRaises(ValueError):cloud_init('-----BEGIN OPENSSH PRIVATE KEY-----')

    def test_progress_counts_validated_results_not_api_successes(self):
        rows=[row('a','bedroom',['bed','desk','chair']),row('b','living_room',['sofa','chair','coffee table'])]
        plan=prepare(rows,per_room=1)
        with temporary_directory() as directory:
            root=Path(directory)
            (root/'campaign-v1.json').write_text(json.dumps(plan))
            (root/'layoutgpt').mkdir()
            (root/'layoutgpt/inference-ledger.json').write_text(json.dumps({'entries':{
                'layoutgpt-bedroom-000':{'status':'complete','actualUsd':.1,'wallSeconds':3}}}))
            state=snapshot(root,plan,{})
            self.assertEqual(0,state['completed'])
            self.assertEqual(.1,state['apiAccountedUsd'])
            (root/'layoutgpt/export.json').write_text(json.dumps({'attempts':[{
                'id':'layoutgpt-bedroom-000','geometryStatus':'complete','inventorySatisfied':True}]}))
            state=snapshot(root,plan,{})
            self.assertEqual(1,state['completed'])

    def test_bedside_role_requires_actual_bed_relation(self):
        task={'roomType':'bedroom','inventory':{'bed':1,'nightstand':1}}
        records=metadata()
        self.assertEqual({'bed':'bed','table':'nightstand'},validate_inventory(records,task))
        records['table']['relations']=[{'target_name':'room'}]
        with self.assertRaisesRegex(ValueError,'bedside relationship'):
            validate_inventory(records,task)

    def test_native_missing_or_extra_objects_rejected(self):
        task={'roomType':'bedroom','inventory':{'bed':1,'nightstand':2}}
        with self.assertRaisesRegex(ValueError,'INVENTORY_MISMATCH'):
            validate_inventory(metadata(),task)

    def test_archive_and_embedded_scene_hashes_required(self):
        with temporary_directory() as directory:
            task=Path(directory)
            raw=b'{"id":"fixture"}'
            archive=task/'artifacts.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                member=tarfile.TarInfo('scene.json');member.size=len(raw)
                tar.addfile(member,io.BytesIO(raw))
            record={'artifactsSha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                    'sceneSha256':'0'*64}
            with self.assertRaisesRegex(ValueError,'Scene metadata checksum'):
                verify_archive(task,record)
            self.assertFalse((task/'verified.json').exists())
            record['sceneSha256']=hashlib.sha256(raw).hexdigest()
            verify_archive(task,record)
            self.assertEqual(raw,(task/'scene.json').read_bytes())
            self.assertTrue((task/'verified.json').exists())
            record['artifactsSha256']='0'*64
            with self.assertRaisesRegex(ValueError,'Archive checksum'):
                verify_archive(task,record)

    def test_credit_exhaustion_visible_without_inference_calls(self):
        rows=[row('a','bedroom',['bed','desk','chair']),row('b','living_room',['sofa','chair','coffee table'])]
        plan=prepare(rows,per_room=1)
        with temporary_directory() as directory:
            root=Path(directory)
            (root/'campaign-v1.json').write_text(json.dumps(plan))
            (root/'layoutgpt').mkdir()
            (root/'layoutgpt/inference-ledger.json').write_text(json.dumps({'entries':{
                'layoutgpt-bedroom-000':{'status':'error','errorCode':'credit_balance_exhausted','reservedUsd':.31}}}))
            state=snapshot(root,plan,{})
            self.assertIn('balance exhausted',state['warning'])
            self.assertIsNone(state['etaSeconds'])


if __name__=='__main__':unittest.main()
