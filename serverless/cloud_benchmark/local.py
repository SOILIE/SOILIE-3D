"""Resume missing local requests in new, provenance-pinned segments.

Original scenes are immutable. A failed request retries its original seed,
not an easier replacement. Already completed requests are never regenerated.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from serverless.benchmark.audit_support_corrections import audit_record
from serverless.benchmark.balanced_campaign import implementation_digest
from serverless.benchmark.run_batch import run_lock, terminate_tree
from serverless.benchmark.supervise import command as supervised
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.design import freeze


def successful_sources(campaign,output):
    found={}
    paths=list(campaign.glob('living-*/attempt-*.json'))+list((output/'local-segments').glob('*/attempt-*.json'))
    for path in paths:
        row=json.loads(path.read_text())
        if row['status']!='complete':
            continue
        seed=row['request']['seed']
        if seed in found:
            raise ValueError('Duplicate successful local request: '+str(seed))
        found[seed]=path
    return found


def missing_ranges(shard,completed):
    """Contiguous missing seed ranges, including holes left by failed attempts."""
    ranges=[]
    for index in range(shard['target']):
        if shard['seed']+index*shard['seedStep'] in completed:
            continue
        if ranges and ranges[-1][1]==index:
            ranges[-1]=(ranges[-1][0],index+1)
        else:
            ranges.append((index,index+1))
    return ranges


def prepare_jobs(plan,campaign,output,runtime,blender):
    manifest=output/'local-tasks.json'
    digest=implementation_digest(runtime)
    if manifest.exists():
        document=json.loads(manifest.read_text())
        if document['implementationSha256']!=digest:
            raise ValueError('Execution code changed; validate maintenance before creating another segment')
        return document['tasks']
    complete=successful_sources(campaign,output)
    tasks=[]
    for shard in plan['localLivingShards']:
        for start,end in missing_ranges(shard,complete):
            name=f"living-{shard['index']:02d}-{start:05d}"
            folder=output/'local-segments'/name
            tasks.append({'id':name,'kind':'generation','target':end-start,'folder':str(folder),
                'command':[sys.executable,'-m','serverless.benchmark.run_batch','--runtime',str(runtime),
                    '--blender',str(blender),'--output',str(folder),'--target',str(end-start),
                    '--max-attempts',str(end-start),'--room-type','living_room',
                    '--object-count',str(shard['objectCount']),'--seed',str(shard['seed']+start*shard['seedStep']),
                    '--seed-step',str(shard['seedStep']),'--blender-threads','1','--parallel-workers','6',
                    '--support','--solid-mesh-overlap']})
    for start in range(0,2500,100):
        selected=plan['localBedrooms'][start:start+100]
        if all((campaign/'bedroom-repaired'/row['file']).exists() for row in selected):
            continue
        tasks.append({'id':f'repair-{start:05d}','kind':'repair','target':len(selected),
            'files':[str(campaign/'bedroom-repaired'/row['file']) for row in selected],
            'command':[str(blender),'--background','--factory-startup','--threads','1','--python-exit-code','2',
                '--python',str(runtime/'serverless/benchmark/settle_saved.py'),'--',
                '--input',str(campaign/'bedroom-source'),'--output',str(campaign/'bedroom-repaired'),
                '--start',str(start),'--limit',str(len(selected))]})
    # Keep some correction work moving while long generation shards run.
    repairs=[task for task in tasks if task['kind']=='repair']
    generations=[task for task in tasks if task['kind']=='generation']
    tasks=[]
    for index,task in enumerate(generations):
        tasks.append(task)
        if index%2==1 and repairs:
            tasks.append(repairs.pop(0))
    tasks.extend(repairs)
    write_json(manifest,{'implementationSha256':digest,'tasks':tasks})
    return tasks


def task_complete(task):
    if task['kind']=='repair':
        return all(Path(path).exists() for path in task['files'])
    rows=[json.loads(path.read_text()) for path in Path(task['folder']).glob('attempt-*.json')]
    return len(rows)==task['target'] and all(row['status']=='complete' for row in rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('campaign','output','blender'):
        parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    campaign,output,blender=args.campaign.resolve(),args.output.resolve(),args.blender.resolve()
    root=Path(__file__).resolve().parents[2]
    if not (campaign/'STOP').exists():
        raise ValueError('Stop the superseded scheduler first')
    old=json.loads((campaign/'execution.json').read_text())
    for name,pid in old.get('activePids',{}).items():
        cmd=Path('/proc')/str(pid)/'cmdline'
        if cmd.exists() and b'serverless.benchmark.run_batch' in cmd.read_bytes() and name.encode() in cmd.read_bytes():
            raise ValueError('Original local worker is still active: '+name)
    plan=freeze(campaign,output)
    logs=output/'local-logs'; logs.mkdir(exist_ok=True)
    active={}
    def stop(signum,frame):
        raise KeyboardInterrupt('Stopped; completed evidence remains checkpointed')
    signal.signal(signal.SIGTERM,stop)
    with run_lock(output/'local-controller'):
        tasks=prepare_jobs(plan,campaign,output,root,blender)
        try:
            pending=[task for task in tasks if not task_complete(task)]
            last=0
            while pending or active:
                while pending and len(active)<6:
                    task=pending.pop(0)
                    handle=(logs/(task['id']+'.log')).open('ab')
                    env={**os.environ,'PYTHONHASHSEED':'0','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1',
                         'MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1'}
                    child=subprocess.Popen(supervised(task['command']),cwd=root,env=env,stdout=handle,
                        stderr=subprocess.STDOUT,start_new_session=True)
                    active[task['id']]=(child,handle,task)
                for name,(child,handle,task) in list(active.items()):
                    if child.poll() is None:
                        continue
                    handle.close(); del active[name]
                    if child.returncode or not task_complete(task):
                        raise RuntimeError('Local request failed; inspect retained evidence: '+name)
                if time.monotonic()-last>30:
                    state={'activePids':{name:item[0].pid for name,item in active.items()},
                           'pending':[task['id'] for task in pending],
                           'livingRoomsComplete':len(successful_sources(campaign,output)),
                           'bedroomsRepaired':sum((campaign/'bedroom-repaired'/row['file']).exists() for row in plan['localBedrooms'])}
                    write_json(output/'local-progress.json',state)
                    print(json.dumps(state),flush=True); last=time.monotonic()
                time.sleep(2)
            accepted=[]
            for item in plan['localBedrooms']:
                source=(campaign/'bedroom-source'/item['file']).read_bytes()
                path=campaign/'bedroom-repaired'/item['file']
                audit_record(json.loads(source),json.loads(path.read_bytes()),hashlib.sha256(source).hexdigest())
                accepted.append({'roomType':'bedroom','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            sources=successful_sources(campaign,output)
            for shard in plan['localLivingShards']:
                for index in range(shard['target']):
                    seed=shard['seed']+index*shard['seedStep']
                    path=sources[seed]; raw=path.read_bytes(); row=json.loads(raw)
                    if row['request']['roomType']!='living_room' or row['request']['objectCount']!=shard['objectCount']:
                        raise ValueError('Local request differs from allocation')
                    accepted.append({'roomType':'living_room','path':str(path),'sha256':hashlib.sha256(raw).hexdigest()})
            write_json(output/'local-completion.json',{'complete':True,'bedrooms':2500,'livingRooms':2500,'scenes':accepted})
        finally:
            for child,handle,_ in active.values():
                if child.poll() is None:
                    terminate_tree(child)
                handle.close()


if __name__=='__main__':
    main()
