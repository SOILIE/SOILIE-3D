"""Build and publish an append-only public layout archive, never private logs.

Only completed measured scenes enter the archive. Content-addressed artifacts
are immutable; the dated manifest advances only after all uploads succeed.
The published data catalog is merged conditionally, preserving unrelated keys.
No model execution, bucket policy changes, deletes or lifecycle changes occur.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, UTC
import gzip
import hashlib
import json
from pathlib import Path
import re

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_KEYS = {'sessionToken','sessionId','invitation','participantLabel','visitorId','accountId',
                'awsAccountId','functionArn','outputDirectory','errorTrace','errorTail','command'}


def public_only(value):
    if isinstance(value,dict):
        if PRIVATE_KEYS & value.keys():
            raise ValueError('Private execution or respondent metadata cannot be published')
        for item in value.values():
            public_only(item)
    elif isinstance(value,list):
        for item in value:
            public_only(item)
    elif isinstance(value,str) and re.search(r'arn:aws:|(?<![A-Za-z])[A-Za-z]:[\\/]|/mnt/[a-z]/|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',value):
        raise ValueError('Private path or credential-like value cannot be published')


def packed(value):
    public_only(value)
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def prefix(day):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('Archive date must be YYYY-MM-DD in UTC')
    return f'files/outputs/benchmark-{day}/'


def build(measurements, comparison, reviews, day, output):
    from serverless.benchmark.stimuli import diagram
    output.mkdir(parents=True,exist_ok=True)
    records, files = [], {}
    def artifact(folder, data, extension, content_type):
        body = packed(data) if extension == 'json' else data.encode('utf-8')
        sha = hashlib.sha256(body).hexdigest()
        relative = f'{folder}/{sha[:24]}.{extension}'
        path = output/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.read_bytes() != body:
            raise ValueError('Content-addressed archive collision')
        path.write_bytes(body)
        files[relative] = {'sha256':sha,'bytes':len(body),'contentType':content_type}
        return relative
    seen = set()
    for row in measurements['rows']:
        scene = row['scene']
        identity = (scene['model'],scene['id'])
        if identity in seen or not all(re.fullmatch(r'[a-z0-9_-]+',scene[key]) for key in ('id','model')):
            raise ValueError('Duplicate or unsafe scene identifier')
        seen.add(identity)
        public_only(row)
        folder = f"scenes/{scene['model']}/{scene['id']}"
        record = {'id':scene['id'],'model':scene['model'],'roomType':scene['roomType'],'cohort':scene.get('cohort','comparison'),
                  'objects':[obj['label'] for obj in scene['objects']],
                  'geometry':artifact(folder,row,'json','application/json'),
                  'diagram':artifact(folder,diagram(scene),'svg','image/svg+xml')}
        records.append(record)
    summary = artifact('evidence',comparison,'json','application/json')
    reports = [artifact('ai-reviews',review,'json','application/json') for review in reviews]
    from serverless.study.combine_pilots import combine
    from serverless.benchmark.interpretation import discussion
    combined = combine(reviews) if reviews else None
    combined_path = artifact('ai-reviews',combined,'json','application/json') if combined else None
    narrative = artifact('evidence',discussion(comparison,combined),'md','text/markdown; charset=utf-8')
    # Preserve the original response exports and resolve their immutable image
    # URLs into this archive without rewriting historical stimulus versions.
    stimuli = {}
    diagrams = {Path(path).stem:path for path in files if path.endswith('.svg')}
    for report in reviews:
        for case in report['stimuli']:
            for key in ('soilieImage','baselineImage'):
                source = case[key]
                if Path(source).stem not in diagrams:
                    raise ValueError('Reviewed stimulus is missing from the scene archive')
                stimuli[source] = diagrams[Path(source).stem]
    document = {'schemaVersion':1,'archiveDateUtc':day,'generatedAt':datetime.now(UTC).isoformat(),
                'prefix':prefix(day),'counts':dict(Counter(row['model'] for row in records)),
                'scenes':records,'comparison':summary,'aiReviews':reports,'combinedAiPilot':combined_path,
                'interpretation':narrative,'reviewStimulusPaths':stimuli,'files':files,
                'description':'Final layout observations and standardized plan/oblique bounding-box diagrams, not photorealistic renders or imagination sequences.',
                'sampling':'All completed measured scenes in this checkpoint, not a quality-selected gallery. Failed attempts remain in the comparison accounting.',
                'retention':'Research archive outside generated/; no seven-day expiry rule applies.',
                'aiScope':'AI-only exploratory evaluation. No human validation claim. Each wave retains its frozen stimulus version.',
                'sources':{'soilie':'https://github.com/soilie/SOILIE-3D',
                           'layoutgpt':'https://github.com/weixi-feng/LayoutGPT',
                           'infinigen':'https://github.com/princeton-vl/infinigen/tree/indoors-initial'}}
    (output/'manifest.json').write_bytes(packed(document))
    return document


def decode_index(response):
    body = response['Body'].read()
    return json.loads(gzip.decompress(body) if response.get('ContentEncoding') == 'gzip' else body)


def merge_index(client,bucket,keys):
    for _ in range(5):
        previous = client.get_object(Bucket=bucket,Key='catalog/files-index.json')
        document = decode_index(previous)
        if document.get('schemaVersion') != 1 or not isinstance(document.get('keys'),list):
            raise ValueError('Unexpected published index schema')
        document['keys'] = sorted(set(document['keys'])|set(keys))
        document['updatedAt'] = datetime.now(UTC).isoformat()
        body = packed(document)
        options = {}
        if previous.get('ContentEncoding') == 'gzip':
            body = gzip.compress(body,mtime=0)
            options['ContentEncoding'] = 'gzip'
        try:
            client.put_object(Bucket=bucket,Key='catalog/files-index.json',Body=body,
                              ContentType='application/json',CacheControl='no-cache',
                              ServerSideEncryption='AES256',IfMatch=previous['ETag'],**options)
            return len(document['keys'])
        except ClientError as error:
            if error.response['Error']['Code'] not in ('PreconditionFailed','ConditionalRequestConflict'):
                raise
    raise RuntimeError('Published index kept changing; uploaded artifacts remain safe, but index update was not applied')


def publish(client,bucket,output):
    document = json.loads((output/'manifest.json').read_bytes())
    expected_prefix = prefix(document['archiveDateUtc'])
    if document['prefix'] != expected_prefix:
        raise ValueError('Archive prefix is not the expected dated outputs folder')
    public_only(document)
    manifest_key = expected_prefix+'manifest.json'
    published_files = {}
    try:
        previous = client.get_object(Bucket=bucket,Key=manifest_key)
        prior = json.loads(previous['Body'].read())
        if not {row['id'] for row in prior['scenes']} <= {row['id'] for row in document['scenes']}:
            raise ValueError('An older checkpoint cannot replace a larger public archive')
        manifest_condition = {'IfMatch':previous['ETag']}
        published_files = prior.get('files',{})
    except ClientError as error:
        if error.response['Error']['Code'] not in ('NoSuchKey','404'):
            raise
        manifest_condition = {'IfNoneMatch':'*'}
    def immutable(key,body,content_type,sha):
        try:
            client.put_object(Bucket=bucket,Key=key,Body=body,ContentType=content_type,
                              CacheControl='public,max-age=31536000,immutable',ServerSideEncryption='AES256',
                              Metadata={'sha256':sha},IfNoneMatch='*')
        except ClientError as error:
            if error.response['Error']['Code'] != 'PreconditionFailed':
                raise
            remote = client.head_object(Bucket=bucket,Key=key)
            if remote.get('Metadata',{}).get('sha256') != sha:
                raise ValueError('Existing public artifact does not match the requested content')
    def upload(item):
        relative, metadata = item
        path = (output/relative).resolve()
        if not path.is_relative_to(output.resolve()) or relative.startswith('/'):
            raise ValueError('Artifact escaped the local archive')
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != metadata['sha256']:
            raise ValueError('Local artifact no longer matches its manifest')
        key = expected_prefix+relative
        # The previous manifest was published only after its immutable files.
        # Reuse those receipts instead of charging a PUT and HEAD for every
        # unchanged layout each time the growing campaign is published.
        if published_files.get(relative) != metadata:
            immutable(key,body,metadata['contentType'],metadata['sha256'])
        return key
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(upload,document['files'].items()))
    # Publish the pointer last, preserving the previous manifest as immutable
    # evidence. Readers therefore never see a manifest with missing artifacts.
    body = packed(document)
    receipt_key = expected_prefix+'snapshots/'+hashlib.sha256(body).hexdigest()[:24]+'.json'
    immutable(receipt_key,body,'application/json',hashlib.sha256(body).hexdigest())
    client.put_object(Bucket=bucket,Key=manifest_key,Body=body,ContentType='application/json',
                      CacheControl='public,max-age=60',ServerSideEncryption='AES256',**manifest_condition)
    keys.extend([receipt_key,manifest_key])
    count = merge_index(client,bucket,keys)
    return {'prefix':expected_prefix,'artifactCount':len(keys),'publishedIndexKeys':count,'counts':document['counts']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--measurements',type=Path)
    parser.add_argument('--comparison',type=Path)
    parser.add_argument('--extra-measurements',type=Path,nargs='*',default=[])
    parser.add_argument('--reviews',type=Path,nargs='*',default=[])
    parser.add_argument('--date',default=datetime.now(UTC).date().isoformat())
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--upload-only',action='store_true')
    parser.add_argument('--profile',default='darkest')
    parser.add_argument('--bucket',default='soilie3d-data')
    args = parser.parse_args()
    args.output = args.output.resolve()
    if not args.output.is_relative_to(ROOT/'.codex'):
        raise ValueError('Archive staging must stay in project .codex')
    if not args.upload_only:
        if not args.measurements or not args.comparison:
            parser.error('--measurements and --comparison are required for a build')
        measurements = json.loads(args.measurements.read_bytes())
        for path in args.extra_measurements:
            extra = json.loads(path.read_bytes())
            if extra.get('cohort') != 'diversity' or any(row['scene'].get('cohort') != 'diversity' for row in extra['rows']):
                raise ValueError('Additional archive inputs must be labelled as a separate diversity cohort')
            measurements['rows'].extend(extra['rows'])
        document = build(measurements,json.loads(args.comparison.read_bytes()),
                         [json.loads(path.read_bytes()) for path in args.reviews],args.date,args.output)
        print(json.dumps({'builtScenes':len(document['scenes']),'files':len(document['files'])}),flush=True)
    if args.publish:
        import boto3
        client = boto3.Session(profile_name=args.profile).client('s3',region_name='ca-central-1')
        print(json.dumps(publish(client,args.bucket,args.output)),flush=True)


if __name__ == '__main__':
    main()
