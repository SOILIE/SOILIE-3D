"""Plain-language checkpoint conclusions generated from evidence, not slogans."""
import statistics


def discussion(comparison, pilot):
    lines = ['# Room-generation comparison: interim findings','',
             'This is an unfinished benchmark checkpoint, not a final model ranking. All statements below refer to the archived measurements.', '',
             '## Spatial quality','']
    for result in comparison.get('comparisons',[]):
        baseline = {'layoutgpt':'LayoutGPT','infinigen':'Infinigen Indoors'}[result['baseline']]
        counts = result['counts']
        if not counts.get('soilie') or not counts.get(result['baseline']):
            continue
        lines += [f"SOILIE contributes {counts['soilie']} layouts and {baseline} contributes {counts[result['baseline']]} across {len(result['sharedStrata'])} shared room-type, furniture-count and density groups. Each group has equal weight; these are not identical-input scene pairs.", '']
        for key,label in [('meanWorstOverlapPct','Object overlap'),('meanOutsideFootprintPct','Outside-room footprint')]:
            metric = result['metrics'][key]
            if metric['available']:
                a,b = metric['means']['soilie'],metric['means'][result['baseline']]
                direction = 'lower' if a < b else 'higher' if a > b else 'equal'
                lines += [f"- {label}: {a:.2f}% for SOILIE versus {b:.2f}% for {baseline}. SOILIE has {direction} measured intrusion in this shared subset."]
        lines += ['', 'Overlap is each object’s largest intersection with another object divided by its own box volume, averaged within scenes and then across groups. It is a bounding-box proxy, not solid-mesh collision. Outside-room footprint measures how much of an object’s plan-view area lies beyond the room.', '']
    stages = comparison.get('beforeAfter',[])
    if stages:
        averages = {key:statistics.fmean(v[key] for v in stages) for key in ('beforeSeparation','afterSeparation','final')}
        lines += [f"Across {len(stages)} completed SOILIE scenes, mean object overlap is {averages['beforeSeparation']:.2f}% before its separation step, {averages['afterSeparation']:.2f}% immediately afterward, and {averages['final']:.2f}% at final placement. Later placement steps can change the result; the final value is the one used for model comparisons.", '']
    runs = comparison.get('runs',[])
    attempted = sum(v['attempted'] for v in runs)
    completed = sum(v['completed'] for v in runs)
    if attempted:
        lines += [f"Reliability is a separate limitation: {completed}/{attempted} attempts completed, while {attempted-completed} failed or timed out. Successful-scene geometry alone does not describe this failure rate.", '']
    lines += ['## Generation speed','']
    timing = comparison.get('timing',{}).get('soilie',{})
    if timing.get('completedPerMinute'):
        lines += [f"The checkpoint produces {timing['completedPerMinute']:.2f} completed placements per active generation minute, counting unsuccessful attempt time. Initialization is included; image generation and observation overhead are excluded. The 10,000-bedroom campaign is not yet complete. Pauses are not compute time.", '']
    lines += ['LayoutGPT’s released JSON does not supply inference duration. GRAINS timing is a published reference on different hardware and a different workload, not a same-machine speed test. No universal faster-or-cheaper claim follows from these data.', '', '## AI visual pilot','']
    for row in pilot.get('conditions',[]) if pilot else []:
        name = {'layoutgpt':'LayoutGPT','infinigen':'Infinigen Indoors'}[row['baseline']]
        votes = row['votes']
        lines += [f"For {row['distinctPairs']} distinct matched pairs, the ten prompt profiles per wave produced {votes.get('soilie',0)} SOILIE preferences, {votes.get(row['baseline'],0)} {name} preferences and {votes.get('tie',0)} ties. These are repeated AI judgments of spatial arrangements, not human validation.", '']
    lines += ['## What this means together','',
              'Low measured overlap is a useful geometric property, but it does not establish a convincing or functional room. Interpret geometry alongside failure rate and visual preferences, including results that do not favour SOILIE. Do not select additional scenes based on whether they improve the headline.', '',
              'A useful discussion hypothesis is that broader recorded room observations and better coverage of ordered object triplets could expand the range of supported arrangements. Targeted debugging might improve completion or placement quality. Neither improvement has been demonstrated here. Test each on held-out scenes with fixed evaluation rules, and report the revised model separately from this publication-branch baseline.', '',
              'The diversity cohort explores all existing input modes and uncommon catalog combinations. It remains outside the controlled bedroom throughput totals and model ranking. An empty preset is reported as an input-coverage limitation, not replaced with newly compiled data.', '']
    return '\n'.join(lines)
