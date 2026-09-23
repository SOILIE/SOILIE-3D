"""Plain-language conclusions generated from the archived evidence."""
import statistics


BASELINE_LABELS = {
    'layoutgpt': 'LayoutGPT',
    'infinigen': 'Infinigen Indoors',
    'infinigen_controlled': 'Infinigen Indoors (controlled six-object task)',
}


def discussion(comparison, pilot):
    lines = ['# Room-generation comparison','',
             'This summary is generated from the archived measurements. It reports each measured question directly rather than combining unlike workloads into an overall model ranking.', '',
             '## Spatial quality','']
    for result in comparison.get('comparisons',[]):
        baseline = BASELINE_LABELS[result['baseline']]
        counts = result['counts']
        if not counts.get('soilie') or not counts.get(result['baseline']):
            continue
        lines += [f"SOILIE contributes {counts['soilie']} layouts and {baseline} contributes {counts[result['baseline']]} across {len(result['sharedStrata'])} matched workload groups. Each group fixes room type, furniture count, and a density interval; groups receive equal weight. These are similar completed workloads, not identical-input scene pairs.", '']
        for key,label in [('meanWorstSolidOverlapPct','Occupied mesh overlap'),('meanWorstEnvelopeOverlapPct','Object-envelope overlap'),('meanOutsideFootprintPct','Outside-room footprint')]:
            metric = result['metrics'][key]
            if metric['available']:
                a,b = metric['means']['soilie'],metric['means'][result['baseline']]
                direction = 'lower' if a < b else 'higher' if a > b else 'equal'
                lines += [f"- {label}: {a:.2f}% for SOILIE versus {b:.2f}% for {baseline}. SOILIE has {direction} measured intrusion in this shared subset."]
        lines += ['', 'Occupied mesh overlap uses exact evaluated solids when those solids are available. Object-envelope overlap is the separate cross-source diagnostic that remains possible for box-only releases. Outside-room footprint measures how much of an object’s plan-view area lies beyond the room.', '']
    stages = comparison.get('beforeAfter',[])
    if stages:
        averages = {key:statistics.fmean(v[key] for v in stages) for key in ('beforeSeparation','afterSeparation','final')}
        lines += [f"Across {len(stages)} completed SOILIE scenes, mean object overlap is {averages['beforeSeparation']:.2f}% before its separation step, {averages['afterSeparation']:.2f}% immediately afterward, and {averages['final']:.2f}% at final placement. Later placement steps can change the result; the final value is the one used for model comparisons.", '']
    runs = comparison.get('runs',[])
    attempted = sum(v['attempted'] for v in runs)
    completed = sum(v['completed'] for v in runs)
    if attempted:
        lines += [f"The measured SOILIE corpus contains {completed} completed layouts from {attempted} recorded generation attempts.", '']
    lines += ['## Generation speed','']
    timing = comparison.get('timing',{}).get('soilie',{})
    if timing.get('completedPerMinute'):
        latency = timing.get('completedLatencySeconds', {})
        detail = ''
        if latency.get('median') is not None and latency.get('p95') is not None:
            detail = f" Median completed-layout latency is {latency['median']:.2f} seconds and the 95th percentile is {latency['p95']:.2f} seconds."
        lines += [f"SOILIE produces {timing['completedPerMinute']:.2f} completed placements per successful-generation minute.{detail} Initialization is included; image rendering, later geometry measurement, and pauses between sessions are excluded.", '']
    lines += ['LayoutGPT’s released JSON does not supply inference duration. GRAINS timing is a published reference on different hardware and a different workload, not a same-machine speed test. No universal speed claim follows from these data.', '', '## AI visual pilot','']
    for row in pilot.get('conditions',[]) if pilot else []:
        name = BASELINE_LABELS[row['baseline']]
        votes = row['votes']
        lines += [f"For {row['distinctPairs']} distinct matched pairs, the ten prompt profiles per wave produced {votes.get('soilie',0)} SOILIE preferences, {votes.get(row['baseline'],0)} {name} preferences and {votes.get('tie',0)} ties. These are repeated AI judgments of spatial arrangements, not human validation.", '']
    lines += ['## What this means together','',
              'Low measured overlap is a useful geometric property, but it does not establish a convincing or functional room. Interpret geometry alongside failure rate and visual preferences, including results that do not favour SOILIE. Do not select additional scenes based on whether they improve the headline.', '',
              'A useful discussion hypothesis is that broader recorded room observations and better coverage of ordered object triplets could expand the range of supported arrangements. Targeted debugging might improve placement quality. Neither improvement has been demonstrated here. Test each on held-out scenes with fixed evaluation rules.', '',
              'The diversity cohort explores all existing input modes and uncommon catalog combinations. It remains outside the controlled bedroom throughput totals and model ranking. An empty preset is reported as an input-coverage limitation, not replaced with newly compiled data.', '']
    return '\n'.join(lines)
