"""Attach separately sampled support only after exact placement parity checks."""
from copy import deepcopy
import argparse
import json
from pathlib import Path

from serverless.benchmark.verify_parity import compare, digest


def merge_support(attempts, directories, provenance_hashes):
    indexed = {row['id']:row for row in attempts}
    updated = deepcopy(indexed)
    checks, matched = [], set()
    attached_samples, superseded_samples = 0, 0
    for directory in directories:
        config = json.loads((directory/'run.json').read_text())
        if not config['support'] or config['roomFitIncluded']:
            raise ValueError('Expected support-only measurement replays without website fitting')
        if digest(config['provenance']) not in provenance_hashes:
            raise ValueError('Support replay runtime differs from the benchmark')
        for path in sorted(directory.glob('attempt-*.json')):
            replay = json.loads(path.read_text())
            identifier = replay['id']
            if identifier not in indexed or identifier in matched:
                raise ValueError('Support replay must refer to one unique benchmark attempt')
            check = compare(indexed[identifier],replay)
            matched.add(identifier)
            checks.append(dict(check,id=identifier))
            if replay['status'] == 'complete':
                sampled = [obj for obj in replay['stages']['final']['objects'] if 'support' in obj]
                supports = {obj['id']:obj['support'] for obj in sampled if obj['support'].get('samplingVersion') == 2}
                attached_samples += len(supports)
                superseded_samples += len(sampled)-len(supports)
                for obj in updated[identifier]['stages']['final']['objects']:
                    if obj['id'] in supports:
                        obj['support'] = supports[obj['id']]
    return list(updated.values()), {'attemptsChecked':len(checks), 'samplingVersion':2,
        'currentSampleObjects':attached_samples, 'supersededSampleObjectsExcluded':superseded_samples,
        'method':'Real bottom mesh vertices plus lower-surface rays, checked against actual supporting meshes. Exact equality of all placement stages is required. Replays do not add scenes or enter generation timing. Superseded grid-only probes are excluded.',
        'checks':checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--replays', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(path.read_text()) for path in sorted(args.baseline.glob('attempt-*.json'))]
    config = json.loads((args.baseline/'run.json').read_text())
    _, result = merge_support(rows,[args.replays],{digest(config['provenance'])})
    if not result['attemptsChecked']:
        raise ValueError('No support replays were checked')
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'attemptsChecked':result['attemptsChecked']}),flush=True)


if __name__ == '__main__':
    main()
