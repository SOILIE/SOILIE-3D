"""Publish the validated current comparison under the one discoverable S3 root."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re

from serverless.benchmark.archive import packed, merge_index
from serverless.benchmark.stream_archive import upload
from serverless.cloud_benchmark.staged_pilot import read, digest
from serverless.cloud_benchmark.full_review import bucket
from serverless.cloud_benchmark.audited_review_release import counts

PREFIX='files/outputs/website-comparisons/'
FILES=('comparison.json','status.json','room-measurements.json','support-measurements.json',
       'cost-measurements.json','layoutgpt-scale.json','publication-inputs.json','native-floor-measurements.json',
       'review-manifest.json','review-examples.json','ai-pilot-summary.json','ai-pilot-responses.json',
       'ai-pilot-infinigen-summary.json','ai-pilot-infinigen-responses.json')
AUDIT_FILES=('timing-measurements.json','coverage-audit.json','native-contact-measurements.json')


def validate(directory):
    directory=Path(directory)
    document=read(directory/'comparison.json')
    expected=document.pop('evidenceDigest')
    if digest(document)!=expected: raise ValueError('Comparison changed after compilation')
    manifest=read(directory/'review-manifest.json')
    if document['aiReview']['protocolSha256']!=manifest['protocolSha256'] or not manifest['audit']['passed']:
        raise ValueError('Unverified review provenance')
    if document['aiReview']['manifestSha256']!=digest((directory/'review-manifest.json').read_bytes()):
        raise ValueError('Review manifest changed')
    for name, checksum in manifest['files'].items():
        if name not in FILES or digest((directory/name).read_bytes())!=checksum: raise ValueError('Public review export changed')
    for stem in ('ai-pilot','ai-pilot-infinigen'):
        summary, responses=read(directory/(stem+'-summary.json')),read(directory/(stem+'-responses.json'))
        if summary['protocolSha256']!=manifest['protocolSha256'] or not summary['releaseEligible']:
            raise ValueError('Summary protocol mismatch')
        answers={r['assignmentId']:r for r in responses['responses']}
        if len(answers)!=2400 or len(responses['pairs'])!=1200: raise ValueError('Incomplete review baseline')
        used=set()
        for pair in responses['pairs']:
            a,b=[answers[k] for k in pair['assignmentIds']]
            if a['pairedWith']!=b['assignmentId'] or a['leftCondition']!=b['rightCondition'] or any(a[k]!=b[k] for k in ('pairId','profile','roomType','baseline')):
                raise ValueError('Unpaired responses')
            def physical(r):
                return 'tie' if r['judgement']=='tie' else 'model_a' if r[r['judgement']+'Condition']=='soilie' else 'model_b'
            if bucket(physical(a),physical(b))!=(pair['bucket'],pair['subtype']): raise ValueError('Altered verdict')
            for key in pair['assignmentIds']:
                if key in used: raise ValueError('A judgment contributed twice')
                used.add(key)
        for room, dimensions in summary['dimensionsByRoomType'].items():
            for row in dimensions:
                actual=counts([p for p in responses['pairs'] if p['roomType']==room and p['profile']==row['id']])
                if any(row[k]!=v for k,v in actual.items()) or actual['pairs']!=120: raise ValueError('Chart differs from responses')
    records=read(directory/'room-measurements.json')
    totals=Counter(r['model'] for r in records['rows'])
    if len({r['sceneId'] for r in records['rows']})!=len(records['rows']) or dict(totals)!={m:r['n'] for m,r in document['models'].items()}:
        raise ValueError('Geometry counts differ')
    if Counter(r['roomType'] for r in records['rows'] if r['model']=='soilie')!={'bedroom':5000,'living_room':5000}:
        raise ValueError('SOILIE corpus is incomplete')
    names=FILES+(AUDIT_FILES if 'coverage' in document else ())
    if 'boxSupport' in document:
        name='box-support-measurements.json'
        if digest((directory/name).read_bytes())!=document['boxSupport']['sha256']:
            raise ValueError('Box support measurements changed')
        names+=(name,)
    files={name:('application/json',(directory/name).read_bytes()) for name in names}
    # Companion explanations and tabular exports share the exact measurement
    # hashes. Keep them in the same discoverable analysis archive as the data.
    notes_path=directory/'support-explanations.json'
    if notes_path.exists():
        notes=read(notes_path)
        if notes['evidenceDigest']!=expected or any(
                name not in names or digest((directory/name).read_bytes())!=checksum
                for name,checksum in notes['inputs'].items()):
            raise ValueError('Support explanations use stale evidence')
        files['support-explanations.json']=('application/json',notes_path.read_bytes())
    for mime,raw in files.values():
        import json
        packed(json.loads(raw))
    download_path=directory/'downloads/manifest.json'
    if download_path.exists():
        downloads=read(download_path)
        if any(name not in names or digest((directory/name).read_bytes())!=checksum
               for name,checksum in downloads['inputs'].items()):
            raise ValueError('Downloads use stale measurements')
        allowed={'room-results.csv':'text/csv; charset=utf-8',
                 'room-results.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
        if set(downloads['files'])!=set(allowed): raise ValueError('Unexpected download filenames')
        for name,mime in allowed.items():
            raw=(directory/'downloads'/name).read_bytes()
            if digest(raw)!=downloads['files'][name]: raise ValueError('Download checksum differs')
            files['downloads/'+name]=(mime,raw)
        files['downloads/manifest.json']=('application/json',download_path.read_bytes())
    for example in document.get('illustrations',[]):
        name=example['image'].removeprefix('benchmarks/')
        if not re.fullmatch(r'illustrations/[a-f0-9]{24}\.svg',name): raise ValueError('Unsafe figure path')
        raw=(directory/name).read_bytes()
        if digest(raw)[:24]!=Path(name).stem: raise ValueError('Changed figure')
        files[name]=('image/svg+xml',raw)
    return files, expected


def publish(directory, version, corrected=None):
    import boto3
    if not re.fullmatch(r'\d+\.\d+\.\d+',version): raise ValueError('Invalid website version')
    files,evidence=validate(directory)
    target=PREFIX+'analysis-v'+version+'-'+evidence[:12]+'/'
    client=boto3.Session(profile_name='darkest',region_name='ca-central-1').client('s3')
    bucket_name='soilie3d-data'
    def send(item):
        name,(mime,raw)=item
        return upload(client,bucket_name,target+name,raw,mime,digest(raw),len(raw))
    with ThreadPoolExecutor(max_workers=8) as pool: receipts=list(pool.map(send,files.items()))
    response=client.get_object(Bucket=bucket_name,Key=PREFIX+'manifest.json')
    import json
    manifest=json.loads(response['Body'].read())
    if corrected:
        from serverless.benchmark.stimuli import diagram
        index={r['id']:r for r in manifest['quantitativeRooms']}
        for row in read(corrected)['rows']:
            scene=row['scene']
            if scene['id'] not in index:
                entry={'id':scene['id'],'model':scene['model'],'roomType':scene['roomType'],
                    'objects':[obj['label'] for obj in scene['objects']],
                    'artifacts':{},'uses':['published-quantitative-cohort']}
                index[scene['id']]=entry
                manifest['quantitativeRooms'].append(entry)
            entry=index[scene['id']]
            for label,raw,ext,mime in [('geometry',packed(row),'json','application/json'),('diagram',diagram(scene).encode(),'svg','image/svg+xml')]:
                key=PREFIX+'rooms/'+scene['model']+'/'+scene['roomType']+'/'+scene['id']+'/'+digest(raw)[:24]+'.'+ext
                receipt=upload(client,bucket_name,key,raw,mime,digest(raw),len(raw))
                entry['artifacts'][label]=receipt; receipts.append(receipt)
    review=read(Path(directory)/'review-manifest.json')
    manifest.update(aiResultsStatus='complete',currentAnalysis={'prefix':target,'websiteVersion':version,'evidenceDigest':evidence,'files':receipts[:len(files)]})
    manifest['currentReview'].update(status='complete',judgments=4800,protocolSha256=review['protocolSha256'],resultsPrefix=target)
    client.put_object(Bucket=bucket_name,Key=PREFIX+'manifest.json',Body=packed(manifest),ContentType='application/json',CacheControl='public,max-age=60',IfMatch=response['ETag'],ServerSideEncryption='AES256')
    text=('# Website room-generation comparisons\n\n'
          'The current methods, measurements and AI judgments are in `'+target.removeprefix(PREFIX)+'`.\n\n'
          '- `rooms/`: quantitative cohort, including 10,000 SOILIE rooms.\n'
          '- `inventory-conditioned/`: baseline proposals for matching furniture inventories.\n'
          '- `review/`: frozen prompts, diagrams, reference values and input checks.\n'
          '- `native-source-scenes/`: saved native Blender outputs.\n\n'
          'Each room pair is judged on five dimensions in two isolated, opposite-side contexts. The final analysis reports all ties and disagreements. These are AI judgments, not human validation.\n')
    client.put_object(Bucket=bucket_name,Key=PREFIX+'README.md',Body=text.encode(),ContentType='text/markdown; charset=utf-8',CacheControl='public,max-age=60',ServerSideEncryption='AES256')
    merge_index(client,bucket_name,[r['key'] for r in receipts]+[PREFIX+'manifest.json',PREFIX+'README.md'])
    return {'prefix':target,'verifiedFiles':len(receipts),'evidenceDigest':evidence}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True);p.add_argument('--version',required=True)
    p.add_argument('--corrected',type=Path)
    print(publish(**vars(p.parse_args())))
