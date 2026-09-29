"""Pull completed cloud evidence through this local node, with hash verification.

There is no public receiver or cloud-held local credential. SSH uses the pinned
private jump route. Remote archives remain intact; local copies become visible
only after SHA-256 validation. A low disk condition pauses transfer, not jobs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

from serverless.arbutus.worker import save, sha


def ssh(config,command):
    return subprocess.check_output(['ssh','-F',str(config),'soilie-mike-dev',command],timeout=60,text=True)


def snapshot(root,plan,remote_state,warning=None):
    receipts={}
    for directory in ('local','cloud'):
        for path in (root/directory).glob('*/receipt.json'):
            row=json.loads(path.read_bytes())
            if row['planSha256']!=sha(root/'campaign-v1.json'):
                raise ValueError('Receipt from another campaign')
            if row['status']=='delegated': continue
            receipts[row['id']]=row
    for row in remote_state.get('receipts',[]):
        receipts.setdefault(row['id'],row)
    running=set(remote_state.get('running',[]))
    if (root/'local/controller.lock').exists():
        for start in (root/'local').glob('*/attempt-*/started.json'):
            identity=start.parent.parent.name
            if identity not in receipts and not (start.parent/'receipt.json').exists(): running.add(identity)
    ledger_path=root/'layoutgpt/inference-ledger.json'
    ledger=json.loads(ledger_path.read_bytes()) if ledger_path.exists() else {'entries':{}}
    export_path=root/'layoutgpt/export.json'
    exported=json.loads(export_path.read_bytes()) if export_path.exists() else {'attempts':[]}
    valid={r['id'] for r in exported['attempts'] if r.get('geometryStatus')=='complete' and r.get('inventorySatisfied')}
    groups=[]
    for baseline in ('infinigen','layoutgpt'):
        selected=[t for t in plan['tasks'] if t['baseline']==baseline]
        retained=sum(not t['needsGeneration'] for t in selected)
        if baseline=='infinigen':
            complete=[r for t in selected if (r:=receipts.get(t['id'])) and r['status']=='complete']
            completed=retained+len(complete)
            failed=sum(receipts.get(t['id'],{}).get('status')=='failed' for t in selected)
            active=sum(t['id'] in running for t in selected)
            delivered=sum((root/'cloud'/t['id']/'verified.json').exists() or
                          (root/'local'/t['id']/'s3-receipt.json').exists() or
                          (root/'local'/t['id']/receipts[t['id']].get('artifactPath','artifacts.tar.gz')).exists()
                          for t in selected if receipts.get(t['id'],{}).get('status')=='complete')
        else:
            complete=[]
            completed=retained+sum(t['id'] in valid for t in selected)
            failed=sum(ledger['entries'].get(t['id'],{}).get('status') in ('error','uncertain') for t in selected)
            active=sum(ledger['entries'].get(t['id'],{}).get('status')=='reserved' for t in selected)
            delivered=completed-retained
        eta=None
        # Elapsed completion rate is shown only after a useful sample. This
        # includes host contention and transfer overhead, not CPU-only times.
        if len(complete)>=4 and active:
            elapsed=time.time()-min(r['startedAt'] for r in complete)
            eta=(len(selected)-completed)*elapsed/len(complete)
        elif baseline=='layoutgpt' and active:
            durations=[r['wallSeconds'] for r in ledger['entries'].values()
                       if r['status']=='complete' and r.get('wallSeconds')]
            if len(durations)>=4:
                eta=(len(selected)-completed)*sum(durations)/len(durations)/max(1,active)
        details={room:sum((not t['needsGeneration']) or (receipts.get(t['id'],{}).get('status')=='complete'
            if baseline=='infinigen' else t['id'] in valid) for t in selected if t['roomType']==room)
            for room in ('bedroom','living_room')}
        archived=sum(any((root/directory/t['id']/'s3-receipt.json').exists() for directory in ('local','cloud'))
                     if baseline=='infinigen' else (root/'s3/layoutgpt'/t['id']/'receipt.json').exists()
                     for t in selected)
        groups.append({'key':baseline,'expected':len(selected),'completed':completed,'retained':retained,'archived':archived,
            'active':active,'failed':failed,'delivered':delivered,'etaSeconds':eta,'rooms':details})
    spent=sum(attempt.get('actualUsd',attempt.get('reservedUsd',0))
              for r in ledger['entries'].values() for attempt in [r,*r.get('previousAttempts',[])])
    balance_exhausted=any(r.get('errorCode')=='credit_balance_exhausted' for r in ledger['entries'].values())
    if balance_exhausted and not warning:
        warning='LayoutGPT paused: OpenAI API credit balance exhausted. Infinigen continues.'
    state={'updatedAt':datetime.now(timezone.utc).isoformat(),'groups':groups,
        'expected':sum(g['expected'] for g in groups),'completed':sum(g['completed'] for g in groups),
        'apiAccountedUsd':spent,'apiCapUsd':35,'warning':warning,
        'state':'complete' if all(g['completed']==g['expected'] and g['delivered']+g['retained']==g['expected'] for g in groups) else 'running'}
    remaining=[g for g in groups if g['completed']<g['expected']]
    if (root/'s3/settings.json').exists() and any(g['archived']<g['expected'] for g in groups):
        state['state']='running'  # Completion includes the requested S3 handoff.
    state['etaSeconds']=max((g['etaSeconds'] for g in remaining),default=0) if all(g['etaSeconds'] is not None for g in remaining) else None
    save(root/'progress.json',state)
    return state


def verify_archive(task, record):
    """Verify the complete archive and compact geometry before marking delivery."""
    archive=task/'artifacts.tar.gz'
    if sha(archive)!=record['artifactsSha256']:
        raise ValueError('Archive checksum mismatch')
    with tarfile.open(archive) as tar:
        member=tar.getmember('scene.json')
        if not member.isfile() or member.size>10*1024**2:
            raise ValueError('Unexpected scene metadata')
        raw=tar.extractfile(member).read()
        if hashlib.sha256(raw).hexdigest()!=record['sceneSha256']:
            raise ValueError('Scene metadata checksum mismatch')
        (task/'scene.json').write_bytes(raw)
    save(task/'verified.json',{'sha256':record['artifactsSha256'],'verifiedAt':time.time()})


def transfer_archive(root,record):
    task=root/'cloud'/record['id']
    artifact_path=record.get('artifactPath','artifacts.tar.gz')
    if not re.fullmatch(r'(?:attempt-\d{2}/)?artifacts\.tar\.gz',artifact_path):
        raise ValueError('Invalid remote artifact path')
    temporary=task/'artifacts.tar.gz.partial'
    subprocess.run(['scp','-F',str(root/'ssh-config'),
        'soilie-mike-dev:/mnt/soilie/campaign/'+record['id']+'/'+artifact_path,str(temporary)],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=900,check=True)
    if sha(temporary)!=record['artifactsSha256']:
        raise ValueError('Archive checksum mismatch')
    temporary.replace(task/'artifacts.tar.gz')
    verify_archive(task,record)


def run(root,once=False,transfers=4):
    plan=json.loads((root/'campaign-v1.json').read_bytes())
    plan_sha=sha(root/'campaign-v1.json')
    task_ids={t['id'] for t in plan['tasks'] if t['baseline']=='infinigen'}
    remote={}
    pending={}
    compiled_mtime=None
    # Worker threads only write their own task's artifacts. This controller is
    # the sole writer of receipts and progress, so downloads cannot race saves.
    with ThreadPoolExecutor(max_workers=transfers) as pool:
        while True:
            warning=None
            for identifier,(future,_size) in list(pending.items()):
                if future.done():
                    try: future.result()
                    except Exception as error:
                        warning=type(error).__name__+': transfer will retry; remote evidence retained'
                    del pending[identifier]
            try:
                remote=json.loads(ssh(root/'ssh-config',
                    'cd /mnt/soilie/backend && python3 -m serverless.arbutus.remote_status'))
                for record in remote['receipts']:
                    if record['planSha256']!=plan_sha or record['id'] not in task_ids:
                        raise ValueError('Cloud receipt does not match frozen campaign')
                    task=root/'cloud'/record['id']
                    task.mkdir(parents=True,exist_ok=True)
                    save(task/'receipt.json',record)
                    if (record['status']!='complete' or (task/'verified.json').exists()
                            or record['id'] in pending or len(pending)>=transfers): continue
                    # Reserve space for ALL in-flight transfers before starting
                    # another one; concurrency must not defeat the disk guard.
                    reserved=sum(size for _future,size in pending.values())
                    if shutil.disk_usage(root).free < 12*1024**3+reserved+record['artifactsBytes']:
                        warning='Transfer paused: preserving 12 GiB local disk reserve'
                        break
                    pending[record['id']]=(pool.submit(transfer_archive,root,record),record['artifactsBytes'])
            except (OSError,ValueError,subprocess.SubprocessError) as error:
                warning=type(error).__name__+': transfer will retry; remote evidence retained'
            # Recompile only after a ledger change. This never starts paid calls.
            ledger=root/'layoutgpt/inference-ledger.json'
            if ledger.exists() and ledger.stat().st_mtime_ns!=compiled_mtime:
                observed_mtime=ledger.stat().st_mtime_ns
                try:
                    subprocess.run([sys.executable,'-m','serverless.benchmark.import_layoutgpt_controlled',
                        '--folder',str(root/'layoutgpt'),'--parser',str(root.parents[1]/'.codex/layoutgpt-source/parse_llm_output.py')],
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90,check=True)
                    compiled_mtime=observed_mtime
                except (OSError,subprocess.SubprocessError):
                    warning='LayoutGPT geometry export needs attention; paid responses are preserved'
            state=snapshot(root,plan,remote,warning)
            if once or state['state']=='complete': break
            time.sleep(5)
    # The one-shot command waits for its downloads, then publishes final counts.
    for future,_size in pending.values(): future.result()
    snapshot(root,plan,remote,warning)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--once',action='store_true')
    p.add_argument('--transfers',type=int,choices=range(1,5),default=4)
    args=p.parse_args()
    root=args.root.resolve()
    lock=root/'relay.lock'
    with lock.open('x') as f: json.dump({'pid':os.getpid()},f)
    try: run(root,args.once,args.transfers)
    finally: lock.unlink()


if __name__=='__main__': main()
