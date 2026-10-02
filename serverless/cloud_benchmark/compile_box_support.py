"""Measure archived boxes locally; no generation or AI calls.

Download only the exact published geometry cohort, verify each archived SHA,
apply the existing independently verified LayoutGPT physical scale, and retain
per-object measurements. Original rooms and frozen AI evidence are untouched.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil

from serverless.benchmark.archive import packed
from serverless.benchmark.box_support import METHOD, MOUNTED, measure_box_support
from serverless.benchmark.geometry import summarize
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.staged_pilot import read, digest

PREFIX='files/outputs/website-comparisons/'
METHODS = (
    'For each furniture instance except architecture and explicitly wall/ceiling-mounted categories, measure the smallest '
    'nonnegative downward translation to the horizontal floor reference plane or an enclosing '
    'furniture box with a lower bottom and positive overlapping plan area. Architectural boxes '
    'are excluded from candidate supports for all models. Same-assembly parts are excluded. '
    'Use actual convex box faces, including tilted boxes; floor wins numerical ties within 0.00001 cm. '
    'Touching or intersecting boxes have zero gap. Below-floor depth is reported separately. '
    'Average objects within each room; each room has equal weight. Category-specific means exclude '
    'rooms with no object in that category. Boxes fill empty furniture space and cannot establish '
    'mesh contact, stable support or contact with holes in a real floor. An object resting on '
    'an architectural ledge may therefore have a nonzero furniture-only gap.'
)


def one(task):
    path, entry, scale = task
    raw=Path(path).read_bytes()
    if digest(raw)!=entry['artifacts']['geometry']['sha256']:
        raise ValueError('Cached geometry changed')
    scene=read(path)['scene']
    if any(scene[k]!=entry[v] for k,v in [('id','id'),('model','model'),('roomType','roomType')]):
        raise ValueError('Archive identity mismatch')
    original=digest(scene)
    factor=1.0
    if scene['model']=='layoutgpt':
        if scale['sceneSha256']!=original or scene['units']!='px':
            raise ValueError('Physical scale belongs to a different LayoutGPT prediction: '+scene['id'])
        factor=scale['metresPerPixel']
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('Invalid physical scale')
        scene=deepcopy(scene); scene['units']='m'
        for item in scene['objects']:
            item['corners']=[[v*factor for v in point] for point in item['corners']]
        scene['room']['floorZ']*=factor
    result=measure_box_support(scene)
    return dict(sceneId=scene['id'],model=scene['model'],roomType=scene['roomType'],
                geometrySha256=digest(raw),sourceSceneSha256=original,metresPerSourceUnit=factor,**result)


def compile_boxes(publication, output, cache):
    import boto3
    from botocore.config import Config
    publication,output,cache=map(Path,(publication,output,cache))
    if publication.resolve()!=output.resolve(): shutil.copytree(publication,output,dirs_exist_ok=True)
    cache.mkdir(parents=True,exist_ok=True)
    client=boto3.Session(profile_name='darkest',region_name='ca-central-1').client('s3',config=Config(max_pool_connections=20))
    manifest=client.get_object(Bucket='soilie3d-data',Key=PREFIX+'manifest.json')['Body'].read()
    entries=read(publication/'room-measurements.json')['rows']
    expected={r['sceneId'] for r in entries}
    source=json.loads(manifest)['quantitativeRooms']
    if len(source)!=len(expected) or {r['id'] for r in source}!=expected:
        raise ValueError('Archive must match the complete current quantitative cohort')
    scales={r['sceneId']:r for r in read(publication/'layoutgpt-scale.json')['rooms']}
    def fetch(entry):
        artifact=entry['artifacts']['geometry']; key=artifact['key']
        if not key.startswith(PREFIX+'rooms/') or not key.endswith('.json'):
            raise ValueError('Unexpected geometry key')
        path=cache/(artifact['sha256']+'.json')
        if not path.exists():
            raw=client.get_object(Bucket='soilie3d-data',Key=key)['Body'].read()
            if digest(raw)!=artifact['sha256'] or len(raw)!=artifact['bytes']:
                raise ValueError('Archive checksum/size mismatch')
            path.write_bytes(raw)
        return str(path),entry,scales.get(entry['id'])
    tasks=[]
    with ThreadPoolExecutor(max_workers=16) as pool:
        for task in pool.map(fetch,source):
            tasks.append(task)
            if len(tasks)%1000==0:print(json.dumps({'verifiedDownloads':len(tasks),'total':len(source)}),flush=True)
    results=[]
    with ProcessPoolExecutor(max_workers=4) as pool:
        for record in pool.map(one,tasks,chunksize=20):
            results.append(record)
            if len(results)%1000==0:print(json.dumps({'measuredRooms':len(results),'total':len(source)}),flush=True)
    results.sort(key=lambda r:r['sceneId'])
    payload={'schemaVersion':1,'method':METHOD,'methods':METHODS,'excludedCategories':sorted(MOUNTED),
             'archiveManifestSha256':digest(manifest),'rooms':results}
    packed(payload); write_json(output/'box-support-measurements.json',payload)
    document=read(publication/'comparison.json')
    summaries={}
    for room in ('bedroom','living_room'):
        summaries[room]={}
        for model in document['models']:
            rows=[r for r in results if r['roomType']==room and r['model']==model]
            summaries[room][model]={'n':len(rows),'metrics':{key:summarize([r[key] for r in rows if r[key] is not None])
                for key in ('gapCm','floorGapCm','objectGapCm','belowFloorCm')},
                'objects':dict(Counter(o['supportKind'] for r in rows for o in r['objects'])),
                'excludedObjects':sum(len(r['excludedObjects']) for r in rows)}
    document['boxSupport']={'file':'box-support-measurements.json','method':METHOD,'methods':METHODS,
        'rooms':len(results),'sha256':digest((output/'box-support-measurements.json').read_bytes()),'byRoomType':summaries}
    document.pop('evidenceDigest');document['evidenceDigest']=digest(document)
    write_json(output/'comparison.json',document)
    inputs=read(publication/'publication-inputs.json');inputs['boxSupportSha256']=document['boxSupport']['sha256']
    write_json(output/'publication-inputs.json',inputs)
    print(json.dumps({'complete':True,'rooms':len(results),'digest':document['evidenceDigest']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('publication','output','cache'):parser.add_argument('--'+key,type=Path,required=True)
    compile_boxes(**vars(parser.parse_args()))
