"""Publish an audited result bundle beside the streamed research outputs.

Only the explicit public allowlist is uploaded. Private reviewer sessions,
working packets and invocation records never enter this archive. Publication
uses immutable hashes, verifies S3 receipts, then updates the shared Data index.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re

from serverless.benchmark.archive import merge_index, packed, prefix
from serverless.benchmark.stream_archive import upload
from serverless.cloud_benchmark.publication_views import verified_reviews

PUBLIC_FILES = ('comparison.json', 'status.json', 'room-measurements.json',
                'support-measurements.json', 'publication-inputs.json', 'review-manifest.json',
                'ai-pilot-summary.json', 'ai-pilot-responses.json',
                'ai-pilot-infinigen-summary.json', 'ai-pilot-infinigen-responses.json')


def release_files(directory):
    """Return only a complete, internally consistent, public-safe release."""
    comparison = json.loads((directory / 'comparison.json').read_bytes())
    if comparison.get('schemaVersion') != 4 or comparison['aiReview'].get('ready') is not True:
        raise ValueError('A completed comparison release is required')
    digest = comparison.pop('evidenceDigest')
    if hashlib.sha256(packed(comparison)).hexdigest() != digest:
        raise ValueError('Comparison evidence changed after compilation')
    cohort = comparison['aiReview']['cohortSha256']
    verified_reviews(directory, cohort)
    if hashlib.sha256((directory / 'review-manifest.json').read_bytes()).hexdigest() != comparison['aiReview']['manifestSha256']:
        raise ValueError('Comparison names a different review manifest')
    records = json.loads((directory / 'room-measurements.json').read_bytes())
    identities = [row['sceneId'] for row in records['rows']]
    if len(identities) != len(set(identities)) or records['cohortSha256'] != cohort:
        raise ValueError('Duplicate or mismatched measurement identities')
    counts = Counter(row['model'] for row in records['rows'])
    if dict(counts) != {key: model['n'] for key, model in comparison['models'].items()}:
        raise ValueError('Download counts differ from the charts')
    soilie_rooms = Counter(row['roomType'] for row in records['rows'] if row['model'] == 'soilie')
    if counts.get('soilie') != 10000 or soilie_rooms != {'bedroom': 5000, 'living_room': 5000}:
        raise ValueError('The final SOILIE cohort must contain 5,000 rooms of each type')
    status = json.loads((directory / 'status.json').read_bytes())
    if status['analysis']['state'] != 'complete' or status['corpus']['completedLayouts'] != 10000:
        raise ValueError('Release status is incomplete')
    files = {}
    for name in PUBLIC_FILES:
        body = (directory / name).read_bytes()
        # Validate recursively without reserializing, preserving signed hashes.
        packed(json.loads(body))
        files[name] = ('application/json', body)
    if comparison.get('layoutgptPhysicalScale'):
        metadata = comparison['layoutgptPhysicalScale']
        body = (directory / 'layoutgpt-scale.json').read_bytes()
        if metadata['file'] != 'layoutgpt-scale.json' or hashlib.sha256(body).hexdigest() != metadata['sha256']:
            raise ValueError('LayoutGPT physical scale evidence differs')
        packed(json.loads(body))
        files['layoutgpt-scale.json'] = ('application/json', body)
    if comparison.get('cost', {}).get('measurements'):
        metadata = comparison['cost']['measurements']
        body = (directory / 'cost-measurements.json').read_bytes()
        if metadata['file'] != 'cost-measurements.json' or hashlib.sha256(body).hexdigest() != metadata['sha256']:
            raise ValueError('Per-room cost evidence differs')
        document = json.loads(body)
        packed(document)
        if len(document['rows']) != metadata['rows']:
            raise ValueError('Per-room cost evidence count differs')
        files['cost-measurements.json'] = ('application/json', body)
    images = set()
    for example in comparison.get('illustrations', []):
        url = example['image']
        if not re.fullmatch(r'benchmarks/illustrations/[a-f0-9]{24}\.svg', url):
            raise ValueError('Unexpected illustration path')
        images.add('illustrations/' + Path(url).name)
    for name in ('ai-pilot-responses.json', 'ai-pilot-infinigen-responses.json'):
        for pair in json.loads(files[name][1])['stimuli']:
            urls = [variant[field] for variant in [pair, *pair.get('profileImages', {}).values()]
                    for field in ('soilieImage', 'baselineImage')]
            for url in urls:
                if not re.fullmatch(r'/benchmarks/stimuli/[a-f0-9]{24}\.svg', url):
                    raise ValueError('Unexpected stimulus path')
                images.add('stimuli/' + Path(url).name)
    for name in images:
        body = (directory / name).read_bytes()
        if hashlib.sha256(body).hexdigest()[:24] != Path(name).stem:
            raise ValueError('Stimulus changed after review')
        files[name] = ('image/svg+xml', body)
    return files, cohort, dict(counts)


def publish(client, bucket, directory, day, version, revision=None):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Use the website semantic version')
    files, cohort, counts = release_files(directory)
    if revision is not None and not re.fullmatch(r'[a-f0-9]{12}', revision):
        raise ValueError('Archive revision must be a 12-character content hash')
    target = prefix(day) + 'analysis-v' + version + ('-' + revision if revision else '') + '/'
    def send(item):
        name, (mime, body) = item
        checksum = hashlib.sha256(body).hexdigest()
        receipt = upload(client, bucket, target + name, body, mime, checksum, len(body))
        return name, {key: receipt[key] for key in ('sha256', 'bytes')}
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = dict(pool.map(send, sorted(files.items())))
    manifest = packed({'schemaVersion': 1, 'websiteVersion': version, 'cohortSha256': cohort,
                       'modelCounts': counts, 'files': receipts})
    key = target + 'manifest.json'
    upload(client, bucket, key, manifest, 'application/json', hashlib.sha256(manifest).hexdigest(), len(manifest))
    indexed = merge_index(client, bucket, [target + name for name in files] + [key])
    return {'prefix': target, 'files': len(files) + 1, 'indexedKeys': indexed, 'modelCounts': counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--revision', help='Content-hash suffix for a no-version-bump amendment')
    parser.add_argument('--profile', default='darkest')
    parser.add_argument('--bucket', default='soilie3d-data')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    files, cohort, counts = release_files(args.directory)
    if args.publish:
        import boto3
        client = boto3.Session(profile_name=args.profile).client('s3', region_name='ca-central-1')
        result = publish(client, args.bucket, args.directory, args.date, args.version, args.revision)
    else:
        result = {'validatedFiles': len(files), 'cohortSha256': cohort, 'modelCounts': counts}
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
