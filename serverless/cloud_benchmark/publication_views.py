"""Build room-stratified website evidence without rerunning geometry or models.

The expensive SOILIE stage audit is immutable input. New baseline exports can
be added independently; private API receipts never pass through to the site.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import shutil

from serverless.benchmark.cost import (
    monthly_lambda_budget, token_charge, validate_public_rate_card, worker_scenario,
)
from serverless.benchmark.geometry import summarize
from serverless.benchmark.nonhuman import validity_rates
from serverless.benchmark.publish_comparison import (
    LABELS, aggregate, compare, inventory_summary, write_support_evidence,
)
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.evidence import sha
from serverless.cloud_benchmark.publish import timing_conditions
from serverless.cloud_benchmark.expanded_reviews import completed_rows

ROOMS = ('bedroom', 'living_room')


def positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Finite positive timing required')
    return value


def model_summary(model, rows):
    return {'label': LABELS[model], 'n': len(rows), 'metrics': aggregate(rows),
            'inventory': inventory_summary(rows), 'validityRates': validity_rates(rows)}


def mesh_check_coverage(rows):
    """Distinguish separation proofs from surface tests and solid volumes."""
    fields = ('pairCount', 'broadPhaseDisjointPairs', 'preservedBoundsDisjointPairs',
              'numericalContactPairs', 'booleanPairs', 'surfaceDisjointPairs')
    result = {}
    for model in LABELS:
        checks = [row['scene']['solidMeshOverlap'] for row in rows
                  if row['scene']['model'] == model and row['scene'].get('solidMeshOverlap')]
        # A crossing open surface is a detected intersection even though no
        # enclosed-volume percentage exists. Do not drop it with null metrics.
        def detected(check):
            if check.get('overlapPairs'):
                return True
            return any((match := re.search(r'(\d+) intersecting triangle pair', pair.get('reason', '')))
                       and int(match[1]) > 0 for pair in check.get('unavailablePairs', []))
        result[model] = {**{field: sum(check.get(field, 0) for check in checks) for field in fields},
                         'roomsUsingSurfaceTests': sum(check.get('surfaceDisjointPairs', 0) > 0 for check in checks),
                         'checkedRooms': len(checks),
                         'detectedIntersectionRooms': sum(bool(detected(check)) for check in checks),
                         'noDetectedIntersectionRooms': sum(check.get('complete', False) and not detected(check) for check in checks),
                         'unresolvedRooms': sum(not check.get('complete', False) and not detected(check) for check in checks)}
    return result


def room_models(rows):
    return {room: {model: model_summary(model, [row for row in rows
                    if row['scene']['model'] == model and row['scene']['roomType'] == room])
                   for model in LABELS} for room in ROOMS}


def completed_calls(exports, room='living_room'):
    """Require a closed batch; never silently omit rejected or uncertain calls."""
    calls, ids = [], set()
    if room not in ROOMS:
        raise ValueError('Unsupported API timing room type')
    for export in exports:
        if not export.get('complete') or export.get('reservedUncertainUsd') != 0:
            raise ValueError('Closed LayoutGPT batch required')
        scenes = {row['scene']['id']: row['scene'] for row in export['rows']}
        if len(scenes) != len(export['attempts']):
            raise ValueError('Every API attempt must have a measured final layout')
        for attempt in export['attempts']:
            name = attempt['id']
            if name in ids:
                raise ValueError('Repeated API attempt across exports')
            ids.add(name)
            scene = scenes.get('layoutgpt-' + name)
            selection_valid = (attempt.get('countSatisfied') and type(attempt['requestedObjects']) is int
                               and attempt['requestedObjects'] in (3, 4, 5, 6)) if room == 'living_room' else (
                               export.get('variant') == 'bedroom-original-prompt-timing'
                               and attempt['requestedObjects'] is None and attempt.get('finishReason') == 'stop')
            if (attempt['status'] != 'complete' or attempt.get('geometryStatus') != 'complete'
                    or attempt.get('unparsedLines') != 0 or not selection_valid
                    or not scene or scene['roomType'] != room
                    or scene['provenance']['model'] != 'gpt-4-0613'):
                raise ValueError('Cost cohort must account for every attempted room proposal')
            usage = attempt['usage']
            for key in ('prompt_tokens', 'completion_tokens'):
                if type(usage[key]) is not int or usage[key] < 0:
                    raise ValueError('Invalid recorded token usage')
            calls.append({'id': name, 'requestedObjects': attempt['requestedObjects'],
                'seconds': positive(attempt['wallSeconds']),
                'inputTokens': usage['prompt_tokens'], 'outputTokens': usage['completion_tokens'],
                'model': scene['provenance']['model']})
    if not calls:
        raise ValueError('No recorded LayoutGPT calls')
    return calls


def native_timing(export):
    # Concurrent construction is valid geometry, but not an isolated stopwatch.
    return {room: latency([positive(row['generationSeconds']) for row in export['attempts']
                          if row['roomType'] == room and row['status'] == 'complete'
                          and row.get('timingEligible', True)]) for room in ROOMS}


def latency(seconds):
    return {'completedLatencySeconds': summarize(seconds), 'completed': len(seconds),
            'successfulGenerationSeconds': sum(seconds),
            'completedPerMinute': 60 * len(seconds) / sum(seconds) if seconds else None}


def measured_cost(sources, calls, rates, native=None, bedroom_calls=None):
    validate_public_rate_card(rates)
    cloud = [row for row in sources if row['platform'] == 'AWS Lambda' and row['roomType'] == 'living_room']
    if len(cloud) != 2500:
        raise ValueError('Complete 2,500-room cloud living-room condition required')
    seconds = [positive(row['generationSeconds']) for row in cloud]
    prices = [token_charge(row['inputTokens'], row['outputTokens'], rates['gpt4']) for row in calls]
    soilie = summarize([worker_scenario(value, rates['lambda'])['usd'] for value in seconds])
    layout = summarize(prices)
    result = {'schemaVersion': 3, 'currency': 'USD', 'recordedApiRoomType': 'living_room', 'rateCard': rates,
        'scope': 'Per-room generation-stage costs for bedrooms and living rooms, calculated from recorded usage at public rates. SOILIE requests 3–6 objects; LayoutGPT uses its original bedroom prompt and a 3–6-object living-room prompt. Any hypothetical Infinigen values are separately identified. Images, evaluation, orchestration and downstream contact correction are excluded.',
        'soilie': {'usd': soilie, 'seconds': summarize(seconds), 'memoryMb': 4096,
            'ephemeralStorageMb': 10240,
            'basis': 'Measured AWS Lambda generation-stage seconds priced at public 4 GB x86-64 compute, 10 GB temporary-storage and request rates. Not complete billed invocation time.'},
        'layoutgpt': {'usd': layout, 'inputTokens': summarize([row['inputTokens'] for row in calls]),
            'outputTokens': summarize([row['outputTokens'] for row in calls]),
            'models': sorted({row['model'] for row in calls}),
            'requestedObjectCounts': dict(sorted(Counter(row['requestedObjects'] for row in calls).items())),
            'basis': 'Recorded prompt and completion tokens from GPT-4 living-room calls, priced at public token rates. Four retrieved examples and an explicit 3–6-object instruction.'},
        'freeTier': {'monthlyLayouts': 600, 'accountUsageIncluded': False,
            'assumptions': '600 SOILIE living rooms per month, one invocation per room, using the observed living-room generation-stage mean as total billed duration; no retries or other account usage. Extra invocation overhead would increase the estimate.',
            'allowanceAvailable': monthly_lambda_budget(600, summarize(seconds)['mean'], 4096, 10240,
                                                       rates['lambda'], 400000, 1000000),
            'allowanceExhausted': monthly_lambda_budget(600, summarize(seconds)['mean'], 4096, 10240, rates['lambda'])},
        'limitations': ['Generation-stage compute is not a complete hosted-service bill.',
            'Different room requests and model outputs: this comparison prices generation, not equal quality.',
            'Prices exclude credits, taxes and discounts; a different LLM requires its own quality evaluation.']}
    # Keep one price per observation. Quantiles describe variation across rooms,
    # never a confidence interval or uncertainty about an individual's invoice.
    observations, by_room = [], {}
    for room in ROOMS:
        workers = [row for row in sources if row['platform'] == 'AWS Lambda' and row['roomType'] == room]
        for index, row in enumerate(workers):
            seconds = positive(row['generationSeconds'])
            observations.append({'id': f'soilie-{room}-{index:04d}', 'model': 'soilie',
                'roomType': room, 'basis': 'measured-generation-stage', 'seconds': seconds,
                'usd': worker_scenario(seconds, rates['lambda'])['usd']})
        proposals = calls if room == 'living_room' else (bedroom_calls or [])
        for row in proposals:
            observations.append({'id': row['id'], 'model': 'layoutgpt', 'roomType': room,
                'basis': 'recorded-api-tokens', 'inputTokens': row['inputTokens'],
                'outputTokens': row['outputTokens'], 'seconds': row['seconds'],
                'usd': token_charge(row['inputTokens'], row['outputTokens'], rates['gpt4'])})
        for row in (native or {}).get('attempts', []):
            if row['roomType'] != room or row['status'] != 'complete' or not row.get('timingEligible', True):
                continue
            seconds = positive(row['generationSeconds'])
            observations.append({'id': row['id'], 'model': 'infinigen', 'roomType': room,
                'basis': 'hypothetical-runtime-transfer', 'seconds': seconds,
                'usd': worker_scenario(seconds, rates['lambda'])['usd']})
        by_room[room] = {
            model: summarize([row['usd'] for row in observations if row['model'] == model and row['roomType'] == room])
            for model in ('soilie', 'layoutgpt', 'infinigen')}
    result.update(byRoomType=by_room, observations=observations,
        distributionMeaning='Across-room variation at fixed tariffs: median, quartiles, full observed range and 95th percentile. Not confidence intervals or billing-error estimates.',
        infinigenBasis='Illustration only: price each isolated desktop construction time as if a 4 GB x86-64 worker with 10 GB temporary storage achieved the same duration. Infinigen was not run on AWS Lambda; CPU equivalence, memory sufficiency and container compatibility are unverified. Not a measured cloud cost or a demonstrated cheapest deployment.',
        missing={'layoutgptBedroom': 'These are official released layouts, not timed calls made for this experiment. They contain neither API latency nor token-usage receipts. Living-room calls cannot supply bedroom measurements.',
                 'infinigenControlled': 'Controlled-inventory rooms ran on shared CPU workers. Elapsed time includes contention and no per-room allocated CPU/memory ledger was recorded, so an isolated-runtime cost estimate is not supplied.',
                 'grains': 'No per-room inference usage or matching hardware cost record is available; the paper supplies only a batch timing reference.'})
    if bedroom_calls:
        result['missing'].pop('layoutgptBedroom')
        result['bedroomPilot'] = {'completed': len(bedroom_calls),
            'basis': 'Fresh GPT-4 bedroom calls using eight retrieved examples and a 512-token output limit, without an object-count instruction. Separate timing/usage pilot, not receipts for the 423 released geometry samples.'}
    return result


def merge_geometry(rows, additions):
    """Allow identical source reuse, never duplicate sample weight or changed geometry."""
    indexed = {row['scene']['id']: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError('Repeated final geometry')
    for row in additions:
        identity = row['scene']['id']
        if identity in indexed and indexed[identity] != row:
            raise ValueError('Conflicting measured geometry')
        indexed[identity] = row
    return list(indexed.values())


def verified_reviews(directory, cohort_sha):
    """A release requires complete, hash-matched reports for both comparisons."""
    manifest = json.loads((directory / 'review-manifest.json').read_bytes())
    expected = {'layoutgpt', 'infinigen_controlled'}
    if (manifest.get('releaseEligible') is not True or manifest.get('cohortSha256') != cohort_sha
            or set(manifest['comparisons']) != expected):
        raise ValueError('Reviews do not certify this completed cohort')
    files = []
    for baseline, stem in (('layoutgpt', 'ai-pilot'), ('infinigen_controlled', 'ai-pilot-infinigen')):
        report = manifest['comparisons'][baseline]
        names = {stem + suffix for suffix in ('-summary.json', '-responses.json')}
        if (report.get('releaseEligible') is not True
                or report['pairs'] != {room: 120 for room in ROOMS} or set(report['files']) != names):
            raise ValueError('Exactly 120 reviewed pairs per room and baseline required')
        for name, checksum in report['files'].items():
            path = directory / name
            if sha(path.read_bytes()) != checksum:
                raise ValueError('Changed reviewer export')
            document = json.loads(path.read_bytes())
            if (document.get('releaseEligible') is not True or document.get('cohortSha256') != cohort_sha
                    or document.get('reviewersCompleted') != 10):
                raise ValueError('Incomplete reviewer export')
            files.append(name)
    return files


def compile_views(base, evidence, layoutgpt, native, rates, output,
                  expansion=None, controlled=None, reviews=None, layoutgpt_scale=None, bedroom_timing=None, infinigen_cloud=None):
    raw = (evidence / 'measured-scenes.json').read_bytes()
    cohort = json.loads((evidence / 'cohort.json').read_bytes())
    document = json.loads(base.read_bytes())
    digest = document.pop('evidenceDigest')
    if sha(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()) != digest:
        raise ValueError('SOILIE stage audit digest differs')
    if (not cohort['complete'] or sha(raw) != cohort['measurementsSha256']
            or document['cohort']['measurementsSha256'] != cohort['measurementsSha256']):
        raise ValueError('Publication and measurements must describe the same completed cohort')
    conditions = timing_conditions(cohort['sources'])
    exports = [json.loads(path.read_bytes()) for path in layoutgpt]
    calls = completed_calls(exports)
    bedroom_calls = completed_calls([json.loads(bedroom_timing.read_bytes())], 'bedroom') if bedroom_timing else []
    if bedroom_timing and len(bedroom_calls) != 20:
        raise ValueError('Complete 20-call bedroom timing pilot required')
    # Original bedroom outputs and the controlled living-room calls are separate
    # strata, not a synthetic single inference batch. Selection never uses scores.
    rows = [row for row in json.loads(raw)['rows'] if not
            (row['scene']['model'] == 'layoutgpt' and row['scene']['roomType'] == 'living_room')]
    rows.extend(row for export in exports for row in export['rows'])
    additions, checkpoints = [], {}
    if controlled:
        from serverless.benchmark.geometry import measure
        for path in controlled:
            export = json.loads(path.read_bytes())
            if export['invalidArtifacts']:
                raise ValueError('Invalid controlled supplement')
            additions.extend({'scene': scene, 'metrics': measure(scene)} for scene in export['scenes'])
    if expansion:
        if not json.loads((expansion / 'complete.json').read_bytes())['complete']:
            raise ValueError('Both expansion room strata must be complete')
        for room in ROOMS:
            measured, _pairs, checksum = completed_rows(expansion, room)
            additions.extend(measured)
            checkpoints[room] = checksum
    rows = merge_geometry(rows, additions)
    # Scale is additional measurement evidence, not a change to reviewed boxes.
    # Keep the original scene digest and dimensionless metrics byte-for-byte.
    scale_document = None
    if layoutgpt_scale:
        from serverless.benchmark.layoutgpt_scale import apply_clearance
        scale_document = json.loads(layoutgpt_scale.read_bytes())
        apply_clearance(rows, scale_document)
    document.update(schemaVersion=4,
        models={model: model_summary(model, [row for row in rows if row['scene']['model'] == model]) for model in LABELS},
        modelsByRoomType=room_models(rows), comparisons=compare(rows),
        meshCheckCoverage=mesh_check_coverage(rows),
        meshCheckCoverageByRoomType={room: mesh_check_coverage([row for row in rows if row['scene']['roomType'] == room]) for room in ROOMS},
        layoutgptSources={'bedroom': 'Official released GPT-4 layouts, eight retrieved examples.',
            'living_room': 'Recorded GPT-4 calls, four retrieved examples, requested counts cycling 3–6.',
            'exportSha256': [sha(path.read_bytes()) for path in layoutgpt]},
        cost=measured_cost(cohort['sources'], calls, json.loads(rates.read_bytes()), json.loads(native.read_bytes()), bedroom_calls),
        # A deliberate gate: final review exports must name this geometry cohort.
        # Do not pair newly measured rooms with earlier website judgement totals.
        aiReview={'ready': False, 'cohortSha256': cohort['measurementsSha256']},
        timing={**document['timing'], 'soilieConditions': conditions,
            'infinigenByRoomType': native_timing(json.loads(native.read_bytes())),
            'layoutgptByRoomType': {'living_room': {**latency([row['seconds'] for row in calls]),
                'stage': 'API request to complete response; includes network and provider queue time. Excludes prompt retrieval, parsing and image rendering.'}}})
    if bedroom_calls:
        document['timing']['layoutgptByRoomType']['bedroom'] = {
            **latency([row['seconds'] for row in bedroom_calls]),
            'stage': 'Twenty fresh API calls using the original K=8 bedroom prompt, 512-token output limit and no count instruction. API request to complete response, including network/provider queue; excludes prompt retrieval, parsing, meshes and rendering. The 423 released bedroom layouts remain the geometry cohort.',
            'evidence': 'measured-api-pilot', 'sourceSha256': sha(bedroom_timing.read_bytes())}
    if infinigen_cloud:
        from serverless.infinigen_cloud.publication import measured_rows, completion_coverage
        cloud_rows = measured_rows(infinigen_cloud)
        cloud_coverage = completion_coverage(infinigen_cloud)
        cloud_models = ('infinigen', 'infinigen_controlled')
        document['timing']['infinigenCloudByRoomType'] = {
            room: {model: {**latency([row['seconds'] for row in cloud_rows if row['roomType'] == room and row['model'] == model]),
                          'platform': 'AWS Lambda', 'memoryMb': 6144, 'blenderThreads': 4,
                          'completion': cloud_coverage[room][model]}
                   for model in cloud_models} for room in ROOMS}
        cost = document['cost']
        # Replace hypothetical transferred durations with actual cloud-stage
        # observations. Geometry and AI votes remain pinned to their own cohorts.
        cost['observations'] = [row for row in cost['observations'] if row['model'] != 'infinigen']
        for row in cloud_rows:
            cost['observations'].append({**row, 'usd': worker_scenario(row['seconds'], cost['rateCard']['lambda'], 6144)['usd']})
        for room in ROOMS:
            for model in cloud_models:
                cost['byRoomType'][room][model] = summarize([row['usd'] for row in cost['observations'] if row['roomType'] == room and row['model'] == model])
        cost['missing'].pop('infinigenControlled', None)
        cost['infinigenCloud'] = {'attemptsPerCondition': 20, 'completion': cloud_coverage, 'memoryMb': 6144,
            'sourceSha256': sha(infinigen_cloud.read_bytes()),
            'stage': 'Blender startup, solving, procedural meshes, camera preparation and scene serialization. Excludes validation, compression, artifact transfer and image rendering.'}
        cost['infinigenBasis'] = 'Measured construction-stage durations on 6 GB x86-64 AWS Lambda workers with 10 GB temporary storage, priced at public tariffs. Not complete billed invocation time.'
        cost['scope'] = 'Per-room generation-stage costs for bedrooms and living rooms, calculated from recorded cloud durations and API tokens at public rates. SOILIE places existing meshes; LayoutGPT proposes boxes; Infinigen constructs procedural meshes in room-scale and controlled-inventory conditions. Images, evaluation, orchestration and downstream contact correction are excluded.'
    # Selected extreme diagrams were made for the original baseline corpus;
    # retain only those whose source model/corpus has not changed.
    document['illustrations'] = [row for row in document['illustrations'] if row['model'] != 'layoutgpt']
    document['metricDefinitions']['meanWorstSolidOverlapPct'].update(
        title='Mesh intersection diagnostic',
        meaning='Disjoint bounds establish separation. Closed intersecting meshes permit Boolean volume measurement. '
                'Open or non-manifold assets are checked for triangle-surface crossings instead; disjoint surfaces '
                'do not establish a solid volume or exclude containment inside an undefined interior. '
                'A zero diagnostic means no intersection detected under these tests, not a solid-volume measurement for every pair.')
    if expansion:
        document['infinigenControlledConfiguration'] = {
            'profile': 'controlled inventory; official fast_solve',
            'sourceCommit': 'fb7991e06580639202a4687937082cb63e931eb0',
            'bedroom': 'Three to six requested roles: bed, side table, floor lamp, then storage, desk and rug. The initial fixed-six inputs and subsequent cyclic counts remain identified per scene.',
            'living_room': 'Six roles: sofa, TV stand, storage, side table, coffee table and rug.',
            'postGenerationObjectRemoval': False,
            'checkpointSha256': checkpoints,
            'countsByRoom': document['models']['infinigen_controlled']['inventory']['roomTypes'],
            'timingEligible': False}
        document['timing'].pop('infinigenControlled', None)
        document['illustrations'] = [row for row in document['illustrations'] if row['model'] == 'infinigen']
    review_files = []
    if reviews:
        if not expansion:
            raise ValueError('Final reviews require the completed expansion geometry')
        review_files = verified_reviews(reviews, cohort['measurementsSha256'])
        # Match every reviewed geometry digest against the actual published rows.
        from serverless.benchmark.stimuli import digest as scene_digest
        by_id = {row['scene']['id']: row['scene'] for row in rows}
        for name in review_files:
            if not name.endswith('-responses.json'):
                continue
            report = json.loads((reviews / name).read_bytes())
            for pair in report['stimulusEvidence']:
                for side in ('soilie', 'baseline'):
                    if scene_digest(by_id[pair[side + 'Scene']]) != pair[side + 'Digest']:
                        raise ValueError('Published geometry differs from reviewed geometry')
        document['aiReview'].update(ready=True, pairsPerRoomPerBaseline=120,
                                    manifestSha256=sha((reviews / 'review-manifest.json').read_bytes()))
    output.mkdir(parents=True, exist_ok=True)
    if scale_document:
        document['layoutgptPhysicalScale'] = {'rooms': len(scale_document['rooms']),
            'file': 'layoutgpt-scale.json', 'sha256': sha(layoutgpt_scale.read_bytes()),
            'method': scale_document['method'], 'source': scale_document['scaleImplementation']}
        # Preserve the bytes whose hash the document certifies, even if the
        # caller supplied an equivalent JSON file with different formatting.
        if layoutgpt_scale.resolve() != (output / 'layoutgpt-scale.json').resolve():
            shutil.copyfile(layoutgpt_scale, output / 'layoutgpt-scale.json')
    # Publish an allowlisted numeric ledger, not account receipts or provider IDs.
    observations = document['cost'].pop('observations')
    cost_evidence = {'schemaVersion': 1, 'currency': 'USD', 'rateCard': document['cost']['rateCard'],
                     'distributionMeaning': document['cost']['distributionMeaning'], 'rows': observations}
    write_json(output / 'cost-measurements.json', cost_evidence)
    document['cost']['measurements'] = {'file': 'cost-measurements.json',
        'sha256': sha((output / 'cost-measurements.json').read_bytes()), 'rows': len(observations)}
    document['evidenceDigest'] = sha(json.dumps(document, sort_keys=True, separators=(',', ':')).encode())
    # Reproduce the retained explanatory image from its measured scene, rather
    # than relying on an untracked image from a previous website build.
    from serverless.benchmark.stimuli import diagram
    by_id = {row['scene']['id']: row['scene'] for row in rows}
    for example in document['illustrations']:
        pair = example['highlightedPair']
        body = diagram(by_id[example['sceneId']], {pair['a'], pair['b']}, show_fronts=False).encode()
        name = Path(example['image']).name
        if sha(body)[:24] != Path(name).stem:
            raise ValueError('Reproduced explanatory diagram differs')
        (output / 'illustrations').mkdir(exist_ok=True)
        (output / 'illustrations' / name).write_bytes(body)
    write_json(output / 'comparison.json', document)
    write_support_evidence(rows, output)
    # Compact downloadable row-level metrics, without private sessions, machine
    # paths, invocation receipts or account identifiers.
    write_json(output / 'room-measurements.json', {'schemaVersion': 1,
        'cohortSha256': cohort['measurementsSha256'], 'metricDefinitions': document['metricDefinitions'],
        'rows': [{'sceneId': row['scene']['id'], 'model': row['scene']['model'],
                  'roomType': row['scene']['roomType'], 'metrics': row['metrics']} for row in rows]})
    if reviews:
        for name in review_files + ['review-manifest.json']:
            shutil.copyfile(reviews / name, output / name)
        shutil.copytree(reviews / 'stimuli', output / 'stimuli', dirs_exist_ok=True)
        counts = {model: summary['n'] for model, summary in document['models'].items()}
        write_json(output / 'status.json', {'schemaVersion': 1,
            'analysis': {'state': 'complete', 'summary':
                f"10,000 completed SOILIE layouts (5,000 bedrooms and 5,000 living rooms), {counts['layoutgpt']} LayoutGPT layouts, {counts['infinigen']} room-scale and {counts['infinigen_controlled']} controlled-inventory Infinigen rooms."},
            'corpus': {'completedLayouts': 10000, 'roomTypes': {'bedroom': 5000, 'living_room': 5000}},
            'phases': [{'id': key, 'state': 'complete'} for key in ('measurement', 'comparison', 'ai-review')],
            'aiPilot': {'open': False, 'pairsPerRoomPerBaseline': 120, 'reviewers': 10},
            'humanParticipants': 0})
    write_json(output / 'publication-inputs.json', {'schemaVersion': 1,
        'cohortSha256': cohort['measurementsSha256'], 'baseSha256': sha(base.read_bytes()),
        'layoutgptSha256': [sha(path.read_bytes()) for path in layoutgpt], 'nativeSha256': sha(native.read_bytes()),
        'ratesSha256': sha(rates.read_bytes()), 'expansionCheckpointsSha256': checkpoints,
        'aiReviewsReady': bool(reviews),
        'layoutgptPhysicalScaleSha256': sha(layoutgpt_scale.read_bytes()) if layoutgpt_scale else None,
        'layoutgptBedroomTimingSha256': sha(bedroom_timing.read_bytes()) if bedroom_timing else None,
        'infinigenCloudTimingSha256': sha(infinigen_cloud.read_bytes()) if infinigen_cloud else None})
    print(json.dumps({'models': {key: value['n'] for key, value in document['models'].items()},
                      'recordedApiCalls': len(calls) + len(bedroom_calls),
                      'recordedApiCallsByRoom': {'bedroom': len(bedroom_calls), 'living_room': len(calls)},
                      'aiReviewsReady': bool(reviews)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'evidence', 'native', 'rates', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--layoutgpt', type=Path, action='append', required=True)
    parser.add_argument('--expansion', type=Path)
    parser.add_argument('--controlled', type=Path, action='append', default=[])
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--layoutgpt-scale', type=Path)
    parser.add_argument('--bedroom-timing', type=Path)
    parser.add_argument('--infinigen-cloud', type=Path)
    compile_views(**vars(parser.parse_args()))


if __name__ == '__main__':
    main()
