"""Prepare/run read-only native-floor checks; never regenerate furniture."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

def read(path):
    return json.loads(Path(path).read_bytes())


def write_json(path, data):
    Path(path).write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')


def prepare(backend, output):
    backend, output = Path(backend), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    archive=read(backend/'.codex/arbutus-inventory/comparison-archive/manifest.json')
    rows={r['scene']['id']:r for r in read(backend/'.codex/benchmark/soilie-platform-grid-final/evidence/measured-scenes.json')['rows']}
    for scene in read(backend/'.codex/benchmark/infinigen-controlled-living-supplement/export.json')['scenes']:
        rows[scene['id']]={'scene':scene}
    expansion=backend/'.codex/benchmark/infinigen-expanded-120'
    for room in ('bedroom','living_room'):
        for attempt in read(expansion/room/'checkpoint.json')['attempts']:
            if 'measured' in attempt:
                row=read(expansion/attempt['measured'])
                rows[row['scene']['id']]=row
    sidecars={}
    for directory in (backend/'.codex/benchmark').glob('infinigen*'):
        if directory.is_dir():
            for state in directory.rglob('solve_state.json'):
                sidecars[hashlib.sha256(state.read_bytes()).hexdigest()]=state
    native_hashes={r['artifact']['originalBlendSha256']:r['artifact'] for r in read(backend/'.codex/benchmark/soilie-platform-grid-final/completed-blend-archive/manifest.json')['scenes'].values()}
    jobs=[]
    for entry in archive['quantitativeRooms']:
        if not entry['model'].startswith('infinigen'): continue
        scene=rows[entry['id']]['scene']
        provenance=scene['provenance']
        artifact=entry['artifacts'].get('blenderScene')
        if artifact is None:
            # Seed-derived scene IDs can recur across native/controlled runs.
            # The saved blend digest, not a stripped scene ID, identifies input.
            artifact=native_hashes[provenance['blendSha256']]
        state=sidecars[provenance['stateSha256']]
        jobs.append({'id':scene['id'], 'roomType':scene['roomType'], 'blendSha256':provenance['blendSha256'],
            'archiveSha256':artifact['sha256'], 'url':'https://soilie3d-data.s3.ca-central-1.amazonaws.com/'+artifact['key'],
            'state':state.read_text(encoding='utf-8'), 'tags':state.with_name('MaskTag.json').read_text(encoding='utf-8')})
    if len(jobs)!=329: raise ValueError('Expected all 329 published Infinigen rooms')
    write_json(output/'jobs.json',jobs)
    write_json(output/'original-rows.json',{'rows':[rows[j['id']] for j in jobs]})
    return {'jobs':len(jobs)}


def run(root, runtime, backend, workers):
    root,runtime,backend=map(lambda p:Path(p).resolve(),(root,runtime,backend))
    if not root.is_relative_to(backend/'.codex'):
        raise ValueError('Scratch must be inside this backend .codex')
    def one(job):
        target=root/'floors'/(job['id']+'.json')
        if target.exists():
            if read(target)['blendSha256']!=job['blendSha256']: raise ValueError('Cached native changed')
            return
        scratch=root/'native'/job['id']
        scratch.mkdir(parents=True,exist_ok=True)
        archive=scratch/'scene.blend.gz'
        urllib.request.urlretrieve(job['url'],archive)
        with archive.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=job['archiveSha256']: raise ValueError('Archive changed')
        with gzip.open(archive,'rb') as src,(scratch/'scene.blend').open('wb') as dst:
            shutil.copyfileobj(src,dst)
        for name,key in [('solve_state.json','state'),('MaskTag.json','tags')]:
            (scratch/name).write_text(job[key],encoding='utf-8')
        env=dict(os.environ,PYTHONPATH=f'{backend}:{runtime}/infinigen-env/lib/python3.10/site-packages:{runtime}/infinigen',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
        cmd=[str(runtime/'tools/blender-3.6.0-linux-x64/blender'),'-b',str(scratch/'scene.blend'),'--threads','1','--python-use-system-env','--python-exit-code','2','--python',str(backend/'serverless/benchmark/reextract_infinigen_floor.py'),'--','--state',str(scratch/'solve_state.json'),'--room-type',job['roomType'],'--output',str(target)]
        with (scratch/'read.log').open('w') as log:
            subprocess.run(cmd,env=env,cwd=runtime/'infinigen',stdout=log,stderr=log,timeout=240,check=True)
        result=read(target)
        if result['blendSha256']!=job['blendSha256'] or result['furnitureModified'] is not False:
            raise ValueError('Loaded native mismatch')
        # Only this job's downloaded copies; the checksummed S3 originals remain.
        for name in ('scene.blend','scene.blend.gz'):
            (scratch/name).unlink()
        print(job['id'],flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one,read(root/'jobs.json')))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--backend',type=Path,required=True)
    parser.add_argument('--runtime',type=Path)
    parser.add_argument('--workers',type=int,default=8)
    args=parser.parse_args()
    if args.runtime: run(**vars(args))
    else: print(prepare(args.backend,args.root))
