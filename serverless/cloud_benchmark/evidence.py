"""Compile a completed allocation without selecting its fastest/cleanest rooms.

The cloud allocation is already complete and balanced by room type, so it can
supply immutable AI stimuli while the independent local allocation continues.
Source records remain untouched; downstream corrections must pass their audit.
No account identifiers, receipts, or local paths enter the public scene data.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from modules.support_settlement import FIXED_CLASSES
from serverless.benchmark.audit_support_corrections import audit_record
from serverless.benchmark.geometry import measure
from serverless.benchmark.publish_comparison import attach_front_directions
from serverless.benchmark.reobserve_contacts import audit_observation, flagged
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.local import successful_sources


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_final(row):
    solid=row['stages']['final'].get('solidMeshOverlap',{})
    value=solid.get('maxOverlapPct')
    if (row['status']!='complete' or not solid.get('complete') or
            type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=.0001 or flagged(row)):
        raise ValueError('Incomplete final geometry: '+row['id'])
    for item in row['stages']['final']['objects']:
        if item['label'] not in FIXED_CLASSES:
            for key in ('gapM','belowFloorM'):
                gap=item.get('support',{}).get(key)
                if type(gap) not in (int,float) or not math.isfinite(gap) or not 0<=gap<=1e-5:
                    raise ValueError('Invalid support evidence: '+row['id'])
    if not math.isfinite(row['generationSeconds']) or row['generationSeconds']<=0:
        raise ValueError('Invalid measured generation time')


def corrected_source(grid, task, entry):
    seed=str(task['seed'])
    path=grid/'downloads'/f'{seed}.json'
    raw=path.read_bytes()
    if entry['status']!='complete' or sha(raw)!=entry['sha256']:
        raise ValueError('Incomplete or modified cloud evidence: '+seed)
    original=json.loads(raw)
    row=original
    if row['status']!='complete' or any(row['request'][key]!=task[key]
            for key in ('seed','roomType','objectCount')):
        raise ValueError('Request differs from fixed allocation: '+seed)
    correction=grid/'geometry-corrections'/path.name
    observation=grid/'contact-observations'/path.name
    if correction.exists():
        path=correction
        row=json.loads(path.read_bytes())
        audit_record(original,row,sha(raw))
    elif observation.exists():
        path=observation
        row=json.loads(path.read_bytes())
        if row['contactObservation']['sourceSha256']!=sha(raw):
            raise ValueError('Observation source mismatch: '+seed)
        audit_observation(original,row)
    validate_final(row)
    return row, {'seed':task['seed'],'roomType':task['roomType'],
                 'originalSha256':sha(raw),'finalSha256':sha(path.read_bytes()),
                 'generationSeconds':original['generationSeconds'],
                 'correctionSeconds':row.get('supportCorrection',{}).get('correctionSeconds',0),
                 'implementation':original['implementation']}


def local_records(grid, campaign):
    """Require every allocated local request; a partial set cannot be published."""
    completion=json.loads((grid/'local-completion.json').read_bytes())
    if not completion.get('complete') or (completion.get('bedrooms'),completion.get('livingRooms'))!=(2500,2500):
        raise ValueError('Local allocation is not complete')
    plan=json.loads((grid/'plan.json').read_bytes())
    records=[]
    for item in plan['localBedrooms']:
        raw=(campaign/'bedroom-source'/item['file']).read_bytes()
        if sha(raw)!=item['sha256']:
            raise ValueError('Frozen local bedroom changed')
        source=json.loads(raw)
        path=campaign/'bedroom-repaired'/item['file']
        derived=json.loads(path.read_bytes())
        audit_record(source,derived,sha(raw))
        records.append((derived,{'originalSha256':sha(raw),'finalSha256':sha(path.read_bytes())}))
    sources=successful_sources(campaign,grid)
    requested={shard['seed']+index*shard['seedStep']:shard
               for shard in plan['localLivingShards'] for index in range(shard['target'])}
    if set(sources)!=set(requested) or len(requested)!=2500:
        raise ValueError('Local living-room seeds differ from the frozen allocation')
    for seed,shard in requested.items():
        raw=sources[seed].read_bytes(); source=json.loads(raw)
        if (source['request']['roomType']!='living_room' or source['request']['objectCount']!=shard['objectCount']):
            raise ValueError('Local living-room request differs')
        path=grid/'local-geometry-corrections'/f'{seed}.json'
        if path.exists():
            derived=json.loads(path.read_bytes()); audit_record(source,derived,sha(raw))
        else:
            path=sources[seed]; derived=source
        records.append((derived,{'originalSha256':sha(raw),'finalSha256':sha(path.read_bytes())}))
    if len(records)!=5000 or len({row['id'] for row,_ in records})!=5000:
        raise ValueError('The local allocation must contain exactly 5,000 distinct scenes')
    for row,source in records:
        validate_final(row)
        source.update(seed=row['request']['seed'],roomType=row['request']['roomType'],platform='local',
                      generationSeconds=row['generationSeconds'],
                      correctionSeconds=row.get('supportCorrection',{}).get('correctionSeconds',0),
                      implementation=row.get('implementation'))
    return records


def compile_full(grid, campaign, cloud_evidence, output):
    """Freeze all 10,000 finals only after both allocation gates have passed."""
    cloud=json.loads((cloud_evidence/'cohort.json').read_bytes())
    if not cloud['complete'] or cloud['soilieScenes']!=5000:
        raise ValueError('Complete cloud measurements required')
    raw=(cloud_evidence/'measured-scenes.json').read_bytes()
    if cloud.get('measurementsSha256')!=sha(raw):
        raise ValueError('Cloud measured scene checksum mismatch')
    rows=json.loads(raw)['rows']
    records=local_records(grid,campaign)
    output.mkdir(parents=True,exist_ok=True)
    for index,(row,_) in enumerate(records):
        scene=attach_front_directions(row['stages']['final'])
        rows.append({'scene':scene,'metrics':measure(scene)})
        if (index+1)%250==0:
            print(json.dumps({'measuredLocalScenes':index+1}),flush=True)
    if len({row['scene']['id'] for row in rows})!=len(rows):
        raise ValueError('Repeated scene across local and cloud allocations')
    write_json(output/'measured-scenes.json',{'schemaVersion':2,'rows':rows})
    sources=[{**source,'platform':'AWS Lambda'} for source in cloud['sources']]+[source for _,source in records]
    conditions=Counter((source['platform'],source['roomType']) for source in sources)
    if conditions!={(platform,room):2500 for platform in ('local','AWS Lambda') for room in ('bedroom','living_room')}:
        raise ValueError('The four conditions must each contain 2,500 rooms')
    write_json(output/'cohort.json',{'schemaVersion':1,'complete':True,'soilieScenes':10000,
        'roomCounts':{'bedroom':5000,'living_room':5000},'platform':'local and AWS Lambda',
        'selection':'The complete frozen four-condition allocation, with no quality-based exclusions.',
        'measurementsSha256':sha((output/'measured-scenes.json').read_bytes()),
        'baselineEvidenceSha256':cloud['baselineEvidenceSha256'],'sources':sources})
    print(json.dumps({'complete':True,'soilieScenes':10000}),flush=True)


def compile_cloud(grid, baseline, output):
    plan=json.loads((grid/'plan.json').read_bytes())
    ledger=json.loads((grid/'ledger.json').read_bytes())
    completion=json.loads((grid/'completion.json').read_bytes())
    requests=plan['requests']
    counts=Counter((task['roomType'],task['objectCount']) for task in requests)
    if (not completion.get('complete') or len(ledger['entries'])!=5000 or
            counts!={(room,count):625 for room in ('bedroom','living_room') for count in range(3,7)} or
            len({task['seed'] for task in requests})!=5000):
        raise ValueError('The complete fixed cloud allocation is required')
    output.mkdir(parents=True,exist_ok=True)
    cache=output/'measured-cache'; cache.mkdir(exist_ok=True)
    root=Path(__file__).resolve().parents[2]
    code_digest=sha(b''.join((root/name).read_bytes() for name in (
        'serverless/benchmark/geometry.py','serverless/benchmark/publish_comparison.py',
        'serverless/cloud_benchmark/evidence.py')))
    rows=[]; provenance=[]
    for index,task in enumerate(requests):
        record,source=corrected_source(grid,task,ledger['entries'][str(task['seed'])])
        scene=attach_front_directions(record['stages']['final'])
        key=sha((source['finalSha256']+code_digest).encode())
        path=cache/(key+'.json')
        if path.exists():
            measured=json.loads(path.read_bytes())
        else:
            measured={'scene':scene,'metrics':measure(scene)}
            write_json(path,measured)
        rows.append(measured); provenance.append(source)
        if (index+1)%250==0:
            print(json.dumps({'measuredCloudScenes':index+1}),flush=True)
    baseline_raw=baseline.read_bytes()
    # Preserve the exact released/imported baseline geometry already used by
    # the shared evaluator. Do not carry earlier SOILIE scenes or AI votes over.
    baselines=[row for row in json.loads(baseline_raw)['rows'] if row['scene']['model']!='soilie']
    rows.extend(baselines)
    if len({row['scene']['id'] for row in rows})!=len(rows):
        raise ValueError('Duplicate scene identities')
    write_json(output/'measured-scenes.json',{'schemaVersion':2,'rows':rows})
    document={'schemaVersion':1,'complete':True,'soilieScenes':5000,
              'roomCounts':{'bedroom':2500,'living_room':2500},'platform':'AWS Lambda',
              'selection':'Entire completed cloud allocation, independent of completion order and quality scores.',
              'measurementsSha256':sha((output/'measured-scenes.json').read_bytes()),
              'baselineEvidenceSha256':sha(baseline_raw),'measurementCodeSha256':code_digest,
              'sources':provenance}
    write_json(output/'cohort.json',document)
    print(json.dumps({'complete':True,'scenes':dict(Counter(row['scene']['model'] for row in rows))}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grid',type=Path,required=True)
    parser.add_argument('--baselines',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--campaign',type=Path,help='Compile the final full cohort instead of cloud-only evidence')
    parser.add_argument('--cloud-evidence',type=Path)
    args=parser.parse_args()
    if args.campaign:
        if not args.cloud_evidence:
            parser.error('--campaign requires --cloud-evidence')
        compile_full(args.grid,args.campaign,args.cloud_evidence,args.output)
    else:
        if not args.baselines:
            parser.error('Cloud-only compilation requires --baselines')
        compile_cloud(args.grid,args.baselines,args.output)


if __name__=='__main__':
    main()
