"""Quality-blind inventory matching; never edits frozen review campaigns.

Exact means equal multisets of the neutral presentation categories, including
duplicate instances. One substitution replaces one instance, not an arbitrary
semantic family. Equal room type, object count, room anchors and the original
density caliper remain required. Assignment maximizes cardinality, then retains
existing pairs, then prefers fewer substitutions and smaller density gaps.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from serverless.benchmark.geometry import furniture
from serverless.benchmark.review_annotations import presentation_label

POLICY = 'neutral-inventory-one-substitution-v1'


def signature(scene, category_aliases=None):
    aliases = category_aliases or {}
    return Counter(aliases.get(label, label) for label in
                   (presentation_label(item['label']) for item in furniture(scene)))


def substitutions(left, right):
    """None for unequal inventories; otherwise unmatched instances per side."""
    if sum(left.values()) != sum(right.values()):
        return None
    return sum((left - right).values())


def match(left, right, existing=(), maximum_substitutions=1, density_caliper=.25, category_aliases=None):
    if maximum_substitutions not in (0, 1):
        raise ValueError('Only exact or one-substitution inventories are supported')
    if density_caliper is not None and not 0 <= density_caliper <= 1:
        raise ValueError('Density caliper must be between zero and one, or None')
    left = sorted(left, key=lambda row: row['scene']['id'])
    right = sorted(right, key=lambda row: row['scene']['id'])
    old = set(existing)
    if not right or not left:
        return []
    if len({r['scene']['id'] for r in left}) != len(left) or len({r['scene']['id'] for r in right}) != len(right):
        raise ValueError('Scene identities must be unique within each condition')
    # Each objective's unit exceeds every possible sum of lower priorities.
    n = len(right)
    difference_weight = n + 1
    retention_weight = (n + 1) * (difference_weight + 1)
    unmatched = (n + 1) * (retention_weight + difference_weight + 1)
    costs = np.full((n, len(left) + n), unmatched * 2, dtype=float)
    costs[:, len(left):] = unmatched
    signatures = [signature(row['scene'], category_aliases) for row in left]
    candidates = {}
    for i, baseline in enumerate(right):
        b = baseline['scene']
        bs = signature(b, category_aliases)
        for j, candidate in enumerate(left):
            a, sa = candidate['scene'], signatures[j]
            if a['roomType'] != b['roomType']:
                continue
            anchor = {'bedroom': 'bed', 'living_room': 'sofa'}.get(a['roomType'])
            if anchor and sa[anchor] != bs[anchor]:
                continue
            difference = substitutions(sa, bs)
            if difference is None or difference > maximum_substitutions:
                continue
            densities = [candidate['metrics'].get('furnitureDensity'), baseline['metrics'].get('furnitureDensity')]
            if any(value is None or not np.isfinite(value) for value in densities):
                continue
            gap = abs(densities[0] - densities[1])
            if density_caliper is not None and gap > density_caliper:
                continue
            retained = (a['id'], b['id']) in old
            stable = int(hashlib.sha256((POLICY+a['id']+b['id']).encode()).hexdigest()[:8], 16) / 2**32
            # Bound this last objective even in the inventory-only sensitivity.
            costs[i,j] = (not retained)*retention_weight + difference*difference_weight + min(gap, 1) + stable*1e-7
            candidates[i,j] = {'soilieScene': a['id'], 'baselineScene': b['id'],
                               'substitutions': difference, 'retainedPair': retained,
                               'densityDifference': gap, 'soilieInventory': dict(sorted(sa.items())),
                               'baselineInventory': dict(sorted(bs.items()))}
    ii, jj = linear_sum_assignment(costs)
    return [candidates[i,j] for i,j in zip(ii,jj) if (i,j) in candidates]


def analyze(base_path, frozen_scenes_path, protocol_path, output, supplements=(), expansion=None,
            side_table_equivalence=False):
    from serverless.cloud_benchmark.staged_pilot import load_frozen
    protocol = load_frozen(Path(protocol_path).parent)
    base = json.loads(Path(base_path).read_bytes())['rows']
    # Without additions, inspect the frozen 120 baseline rooms in each stratum.
    # With additions, also use every released bedroom and completed controlled
    # output. Do not mix the old unconstrained LayoutGPT living-room condition.
    full_pool = bool(supplements or expansion)
    rows = {row['scene']['id']: row for row in base if row['scene']['model']=='soilie' or
            (full_pool and row['scene']['model'] in ('layoutgpt', 'infinigen_controlled') and not
             (row['scene']['model']=='layoutgpt' and row['scene']['roomType']=='living_room'))}
    input_paths = [base_path, frozen_scenes_path, protocol_path]
    for path in supplements:
        doc = json.loads(Path(path).read_bytes())
        if doc.get('invalidArtifacts') or doc.get('complete') is False:
            raise ValueError('Only completed valid supplements are eligible')
        for scene in doc['scenes']:
            rows[scene['id']] = {'scene': scene}
        input_paths.append(path)
    if expansion:
        from serverless.cloud_benchmark.expanded_reviews import completed_rows
        for room in ('bedroom', 'living_room'):
            additions, _, _ = completed_rows(Path(expansion), room)
            rows.update((row['scene']['id'], row) for row in additions)
            input_paths.append(Path(expansion) / room / 'checkpoint.json')
    frozen = json.loads(Path(frozen_scenes_path).read_bytes())['scenes']
    # Current paired source scenes carry final annotations/geometry. Density is
    # recomputed identically for both methods; no quality score enters selection.
    from serverless.benchmark.geometry import Box, room_geometry
    rows.update((scene['id'], {'scene': scene}) for scene in frozen)
    for row in rows.values():
        scene = row['scene']
        area = room_geometry(scene['room']).area
        if area <= 0:
            raise ValueError('Room area must be positive')
        row['metrics'] = {'furnitureDensity':
            sum(Box(item).footprint.area for item in furniture(scene))/area}
    old = defaultdict(set)
    for key, source in protocol['sceneProvenance'].items():
        if not key.endswith(':soilie'):
            continue
        pair_id=key[:-7]
        baseline=protocol['sceneProvenance'][pair_id+':baseline']['sceneId']
        old[rows[baseline]['scene']['model']].add((source['sceneId'], baseline))
    aliases = {'nightstand': 'side table'} if side_table_equivalence else {}
    report={'policy':POLICY, 'categoryAliases':aliases, 'fullBaselinePool': full_pool, 'groups':{}, 'inputs':{}}
    for path in input_paths:
        report['inputs'][str(path)] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    # Read existence only: no preference, confidence or explanation may affect
    # matching. Retention is a cost-saving priority, not an outcome filter.
    saved = {path.stem for path in (Path(protocol_path).parent / 'responses').glob('*.json')}
    assigned = defaultdict(list)
    for assignment in protocol['assignments']:
        pair_id = assignment['pairId']
        identities = tuple(protocol['sceneProvenance'][pair_id+':'+side]['sceneId'] for side in ('soilie','baseline'))
        if assignment['assignmentId'] in saved:
            assigned[identities].append(assignment['profile'])
    for baseline in ('layoutgpt','infinigen_controlled'):
        for room in ('bedroom','living_room'):
            left=[r for r in rows.values() if r['scene']['model']=='soilie' and r['scene']['roomType']==room]
            right=[r for r in rows.values() if r['scene']['model']==baseline and r['scene']['roomType']==room]
            # Existing broad-inventory matching allowed a wider density gap
            # for Infinigen; disclose rather than silently tighten that too.
            caliper = .25 if baseline == 'layoutgpt' else 1
            group={'availableSOILIE':len(left),'availableBaseline':len(right), 'densityCaliper':caliper}
            previous=[(a,b) for a,b in old[baseline] if rows[b]['scene']['roomType']==room]
            differences = [substitutions(signature(rows[a]['scene'],aliases),signature(rows[b]['scene'],aliases)) for a,b in previous]
            group['existingExact']=sum(value==0 for value in differences)
            group['existingWithinOne']=sum(value is not None and value<=1 for value in differences)
            for limit, name, density in ((0,'exact',caliper),(1,'withinOne',caliper),(1,'inventoryOnly',None)):
                paired=match(left,right,previous,limit,density,aliases)
                reusable = Counter(profile for row in paired for profile in
                    assigned[row['soilieScene'],row['baselineScene']])
                group[name]={'count':len(paired),'retainedPairs':sum(r['retainedPair'] for r in paired),
                             'exactPairs':sum(r['substitutions']==0 for r in paired),
                             'savedJudgmentsOnRetainedPairs': dict(reusable), 'pairs':paired}
            report['groups'][baseline+':'+room]=group
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf8') as stream:
        json.dump(report,stream,indent=2)
    return {k:{field:value for field,value in v.items() if field not in ('exact','withinOne','inventoryOnly')}|
            {name:{field:value for field,value in v[name].items() if field!='pairs'} for name in ('exact','withinOne','inventoryOnly')}
            for k,v in report['groups'].items()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--scenes',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--supplements',type=Path,nargs='*',default=[])
    parser.add_argument('--expansion',type=Path)
    parser.add_argument('--side-table-equivalence',action='store_true',
                        help='Sensitivity only: count nightstand and side table as one inventory class')
    args=parser.parse_args()
    print(json.dumps(analyze(args.base,args.scenes,args.protocol,args.output,args.supplements,args.expansion,
                             args.side_table_equivalence),indent=2))
