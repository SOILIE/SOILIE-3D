"""Activate verified presentation-only review inputs; never reuse earlier votes.

Activation requires an explicit CLI flag and produces new private sessions.
The prepared input directory remains immutable and contains no credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shutil

from serverless.benchmark.stimuli import digest
from serverless.benchmark.review_annotations import PRESENTATION_VERSION
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.reviews import PLAN
from serverless.study.service import StudyService, prompt_text
from serverless.study.store import SQLiteStudyStore


def activate(prepared, output, recorded_benchmarks):
    manifest = json.loads((prepared / 'manifest.json').read_bytes())
    if manifest['status'] != 'prepared_not_started' or manifest['geometryChanged']:
        raise ValueError('Require verified, inactive presentation-only inputs')
    if output.exists():
        raise ValueError('Fresh reviews require a new directory')
    image_dir = prepared / 'site/benchmarks/stimuli'
    for image in image_dir.glob('*.svg'):
        body = image.read_bytes()
        if hashlib.sha256(body).hexdigest()[:24] != image.stem:
            raise ValueError('Image content differs from its immutable filename')
        if any(cue in body.decode().lower() for cue in ('judge ', 'single bed', 'double bed', 'simple desk', 'cell shelf')):
            raise ValueError('Unremoved instruction or naming cue')
    output.mkdir(parents=True)
    shutil.copytree(prepared / 'site', output / 'site')
    shutil.copyfile(prepared / 'source-scenes.json', output / 'source-scenes.json')
    protocols = []
    for name, report_name in (('set-a', 'ai-pilot-responses.json'), ('set-b', 'ai-pilot-infinigen-responses.json')):
        raw = (prepared / name / 'protocol.json').read_bytes()
        document = json.loads(raw)
        if not document['presentationPolicy'].startswith(PRESENTATION_VERSION):
            raise ValueError('Wrong annotation policy')
        report_raw = (recorded_benchmarks / report_name).read_bytes()
        if hashlib.sha256(report_raw).hexdigest() != document['sourceReportSha256']:
            raise ValueError('Recorded pair selection provenance differs')
        document['sampling']['sourceSampling'] = [row['sampling'] for row in json.loads(report_raw)['sourceStudies']]
        document['sampling']['qualityScoresUsed'] = False
        rooms = {row['caseId']: row['matchingStratum'][0] for row in document['stimulusEvidence']}
        for case in document['cases']:
            case['balanceStratum'] = rooms[case['id']]
        document['pilotCollectionEnabled'] = True
        document['preparedProtocolSha256'] = hashlib.sha256(raw).hexdigest()
        document['studyVersion'] = 'functional-front-review-' + digest(document)[:20]
        folder = output / name
        private = folder / 'private'
        private.mkdir(parents=True)
        write_json(folder / 'protocol.json', document)
        secret = secrets.token_bytes(32)
        (private / 'session-secret').write_bytes(secret)
        service = StudyService(document, SQLiteStudyStore(private / 'pilot.sqlite3'), secret, enabled=True)
        for index, profile in enumerate(PLAN, 1):
            reviewer = f'reviewer-{index:02}'
            session = service.start({'invitation': service.invite(reviewer, profile, 'GPT-5.6 Sol (Extra High reasoning effort)')})
            write_json(private / (reviewer + '.json'), session)
            public = {key: value for key, value in session.items() if key not in ('sessionToken', 'sessionId')}
            public['prompt'] = prompt_text(document, profile)
            packet = output / 'packets' / reviewer
            packet.mkdir(parents=True, exist_ok=True)
            write_json(packet / (name + '.json'), public)
        protocols.append({'set': name, 'studyVersion': document['studyVersion'], 'pairs': len(document['cases'])})
    write_json(output / 'manifest.json', {'status': 'sessions_prepared', 'reviewerPlan': PLAN,
        'protocols': protocols, 'priorVotesReused': False, 'geometryChanged': False,
        'presentationPolicy': PRESENTATION_VERSION})
    print(json.dumps({'sessions': 20, 'reviewers': 10, 'pairs': 480, 'priorVotesReused': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--recorded-benchmarks', type=Path)
    parser.add_argument('--authorize-review', action='store_true')
    parser.add_argument('--status', type=Path)
    args = parser.parse_args()
    if args.status:
        from serverless.cloud_benchmark.review_work import status
        status(args.status)
    elif all((args.prepared, args.output, args.recorded_benchmarks, args.authorize_review)):
        activate(args.prepared, args.output, args.recorded_benchmarks)
    else:
        parser.error('Activation requires --prepared, --output, --recorded-benchmarks and --authorize-review')
