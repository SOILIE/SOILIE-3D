"""Derive publication results from the audited, balanced four-condition cohort.

Never pool desktop and Lambda durations. Final-geometry repairs are timed
separately; they cannot silently inherit an earlier generation's stopwatch.
Private invocation receipts and infrastructure identifiers are not published.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path

from serverless.benchmark.geometry import measure, summarize
from serverless.benchmark.nonhuman import soilie_diagnostics, validity_rates
from serverless.benchmark.publish_comparison import (
    LABELS, METRICS, aggregate, compare, inventory_summary, write_support_evidence,
)
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.evidence import corrected_source, local_records, sha


def timing_conditions(sources):
    expected = {(platform, room): 2500 for platform in ('local', 'AWS Lambda')
                for room in ('bedroom', 'living_room')}
    if Counter((row['platform'], row['roomType']) for row in sources) != expected:
        raise ValueError('Exactly 2,500 observations per room/platform condition required')
    if len({row['seed'] for row in sources}) != 10000:
        raise ValueError('Repeated seed in publication cohort')
    result = []
    for platform, room in expected:
        records = [row for row in sources if (row['platform'], row['roomType']) == (platform, room)]
        seconds = [row['generationSeconds'] for row in records]
        correction = [row['correctionSeconds'] for row in records]
        if (any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in seconds)
                or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in correction)):
            raise ValueError('Invalid generation or correction timer')
        result.append({'platform': platform, 'roomType': room, 'completed': len(records),
            'completedLatencySeconds': summarize(seconds),
            'successfulGenerationSeconds': sum(seconds),
            'completedPerMinute': 60 * len(seconds) / sum(seconds),
            'downstreamCorrectionSeconds': summarize(correction),
            'correctedScenes': sum(value > 0 for value in correction),
            'stage': 'Initialization, selection, object placement and room construction; excludes image rendering, measurement and downstream contact correction.'})
    return result


def compile_publication(evidence, baseline, grid, campaign, output):
    cohort = json.loads((evidence / 'cohort.json').read_bytes())
    raw = (evidence / 'measured-scenes.json').read_bytes()
    if not cohort.get('complete') or cohort['soilieScenes'] != 10000 or sha(raw) != cohort['measurementsSha256']:
        raise ValueError('Final complete audited measurements required')
    # Validate the baseline provenance rather than trusting an arbitrary older
    # comparison document. Only baseline configuration and timing are retained.
    if sha((baseline / 'measured-scenes.json').read_bytes()) != cohort['baselineEvidenceSha256']:
        raise ValueError('Baseline measurement provenance differs')
    previous = json.loads((baseline / 'comparison.json').read_bytes())
    rows = json.loads(raw)['rows']
    groups = {model: [row for row in rows if row['scene']['model'] == model] for model in LABELS}
    if len(groups['soilie']) != 10000 or len({row['scene']['id'] for row in rows}) != len(rows):
        raise ValueError('Missing or repeated final scenes')
    conditions = timing_conditions(cohort['sources'])
    plan = json.loads((grid / 'plan.json').read_bytes())
    ledger = json.loads((grid / 'ledger.json').read_bytes())
    records = [corrected_source(grid, task, ledger['entries'][str(task['seed'])])[0]
               for task in plan['requests']]
    records.extend(row for row, _ in local_records(grid, campaign))
    final_metrics = {row['scene']['id']: row['metrics'] for row in groups['soilie']}
    if {row['id'] for row in records} != set(final_metrics):
        raise ValueError('Timing and final geometry refer to different cohorts')
    before_after, stages = [], {}
    for index, row in enumerate(records):
        measured = {'final': final_metrics[row['id']]}
        for stage in ('beforeSeparation', 'afterSeparation'):
            # Envelope intersections do not depend on units. Suppressing the
            # unrelated physical-clearance computation saves a second full pass.
            measured[stage] = measure({**row['stages'][stage], 'units': 'envelope-only'})
        stages[row['id']] = measured
        before_after.append({'id': row['id'], **{stage: value['meanWorstEnvelopeOverlapPct']
                                                for stage, value in measured.items()}})
        if (index + 1) % 1000 == 0:
            print(json.dumps({'stageObservations': index + 1}), flush=True)
    retained = ('layoutgptSources', 'layoutgptInvalidArtifacts', 'infinigenInvalidArtifacts',
        'infinigenConfiguration', 'infinigenControlledInvalidArtifacts',
        'infinigenControlledConfiguration', 'grainsAvailability')
    document = {key: deepcopy(previous[key]) for key in retained}
    models = {model: {'label': LABELS[model], 'n': len(group), 'metrics': aggregate(group),
                     'inventory': inventory_summary(group), 'validityRates': validity_rates(group)}
              for model, group in groups.items()}
    document.update(schemaVersion=3, metricDefinitions=METRICS, models=models,
        modelsByRoomType={room: {model: aggregate([row for row in group if row['scene']['roomType'] == room])
                                for model, group in groups.items()}
                          for room in ('bedroom', 'living_room')},
        comparisons=compare(rows), runs=conditions, invalidGeometry=[], beforeAfter=before_after,
        cohort={'completed': 10000, 'roomTypes': cohort['roomCounts'],
                'measurementsSha256': cohort['measurementsSha256'], 'modelVersion': '4.0.2',
                'selection': cohort['selection'],
                'sourceImplementations': list({json.dumps(source['implementation'], sort_keys=True):
                                              source['implementation'] for source in cohort['sources']}.values())},
        supportReplays={'matchedScenes': 10000, 'source': 'Audited final placements'},
        nonHumanDiagnostics=soilie_diagnostics(records, stage_metrics=stages), humanParticipants=0,
        timing={**{key: previous['timing'][key] for key in ('layoutgpt', 'infinigen', 'infinigenControlled', 'grains')},
                'soilieConditions': conditions,
                'hardware': previous['runs'][0]['sessionTiming']['hardwareSnapshots'],
                'interpretation': 'Four observed conditions, not a serial 10,000-room timing. Local runs shared a desktop; Lambda used separate 4 GB x86-64 workers. Waiting, geometry measurement and rendering are excluded. Downstream correction time is reported separately.'},
        selectionExplanation={'duplicatesAllowed': True, 'requestedCounts': [3,4,5,6],
            'description': 'Room presets draw compatible object lists with duplicates allowed. Windows and blinds can be consolidated by the model.'},
        method='Final placements, grouped by room type, exact furniture-instance count and 0.25-wide footprint-density bins. Shared groups receive equal weight; unshared outputs remain in native distributions. Auto-built and fixed-boundary rooms are different tasks.',
        illustrations=[row for row in previous['illustrations'] if row['model'] != 'soilie'])
    document['evidenceDigest'] = sha(json.dumps(document, sort_keys=True, separators=(',', ':')).encode())
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'comparison.json', document)
    write_support_evidence(rows, output)
    # Compact checksummed provenance stays separate from browser chart data.
    write_json(output / 'cohort-provenance.json', cohort)
    print(json.dumps({'publication': str(output), 'scenes': 10000, 'conditions': len(conditions)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence', 'baseline', 'grid', 'campaign', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    compile_publication(**vars(args))


if __name__ == '__main__':
    main()
