"""Audit actual frozen review inputs and prepare inactive, corrected stimuli.

No reviewers, sessions, API calls, sampling, or votes are created. Prior images
and responses stay untouched; new image hashes identify the corrected inputs.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from serverless.benchmark.stimuli import digest, stimulus_images, SEMANTIC_FAMILIES
from serverless.benchmark.review_annotations import ALIASES, FRONT_MEANINGS, PRESENTATION_VERSION, functional_front
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.reviews import PLAN

REPORTS = ('ai-pilot-responses.json', 'ai-pilot-infinigen-responses.json')
OLD_INSTRUCTION = 'Judge the relative size differences among the objects present in each room.'


def audit(benchmarks):
    records, images = [], set()
    for filename in REPORTS:
        raw = (benchmarks / filename).read_bytes()
        report = json.loads(raw)
        footer = labels = 0
        for case in report['stimuli']:
            for side in ('soilieImage', 'baselineImage'):
                path = benchmarks / 'stimuli' / Path(case[side]).name
                body = path.read_bytes()
                sha = hashlib.sha256(body).hexdigest()
                if path.stem != sha[:24]:
                    raise ValueError('Recorded image content differs from its immutable reference')
                images.add(sha)
                text = body.decode()
                footer += OLD_INSTRUCTION in text
                labels += 'single bed' in text or 'double bed' in text
        records.append({'responsesFile': filename, 'responsesSha256': hashlib.sha256(raw).hexdigest(),
            'pairs': len(report['stimuli']), 'imageReferences': 2 * len(report['stimuli']),
            'sizeInstructionReferences': footer, 'bedSubtypeLabelReferences': labels,
            'responsesIncludingRepeats': len(report['responses']),
            'mainResponsesByDimension': dict(Counter(row['promptProfile'] for row in report['responses'] if not row['repeatOf'])),
            'studyVersion': report['studyVersion'],
            'sourceStimulusVersionDigest': report['stimulusVersionDigest']})
    return {'schemaVersion': 1, 'status': 'rerun_required', 'uniqueImages': len(images), 'reports': records,
        'findings': ['A size-specific directive was embedded in images shared by every review dimension.',
                     'Display labels and colours used source-specific names even though pair matching used shared families.'],
        'interpretation': 'These are input confounds, not proof of a particular bias direction or effect size. Existing votes cannot establish results under corrected inputs. All five dimensions require fresh review, including proportions because naming cues affect that task too.',
        'unaffected': ['Scene geometry', 'Deterministic geometry measurements', 'Generation timings', 'Recorded usage costs'],
        'reviewersStarted': 0}


def prepare(benchmarks, scene_paths, output):
    findings = audit(benchmarks)
    if output.exists():
        prior_audit = output / 'input-audit.json'
        if (output / 'manifest.json').exists() or not prior_audit.exists() or json.loads(prior_audit.read_bytes()) != findings:
            raise ValueError('Use a new directory; never overwrite a frozen or unrelated review set')
    reports = [json.loads((benchmarks / name).read_bytes()) for name in REPORTS]
    required = {(row[side + 'Scene'], row[side + 'Digest'])
                for report in reports for row in report['stimulusEvidence'] for side in ('soilie', 'baseline')}
    scenes = {}
    for path in scene_paths:
        document = json.loads(path.read_bytes())
        for row in document.get('rows', document.get('scenes', [])):
            scene = row.get('scene', row)
            key = (scene['id'], digest(scene))
            if key in required:
                scenes[key] = scene
    missing = required - scenes.keys()
    if missing:
        raise ValueError(f'Missing {len(missing)} exact source scenes; first: {sorted(missing)[:3]}')
    output.mkdir(parents=True, exist_ok=True)
    stimuli = output / 'site/benchmarks/stimuli'
    stimuli.mkdir(parents=True, exist_ok=True)
    write_json(output / 'input-audit.json', findings)
    front_counts = Counter()
    for scene in scenes.values():
        if scene.get('fixture'):
            raise ValueError('Synthetic geometry cannot enter a review packet')
        for item in scene['objects']:
            if item.get('kind', 'furniture') == 'furniture':
                front = functional_front(scene, item)
                front_counts[(scene['model'], 'marked' if front else 'unmarked')] += 1
    write_json(output / 'source-scenes.json', {'scenes': list(scenes.values())})
    write_json(output / 'annotation-audit.json', {
        'policy': PRESENTATION_VERSION, 'sourceScenes': len(scenes),
        'counts': [{'model': model, 'status': status, 'objects': count}
                   for (model, status), count in sorted(front_counts.items())],
        'geometryChanged': False, 'sourcesVerifiedAgainstRecordedDigests': True,
        'frontMeanings': FRONT_MEANINGS, 'displayAliases': ALIASES})
    images = {key: stimulus_images(scene, stimuli) for key, scene in sorted(scenes.items())}
    protocols = []
    for report, name in zip(reports, ('set-a', 'set-b')):
        originals = {row['caseId']: row for row in report['stimuli']}
        cases, evidence = [], []
        for row in report['stimulusEvidence']:
            original = originals[row['caseId']]
            first = images[(row['soilieScene'], row['soilieDigest'])]
            second = images[(row['baselineScene'], row['baselineDigest'])]
            cases.append({'id': row['caseId'], 'title': row['matchingStratum'][0].replace('_', ' ').title() + ' arrangement',
                'balanceStratum': row['matchingStratum'][0],
                'relationImage': first['default'], 'comparisonImage': second['default'],
                'profileImages': {'proportions': {'relationImage': first['proportions'], 'comparisonImage': second['proportions']}},
                'comparisonCondition': original['baseline']})
            evidence.append(row)
        protocol = {'schemaVersion': 2, 'evidenceMode': 'visual_only', 'decisionScope': 'focus_only',
            'pilotCollectionEnabled': False, 'humanEnrollmentEnabled': False,
            'presentationPolicy': PRESENTATION_VERSION + '; instructions in prompt only; volume data only for proportions',
            'reviewerPlan': PLAN, 'reviewerConfiguration': report['reviewerConfiguration'],
            'cases': cases, 'stimulusEvidence': evidence,
            'sampling': {'selection': 'Exactly the recorded scene pairs, without reselection or consulting votes.',
                         'sourceSampling': [source['sampling'] for source in report.get('sourceStudies', [])],
                         'qualityScoresUsed': False,
                         'roomTypePairs': dict(Counter(row['matchingStratum'][0] for row in evidence))},
            'sourceReportSha256': findings['reports'][len(protocols)]['responsesSha256'],
            'labelPolicy': {'mapping': ALIASES, 'unmapped': 'Lowercase, underscores/hyphens replaced with spaces.',
                            'duplicates': 'Numbered by position, not asset ID.', 'colours': 'Shared category, never source name.'},
            'frontPolicy': {'version': PRESENTATION_VERSION, 'meanings': FRONT_MEANINGS,
                            'unmarked': 'No asserted functional front; do not score facing.',
                            'method': 'Source-axis conversion only. No object rotation, geometric inference, or preferred-layout correction.'}}
        protocol['studyVersion'] = 'neutral-inputs-' + digest(protocol)[:20]
        (output / name).mkdir(exist_ok=True)
        write_json(output / name / 'protocol.json', protocol)
        protocols.append({'set': name, 'studyVersion': protocol['studyVersion'], 'pairs': len(cases)})
    write_json(output / 'manifest.json', {'schemaVersion': 1, 'status': 'prepared_not_started',
        'reviewersStarted': 0, 'requiresExplicitReviewAuthorization': True, 'protocols': protocols,
        'geometryChanged': False, 'priorVotesReused': False, 'imageFiles': len(list(stimuli.glob('*.svg')))})
    print(json.dumps({'status': 'prepared_not_started', 'pairs': sum(row['pairs'] for row in protocols), 'reviewersStarted': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmarks', type=Path, required=True)
    parser.add_argument('--scene-source', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        prepare(args.benchmarks, args.scene_source, args.output)
    else:
        print(json.dumps(audit(args.benchmarks)))
