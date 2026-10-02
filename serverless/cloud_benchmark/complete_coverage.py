"""Compile every completed comparison batch, retaining workload/hardware strata.

No model calls or quality-based selection. Review packets are immutable inputs;
the quantitative cohort also includes unmatched proposals. Private receipts are
validated locally and reduced to an allowlisted per-observation public ledger.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import shutil

import numpy as np

from serverless.benchmark.geometry import measure, summarize
from serverless.benchmark.layoutgpt_scale import physical_scene
from serverless.benchmark.cost import token_charge
from serverless.benchmark.archive import packed
from serverless.benchmark.publish_comparison import LABELS, compare
from serverless.cloud_benchmark.publication_views import model_summary, room_models, latency, positive, mesh_check_coverage
from serverless.cloud_benchmark.staged_pilot import read, digest
from serverless.cloud_benchmark.checkpoint import write_json

EXPORTS = ('benchmark/layoutgpt-bedroom-timing-20', 'benchmark/layoutgpt-controlled',
           'benchmark/layoutgpt-controlled-supplement', 'arbutus-inventory/layoutgpt',
           'arbutus-inventory/layoutgpt-supplement', 'arbutus-inventory/layoutgpt-topup')
API_STAGE = 'API request to complete response, including network and provider queue. Excludes retrieval, parsing, mesh retrieval and rendering.'
INFI_STAGE = 'Blender startup, constraint solving, procedural meshes, camera preparation and scene saving. Excludes export, measurement, transfer and rendering.'
DESKTOP = 'Core Ultra 7 155H; WSL2 with 8 visible vCPUs and approximately 12 GB RAM'
SERVER = '32-vCPU Xeon (Sapphire Rapids) KVM virtual machine; 118 GiB RAM; shared concurrent workers'


def api_records(export, ledger):
    """An unused planned request is not a failed call; uncertain calls block release."""
    if export.get('reservedUncertainUsd') != 0:
        raise ValueError('Unresolved API charges')
    attempts = export['attempts']
    entries = ledger['entries']
    if {a['id'] for a in attempts} != set(entries) or len(attempts) != len(entries):
        raise ValueError('Every actual API call must be accounted for exactly once')
    scenes = {r['scene']['id']:r['scene'] for r in export['rows']}
    if len(scenes) != len(entries): raise ValueError('Missing completed layout')
    result = []
    for attempt in attempts:
        receipt = entries[attempt['id']]
        scene = scenes['layoutgpt-'+attempt['id']]
        if (attempt['status'] != 'complete' or receipt['status'] != 'complete'
                or attempt.get('geometryStatus') != 'complete' or attempt.get('unparsedLines') != 0
                or scene['provenance']['responseSha256'] != receipt['responseSha256']
                or scene['provenance']['requestSha256'] != receipt['requestSha256']
                or attempt['usage'] != receipt['usage'] or attempt['wallSeconds'] != receipt['wallSeconds']):
            raise ValueError('API result/receipt mismatch')
        result.append({'id':scene['id'],'model':'layoutgpt','roomType':scene['roomType'],
            'condition':export['variant'], 'seconds':positive(receipt['wallSeconds']),
            'requestedObjects':attempt['requestedObjects'],
            'inputTokens':receipt['usage']['prompt_tokens'],'outputTokens':receipt['usage']['completion_tokens'],
            'basis':'recorded-api-tokens','requestSha256':receipt['requestSha256'],
            'responseSha256':receipt['responseSha256']})
    return result


def complete_receipts(root):
    result = {}
    for host in ('cloud','local'):
        for path in sorted((root/host).glob('*/receipt.json')):
            record = read(path)
            if record['status'] == 'delegated': continue
            if record['status'] != 'complete': raise ValueError('Unfinished inventory task')
            if record['id'] in result: raise ValueError('Repeated completed inventory task')
            result[record['id']] = record
    if len(result) != 240: raise ValueError('Expected every completed inventory task')
    return result


def old_infinigen_timing(scratch, original):
    # Bind each timer to its actual saved solve state, not just the seed, which
    # recurs in room-scale and controlled runs.
    candidates = {}
    for run_path in (scratch/'benchmark').glob('infinigen*/**/run.json'):
        config = read(run_path)
        for path in run_path.parent.glob('attempt-*.json'):
            attempt = read(path)
            if attempt.get('status') != 'complete': continue
            state = run_path.parent/Path(attempt['outputDirectory']).name/'solve_state.json'
            if not state.exists(): continue
            key = digest(state.read_bytes())
            candidates.setdefault(key, []).append((attempt,config))
    rows=[]
    for row in original:
        scene=row['scene']; choices=candidates.get(scene['provenance']['stateSha256'],[])
        if not choices: raise ValueError('Missing native timer '+scene['id'])
        # Re-exporting the same native scene is not another generation.
        unique={a['generationSeconds'] for a,c in choices}
        if len(unique)!=1: raise ValueError('Ambiguous native timer '+scene['id'])
        attempt,config=choices[0]
        rows.append({'id':scene['id'],'model':scene['model'],'roomType':scene['roomType'],
            'seconds':positive(attempt['generationSeconds']), 'platform':'cpu-desktop',
            'hardware':DESKTOP, 'blenderThreads':config['blenderThreads'],
            'condition':'room-scale' if scene['model']=='infinigen' else 'controlled-role-schedule',
            'stage':INFI_STAGE,'concurrent':True})
    return rows


def update_contacts(scene, record):
    if record['furnitureModified'] is not False: raise ValueError('Measurements cannot move furniture')
    items={obj['id']:obj for obj in scene['objects']}
    for observation in record['objects']:
        if observation['id'] not in items: raise ValueError('Wrong native object')
        items[observation['id']]['support']=observation['support']
    return scene


def compile_all(backend, website, contacts, output):
    backend,website,contacts,output=map(Path,(backend,website,contacts,output))
    scratch=backend/'.codex'; source=website/'benchmarks'
    shutil.copytree(source,output,dirs_exist_ok=True)
    document=read(source/'comparison.json'); public=read(source/'room-measurements.json')
    by_id={r['sceneId']:r for r in public['rows']}
    support=read(source/'support-measurements.json')
    support_rooms={r['sceneId']:r for r in support['rooms']}
    original=read(scratch/'publication-022-floor-check/original-rows.json')['rows']
    audited=read(scratch/'arbutus-inventory/review-source-v4-audited/source-scenes.json')['scenes']
    additions=[{'scene':deepcopy(s),'metrics':{}} for s in audited if s['model']=='infinigen_controlled']
    native_records=[]; changed=[]
    for row in original+additions:
        scene=deepcopy(row['scene']); identity=scene['id']
        contact=read(contacts/'floors'/(identity+'.json'))
        expected=scene['provenance'].get('blendSha256') or scene['provenance']['boundaryMeasurement']['nativeBlendSha256']
        if contact['blendSha256']!=expected: raise ValueError('Native scene hash mismatch')
        scene['room']=contact['room']
        update_contacts(scene,contact)
        # Infrastructure names are irrelevant; retain immutable input digests.
        scene['provenance'].pop('host',None)
        metrics=measure(scene)
        by_id[identity]={'sceneId':identity,'model':scene['model'],'roomType':scene['roomType'],'metrics':metrics}
        support_rooms[identity]={'sceneId':identity,'model':scene['model'],'roomType':scene['roomType'],
            'objects':[dict(id=o['id'],label=o['label'],**o['support']) for o in scene['objects'] if o.get('support')]}
        native_records.append({'sceneId':identity,**contact})
        changed.append({'scene':scene,'metrics':metrics})
    if len(native_records)!=569: raise ValueError('Every native geometry room needs contact verification')
    # Metadata comes from the authors, keyed by room ID and verified against the
    # download manifests. It supplies physical scale, NOT furniture meshes.
    npz={}
    for directory in (scratch/'layoutgpt-scale-cache',scratch/'benchmark/layoutgpt-bedroom-timing-20/data',scratch/'benchmark/layoutgpt-controlled/data'):
        for path in directory.rglob('boxes.npz'): npz.setdefault(path.parent.name,path)
    scale=read(source/'layoutgpt-scale.json'); scale_ids={r['sceneId'] for r in scale['rooms']}
    calls=[]; input_hashes=[]
    for folder in EXPORTS:
        path=scratch/folder/'export.json'; export=read(path)
        input_hashes.append(digest(path.read_bytes()))
        records=api_records(export,read(path.with_name('inference-ledger.json')))
        calls.extend(records)
        for row in export['rows']:
            scene=row['scene']; identity=scene['id']
            if identity in by_id: continue
            metadata=npz[scene['provenance']['sourceRoomId']]
            with np.load(metadata,allow_pickle=False) as data:
                physical,mpp=physical_scene(scene,data['floor_plan_vertices'])
            metrics=deepcopy(row['metrics']); physical_metrics=measure(physical)
            metrics['connectedClearancePct']=physical_metrics['connectedClearancePct']
            metrics['unavailable'].pop('clearance',None)
            by_id[identity]={'sceneId':identity,'model':'layoutgpt','roomType':scene['roomType'],'metrics':metrics}
            changed.append({'scene':scene,'metrics':metrics})
            if identity not in scale_ids:
                scale['rooms'].append({'sceneId':identity,'roomType':scene['roomType'],
                    'sourceRoomId':scene['provenance']['sourceRoomId'],'sceneSha256':digest(scene),
                    'metadataSha256':digest(metadata.read_bytes()),'metresPerPixel':mpp,
                    'roomAreaM2':physical_metrics['roomArea'],'connectedClearancePct':physical_metrics['connectedClearancePct']})
                scale_ids.add(identity)
    if len(calls)!=366 or len({r['id'] for r in calls})!=366: raise ValueError('API coverage mismatch')
    # Rebuild timing from receipts, not from whichever scenes happened to match
    # an AI pair. Never pool different CPUs into one latency distribution.
    timed=old_infinigen_timing(scratch,original)
    for receipt in complete_receipts(scratch/'arbutus-inventory').values():
        vm=receipt['host']=='arbutus'
        timed.append({'id':receipt['id'],'model':'infinigen_controlled','roomType':receipt['task']['roomType'],
            'seconds':positive(receipt['generationSeconds']), 'platform':'cpu-vm' if vm else 'cpu-desktop',
            'hardware':SERVER if vm else DESKTOP,'blenderThreads':receipt['threads'],
            'condition':'inventory-conditioned','stage':INFI_STAGE,'concurrent':True})
    if len(timed)!=569 or len({r['id'] for r in timed})!=569: raise ValueError('Native timing coverage differs')
    groups=[]
    for key in sorted({(r['roomType'],r['model'],r['platform'],r['condition'],r['blenderThreads']) for r in timed}):
        room,model,platform,condition,threads=key
        records=[r for r in timed if (r['roomType'],r['model'],r['platform'],r['condition'],r['blenderThreads'])==key]
        groups.append(dict(roomType=room,model=model,platform=platform,condition=condition,blenderThreads=threads,
            hardware=records[0]['hardware'],stage=INFI_STAGE,**latency([r['seconds'] for r in records])))
    api_groups=[]
    for room,condition in sorted({(r['roomType'],r['condition']) for r in calls}):
        records=[r for r in calls if r['roomType']==room and r['condition']==condition]
        api_groups.append(dict(roomType=room,condition=condition,model='layoutgpt',platform='GPT-4 API',stage=API_STAGE,
            **latency([r['seconds'] for r in records])))
    timing={'schemaVersion':1,'rows':timed+[dict(r,platform='GPT-4 API',stage=API_STAGE) for r in calls],
        'scope':'Completed generation latency. CPU batches include contention; hardware and thread allocations remain separate. No images or measurements timed.'}
    write_json(output/'timing-measurements.json',timing)
    document['timing'].update(cpuConditions=groups,apiConditions=api_groups,
        measurements={'file':'timing-measurements.json','sha256':digest((output/'timing-measurements.json').read_bytes()),'rows':len(timing['rows'])})
    document['timing']['layoutgptByRoomType']={room:dict(**latency([r['seconds'] for r in calls if r['roomType']==room]),
        stage=API_STAGE+' This pooled distribution includes all recorded prompt conditions; condition-specific summaries are supplied separately.') for room in ('bedroom','living_room')}
    costs=read(source/'cost-measurements.json')
    costs['rows']=[r for r in costs['rows'] if r['model']!='layoutgpt']
    for row in calls:
        costs['rows'].append(dict(row,usd=token_charge(row['inputTokens'],row['outputTokens'],document['cost']['rateCard']['gpt4'])))
    for room in ('bedroom','living_room'):
        document['cost']['byRoomType'][room]['layoutgpt']=summarize([r['usd'] for r in costs['rows'] if r['model']=='layoutgpt' and r['roomType']==room])
    document['cost']['apiConditions']=[dict(roomType=g['roomType'],condition=g['condition'],usd=summarize([r['usd'] for r in costs['rows'] if r['model']=='layoutgpt' and r['roomType']==g['roomType'] and r['condition']==g['condition']])) for g in api_groups]
    document['cost']['apiCoverage']={'bedroom':154,'living_room':212,'model':'gpt-4-0613',
        'basis':'Recorded token usage for every completed API call, including unmatched proposals; original, count-constrained and inventory-constrained prompts remain identifiable.'}
    living=[r for r in costs['rows'] if r['model']=='layoutgpt' and r['roomType']=='living_room']
    document['cost']['layoutgpt'].update(usd=summarize([r['usd'] for r in living]),
        inputTokens=summarize([r['inputTokens'] for r in living]), outputTokens=summarize([r['outputTokens'] for r in living]),
        requestedObjectCounts=dict(Counter(r['requestedObjects'] for r in living)),
        basis='Recorded GPT-4 tokens from all living-room calls: count-constrained and inventory-constrained prompts. Each retains its condition in the ledger.')
    rows=[{'scene':{'id':r['sceneId'],'model':r['model'],'roomType':r['roomType']},'metrics':r['metrics']} for r in by_id.values()]
    document['models']={m:model_summary(m,[r for r in rows if r['scene']['model']==m]) for m in LABELS}
    document['modelsByRoomType']=room_models(rows); document['comparisons']=compare(rows)
    fresh_checks=mesh_check_coverage(changed)
    for model in ('infinigen','infinigen_controlled'): document['meshCheckCoverage'][model]=fresh_checks[model]
    for room in ('bedroom','living_room'):
        checks=mesh_check_coverage([r for r in changed if r['scene']['roomType']==room])
        for model in ('infinigen','infinigen_controlled'): document['meshCheckCoverageByRoomType'][room][model]=checks[model]
    public['rows']=list(by_id.values()); public['cohortSha256']=digest(public['rows'])
    document['aiReview']['geometryLedgerSha256']=public['cohortSha256']
    support['rooms']=list(support_rooms.values())
    support['contactMethod']='Sampled distance to a real surface below, with native floor-triangle crossing checks. A crossing has zero separation, not proof of correct support; below-floor depth is reported separately.'
    files={'room-measurements.json':public,'support-measurements.json':support,'cost-measurements.json':costs,
           'layoutgpt-scale.json':scale,'native-contact-measurements.json':{'schemaVersion':1,'rooms':native_records}}
    coverage={'schemaVersion':1,'geometry':{m:document['models'][m]['inventory']['roomTypes'] for m in LABELS},
        'apiCalls':dict(Counter(r['roomType'] for r in calls)),
        'cpuInfinigen':dict(Counter(r['roomType'] for r in timed)),
        'cloudInfinigen':{'bedroom':34,'living_room':37},
        'scope':'All 10,000 retained SOILIE rooms; 423 released LayoutGPT bedrooms and all 366 completed API proposals; 40 room-scale and 529 controlled Infinigen rooms. No quality or matching exclusions from these geometry distributions.',
        'cloudGeometry':'The 71 Lambda timing outputs lack the saved surface-tag dictionary required by the pinned native floor exporter. Their construction times and charges are included; they are not assigned geometry/contact scores.',
        'meshAvailability':'LayoutGPT returns category, position, dimensions and angle. No vertices, faces or mesh asset IDs are returned. Its separate ATISS stage retrieves 3D-FUTURE meshes; running the API ourselves does not create these assets.',
        'inputExportSha256':input_hashes}
    files['coverage-audit.json']=coverage
    files['native-floor-measurements.json']={'schemaVersion':1,
        'method':'Projection of every tagged visible native floor triangle, retaining holes and components; no furniture movement.',
        'rooms':[{key:record[key] for key in ('sceneId','room','roomId','furnitureModified','blendSha256','stateSha256')} for record in native_records]}
    for name,data in files.items(): packed(data); write_json(output/name,data)
    document['coverage']={'file':'coverage-audit.json',**coverage}
    document['nativeFloorMeasurements'].update(rooms=569,sha256=digest((output/'native-floor-measurements.json').read_bytes()))
    for key,name in [('layoutgptPhysicalScale','layoutgpt-scale.json')]:
        document[key].update(rooms=len(scale['rooms']),sha256=digest((output/name).read_bytes()))
    document['cost']['measurements'].update(rows=len(costs['rows']),sha256=digest((output/'cost-measurements.json').read_bytes()))
    document['nativeContactMeasurements']={'file':'native-contact-measurements.json','rooms':569,'sha256':digest((output/'native-contact-measurements.json').read_bytes())}
    document.pop('evidenceDigest',None); document['evidenceDigest']=digest(document)
    packed(document); write_json(output/'comparison.json',document)
    write_json(contacts/'corrected-rows.json',{'rows':changed})
    status=read(source/'status.json'); status['analysis']['summary']='10,000 SOILIE, 789 LayoutGPT, 40 room-scale and 529 controlled-inventory Infinigen rooms; 480 AI-reviewed pairs.'
    write_json(output/'status.json',status)
    inputs=read(source/'publication-inputs.json')
    inputs.update(completeApiExportSha256=input_hashes,roomMeasurementsSha256=digest((output/'room-measurements.json').read_bytes()),
        nativeContactSha256=document['nativeContactMeasurements']['sha256'],timingSha256=document['timing']['measurements']['sha256'])
    write_json(output/'publication-inputs.json',inputs)
    print(json.dumps({'geometry':coverage['geometry'],'timing':len(timing['rows']),'apiCalls':coverage['apiCalls']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('backend','website','contacts','output'):p.add_argument('--'+key,type=Path,required=True)
    compile_all(**vars(p.parse_args()))
