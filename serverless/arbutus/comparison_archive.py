"""One discoverable S3 root for website comparison rooms and review inputs.

Copy verified public objects server-side; never upload private execution folders.
Keep existing public URLs valid. This does not delete remote evidence or publish
unfinished AI results. Each object is checksum-verified before the index points
to it, and progress is resumable without transferring native files via the PC.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from serverless.arbutus.worker import save
from serverless.benchmark.archive import merge_index, packed
from serverless.benchmark.run_batch import run_lock
from serverless.benchmark.stream_archive import upload, verify_remote
from serverless.cloud_benchmark.staged_pilot import read

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / '.codex/benchmark/soilie-platform-grid-final'
BUCKET = 'soilie3d-data'
PREFIX = 'files/outputs/website-comparisons/'


def copy_verified(client, artifact, key):
    if not artifact['key'].startswith('files/outputs/') or not key.startswith(PREFIX):
        raise ValueError('Only the scoped public research archive may be consolidated')
    try:
        client.head_object(Bucket=BUCKET, Key=key)
    except ClientError as error:
        if error.response['Error']['Code'] not in ('404','NoSuchKey','NotFound'):
            raise
        source = client.head_object(Bucket=BUCKET, Key=artifact['key'])
        if source['ContentLength'] != artifact['bytes']:
            raise ValueError('Source artifact size changed')
        client.copy_object(Bucket=BUCKET,Key=key,
            CopySource={'Bucket':BUCKET,'Key':artifact['key']}, CopySourceIfMatch=source['ETag'],
            ChecksumAlgorithm='SHA256',ServerSideEncryption='AES256')
    receipt = verify_remote(client,BUCKET,key,artifact['sha256'],artifact['bytes'])
    return receipt


def folder(scene):
    import re
    values = [scene['model'],scene['roomType'],scene['id']]
    if any(not re.fullmatch('[a-z0-9_-]+',v) for v in values):
        raise ValueError('Unsafe scene path')
    return PREFIX+'rooms/'+'/'.join(values)+'/'


def run(campaign, website, checkpoint):
    campaign, website, checkpoint = map(lambda p:Path(p).resolve(),(campaign,website,checkpoint))
    if campaign != (ROOT/'.codex/arbutus-inventory').resolve() or not checkpoint.is_relative_to(campaign):
        raise ValueError('Unexpected campaign/checkpoint scope')
    client = boto3.Session(profile_name='darkest',region_name='ca-central-1').client('s3',config=Config(max_pool_connections=16))
    with run_lock(checkpoint):
        public_ids = {r['sceneId'] for r in read(website/'benchmarks/room-measurements.json')['rows']}
        base = {r['scene']['id']:r for r in read(BASE/'evidence/measured-scenes.json')['rows']}
        stream = read(BASE/'stream-archive/uploads.json')['scenes']
        if public_ids - stream.keys():
            raise ValueError('A website room has no archived source')
        # Current quantitative cohort, plus every generated inventory proposal.
        # Compact geometry is retained for rejected/unmatched proposals too.
        entries = {key:dict(stream[key]) for key in sorted(public_ids)}
        def publish_existing(key):
            record = entries[key]
            destination = folder(record)
            artifacts = {}
            for name, artifact in record['artifacts'].items():
                if name=='geometry' and key in base:
                    body = packed(base[key])
                    sha = hashlib.sha256(body).hexdigest()
                    artifacts[name] = upload(client,BUCKET,destination+sha[:24]+'.json',body,'application/json',sha,len(body))
                else:
                    artifacts[name] = copy_verified(client,artifact,destination+Path(artifact['key']).name)
            return key, {**record,'artifacts':artifacts,'uses':['published-quantitative-cohort']}
        completed = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(publish_existing,key) for key in entries]
            for future in as_completed(futures):
                key,record = future.result()
                completed[key] = record
                if len(completed)%100==0:
                    save(checkpoint/'progress.json',{'phase':'quantitative-rooms','completed':len(completed),'expected':len(entries),'updatedAt':time.time()})
                    print(json.dumps({'copiedRooms':len(completed),'expected':len(entries)}),flush=True)
        # Native files with preserved source-run identifiers, not a claim that
        # every quantitative room was re-rendered for this archive operation.
        natives = []
        native_manifest = read(BASE/'completed-blend-archive/manifest.json')
        for native in native_manifest['scenes'].values():
            if native['sceneId'] not in public_ids:
                continue
            artifact = native['artifact']
            target = PREFIX+'native-source-scenes/'+native['run']+'/'+native['sceneId']+'/'+Path(artifact['key']).name
            natives.append({**native,'artifact':copy_verified(client,artifact,target)})
        inventory = {}
        expected = len(read(campaign/'campaign-v1.json')['tasks'])
        # The original uploader continues freeing disk while consolidation runs.
        # Read its durable receipts rather than relocating its live destination.
        while len(inventory)<expected:
            markers = list(campaign.glob('*/infinigen-*/s3-receipt.json')) + list(campaign.glob('s3/layoutgpt/*/receipt.json'))
            for marker in markers:
                receipt = read(marker)
                if receipt['id'] in inventory:
                    continue
                record = {k:receipt[k] for k in ('id','baseline','roomType')}
                dest = PREFIX+'inventory-conditioned/'+receipt['baseline']+'/'+receipt['roomType']+'/'+receipt['id']+'/'
                record['artifacts'] = {name:copy_verified(client,a,dest+Path(a['key']).name) for name,a in receipt['artifacts'].items()}
                inventory[receipt['id']] = record
                save(checkpoint/'progress.json',{'phase':'inventory-rooms','completed':len(inventory),'expected':expected,'updatedAt':time.time()})
            if len(inventory)<expected:
                time.sleep(10)
        # Reviewer prompts/packets will be attached after their frozen audit;
        # no session credentials, raw events or unfinished scores enter S3.
        manifest = {'schemaVersion':1,'title':'SOILIE-3D website room-generation comparisons',
            'description':'Comparison geometry and native scene evidence. Published quantitative rooms and inventory-conditioned AI-review inputs are identified separately; they are not interchangeable generation conditions.',
            'quantitativeCounts':dict(Counter(r['model'] for r in completed.values())),
            'quantitativeRooms':list(completed.values()),'inventoryRooms':list(inventory.values()),
            'nativeSourceScenes':natives,'reviewFiles':[],
            'aiResultsStatus':'Collection pending or in progress; no new preference results published.',
            'retention':'Research archive; not subject to generated-scene seven-day expiry.'}
        body = packed(manifest)
        client.put_object(Bucket=BUCKET,Key=PREFIX+'manifest.json',Body=body,ContentType='application/json',CacheControl='no-cache',ServerSideEncryption='AES256')
        keys = [PREFIX+'manifest.json']+[a['key'] for r in [*completed.values(),*inventory.values()] for a in r['artifacts'].values()]
        keys += [r['artifact']['key'] for r in natives]
        merge_index(client,BUCKET,keys)
        save(checkpoint/'progress.json',{'phase':'complete','keys':len(keys),'prefix':PREFIX,'updatedAt':time.time()})
        save(checkpoint/'manifest.json',manifest)
        return {'keys':len(keys),'prefix':PREFIX}


def attach_review(campaign, review):
    """Attach the fully audited protocol without recopying the room archive."""
    from serverless.cloud_benchmark.staged_pilot import load_frozen, digest
    campaign,review=Path(campaign).resolve(),Path(review).resolve()
    if not review.is_relative_to(campaign):
        raise ValueError('Review must belong to this inventory campaign')
    protocol=load_frozen(review)
    for filename in ('preflight.json','diagram-audit.json'):
        check=read(review/filename)
        if not check['passed'] or check['protocolSha256']!=digest(protocol):
            raise ValueError('Review audits must match the frozen protocol')
    client=boto3.Session(profile_name='darkest',region_name='ca-central-1').client('s3',config=Config(max_pool_connections=16))
    previous=client.get_object(Bucket=BUCKET,Key=PREFIX+'manifest.json')
    manifest=json.loads(previous['Body'].read())
    target=PREFIX+'review/'+digest(protocol)[:16]+'/'
    safe={k:v for k,v in protocol.items() if k!='sourceRoot'}
    source=Path(protocol['sourceRoot'])
    payloads=[('protocol.json',packed(safe),'application/json'),
              ('matching.json',packed(read(source/'matching.json')),'application/json'),
              ('input-audit.json',packed(read(source/'preflight.json')),'application/json'),
              ('source-scenes.json',packed(read(source/'source-scenes.json')),'application/json')]
    for name in ('preflight.json','diagram-audit.json','retention-report.json','packets/index.json'):
        payloads.append((name,packed(read(review/name)),'application/json'))
    index=read(review/'packets/index.json')
    files={p['file'] for p in index.values()}|{p[side+'Panel'] for p in index.values() for side in ('left','right')}
    files|={p['file'] for p in protocol['sourceImages'].values()}
    for name in sorted(files):
        payloads.append((name,(review/name).read_bytes(),'image/svg+xml' if name.endswith('.svg') else 'image/png'))
    def put(item):
        name,body,kind=item
        return upload(client,BUCKET,target+name,body,kind,hashlib.sha256(body).hexdigest(),len(body))
    with ThreadPoolExecutor(max_workers=12) as pool:
        artifacts=list(pool.map(put,payloads))
    # The review uses the measured native floor, not an incomplete horizontal-
    # only export. Preserve source receipts, and point current geometry at the
    # corrected representation without regenerating or moving any furniture.
    selected={s['id']:s for s in read(source/'source-scenes.json')['scenes']}
    updated_geometry=[]
    for entry in manifest['inventoryRooms']:
        scene=selected.get(entry['id'])
        if not scene or scene['model']!='infinigen_controlled': continue
        if scene['room'].get('boundaryExtractionVersion')!=2:
            raise ValueError('Native floor audit required before publishing review geometry')
        body=packed(scene)
        checksum=hashlib.sha256(body).hexdigest()
        key=PREFIX+'inventory-conditioned/infinigen/'+scene['roomType']+'/'+scene['id']+'/scene-'+checksum[:24]+'.json'
        artifact=upload(client,BUCKET,key,body,'application/json',checksum,len(body))
        entry.setdefault('sourceGeometry',entry['artifacts']['geometry'])
        entry['artifacts']['geometry']=artifact
        updated_geometry.append(artifact)
    supplemental=[]
    for dirname in ('layoutgpt-supplement','layoutgpt-topup'):
        path=campaign/dirname/'export.json'
        if not path.exists(): continue
        for scene in read(path)['scenes']:
            body=packed(scene)
            checksum=hashlib.sha256(body).hexdigest()
            key=PREFIX+'inventory-conditioned/layoutgpt/supplemental/'+scene['id']+'/'+checksum[:24]+'.json'
            supplemental.append({'id':scene['id'],'geometry':upload(client,BUCKET,key,body,'application/json',checksum,len(body))})
    manifest.update(reviewFiles=artifacts,supplementalLayoutgpt=supplemental,
        currentReview={'protocolSha256':digest(protocol),'prefix':target,'pairs':len(protocol['fullPairIds']),
                       'judgments':protocol['expectedMainJudgements'],'status':'collection-in-progress'})
    text=('# Website room-generation comparison data\n\n'
          'This folder contains the rooms used by the SOILIE-3D website comparisons.\n\n'
          '- `rooms/`: published quantitative cohort, including the final 10,000 SOILIE rooms.\n'
          '- `inventory-conditioned/`: new baseline proposals with recorded furniture inventories, including proposals outside matching limits.\n'
          '- `review/`: frozen matched pairs, numbered room diagrams, exact prompts, size references and validation checks.\n'
          '- `native-source-scenes/`: available native Blender outputs with their source-run identities.\n\n'
          'See `manifest.json` for artifact checksums and conditions. LayoutGPT geometry here is layout-box data, not retrieved furniture meshes. '
          'AI preference results are not yet published. Private credentials, reviewer sessions and execution logs are excluded.\n')
    client.put_object(Bucket=BUCKET,Key=PREFIX+'README.md',Body=text.encode(),ContentType='text/markdown; charset=utf-8',CacheControl='no-cache',ServerSideEncryption='AES256')
    client.put_object(Bucket=BUCKET,Key=PREFIX+'manifest.json',Body=packed(manifest),ContentType='application/json',
                      CacheControl='no-cache',ServerSideEncryption='AES256',IfMatch=previous['ETag'])
    keys=[PREFIX+'README.md',PREFIX+'manifest.json']+[a['key'] for a in artifacts+updated_geometry]+[r['geometry']['key'] for r in supplemental]
    merge_index(client,BUCKET,keys)
    save(campaign/'comparison-archive/review-receipt.json',{'prefix':target,'files':len(keys),'protocolSha256':digest(protocol)})
    return {'prefix':target,'files':len(keys)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--website',type=Path,required=True)
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--review',type=Path)
    p.add_argument('--attach-only',action='store_true')
    a=p.parse_args()
    if bool(a.review) != a.attach_only:
        p.error('--review and --attach-only must be used together after validation')
    print(attach_review(a.campaign,a.review) if a.attach_only else run(a.campaign,a.website,a.checkpoint),flush=True)
