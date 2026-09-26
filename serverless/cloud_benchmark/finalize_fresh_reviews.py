"""Publish only complete, preflighted fresh reviews, with immutable source proof."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.review_reports import source_report, combine_focused, FILES
from serverless.cloud_benchmark.repeat_consistency import audit_repeat_assignments, repeat_consistency
from serverless.study.export_pilot import public_summary
from serverless.study.store import SQLiteStudyStore


def finalize(root, evidence, output):
    preflight = json.loads((root / 'preflight.json').read_bytes())
    if not preflight['passed'] or preflight['geometryChanged']:
        raise ValueError('Successful geometry-preserving preflight is required')
    cohort = json.loads((evidence / 'cohort.json').read_bytes())
    if not cohort['complete'] or cohort['soilieScenes'] != 10000:
        raise ValueError('Final 10,000-scene cohort required')
    raw = (evidence / 'measured-scenes.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != cohort['measurementsSha256']:
        raise ValueError('Cohort evidence changed')
    scenes = {scene['id']: scene for scene in json.loads((root / 'source-scenes.json').read_bytes())['scenes']}
    # Validate every baseline fully before writing any release files.
    reports = {}
    for name, baseline in (('set-a', 'layoutgpt'), ('set-b', 'infinigen_controlled')):
        report = combine_focused([source_report(root, name, scenes)], cohort['measurementsSha256'])
        for session in SQLiteStudyStore(root / name / 'private/pilot.sqlite3').sessions():
            packet = json.loads((root / 'packets' / session['reviewerId'] / (name + '.json')).read_bytes())
            audit_repeat_assignments(session['assignments'], packet['cases'])
        report['delivery'] = {'medium': 'Immutable paired PNGs and a single assigned prompt text file',
                              'additionalInterfaceReminderShown': False,
                              'executionInstructionSummary': 'Inspect every assigned PNG independently in frozen order; do not inspect method identities, prior results or other reviewers. Record the required preference, visible-problem choice, confidence and brief rationale in JSON, saving checkpoints. No aggregate outcome is targeted.',
                              'numericEvidence': 'Relative box volumes for proportions only; no computed quality scores'}
        # File-based reviewers receive the frozen combined prompt, not the
        # separate reminder used by the optional interactive study interface.
        # Omit that unused UI copy from the public record of their inputs.
        for reviewer in report['reviewers']:
            reviewer.pop('interfaceEmphasis', None)
        reports[baseline] = report
    consistency = repeat_consistency(reports)
    # Keep the complete diagnostic even when release is blocked. Never delete
    # disagreements, selectively rerun them, or quietly reuse older votes.
    write_json(root / 'repeat-consistency.json', consistency)
    if not consistency['passed']:
        raise ValueError(f"AI release requires 90% repeat agreement; observed "
                         f"{consistency['agreements']}/{consistency['comparisons']}. "
                         'Investigate repeat-consistency.json without altering saved judgements.')
    for report in reports.values():
        report['repeatConsistency'] = {key: value for key, value in consistency.items() if key != 'controls'}
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for baseline, report in reports.items():
        stem = FILES[baseline]
        write_json(output / (stem + '-responses.json'), report)
        write_json(output / (stem + '-summary.json'), public_summary(report, stem + '-responses.json'))
        images = output / 'stimuli'
        images.mkdir(exist_ok=True)
        for item in report['stimuli']:
            for variant in [item, *item.get('profileImages', {}).values()]:
                for field in ('soilieImage', 'baselineImage'):
                    source = root / 'site' / variant[field].lstrip('/')
                    if hashlib.sha256(source.read_bytes()).hexdigest()[:24] != source.stem:
                        raise ValueError('Immutable stimulus changed')
                    if not (images / source.name).exists():
                        shutil.copyfile(source, images / source.name)
        artifacts[baseline] = {'pairs': report['roomTypePairs'], 'releaseEligible': report['releaseEligible'],
            'files': {stem + suffix: hashlib.sha256((output / (stem + suffix)).read_bytes()).hexdigest()
                      for suffix in ('-responses.json', '-summary.json')}}
    write_json(output / 'review-manifest.json', {'schemaVersion': 1, 'cohortSha256': cohort['measurementsSha256'],
        'comparisons': artifacts, 'releaseEligible': True,
        'repeatConsistency': {key: value for key, value in consistency.items() if key != 'controls'},
        'preflightSha256': hashlib.sha256((root / 'preflight.json').read_bytes()).hexdigest()})
    print(json.dumps({'releaseEligible': True, 'pairs': 480,
                      'responsesIncludingRepeats': sum(len(r['responses']) for r in reports.values())}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'evidence', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    finalize(args.root, args.evidence, args.output)
