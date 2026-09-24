"""Build room-stratified website evidence without rerunning geometry or models.

The expensive SOILIE stage audit is immutable input. New baseline exports can
be added independently; private API receipts never pass through to the site.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

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

ROOMS = ('bedroom', 'living_room')


def positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Finite positive timing required')
    return value


def model_summary(model, rows):
    return {'label': LABELS[model], 'n': len(rows), 'metrics': aggregate(rows),
            'inventory': inventory_summary(rows), 'validityRates': validity_rates(rows)}


def room_models(rows):
    return {room: {model: model_summary(model, [row for row in rows
                    if row['scene']['model'] == model and row['scene']['roomType'] == room])
                   for model in LABELS} for room in ROOMS}


def completed_calls(exports):
    """Require a closed batch; never silently omit rejected or uncertain calls."""
    calls, ids = [], set()
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
            if (attempt['status'] != 'complete' or attempt.get('geometryStatus') != 'complete'
                    or attempt.get('unparsedLines') != 0 or not attempt.get('countSatisfied')
                    or type(attempt['requestedObjects']) is not int or attempt['requestedObjects'] not in (3, 4, 5, 6)
                    or not scene or scene['roomType'] != 'living_room'
                    or scene['provenance']['model'] != 'gpt-4-0613'):
                raise ValueError('Cost cohort must account for every attempted living-room proposal')
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


def measured_cost(sources, calls, rates):
    validate_public_rate_card(rates)
    cloud = [row for row in sources if row['platform'] == 'AWS Lambda' and row['roomType'] == 'living_room']
    if len(cloud) != 2500:
        raise ValueError('Complete 2,500-room cloud living-room condition required')
    seconds = [positive(row['generationSeconds']) for row in cloud]
    prices = [token_charge(row['inputTokens'], row['outputTokens'], rates['gpt4']) for row in calls]
    soilie = summarize([worker_scenario(value, rates['lambda'])['usd'] for value in seconds])
    layout = summarize(prices)
    return {'schemaVersion': 2, 'currency': 'USD', 'roomType': 'living_room', 'rateCard': rates,
        'scope': 'One 3–6-object living-room proposal, excluding images, evaluation, orchestration and downstream contact correction.',
        'soilie': {'usd': soilie, 'seconds': summarize(seconds), 'memoryMb': 4096,
            'ephemeralStorageMb': 10240,
            'basis': 'Measured AWS Lambda generation-stage seconds priced at public 4 GB x86-64 compute, 10 GB temporary-storage and request rates. Not complete billed invocation time.'},
        'layoutgpt': {'usd': layout, 'inputTokens': summarize([row['inputTokens'] for row in calls]),
            'outputTokens': summarize([row['outputTokens'] for row in calls]),
            'models': sorted({row['model'] for row in calls}),
            'requestedObjectCounts': dict(sorted(Counter(row['requestedObjects'] for row in calls).items())),
            'basis': 'Recorded prompt and completion tokens from GPT-4 living-room calls, priced at public token rates. Four retrieved examples and an explicit 3–6-object instruction.'},
        'freeTier': {'monthlyLayouts': 600, 'accountUsageIncluded': False,
            'assumptions': 'One invocation per room, using the observed generation-stage mean as total billed duration; no retries or other account usage. Extra invocation overhead would increase the estimate.',
            'allowanceAvailable': monthly_lambda_budget(600, summarize(seconds)['mean'], 4096, 10240,
                                                       rates['lambda'], 400000, 1000000),
            'allowanceExhausted': monthly_lambda_budget(600, summarize(seconds)['mean'], 4096, 10240, rates['lambda'])},
        'limitations': ['Generation-stage compute is not a complete hosted-service bill.',
            'Different room requests and model outputs: this comparison prices generation, not equal quality.',
            'Prices exclude credits, taxes and discounts; a different LLM requires its own quality evaluation.']}


def compile_views(base, evidence, layoutgpt, native, rates, output):
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
    # Original bedroom outputs and the controlled living-room calls are separate
    # strata, not a synthetic single inference batch. Selection never uses scores.
    rows = [row for row in json.loads(raw)['rows'] if not
            (row['scene']['model'] == 'layoutgpt' and row['scene']['roomType'] == 'living_room')]
    rows.extend(row for export in exports for row in export['rows'])
    if len({row['scene']['id'] for row in rows}) != len(rows):
        raise ValueError('Repeated final geometry')
    document.update(schemaVersion=4,
        models={model: model_summary(model, [row for row in rows if row['scene']['model'] == model]) for model in LABELS},
        modelsByRoomType=room_models(rows), comparisons=compare(rows),
        layoutgptSources={'bedroom': 'Official released GPT-4 layouts, eight retrieved examples.',
            'living_room': 'Recorded GPT-4 calls, four retrieved examples, requested counts cycling 3–6.',
            'exportSha256': [sha(path.read_bytes()) for path in layoutgpt]},
        cost=measured_cost(cohort['sources'], calls, json.loads(rates.read_bytes())),
        # A deliberate gate: final review exports must name this geometry cohort.
        # Do not pair newly measured rooms with earlier website judgement totals.
        aiReview={'ready': False, 'cohortSha256': cohort['measurementsSha256']},
        timing={**document['timing'], 'soilieConditions': conditions,
            'infinigenByRoomType': native_timing(json.loads(native.read_bytes())),
            'layoutgptByRoomType': {'living_room': {**latency([row['seconds'] for row in calls]),
                'stage': 'API request to complete response; includes network and provider queue time. Excludes prompt retrieval, parsing and image rendering.'}}})
    # Selected extreme diagrams were made for the original baseline corpus;
    # retain only those whose source model/corpus has not changed.
    document['illustrations'] = [row for row in document['illustrations'] if row['model'] != 'layoutgpt']
    document['evidenceDigest'] = sha(json.dumps(document, sort_keys=True, separators=(',', ':')).encode())
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'comparison.json', document)
    write_support_evidence(rows, output)
    write_json(output / 'publication-inputs.json', {'schemaVersion': 1,
        'cohortSha256': cohort['measurementsSha256'], 'baseSha256': sha(base.read_bytes()),
        'layoutgptSha256': [sha(path.read_bytes()) for path in layoutgpt], 'nativeSha256': sha(native.read_bytes()),
        'ratesSha256': sha(rates.read_bytes()), 'aiReviewsReady': False})
    print(json.dumps({'models': {key: value['n'] for key, value in document['models'].items()},
                      'recordedApiCalls': len(calls), 'aiReviewsReady': False}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'evidence', 'native', 'rates', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--layoutgpt', type=Path, action='append', required=True)
    compile_views(**vars(parser.parse_args()))


if __name__ == '__main__':
    main()
