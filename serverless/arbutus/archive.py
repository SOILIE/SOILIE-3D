"""Stream completed campaign outputs to S3 and evict verified local binaries.

The public bundle contains native scene files, not invocation logs or keys.
Private diagnostic files remain local. Every original archive member is hashed
and accounted for before its container can be deleted. Pending jobs are never
inputs, and task receipts remain available for resumption.
"""
import argparse
from datetime import date
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import time

import boto3

from serverless.arbutus.worker import save, sha
from serverless.benchmark.archive import merge_index, packed, public_only
from serverless.benchmark.run_batch import run_lock
from serverless.benchmark.stream_archive import upload, verify_remote

ROOT=Path(__file__).resolve().parents[2]
BUCKET='soilie3d-data'


def owned(path,root):
    """Reject path traversal and symlinks before reading or deleting a file."""
    root=root.resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root) or path.resolve()==root:
        raise ValueError('Artifact escaped its owned task directory')
    return path


def member_name(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe archive member')
    return path


class HashReader:
    def __init__(self,stream):
        self.stream=stream
        self.digest=hashlib.sha256()
    def read(self,size=-1):
        body=self.stream.read(size)
        self.digest.update(body)
        return body


def bundle(source,task):
    """Repack scene files losslessly; retain excluded diagnostics privately."""
    target=owned(task/'s3-native.tar.gz',task)
    manifest=[]
    seen=set()
    # Stable gzip/tar headers prevent orphaned duplicate keys on safe retries.
    with tarfile.open(source,'r|gz') as original, target.open('wb') as output, \
         gzip.GzipFile(fileobj=output,mode='wb',compresslevel=1,mtime=0,filename='') as compressed, \
         tarfile.open(fileobj=compressed,mode='w|') as public:
        for member in original:
            name=member_name(member.name)
            if not member.isfile() or member.name in seen:
                raise ValueError('Non-file or duplicate archive member')
            seen.add(member.name)
            content=original.extractfile(member)
            publish=name.parts[0]=='scene' and name.suffix not in ('.log','.pickle')
            # JSON/text can expose execution paths. Retain those privately if
            # they fail the same privacy checks used by the research archive.
            if name.suffix in ('.json','.txt','.csv','.log','.pickle'):
                if member.size>16*1024**2: raise ValueError('Unexpected diagnostic size')
                raw=content.read()
                content=io.BytesIO(raw)
                if publish:
                    try: public_only(json.loads(raw) if name.suffix=='.json' else raw.decode('utf-8'))
                    except (ValueError,UnicodeError): publish=False
            reader=HashReader(content)
            if publish:
                clean=tarfile.TarInfo(name.as_posix())
                clean.size=member.size
                clean.mode=0o644
                public.addfile(clean,reader)
            else:
                private=owned(task/'private-evidence'/name.as_posix(),task)
                private.parent.mkdir(parents=True,exist_ok=True)
                with private.open('wb') as output:
                    for chunk in iter(lambda:reader.read(1024**2),b''): output.write(chunk)
            manifest.append({'name':name.as_posix(),'sha256':reader.digest.hexdigest(),
                             'bytes':member.size,'public':publish})
    if not any(m['name']=='scene/scene.blend' and m['public'] for m in manifest):
        raise ValueError('Native Blender scene missing from bundle')
    # Read back the actual staged bytes, not just input hashes, before upload.
    with tarfile.open(target,'r|gz') as staged:
        expected={m['name']:m for m in manifest if m['public']}
        for member in staged:
            reader=HashReader(staged.extractfile(member))
            while reader.read(1024**2): pass
            entry=expected.pop(member.name)
            if reader.digest.hexdigest()!=entry['sha256'] or member.size!=entry['bytes']:
                raise ValueError('Native bundle is not lossless')
        if expected: raise ValueError('Native bundle omitted files')
    for entry in manifest:
        if not entry['public'] and sha(task/'private-evidence'/entry['name'])!=entry['sha256']:
            raise ValueError('Private diagnostics were not retained')
    return target,manifest


def evict_verified(task,source,receipt,local):
    """Delete exact completed files only, after all S3 artifacts are verified."""
    freed=0
    for member in receipt['members']:
        if not member['public']:
            private=owned(task/'private-evidence'/member['name'],task)
            if not private.exists() or sha(private)!=member['sha256']:
                raise ValueError('Private evidence missing before eviction')
    if source.exists():
        owned(source,task)
        if sha(source)!=receipt['sourceArchiveSha256']:
            raise ValueError('Original archive changed before eviction')
    # The native local files duplicate members of the now-verified archive.
    # Hash-check and unlink them individually, never recursively remove a run.
    if local:
        for member in receipt['members']:
            if not member['public'] or not member['name'].startswith('scene/'): continue
            path=owned(source.parent/member['name'],task)
            if path.exists():
                if sha(path)!=member['sha256']: raise ValueError('Native output changed before eviction')
                freed+=path.stat().st_size
                path.unlink()
    if source.exists():
        freed+=source.stat().st_size
        source.unlink()
    staged=owned(task/'s3-native.tar.gz',task)
    if staged.exists():
        if sha(staged)!=receipt['artifacts']['native']['sha256']:
            raise ValueError('Staged archive differs from verified S3 content')
        staged.unlink()
    return freed


def archive_room(client,root,directory,record,prefix):
    task=owned(root/directory/record['id'],root)
    local=directory=='local'
    relative=record.get('artifactPath','artifacts.tar.gz') if local else 'artifacts.tar.gz'
    if not re.fullmatch(r'(?:attempt-\d{2}/)?artifacts\.tar\.gz',relative):
        raise ValueError('Unexpected completed archive path')
    source=owned(task/relative,task)
    receipt_path=task/'s3-receipt.json'
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_bytes())
        if (receipt['sourceArchiveSha256']!=record['artifactsSha256'] or receipt['prefix']!=prefix
                or receipt['planSha256']!=record['planSha256']):
            raise ValueError('S3 receipt does not describe this campaign')
        if not receipt.get('evictionComplete'):
            for artifact in receipt['artifacts'].values():
                verify_remote(client,BUCKET,artifact['key'],artifact['sha256'],artifact['bytes'])
            receipt['evictedBytes']=receipt.get('evictedBytes',0)+evict_verified(task,source,receipt,local)
            receipt['evictionComplete']=True
            save(receipt_path,receipt)
        return receipt
    if not source.exists() or sha(source)!=record['artifactsSha256']:
        raise ValueError('Completed source archive missing or changed')
    scene_path=owned(source.parent/'scene.json',task)
    if sha(scene_path)!=record['sceneSha256']: raise ValueError('Scene changed before publication')
    scene=json.loads(scene_path.read_bytes())
    public_only(scene)
    staged,members=bundle(source,task)
    folder=prefix+'infinigen/'+record['task']['roomType']+'/'+record['id']+'/'
    artifacts={}
    body=packed(scene)
    digest=hashlib.sha256(body).hexdigest()
    artifacts['geometry']=upload(client,BUCKET,folder+'scene-'+digest[:24]+'.json',body,
        'application/json',digest,len(body))
    digest=sha(staged)
    with staged.open('rb') as body:
        artifacts['native']=upload(client,BUCKET,folder+'native-'+digest[:24]+'.tar.gz',body,
            'application/gzip',digest,staged.stat().st_size)
    receipt={'schemaVersion':1,'id':record['id'],'roomType':record['task']['roomType'],
        'baseline':'infinigen','prefix':prefix,'planSha256':record['planSha256'],
        'sourceArchiveSha256':record['artifactsSha256'],'artifacts':artifacts,'members':members,
        'verifiedAt':time.time(),'evictionComplete':False}
    save(receipt_path,receipt)  # Recovery pointer is durable BEFORE deletion.
    receipt['evictedBytes']=evict_verified(task,source,receipt,local)
    receipt['evictionComplete']=True
    save(receipt_path,receipt)
    return receipt


def public_record(receipt):
    return {k:receipt[k] for k in ('id','baseline','roomType','artifacts')}


def publish_manifest(client,prefix,plan_sha,records):
    """Expose verified rooms incrementally, without waiting for the full batch."""
    manifest={'schemaVersion':1,'planSha256':plan_sha,'scenes':list(records.values()),
        'description':'Inventory-conditioned room outputs. Native Infinigen scene files and LayoutGPT geometry; final pair matching and AI evaluation are pending.',
        'retention':'Research outputs outside the seven-day generated/ expiry prefix.'}
    body=packed(manifest)
    key=prefix+'manifest.json'
    client.put_object(Bucket=BUCKET,Key=key,Body=body,ContentType='application/json',
        CacheControl='no-cache',ServerSideEncryption='AES256')
    merge_index(client,BUCKET,[key]+[a['key'] for r in records.values() for a in r['artifacts'].values()])


def archive_layouts(client,root,plan,prefix,on_ready=None):
    export=root/'layoutgpt/export.json'
    if not export.exists(): return []
    document=json.loads(export.read_bytes())
    current={s['id'].removeprefix('layoutgpt-'):s for s in document['scenes']}
    original=ROOT/'.codex/benchmark/soilie-platform-grid-final/review-functional-fronts-v3/source-scenes.json'
    retained={s['id']:s for s in json.loads(original.read_bytes())['scenes']}
    records=[]
    for task in plan['tasks']:
        if task['baseline']!='layoutgpt': continue
        scene=current.get(task['id']) if task['needsGeneration'] else retained.get(task['retainedBaselineSceneId'])
        if scene is None: continue
        marker=root/'s3/layoutgpt'/task['id']/'receipt.json'
        if marker.exists():
            record=json.loads(marker.read_bytes())
            if record['prefix']!=prefix: raise ValueError('Changed LayoutGPT archive destination')
        else:
            body=packed(scene)
            digest=hashlib.sha256(body).hexdigest()
            key=prefix+'layoutgpt/'+task['roomType']+'/'+task['id']+'/scene-'+digest[:24]+'.json'
            artifact=upload(client,BUCKET,key,body,'application/json',digest,len(body))
            record={'id':task['id'],'baseline':'layoutgpt','roomType':task['roomType'],
                    'prefix':prefix,'artifacts':{'geometry':artifact}}
            marker.parent.mkdir(parents=True,exist_ok=True)
            save(marker,record)
        records.append(public_record(record))
        if on_ready and len(records)%20==0: on_ready(records)
    return records


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--date',required=True)
    p.add_argument('--profile',default='darkest')
    p.add_argument('--once',action='store_true')
    args=p.parse_args()
    root=args.root.resolve()
    if root!=(ROOT/'.codex/arbutus-inventory').resolve(): raise ValueError('Expected owned campaign root')
    day=date.fromisoformat(args.date).isoformat()
    plan_sha=sha(root/'campaign-v1.json')
    plan=json.loads((root/'campaign-v1.json').read_bytes())
    tasks={t['id']:t for t in plan['tasks']}
    prefix=f'files/outputs/inventory-{day}-{plan_sha[:12]}/'
    state_dir=root/'s3'
    client=boto3.Session(profile_name=args.profile,region_name='ca-central-1').client('s3')
    with run_lock(state_dir):
        save(state_dir/'settings.json',{'bucket':BUCKET,'prefix':prefix,'planSha256':plan_sha})
        while True:
            # Durable receipts keep publication monotonic if a later transfer
            # fails: an already published room must never disappear.
            records={}
            for directory in ('cloud','local'):
                for marker in (root/directory).glob('*/s3-receipt.json'):
                    prior=json.loads(marker.read_bytes())
                    if prior['prefix']!=prefix or prior['planSha256']!=plan_sha:
                        raise ValueError('Archive destination changed')
                    records[prior['id']]=public_record(prior)
            def expose_layouts(batch):
                records.update({r['id']:r for r in batch})
                publish_manifest(client,prefix,plan_sha,records)
            # Small geometry files can be published without staging binaries.
            records.update({r['id']:r for r in archive_layouts(client,root,plan,prefix,expose_layouts)})
            warning=None
            # Prioritize the large room archives; small LayoutGPT exports follow.
            for directory in ('local','cloud'):
                for receipt_path in sorted((root/directory).glob('*/receipt.json')):
                    r=json.loads(receipt_path.read_bytes())
                    if r['id'] not in tasks or r['planSha256']!=plan_sha:
                        raise ValueError('Unknown task receipt')
                    if r['status']!='complete': continue
                    if directory=='cloud' and not (receipt_path.parent/'verified.json').exists(): continue
                    try:
                        marker=receipt_path.parent/'s3-receipt.json'
                        if marker.exists() and json.loads(marker.read_bytes()).get('evictionComplete'): continue
                        archived=archive_room(client,root,directory,r,prefix)
                        records[r['id']]=public_record(archived)
                        save(state_dir/'progress.json',{'completed':len(records),'lastScene':r['id'],
                            'updatedAt':time.time(),'warning':None,'prefix':prefix})
                        print(json.dumps({'archived':r['id'],'freedBytes':archived.get('evictedBytes',0)}),flush=True)
                        if len(records)%4==0: publish_manifest(client,prefix,plan_sha,records)
                    except Exception as error:
                        # Retain all local data on verification/network failure.
                        warning=type(error).__name__+': '+str(error)[:250]
                        print(json.dumps({'task':r['id'],'error':warning}),flush=True)
                        break
            publish_manifest(client,prefix,plan_sha,records)
            save(state_dir/'progress.json',{'completed':len(records),'expected':len(tasks),
                'updatedAt':time.time(),'warning':warning,'prefix':prefix})
            if args.once or len(records)==len(tasks): return
            time.sleep(10)


if __name__=='__main__': main()
