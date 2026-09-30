"""Audit the immutable inventory campaign before allocating reviewer calls.

Generation completion alone is insufficient: pair inputs, front annotations,
category counts, density and reference coverage must all pass independently.
No preference or quality score participates in this audit or pairing.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from serverless.arbutus.worker import sha
from serverless.benchmark.geometry import Box, furniture, room_geometry
from serverless.benchmark.inventory_matching import signature, substitutions, match
from serverless.benchmark.numbered_evidence import inventory
from serverless.benchmark.reference_catalog import candidate, coverage, require_coverage
from serverless.cloud_benchmark.staged_pilot import digest, read, write_new

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / '.codex/benchmark/soilie-platform-grid-final'


def density(scene):
    return sum(Box(o).footprint.area for o in furniture(scene)) / room_geometry(scene['room']).area


def collect(root, base=BASE):
    root, base = Path(root), Path(base)
    plan_path = root / 'campaign-v1.json'
    plan = read(plan_path)
    original_path = base / 'evidence/measured-scenes.json'
    if sha(original_path) != plan['sourceSha256']:
        raise ValueError('Frozen SOILIE source changed')
    originals = {r['scene']['id']: r['scene'] for r in read(original_path)['rows']}
    retained = {s['id']: s for s in read(base / 'review-functional-fronts-v3/source-scenes.json')['scenes']}
    layouts = {s['id'].removeprefix('layoutgpt-'): s for s in read(root / 'layoutgpt/export.json')['scenes']}
    scenes, pairs, pending, errors = {}, [], [], []
    for task in plan['tasks']:
        a = originals[task['soilieSceneId']]
        if digest(a) != task['soilieSceneSha256']:
            raise ValueError('SOILIE geometry changed: ' + task['id'])
        if task['baseline'] == 'layoutgpt':
            b = layouts.get(task['id']) if task['needsGeneration'] else retained[task['retainedBaselineSceneId']]
        else:
            candidates = []
            for host in ('cloud', 'local'):
                folder = root / host / task['id']
                receipt = folder / 'receipt.json'
                if not receipt.exists():
                    continue
                record = read(receipt)
                if record['status'] != 'complete':
                    continue
                path = folder / ('scene.json' if host == 'cloud' else record.get('scenePath', 'scene.json'))
                if record['planSha256'] != sha(plan_path) or sha(path) != record['sceneSha256']:
                    raise ValueError('Generation checksum mismatch: ' + task['id'])
                scene=read(path)
                correction=root/'floor-corrections'/(task['id']+'.json')
                if correction.parent.exists() and not correction.exists():
                    raise ValueError('Native floor audit is incomplete: '+task['id'])
                if correction.exists():
                    floor=read(correction)
                    archive=read(folder/'s3-receipt.json')
                    members={m['name']:m['sha256'] for m in archive['members']}
                    if (floor['blendSha256']!=members['scene/scene.blend']
                        or floor['stateSha256']!=members['scene/solve_state.json']
                        or floor['furnitureModified'] is not False
                        or floor['room']['boundaryExtractionVersion']!=2
                        or abs(scene['room']['floorZ']-floor['room']['floorZ'])>1e-9):
                        raise ValueError('Floor re-extraction differs from native source or datum')
                    scene['room']=floor['room']
                    scene['provenance']['boundaryMeasurement']={
                        'version':2,'nativeBlendSha256':floor['blendSha256'],
                        'nativeStateSha256':floor['stateSha256']}
                candidates.append(scene)
            if len(candidates) > 1:
                raise ValueError('Duplicate completion: ' + task['id'])
            b = candidates[0] if candidates else None
        if b is None:
            pending.append(task['id'])
            continue
        left, right = signature(a), signature(b)
        difference = substitutions(left, right)
        gap = abs(density(a) - density(b))
        limit = .25 if task['baseline'] == 'layoutgpt' else 1.0
        if (dict(left) != task['sourceInventory'] or dict(right) != task['inventory']
                or difference is None or difference > 1 or a['roomType'] != b['roomType']):
            errors.append({'task': task['id'], 'error': 'inventory_or_room_mismatch'})
        # Evaluating the numbered keys validates volumes and every asserted front.
        for scene in (a, b):
            if scene.get('fixture') or not room_geometry(scene['room']).is_valid:
                raise ValueError('Invalid real room: ' + scene['id'])
            inventory(scene)
            if scene['id'] in scenes and digest(scenes[scene['id']]) != digest(scene):
                raise ValueError('Conflicting scene identity')
            scenes[scene['id']] = scene
        pairs.append({'taskId': task['id'], 'soilieScene': a['id'], 'baselineScene': b['id'],
                      'baseline': b['model'], 'roomType': b['roomType'],
                      'substitutions': difference, 'densityDifference': gap,
                      'densityCaliper': limit, 'densityEligible': gap <= limit,
                      'retainedInput': not task['needsGeneration']})
    report = {'planSha256': sha(plan_path), 'complete': not pending,
              'pending': pending, 'errors': errors, 'pairs': pairs,
              'strata': dict(Counter(p['baseline'] + ':' + p['roomType'] for p in pairs)),
              'substitutionCounts': dict(Counter(p['substitutions'] for p in pairs)),
              'densityIneligible': [p['taskId'] for p in pairs if not p['densityEligible']],
              'catalogCoverage': coverage(list(scenes.values()), candidate())}
    for supplement in (root/'layoutgpt-supplement/export.json',root/'layoutgpt-topup/export.json'):
        if not supplement.exists(): continue
        for scene in read(supplement)['scenes']:
            inventory(scene)
            if scene['id'] in scenes:
                raise ValueError('Supplement scene identity collision')
            scenes[scene['id']] = scene
    return report, scenes


def rematch(report, scenes, base=BASE):
    """Retain eligible planned pairs, then find one-to-one density matches.

    The full frozen SOILIE pool is eligible. Never change a baseline output or
    drop an object, relax a caliper, or use quality/preference to fill a stratum.
    """
    rows = read(Path(base) / 'evidence/measured-scenes.json')['rows']
    soilie = [r for r in rows if r['scene']['model'] == 'soilie']
    result = []
    for baseline in ('layoutgpt', 'infinigen_controlled'):
        for room in ('bedroom', 'living_room'):
            originals = [p for p in report['pairs'] if (p['baseline'], p['roomType']) == (baseline, room)]
            # Initial assignments are themselves frozen before generation.
            old = {(p['soilieScene'], p['baselineScene']) for p in originals}
            right = [{'scene': scenes[p['baselineScene']],
                      'metrics': {'furnitureDensity': density(scenes[p['baselineScene']])}} for p in originals]
            found = match([r for r in soilie if r['scene']['roomType'] == room], right, old,
                          density_caliper=.25 if baseline == 'layoutgpt' else 1.0)
            tasks = {p['baselineScene']: p['taskId'] for p in originals}
            for pair in found:
                pair.update(baseline=baseline, roomType=room, taskId=tasks[pair['baselineScene']])
            result.extend(found)
    return result


def refresh_matching(root, audit_path, output, base=BASE):
    """Revalidate the selected identities after native measurement correction.

    Do not sample different rooms when every pair still meets the frozen rules.
    A failed pair requires explicit rematching, never a silently widened limit.
    """
    report,scenes=collect(root,base)
    previous=read(audit_path)
    scenes.update({r['scene']['id']:r['scene'] for r in read(Path(base)/'evidence/measured-scenes.json')['rows']
                   if r['scene']['model']=='soilie'})
    pairs=[]
    for original in previous['matchedPairs']:
        a,b=scenes[original['soilieScene']],scenes[original['baselineScene']]
        difference=substitutions(signature(a),signature(b))
        gap=abs(density(a)-density(b))
        if difference is None or difference>1 or gap>(.25 if b['model']=='layoutgpt' else 1.0):
            raise ValueError('Re-extracted pair needs rematching: '+original['taskId'])
        pairs.append({**original,'substitutions':difference,'densityDifference':gap})
    report.update(matchedPairs=pairs,matchedStrata=dict(Counter(p['baseline']+':'+p['roomType'] for p in pairs)),
                  supplement=previous.get('supplement'),parentMatchingSha256=sha(audit_path),
                  nativeFloorRevalidation=True)
    write_new(output,report)
    return {'pairs':len(pairs),'strata':report['matchedStrata'],'pairIdentitiesChanged':False}


def prepare_source(root, audit_path, output, base=BASE):
    """Freeze an audited pair list and unchanged source geometry for rendering."""
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new source directory')
    report, scenes = collect(root, base)
    matching = read(audit_path)
    if not report['complete'] or report['errors'] or matching['planSha256'] != report['planSha256']:
        raise ValueError('Generation/input audit has not passed')
    pairs = matching['matchedPairs']
    source_rows = read(Path(base) / 'evidence/measured-scenes.json')['rows']
    scenes.update({r['scene']['id']:r['scene'] for r in source_rows if r['scene']['model']=='soilie'})
    prior = read(Path(base) / 'review-functional-use-v3-full/protocol.json')
    prior_pairs = {}
    for key, row in prior['sceneProvenance'].items():
        if not key.endswith(':soilie'):
            continue
        pair_id = key[:-len(':soilie')]
        partner = prior['sceneProvenance'][pair_id + ':baseline']
        prior_pairs[row['sceneId'], partner['sceneId']] = pair_id
    selected, groups, seen = {}, {}, set()
    for pair in pairs:
        a, b = scenes[pair['soilieScene']], scenes[pair['baselineScene']]
        inv_a, inv_b = signature(a), signature(b)
        if b['model']=='infinigen_controlled' and b['room'].get('boundaryExtractionVersion')!=2:
            raise ValueError('Re-read all native Infinigen floors before freezing review inputs')
        limit = .25 if b['model'] == 'layoutgpt' else 1.0
        difference = substitutions(inv_a, inv_b)
        anchor = 'bed' if a['roomType']=='bedroom' else 'sofa'
        if (a['roomType'] != b['roomType'] or difference is None or difference > 1
                or inv_a[anchor] != inv_b[anchor] or abs(density(a)-density(b)) > limit):
            raise ValueError('Selected pair violates matching rules')
        for scene in (a,b):
            key = (b['model'], b['roomType'], scene['id'])
            if key in seen:
                raise ValueError('A room is reused within a comparison stratum')
            seen.add(key)
            inventory(scene)
            selected[scene['id']] = scene
        group = 'set-a' if b['model']=='layoutgpt' else 'set-b'
        old_id = prior_pairs.get((a['id'],b['id']))
        case_id = old_id.split(':',1)[1] if old_id else digest([a['id'],b['id']])[:24]
        case = {'id':case_id, 'comparisonCondition':b['model'], 'balanceStratum':b['roomType'],
                'title':('Bedroom' if b['roomType']=='bedroom' else 'Living Room')+' arrangement',
                'relationImage':'pending', 'comparisonImage':'pending'}
        evidence = {'caseId':case_id, 'soilieScene':a['id'], 'baselineScene':b['id'],
                    'soilieDigest':digest(a), 'baselineDigest':digest(b)}
        item = groups.setdefault(group, {'cases':[], 'stimulusEvidence':[]})
        item['cases'].append(case)
        item['stimulusEvidence'].append(evidence)
    catalog = candidate()
    require_coverage(list(selected.values()), catalog)
    catalog = {**catalog, 'version':'catalog-volume-examples-v2', 'status':'frozen-for-inventory-review'}
    for group, value in groups.items():
        write_new(output / group / 'protocol.json', value)
    write_new(output / 'source-scenes.json', {'scenes':list(selected.values())})
    write_new(output / 'catalog.json', catalog)
    audit = {'passed':True, 'planSha256':report['planSha256'], 'matchingSha256':sha(audit_path),
             'developmentPairIds':prior.get('developmentPairIds', []),
             'pairs':len(pairs), 'strata':dict(Counter(p['baseline']+':'+p['roomType'] for p in pairs)),
             'substitutions':dict(Counter(p['substitutions'] for p in pairs)),
             'furnitureGeometryChanged':False, 'catalogCoverage':coverage(list(selected.values()),catalog),
             'nativeFloorExtractions':sum(s['model']=='infinigen_controlled' and s['room'].get('boundaryExtractionVersion')==2 for s in selected.values()),
             'densityDefinition':'Sum of furniture footprint areas divided by room area.',
             'densityCalipers':{'layoutgpt':.25,'infinigen_controlled':1.0},
             'unmatchedTaskIds':sorted({p['taskId'] for p in report['pairs']}-{p['taskId'] for p in pairs}),
             'supplement':matching.get('supplement'),
             'selection':'One-to-one maximum-cardinality matching; retain planned pairs, then fewer substitutions, smaller density gaps and deterministic tie breaks. No quality/preference scores.',
             'scope':'Inventory-conditioned baselines, not unchanged default generation workloads.'}
    write_new(output / 'preflight.json', audit)
    write_new(output / 'matching.json', {'pairs':pairs})
    return audit


def add_supplement(root, audit_path, output, base=BASE, supplement_name='layoutgpt-supplement'):
    root=Path(root)
    report=read(audit_path)
    pairs=list(report['matchedPairs'])
    retained=[p for p in pairs if p['baseline']=='layoutgpt' and p['roomType']=='bedroom']
    rows=read(Path(base)/'evidence/measured-scenes.json')['rows']
    candidates=[r for r in rows if r['scene']['model']=='soilie' and r['scene']['roomType']=='bedroom']
    if supplement_name not in ('layoutgpt-supplement','layoutgpt-topup'):
        raise ValueError('Unknown supplemental batch')
    export=read(root/supplement_name/'export.json')
    requests=read(root/supplement_name/'requests.json')
    available={r['scene']['id']:r for r in export['rows']}
    _, all_scenes=collect(root,base)
    selected=[{'scene':all_scenes[p['baselineScene']],
               'metrics':{'furnitureDensity':density(all_scenes[p['baselineScene']])}} for p in retained]
    task_ids={p['baselineScene']:p['taskId'] for p in retained}
    frozen_pairs=[(p['soilieScene'],p['baselineScene']) for p in retained]
    decisions=[]
    for request in requests['requests']:
        if len(selected)>=120: break
        identifier='layoutgpt-'+request['id']
        if identifier not in available: break
        baseline=available[identifier]
        if dict(signature(baseline['scene']))!=request['requestedInventory']:
            decisions.append({'id':request['id'],'accepted':False,'reason':'requested_inventory_not_returned'})
            continue
        # Solve the complete bipartite assignment: a greedy unused-room test
        # can miss an augmenting path through a previously accepted pair.
        matches=match(candidates,[*selected,baseline],frozen_pairs,density_caliper=.25)
        if len(matches)!=len(selected)+1:
            decisions.append({'id':request['id'],'accepted':False,'reason':'no_unused_match_under_frozen_rules'})
            continue
        selected.append(baseline)
        task_ids[identifier]=request['id']
        retained=matches
        decisions.append({'id':request['id'],'accepted':True})
    pairs=[p for p in pairs if not (p['baseline']=='layoutgpt' and p['roomType']=='bedroom')]
    pairs.extend(dict(p,baseline='layoutgpt',roomType='bedroom',taskId=task_ids[p['baselineScene']]) for p in retained)
    report.update(matchedPairs=pairs,matchedStrata=dict(Counter(p['baseline']+':'+p['roomType'] for p in pairs)),
                  supplement={'requestPlanSha256':sha(root/supplement_name/'requests.json'),
                              'exportSha256':sha(root/supplement_name/'export.json'),
                              'parentMatchingSha256':sha(audit_path),
                              'previousSupplement':report.get('supplement'),
                              'rule':'First feasible proposals in frozen request order. Retain all previously eligible baseline rooms; re-pair SOILIE only if required by one-to-one matching. No quality scores.',
                              'decisions':decisions})
    write_new(output,report)
    return {'strata':report['matchedStrata'],'supplement':report['supplement']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--rematch', action='store_true')
    p.add_argument('--prepare-source', type=Path, help='Existing audited matching JSON')
    p.add_argument('--add-supplement', type=Path, help='Existing matching JSON to augment')
    p.add_argument('--supplement-name',default='layoutgpt-supplement')
    p.add_argument('--refresh-matching',type=Path)
    args = p.parse_args()
    if args.refresh_matching:
        print(json.dumps(refresh_matching(args.root,args.refresh_matching,args.output),indent=2))
        raise SystemExit(0)
    if args.add_supplement:
        print(json.dumps(add_supplement(args.root,args.add_supplement,args.output,supplement_name=args.supplement_name),indent=2))
        raise SystemExit(0)
    if args.prepare_source:
        print(json.dumps(prepare_source(args.root,args.prepare_source,args.output),indent=2))
        raise SystemExit(0)
    report, scenes = collect(args.root)
    if args.rematch:
        report['matchedPairs'] = rematch(report, scenes)
        report['matchedStrata'] = dict(Counter(p['baseline']+':'+p['roomType'] for p in report['matchedPairs']))
        report['retainedPlannedPairs'] = sum(p['retainedPair'] for p in report['matchedPairs'])
    write_new(args.output, report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('pairs', 'matchedPairs', 'catalogCoverage')}, indent=2))
    print(json.dumps(report['catalogCoverage'], indent=2))
