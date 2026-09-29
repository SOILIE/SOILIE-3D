"""Freeze category-compatible inputs before any baseline generation or scoring.

The SOILIE cohort is read, never regenerated or edited. One unsupported item
may be replaced in the baseline REQUEST, not deleted from either output. Every
substitution is explicit; an exact match is preferred. No quality score enters
selection. This is a new inventory-conditioned baseline, not a native workload.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from serverless.benchmark.inventory_matching import signature

SEED='shared-inventory-arbutus-v1'
INFINIGEN={'bed','sofa','chair','desk','storage','nightstand','coffee table','dining table','tv stand'}
LAYOUTGPT={
    'bedroom': {'bed':'double_bed','sofa':'sofa','chair':'chair','desk':'desk','storage':'wardrobe',
                'nightstand':'nightstand','coffee table':'coffee_table','table':'table','tv stand':'tv_stand'},
    'living_room': {'sofa':'multi_seat_sofa','chair':'dining_chair','desk':'desk','storage':'cabinet',
                    'coffee table':'coffee_table','dining table':'dining_table','tv stand':'tv_stand',
                    'shelf':'bookshelf','side table':'corner_side_table'},
}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def target_inventory(scene, baseline):
    inv=signature(scene)
    room=scene['roomType']
    anchor={'bedroom':'bed','living_room':'sofa'}[room]
    if not 3 <= sum(inv.values()) <= 6 or inv[anchor] != 1:
        return None
    supported=INFINIGEN if baseline=='infinigen' else set(LAYOUTGPT[room])
    missing=[name for name,count in sorted(inv.items()) if name not in supported for _ in range(count)]
    if len(missing)>1:
        return None
    target=inv.copy()
    substitutions=[]
    if missing:
        replacement='nightstand' if room=='bedroom' else 'storage'
        target.subtract([missing[0]])
        target += Counter([replacement])
        substitutions=[{'from':missing[0],'to':replacement}]
    # The bedside role has an explicit bed support relationship. It is not a
    # blanket relabelling of every side table in previously generated scenes.
    return {'sourceInventory':dict(sorted(inv.items())), 'inventory':dict(sorted(target.items())),
            'substitutions':substitutions}


def prepare(rows, per_room=120, audit=None):
    tasks=[]
    sources={row['scene']['id']:row['scene'] for row in rows if row['scene']['model']=='soilie'}
    for baseline in ('infinigen','layoutgpt'):
        for room in ('bedroom','living_room'):
            old=[]
            if audit:
                key=('infinigen_controlled' if baseline=='infinigen' else baseline)+':'+room
                old=[p for p in audit['groups'][key]['withinOne']['pairs'] if p['retainedPair']]
            reserved={p['soilieScene'] for p in old}
            for index,pair in enumerate(old):
                scene=sources[pair['soilieScene']]
                if dict(signature(scene)) != pair['soilieInventory']:
                    raise ValueError('Retained inventory audit no longer matches source')
                tasks.append({'id':f'{baseline}-{room}-{index:03d}','baseline':baseline,'roomType':room,
                    'needsGeneration':False,'retainedBaselineSceneId':pair['baselineScene'],
                    'soilieSceneId':scene['id'],'soilieSceneSha256':digest(scene),
                    'sourceInventory':pair['soilieInventory'],'inventory':pair['baselineInventory'],
                    'retentionAudit':pair})
            candidates=[]
            for row in rows:
                scene=row['scene']
                if scene['model']!='soilie' or scene['roomType']!=room or scene['id'] in reserved:
                    continue
                target=target_inventory(scene,baseline)
                if target is None: continue
                candidates.append((len(target['substitutions']),digest([SEED,baseline,room,scene['id']]),scene,target))
            candidates.sort(key=lambda item:item[:2])
            required=per_room-len(old)
            if required<0 or len(candidates)<required:
                raise ValueError(f'Only {len(candidates)} supported {baseline}/{room} inventories; target {per_room}')
            for index,(_,_,scene,target) in enumerate(candidates[:required],start=len(old)):
                tasks.append({'id':f'{baseline}-{room}-{index:03d}', 'baseline':baseline,'roomType':room,
                    'needsGeneration':True,
                    'seed':90000+(1000 if room=='living_room' else 0)+index,
                    'soilieSceneId':scene['id'],'soilieSceneSha256':digest(scene),**target})
    # Alternate room types so a spending stop does not consume the entire cap
    # on bedrooms first. File order is fixed before calls, not quality-adaptive.
    tasks.sort(key=lambda t:(t['baseline'],int(t['id'].split('-')[-1]),t['roomType']))
    return {'schema':'soilie.inventory-campaign/v1','selectionSeed':SEED,'perRoom':per_room,
        'qualitySelection':False,'maxSubstitutions':1,'layoutgptBudgetUsd':35,
        'infinigenSourceCommit':'fb7991e06580639202a4687937082cb63e931eb0',
        'tasks':tasks}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--audit',type=Path)
    args=p.parse_args()
    source=args.source.read_bytes()
    plan=prepare(json.loads(source)['rows'],audit=json.loads(args.audit.read_bytes()) if args.audit else None)
    if args.audit: plan['retentionAuditSha256']=hashlib.sha256(args.audit.read_bytes()).hexdigest()
    plan['sourceSha256']=hashlib.sha256(source).hexdigest()
    plan['implementationSha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f: json.dump(plan,f,indent=2)
    print(json.dumps({f'{baseline}/{room}':{
        'planned':sum(t['baseline']==baseline and t['roomType']==room for t in plan['tasks']),
        'retained':sum(t['baseline']==baseline and t['roomType']==room and not t['needsGeneration'] for t in plan['tasks']),
        'exactInventory':sum(t['baseline']==baseline and t['roomType']==room and t['sourceInventory']==t['inventory'] for t in plan['tasks'])}
        for baseline in ('infinigen','layoutgpt') for room in ('bedroom','living_room')},indent=2))


if __name__=='__main__': main()
