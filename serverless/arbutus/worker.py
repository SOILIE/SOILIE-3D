"""Bounded, resumable Infinigen workers with disjoint local/cloud assignments.

Each task owns one directory and a durable receipt. A partial attempt is never
silently overwritten. Restart skips terminal receipts; interrupted or failed
tasks require inspection. No SSH client lifetime owns the remote controller.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import time

from serverless.benchmark.infinigen_metadata import asset_label, generated_instances
from serverless.benchmark.supervise import command as supervised

ROOT=Path(__file__).resolve().parents[2]
PIN='fb7991e06580639202a4687937082cb63e931eb0'
LABELS={'bed':'bed','sofa':'sofa','chair':'chair','simple_desk':'desk',
        'single_cabinet':'storage','side_table':'nightstand',
        'coffee_table':'coffee table','table_dining':'dining table','t_v_stand':'tv stand'}


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''): h.update(part)
    return h.hexdigest()


def save(path, data):
    temporary=path.with_suffix(path.suffix+'.new')
    temporary.write_text(json.dumps(data,indent=2)+'\n')
    temporary.replace(path)


def selected(task, host):
    # Same immutable partition on either host, independent of completion speed.
    local=int(task['id'].rsplit('-',1)[1])%5==0
    return task['baseline']=='infinigen' and task['needsGeneration'] and local==(host=='local')


def wait_for_space(output,reserve_gib):
    """Backpressure while the S3 uploader frees disk, not a failed generation."""
    announced=False
    while shutil.disk_usage(output).free < reserve_gib*1024**3:
        if not announced:
            print(json.dumps({'status':'waiting_for_disk','reserveGiB':reserve_gib}),flush=True)
            announced=True
        time.sleep(5)


def validate_inventory(records, task):
    _room,instances=generated_instances(records,task['roomType'])
    counts=Counter()
    roles={}
    for identifier,record in instances:
        source=asset_label(record)
        label=LABELS.get(source)
        if label is None: raise ValueError('Unexpected generated class: '+source)
        if label=='nightstand':
            # This role is valid only when the solver records a relationship
            # to an actual bed, not simply because the input asked for one.
            targets=[records.get(r['target_name'],{}) for r in record['relations']]
            if not any('Semantics(bed)' in target.get('tags',[]) for target in targets):
                raise ValueError('Side table lacks the declared bedside relationship')
        counts[label]+=1
        roles[identifier]=label
    if dict(counts)!=task['inventory']:
        raise ValueError('INVENTORY_MISMATCH: '+json.dumps(dict(counts),sort_keys=True))
    return roles


def invoke(command, environment, cwd, log, timeout):
    started=time.perf_counter()
    with log.open('xb') as stream:
        child=subprocess.Popen(supervised(command),cwd=cwd,env=environment,
                               stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            child.wait(timeout=timeout)
        except BaseException:
            os.killpg(child.pid,signal.SIGKILL)
            child.wait()
            raise
    if child.returncode: raise RuntimeError(f'Process exited {child.returncode}: {log.name}')
    return time.perf_counter()-started


def run_attempt(task,args,plan_sha,folder):
    receipt=folder/'receipt.json'
    if receipt.exists():
        prior=json.loads(receipt.read_bytes())
        if prior['planSha256']!=plan_sha: raise ValueError('Cannot resume a changed plan')
        return prior
    if folder.exists(): raise ValueError('Interrupted task needs inspection: '+task['id'])
    # Never let a background process consume the last local disk/RAM margin.
    wait_for_space(args.output,args.reserve_gib)
    folder.mkdir()
    record={'id':task['id'],'status':'running','host':args.host,'planSha256':plan_sha,
            'task':task,'startedAt':time.time(),'threads':args.threads,'sourceCommit':PIN,
            'entrySha256':sha(Path(__file__).with_name('infinigen_entry.py'))}
    save(folder/'started.json',record)
    env=os.environ.copy()
    env.update(PYTHONPATH=os.pathsep.join(map(str,[ROOT,args.dependencies,args.repository])),
        PYTHONHASHSEED='0',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
        SOILIE_INVENTORY_TASK=json.dumps(task),PWD=str(args.repository))
    blender=[str(args.blender),'--background','--threads',str(args.threads),
             '--python-use-system-env','--python-exit-code','2']
    work=folder/'scene'
    work.mkdir()
    parent='Bedroom' if task['roomType']=='bedroom' else 'LivingRoom'
    overrides=['compose_indoors.terrain_enabled=False','compose_indoors.solve_small_enabled=False',
        f"restrict_solving.restrict_parent_rooms=['{parent}']",'restrict_solving.solve_max_rooms=1',
        "restrict_solving.consgraph_filters=['benchmark_inventory']"]
    cmd=blender+['--python',str(Path(__file__).with_name('infinigen_entry.py')),'--',
        '--seed',format(task['seed'],'x'),'--task','coarse','--output_folder',str(work),
        '-g','fast_solve.gin','singleroom.gin','-p',*overrides]
    record['command']=cmd
    try:
        record['generationSeconds']=invoke(cmd,env,args.repository,folder/'generation.log',args.timeout)
        roles=validate_inventory(json.loads((work/'solve_state.json').read_bytes())['objs'],task)
        exported=folder/'scene.json'
        export=[str(args.blender),'--background',str(work/'scene.blend'),'--threads',str(args.threads),
            '--python-use-system-env','--python-exit-code','2','--python',str(ROOT/'serverless/benchmark/export_infinigen.py'),
            '--','--state',str(work/'solve_state.json'),'--room-type',task['roomType'],
            '--id',task['id'],'--output',str(exported)]
        record['exportSeconds']=invoke(export,env,args.repository,folder/'export.log',1200)
        scene=json.loads(exported.read_bytes())
        for obj in scene['objects']:
            obj['sourceLabel']=obj['label']
            obj['label']=roles[obj['id']]
        scene['model']='infinigen_controlled'
        scene['benchmarkVariant']='inventory-conditioned-v1'
        scene['provenance']={'commit':PIN,'planSha256':plan_sha,'task':task,'host':args.host}
        save(exported,scene)
        # Archive only completed evidence. Do not remove native outputs here;
        # local delivery is verified separately before any cleanup is allowed.
        archive=folder/'artifacts.tar.gz'
        with tarfile.open(archive,'x:gz',compresslevel=1) as tar:
            for path in sorted(work.rglob('*')):
                if path.is_file(): tar.add(path,arcname='scene/'+path.relative_to(work).as_posix())
            for path in (exported,folder/'started.json',folder/'generation.log',folder/'export.log'):
                tar.add(path,arcname=path.name)
        record.update(status='complete',artifactsSha256=sha(archive),artifactsBytes=archive.stat().st_size,
                      sceneSha256=sha(exported))
    except Exception as error:
        record.update(status='failed',errorType=type(error).__name__,error=str(error))
    record['finishedAt']=time.time()
    save(receipt,record)
    print(json.dumps({k:record[k] for k in ('id','host','status')}),flush=True)
    return record


def one(task,args,plan_sha):
    folder=args.output/task['id']
    receipt=folder/'receipt.json'
    if receipt.exists():
        prior=json.loads(receipt.read_bytes())
        if prior['planSha256']!=plan_sha: raise ValueError('Changed campaign')
        return prior
    folder.mkdir(exist_ok=True)
    save(folder/'started.json',{'task':task,'startedAt':time.time(),'pid':os.getpid()})
    attempts=[]
    for index in range(args.attempts_per_task):
        seeded={**task,'seed':task['seed']+index*100000,'baseSeed':task['seed'],'attemptIndex':index}
        record=run_attempt(seeded,args,plan_sha,folder/f'attempt-{index:02d}')
        attempts.append({k:record.get(k) for k in ('status','error','generationSeconds','task')})
        if record['status']=='complete':
            record['artifactPath']=f'attempt-{index:02d}/artifacts.tar.gz'
            record['scenePath']=f'attempt-{index:02d}/scene.json'
            break
    record['attempts']=attempts
    save(receipt,record)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','output','repository','dependencies','blender'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--host',choices=['local','arbutus'],required=True)
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--threads',type=int,default=2)
    p.add_argument('--limit',type=int)
    p.add_argument('--timeout',type=int,default=3600)
    p.add_argument('--attempts-per-task',type=int,choices=range(1,11),default=5)
    p.add_argument('--reserve-gib',type=float,default=12)
    args=p.parse_args()
    if not 1<=args.workers<=32 or not 1<=args.threads<=4 or args.workers*args.threads>(os.cpu_count() or 1):
        p.error('Worker/thread budget exceeds available CPU')
    for name in ('plan','output','repository','dependencies','blender'):
        setattr(args,name,getattr(args,name).resolve())
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=args.repository,text=True).strip()
    if revision!=PIN or subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=args.repository,text=True).strip():
        raise ValueError('Pinned unchanged Infinigen source required')
    args.output.mkdir(parents=True,exist_ok=True)
    lock=args.output/'controller.lock'
    with lock.open('x') as f: json.dump({'pid':os.getpid()},f)
    try:
        tasks=[t for t in json.loads(args.plan.read_bytes())['tasks'] if selected(t,args.host)]
        if args.limit: tasks=tasks[:args.limit]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures=[pool.submit(one,task,args,sha(args.plan)) for task in tasks]
            for future in futures: future.result()
    finally:
        lock.unlink()


if __name__=='__main__': main()
