"""Read-only acceptance checks for a saved-scene support correction cohort.

This does not generate scenes or publish results. It can inspect an in-progress
directory, or gate final publication with --require-complete. Original attempts
and timing remain authoritative; changed scene IDs identify stimuli to rerender.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from modules.support_settlement import CONTACT_TOLERANCE_M, FIXED_CLASSES


def compatible_replay(first: dict, second: dict) -> bool:
    """Restoration-only maintenance never permits a changed model or evaluator."""
    return ({key: value for key, value in first.items() if key != 'replaySha256'} ==
            {key: value for key, value in second.items() if key != 'replaySha256'})


def audit_record(source: dict, derived: dict, source_hash: str) -> dict:
    report = derived['supportCorrection']
    if report['sourceSha256'] != source_hash:
        raise ValueError('Source checksum mismatch')
    if report['implementation'].get('observeOnly'):
        raise ValueError('Observation-only record is not a corrected scene')
    if source['status'] != 'complete' or derived['status'] != 'complete':
        raise ValueError('Both original and corrected scenes must be complete')
    for key in ('id', 'attempt', 'request', 'generationSeconds'):
        if source.get(key) != derived.get(key):
            raise ValueError('Original identity, request or timing changed: ' + key)
    if report['originalGenerationSeconds'] != source['generationSeconds']:
        raise ValueError('Original generation time was not preserved')
    seconds = report['correctionSeconds']
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Invalid correction duration')
    for stage in source['stages']:
        if stage != 'final' and source['stages'][stage] != derived['stages'].get(stage):
            raise ValueError('A pre-correction placement stage changed')
    original, final = source['stages']['final'], derived['stages']['final']
    if original['room'] != final['room']:
        raise ValueError('Room boundary changed during vertical correction')
    if [row['id'] for row in original['objects']] != [row['id'] for row in final['objects']]:
        raise ValueError('Object inventory or ordering changed')
    moved = {row['id'] for row in report['moves']}
    if len(moved) != len(report['moves']) or moved - {row['id'] for row in final['objects']}:
        raise ValueError('Unknown or repeated movement record')
    contacts = []
    for old, new in zip(original['objects'], final['objects']):
        for key in ('label', 'asset', 'kind', 'frontDirection'):
            if old.get(key) != new.get(key):
                raise ValueError('Object identity or orientation changed: ' + old['id'])
        if len(old['corners']) != 8 or len(new['corners']) != 8:
            raise ValueError('Invalid bounds')
        shift = new['transform'][2][3] - old['transform'][2][3]
        if old['id'] not in moved and abs(shift) > CONTACT_TOLERANCE_M:
            raise ValueError('Unreported vertical movement: ' + old['id'])
        if old['label'] in FIXED_CLASSES and old['id'] in moved:
            raise ValueError('Mounted or architectural object moved: ' + old['id'])
        for i in range(4):
            for j in range(4):
                delta = new['transform'][i][j] - old['transform'][i][j]
                if not math.isfinite(delta) or ((i, j) != (2, 3) and abs(delta) > CONTACT_TOLERANCE_M):
                    raise ValueError('Nonvertical transform changed: ' + old['id'])
        # Matrix and enclosing corners must describe the same Z-only change.
        for first, second in zip(old['corners'], new['corners']):
            for axis in range(3):
                error = second[axis] - first[axis] - (shift if axis == 2 else 0)
                if not math.isfinite(error) or abs(error) > CONTACT_TOLERANCE_M:
                    raise ValueError('Bounds disagree with vertical displacement: ' + old['id'])
        if old['label'] not in FIXED_CLASSES:
            support = new.get('support', {})
            if support.get('source') not in ('mesh-vertical-contact', 'mesh-extremum-floor-contact') or support.get('samplingVersion') != 3:
                raise ValueError('Missing corrected mesh-support evidence: ' + old['id'])
            if support.get('supportKind') not in ('floor', 'object', 'architecture') or not support.get('supportId'):
                raise ValueError('Missing support identity: ' + old['id'])
            for key in ('gapM', 'belowFloorM'):
                value = support.get(key)
                if value is None or not math.isfinite(value) or value < 0 or value > CONTACT_TOLERANCE_M:
                    raise ValueError('Unresolved support distance: ' + old['id'] + '/' + key)
            contacts.append(support)
    solid = final.get('solidMeshOverlap', {})
    overlap = solid.get('maxOverlapPct')
    if not solid.get('complete') or overlap is None or not math.isfinite(overlap) or not 0 <= overlap <= .0001:
        raise ValueError('Incomplete or overlapping final meshes')
    return {'changedObjects': len(moved), 'contacts': contacts, 'correctionSeconds': seconds}


def audit_cohort(source: Path, derived: Path, expected: int = 10000) -> dict:
    if expected <= 0 or not source.is_dir() or not derived.is_dir():
        raise ValueError('Expected a positive scene count and two existing cohort directories')
    originals = {path.name: path for path in source.glob('attempt-*.json')}
    outputs = {path.name: path for path in derived.glob('attempt-*.json')}
    errors, changed, implementations = [], [], {}
    correction_implementations = set()
    contact_counts = {'floor': 0, 'object': 0, 'architecture': 0}
    validated = changed_objects = 0
    maximum_gap = maximum_penetration = correction_seconds = 0.0
    for name, path in sorted(outputs.items()):
        try:
            if name not in originals:
                raise ValueError('No matching original scene')
            raw = originals[name].read_bytes()
            original, output = json.loads(raw), json.loads(path.read_bytes())
            result = audit_record(original, output, hashlib.sha256(raw).hexdigest())
            for contact in result['contacts']:
                contact_counts[contact['supportKind']] += 1
                maximum_gap = max(maximum_gap, contact['gapM'])
                maximum_penetration = max(maximum_penetration, contact['belowFloorM'])
            if result['changedObjects']:
                changed.append(output['id'])
            changed_objects += result['changedObjects']
            correction_seconds += result['correctionSeconds']
            digest = hashlib.sha256(json.dumps(output['supportCorrection']['implementation'], sort_keys=True).encode()).hexdigest()
            implementations[digest] = implementations.get(digest, 0) + 1
            correction_implementations.add(json.dumps({key: value for key, value in output['supportCorrection']['implementation'].items()
                                                      if key != 'replaySha256'}, sort_keys=True))
            validated += 1
        except (ValueError, KeyError, TypeError, IndexError) as error:
            errors.append({'attempt': name, 'reason': str(error)})
    return {'schemaVersion': 1, 'expectedScenes': expected, 'originalScenes': len(originals),
            'derivedScenes': len(outputs), 'validatedScenes': validated,
            'complete': validated == expected == len(originals) == len(outputs) and not errors and len(correction_implementations) == 1,
            'missingAttempts': sorted(originals.keys() - outputs.keys()), 'errors': errors,
            'changedScenes': len(changed), 'changedObjects': changed_objects, 'changedSceneIds': changed,
            'contactCounts': contact_counts, 'maxSupportGapM': maximum_gap,
            'maxBelowFloorM': maximum_penetration, 'correctionSeconds': correction_seconds,
            'implementationCohorts': implementations,
            'timingNote': 'Correction compute only; not total generation time, restoration or measurement overhead.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--derived', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected', type=int, default=10000)
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    if args.output.resolve().parent in (args.source.resolve(), args.derived.resolve()) and (
        args.output.name.startswith('attempt-') or args.output.name == 'run.json'
    ):
        raise ValueError('An audit report must not overwrite scene evidence')
    report = audit_cohort(args.source, args.derived, args.expected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('complete', 'validatedScenes', 'changedScenes', 'changedObjects', 'maxSupportGapM', 'errors')}))
    if report['errors'] or (args.require_complete and not report['complete']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
