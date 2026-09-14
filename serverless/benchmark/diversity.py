"""Freeze broad inputs without modifying V4 or selecting by output quality.

Preset/random requests still use the original sampler. Explicit requests use
ordered prefixes already in its published combinations, emphasizing catalog
coverage. Catalog membership is not a guarantee that placement will succeed.
"""
import argparse
from collections import Counter
import csv
import hashlib
from itertools import combinations
import json
from pathlib import Path
import random

from serverless.common.v4_runtime import ROOM_COMBINATION_FILES
from serverless.benchmark.run_batch import checkpoint_rows, write_json


def catalog_rows(path):
    with path.open(newline='',encoding='utf-8-sig') as stream:
        return [tuple(v.strip() for v in row if v.strip()) for row in list(csv.reader(stream))[1:]]


def observed_triplets(runtime):
    """Use existing observations through V4's explicit-object input path.

    The refined preset files omit some observed classes. Do not rebuild those
    files or pretend that explicit probes repair the original preset sampler.
    Limit probes to labels with an existing size entry and registered asset.
    """
    with (runtime/'assets/asset_rotations.csv').open(newline='') as stream:
        assets = {row['object_name'] for row in csv.DictReader(stream)
                  if (runtime/'assets'/row['asset_name']).is_file()}
    with (runtime/'data/object_sizes_manual.csv').open(newline='') as stream:
        sizes = {row['object'] for row in csv.DictReader(stream)}
    allowed = assets & sizes
    triplets = set()
    with (runtime/'data/triplets.csv').open(newline='') as stream:
        for row in csv.DictReader(stream):
            labels = tuple(row[key] for key in ('objectA','objectB','objectC'))
            if set(labels) <= allowed:
                triplets.add(labels)
    return triplets


def coverage_sample(candidates, limit, seed):
    """Greedy label/pair coverage, then count balance; never reads geometry."""
    rows = sorted(set(candidates))
    random.Random(seed).shuffle(rows)
    labels, pairs, counts = Counter(), Counter(), Counter()
    chosen = []
    while rows and len(chosen) < limit:
        def priority(row):
            terms = set(row)
            relations = list(combinations(sorted(terms),2))
            return (sum(labels[v] == 0 for v in terms),
                    sum(pairs[v] == 0 for v in relations),
                    -counts[len(row)], sum(1/(1+labels[v]) for v in terms))
        row = max(rows,key=priority)
        rows.remove(row)
        chosen.append(row)
        labels.update(set(row))
        pairs.update(combinations(sorted(set(row)),2))
        counts[len(row)] += 1
    return chosen


def freeze(runtime, seed=40260914, repetitions=4, explicit_limit=128):
    sources = dict(ROOM_COMBINATION_FILES, random='working-combos-refined.csv')
    catalogs = {name:catalog_rows(runtime/'data'/filename) for name,filename in sources.items()}
    requests = []
    for name in sources:
        # Four probes (one per requested count) document an empty source rather
        # than wasting repeated attempts on the identical known input problem.
        repeat_count = repetitions if catalogs[name] else 1
        duplicate_options = (True,False) if catalogs[name] else (True,)
        for _ in range(repeat_count):
            for duplicate in duplicate_options:
                for count in (3,4,5,6):
                    request = {'mode':'random' if name == 'random' else 'room_type',
                               'objectCount':count,'allowDuplicates':duplicate,'sameObjectsAcrossScenes':True}
                    if name != 'random':
                        request['roomType'] = name
                    requests.append(request)
    refined = {row[:count] for row in catalogs['random'] for count in (3,4,5,6) if len(row) >= count}
    triplets = observed_triplets(runtime)
    candidates = refined | triplets
    chosen = coverage_sample(candidates,explicit_limit,seed)
    requests.extend({'mode':'objects','objects':list(row),'sameObjectsAcrossScenes':True} for row in chosen)
    # Interleave modes instead of exhausting one preset before any others run.
    random.Random(seed+1).shuffle(requests)
    for index,request in enumerate(requests):
        request['seed'] = seed+index*997
    return {'schemaVersion':1,'cohort':'diversity','seed':seed,'requests':requests,
            'sourceChecksums':{name:hashlib.sha256((runtime/'data'/filename).read_bytes()).hexdigest() for name,filename in sources.items()},
            'explicitSourceChecksums':{name:hashlib.sha256((runtime/name).read_bytes()).hexdigest()
                                      for name in ('data/triplets.csv','data/object_sizes_manual.csv','assets/asset_rotations.csv')},
            'sourceRows':{name:len(rows) for name,rows in catalogs.items()},
            'registeredAssetTriplets':len(triplets),
            'refinedCatalogClasses':sorted({v for row in refined for v in row}),
            'catalogClasses':sorted({v for row in candidates for v in row}),
            'explicitClasses':sorted({v for row in chosen for v in row}),
            'emptyPresets':[name for name,rows in catalogs.items() if not rows],
            'sampling':'Seeded, outcome-independent exploration of all existing presets, random mode, duplicate policies, ordered catalog prefixes and observed triplets with existing assets and sizes. Preset data is not rebuilt. Not the controlled bedroom throughput cohort.',
            'fallbacks':False,'roomFitIncluded':False}


def export(run, plan):
    from serverless.benchmark.geometry import measure
    attempts = checkpoint_rows(run)
    rows, invalid = [], []
    for attempt in attempts:
        if attempt['status'] != 'complete':
            continue
        scene = attempt['stages']['final']
        if scene.get('cohort') != 'diversity':
            raise ValueError('Exploration must remain separately labelled')
        try:
            rows.append({'scene':scene,'metrics':measure(scene)})
        except ValueError as error:
            invalid.append({'id':scene['id'],'reason':str(error)})
    completed = [a for a in attempts if a['status'] == 'complete']
    return {'schemaVersion':1,'cohort':'diversity','rows':rows,
            'coverage':{'plannedAttempts':len(plan['requests']),'attempted':len(attempts),'completed':len(completed),
                        'failures':dict(Counter(a.get('errorCode') for a in attempts if a['status'] != 'complete')),
                        'sourceRows':plan['sourceRows'],'emptyPresets':plan['emptyPresets'],
                        'catalogClasses':plan['catalogClasses'],
                        'selectedClasses':sorted({v for a in attempts for v in a.get('selection',[])}),
                        'completedClasses':sorted({v for a in completed for v in a.get('selection',[])}),
                        'actualFinalClasses':sorted({v['label'] for row in rows for v in row['scene']['objects']}),
                        'uniqueCompletedSelections':len({tuple(sorted(a.get('selection',[]))) for a in completed}),
                        'invalidGeometry':invalid},'sampling':plan['sampling']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--run',type=Path)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    if args.run:
        write_json(args.output,export(args.run,json.loads(args.plan.read_text())))
    else:
        document = freeze(args.runtime)
        if args.plan.exists() and json.loads(args.plan.read_text()) != document:
            raise ValueError('Frozen exploration inputs changed; choose a new cohort')
        args.plan.parent.mkdir(parents=True,exist_ok=True)
        write_json(args.plan,document)
        print(json.dumps({'plannedAttempts':len(document['requests']),'catalogClasses':len(document['catalogClasses']),
                          'explicitClasses':len(document['explicitClasses']),'emptyPresets':document['emptyPresets']}),flush=True)


if __name__ == '__main__':
    main()
