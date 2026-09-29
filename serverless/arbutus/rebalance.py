"""Hand off untouched local tasks while allowing in-flight Blender jobs to finish.

Run inside WSL. Briefly stop only the owned Python scheduler, not Blender, to
freeze dispatch. Prove all its worker slots are occupied before marking queued
tasks delegated. The existing scheduler skips their durable receipts. Resume
the scheduler in finally; a ready marker gates execution on the other host.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import time

from serverless.arbutus.worker import ROOT, save, selected, sha


def pending_tasks(plan,root,pid,workers):
    active=[]
    pending=[]
    for task in plan['tasks']:
        if not selected(task,'local'): continue
        folder=root/'local'/task['id']
        if (folder/'receipt.json').exists(): continue
        started=folder/'started.json'
        if started.exists() and json.loads(started.read_bytes()).get('pid')==pid:
            active.append(task['id'])
            continue
        if any(folder.glob('attempt-*')) or any(folder.glob('*.new')):
            raise ValueError('Unfinished evidence requires inspection: '+task['id'])
        pending.append(task)
    # Otherwise a thread could be between dequeue and its first checkpoint.
    # Refuse to guess which invisible task it owns; retry at a stable point.
    if len(active)!=workers: raise ValueError('Scheduler between tasks; no handoff made')
    return active,pending


def handoff(root,output):
    if os.name!='posix': raise ValueError('Run this command inside WSL')
    root=root.resolve()
    if root!=(ROOT/'.codex/arbutus-inventory').resolve(): raise ValueError('Expected campaign root')
    if output.exists() or not output.resolve().is_relative_to(root):
        raise ValueError('Use a new campaign-local assignment path')
    plan_path=root/'campaign-v1.json'
    plan=json.loads(plan_path.read_bytes())
    pid=json.loads((root/'local/controller.lock').read_bytes())['pid']
    process=Path('/proc')/str(pid)
    command=process.joinpath('cmdline').read_bytes().decode().split('\0')
    if ('serverless.arbutus.worker' not in command or '--host' not in command
            or command[command.index('--host')+1]!='local'):
        raise ValueError('Controller identity differs')
    directory=Path(os.readlink(process/'cwd'))
    owned_output=(directory/command[command.index('--output')+1]).resolve()
    if owned_output!=root/'local': raise ValueError('Controller belongs to another campaign')
    workers=int(command[command.index('--workers')+1])
    if '\nState:\tT' in (process/'status').read_text(): raise ValueError('Controller was already stopped')
    stopped=False
    try:
        os.kill(pid,signal.SIGSTOP)
        stopped=True
        deadline=time.monotonic()+2
        while '\nState:\tT' not in (process/'status').read_text():
            if time.monotonic()>deadline: raise ValueError('Could not freeze dispatch safely')
            time.sleep(.01)
        active,pending=pending_tasks(plan,root,pid,workers)
        if not pending: raise ValueError('No untouched tasks remain')
        document={'schemaVersion':1,'planSha256':sha(plan_path),'host':'arbutus','originalHost':'local',
            'taskIds':[t['id'] for t in pending],'preservedLocalTasks':active,
            'reason':'Idle cloud capacity; transfer only unstarted tasks without changing generation inputs'}
        save(output,document)
        digest=sha(output)
        for task in pending:
            folder=root/'local'/task['id']
            folder.mkdir(exist_ok=True)
            # Completion is not fabricated: this is explicitly not a scene.
            with (folder/'receipt.json').open('x') as file:
                json.dump({'id':task['id'],'status':'delegated','host':'arbutus',
                    'planSha256':document['planSha256'],'assignmentSha256':digest,'task':task},file,indent=2)
        save(output.with_suffix('.ready.json'),{'assignmentSha256':digest})
        print(json.dumps({'delegated':len(pending),'leftRunningLocally':active,'assignment':str(output)}))
    finally:
        if stopped:
            try: os.kill(pid,signal.SIGCONT)
            except ProcessLookupError: pass


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    handoff(args.root,args.output)
