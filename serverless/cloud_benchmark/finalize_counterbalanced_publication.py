"""Refresh derived publication data from audited reviews and native measurements.

Furniture geometry, historical timings and support samples are immutable. Only
floor-dependent quantities are recomputed from the verified native boundary.
"""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import shutil

from serverless.benchmark.archive import packed
from serverless.benchmark.geometry import measure, summarize
from serverless.benchmark.publish_comparison import compare, LABELS
from serverless.cloud_benchmark.publication_views import model_summary, room_models, latency
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.staged_pilot import read, digest
from serverless.benchmark.cost import worker_scenario

FLOOR_FIELDS = ('roomArea','furnitureDensity','meanOutsideFootprintPct','maxOutsideFootprintPct','connectedClearancePct')


def finalize(website, floors, output, cloud=None):
    website, floors, output = map(Path, (website,floors,output))
    source=website/'benchmarks'
    document=read(source/'comparison.json')
    public=read(source/'room-measurements.json')
    measured={row['sceneId']:row for row in public['rows']}
    original=read(floors/'original-rows.json')['rows']
    boundary_records=[]
    corrected_rows=[]
    for row in original:
        scene=deepcopy(row['scene']); identity=scene['id']
        correction=read(floors/'floors'/(identity+'.json'))
        if (correction['furnitureModified'] is not False
            or correction['blendSha256']!=scene['provenance']['blendSha256']
            or correction['stateSha256']!=scene['provenance']['stateSha256']
            or correction['room']['boundaryExtractionVersion']!=2
            or abs(correction['room']['floorZ']-scene['room']['floorZ'])>1e-9):
            raise ValueError('Native floor identity/datum differs: '+identity)
        scene['room']=correction['room']
        fresh=measure(scene)
        metrics=measured[identity]['metrics']
        for key in FLOOR_FIELDS: metrics[key]=fresh[key]
        old_objects={o['id']:o for o in metrics['objects']}
        for obj in fresh['objects']:
            old_objects[obj['id']]['outsideFootprintPct']=obj['outsideFootprintPct']
        boundary_records.append({'sceneId':identity, **correction})
        corrected_rows.append({'scene':scene,'metrics':metrics})
    if len(boundary_records)!=329 or len({r['sceneId'] for r in boundary_records})!=329:
        raise ValueError('Every published Infinigen room must be checked')
    # Aggregation uses the same complete row ledger as the downloadable results.
    rows=[{'scene':{'id':r['sceneId'],'model':r['model'],'roomType':r['roomType']},'metrics':r['metrics']} for r in public['rows']]
    document['models']={m:model_summary(m,[r for r in rows if r['scene']['model']==m]) for m in LABELS}
    document['modelsByRoomType']=room_models(rows)
    document['comparisons']=compare(rows)
    public['cohortSha256']=digest(public['rows'])
    write_json(output/'room-measurements.json',public)
    write_json(output/'native-floor-measurements.json', {'schemaVersion':1, 'method':'Projection of all tagged visible native floor triangles, retaining holes and components. No furniture movement.', 'rooms':boundary_records})
    write_json(floors/'corrected-rows.json',{'rows':corrected_rows})
    document['nativeFloorMeasurements']={'file':'native-floor-measurements.json','rooms':329,'sha256':digest((output/'native-floor-measurements.json').read_bytes())}
    manifest=read(output/'review-manifest.json')
    document['aiReview']={'ready':True,'protocolSha256':manifest['protocolSha256'],'pairsPerRoomPerBaseline':120,
                          'manifestSha256':digest((output/'review-manifest.json').read_bytes()),
                          'geometryLedgerSha256':public['cohortSha256']}
    costs=read(source/'cost-measurements.json')
    if cloud:
        from serverless.infinigen_cloud.publication import measured_rows, completion_coverage
        cloud_rows=measured_rows(Path(cloud)); coverage=completion_coverage(Path(cloud))
        document['timing']['infinigenCloudByRoomType']={room:{model:{**latency([r['seconds'] for r in cloud_rows if r['roomType']==room and r['model']==model]),
            'platform':'AWS Lambda','memoryMb':6144,'blenderThreads':4,'completion':coverage[room][model]}
            for model in ('infinigen','infinigen_controlled')} for room in ('bedroom','living_room')}
        costs['rows']=[r for r in costs['rows'] if not r['model'].startswith('infinigen')]
        for row in cloud_rows:
            costs['rows'].append({**row,'usd':worker_scenario(row['seconds'],document['cost']['rateCard']['lambda'],6144)['usd']})
        for room in ('bedroom','living_room'):
            for model in ('infinigen','infinigen_controlled'):
                document['cost']['byRoomType'][room][model]=summarize([r['usd'] for r in costs['rows'] if r['roomType']==room and r['model']==model])
        document['cost']['missing'].pop('infinigenControlled',None)
        document['cost']['infinigenCloud']={'attemptsPerCondition':20,'completion':coverage,'memoryMb':6144,
            'sourceSha256':digest(Path(cloud).read_bytes()),
            'stage':'Blender startup, solving, procedural meshes, camera preparation and scene serialization. Excludes validation, compression, transfer and image rendering.'}
        document['cost']['infinigenBasis']='Recorded construction-stage times on 6 GB x86-64 AWS Lambda workers, priced at public rates. These are generation-stage charges, not complete billed invocation costs.'
        document['cost']['scope']='Per-room generation-stage charges calculated from recorded cloud durations and API tokens at public rates. SOILIE places existing meshes; LayoutGPT proposes boxes; Infinigen constructs procedural meshes. Rendering, orchestration, transfers and evaluation are excluded.'
        # Remove the desktop-to-cloud hypothetical values, not just hide them.
        document['cost'].pop('infinigenScenario',None)
    write_json(output/'cost-measurements.json',costs)
    document['cost']['measurements']={'file':'cost-measurements.json','rows':len(costs['rows']),'sha256':digest((output/'cost-measurements.json').read_bytes())}
    document.pop('evidenceDigest',None)
    document['evidenceDigest']=digest(document)
    packed(document)
    write_json(output/'comparison.json',document)
    for name in ('status.json','support-measurements.json','layoutgpt-scale.json'):
        shutil.copyfile(source/name,output/name)
    inputs=read(source/'publication-inputs.json')
    inputs.update(reviewProtocolSha256=manifest['protocolSha256'],roomMeasurementsSha256=digest((output/'room-measurements.json').read_bytes()),
                  nativeFloorsSha256=digest((output/'native-floor-measurements.json').read_bytes()),
                  infinigenCloudTimingSha256=digest(Path(cloud).read_bytes()) if cloud else None)
    write_json(output/'publication-inputs.json',inputs)
    print({'rows':len(rows),'nativeFloors':len(boundary_records),'protocolSha256':manifest['protocolSha256'],'evidenceDigest':document['evidenceDigest']})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('website','floors','output'): p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--cloud',type=Path)
    finalize(**vars(p.parse_args()))
