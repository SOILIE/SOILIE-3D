"""Combine frozen AI-only waves, using scene pairs, not votes, as clusters."""
from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path
import random
import statistics


def combine(reports, seed=20260914, resamples=10000):
    versions, used_scenes, cases = set(), set(), []
    profiles = defaultdict(Counter)
    controls = defaultdict(Counter)
    repeats = agreements = sessions = 0
    for report in reports:
        version = report['studyVersion']
        evidence_mode = report.get('evidenceMode','visual_only')
        if version in versions or report['respondentType'] != 'ai_pilot' or report['humanParticipants'] != 0 or report['reviewersCompleted'] != 10:
            raise ValueError('Only distinct, completed ten-reviewer AI waves can be combined')
        versions.add(version)
        evidence = {v['caseId']:v for v in report['stimulusEvidence']}
        responses = defaultdict(list)
        for row in report['responses']:
            if row['repeatOf'] is None:
                responses[row['caseId']].append(row)
        if set(responses) != set(evidence):
            raise ValueError('Missing or unexpected reviewed pair')
        for case,rows in responses.items():
            if len(rows) != 10 or len({v['reviewerId'] for v in rows}) != 10:
                raise ValueError('Each pair requires ten distinct reviewer sessions')
            baseline = rows[0]['comparisonCondition']
            for side in ('soilieScene','baselineScene'):
                identity = (evidence_mode,baseline,side,evidence[case][side])
                if identity in used_scenes:
                    raise ValueError('Scene reuse across waves would invalidate pair-level aggregation')
                used_scenes.add(identity)
            votes = Counter()
            for row in rows:
                choice = 'tie' if row['judgement'] == 'tie' else row[row['judgement']+'Condition']
                votes[choice] += 1
                profiles[(evidence_mode,baseline,row['promptProfile'])][choice] += 1
            cases.append({'studyVersion':version,'caseId':case,'evidenceMode':evidence_mode,'baseline':baseline,'votes':dict(votes),
                          'soiliePreferenceShareIncludingHalfTies':(votes['soilie']+.5*votes['tie'])/10})
        sessions += len(report['reviewers'])
        wave_repeats = sum(row['repeatComparisons'] for row in report['reviewers'])
        wave_agreements = sum(row['agreements'] for row in report['reviewers'])
        repeats += wave_repeats
        agreements += wave_agreements
        baselines = {row['comparisonCondition'] for row in report['responses']}
        if len(baselines) == 1:
            controls[(evidence_mode, next(iter(baselines)))].update(
                reversedControls=wave_repeats, consistentReversedControls=wave_agreements)
    conditions = []
    for evidence_mode,baseline in sorted({(row['evidenceMode'],row['baseline']) for row in cases}):
        selected = [row for row in cases if row['baseline'] == baseline and row['evidenceMode'] == evidence_mode]
        votes = sum((Counter(row['votes']) for row in selected),Counter())
        values = [row['soiliePreferenceShareIncludingHalfTies'] for row in selected]
        randomizer = random.Random(f'{seed}:{evidence_mode}:{baseline}')
        samples = sorted(statistics.fmean(randomizer.choices(values,k=len(values))) for _ in range(resamples))
        condition_controls = controls[(evidence_mode, baseline)]
        conditions.append({'evidenceMode':evidence_mode,'baseline':baseline,'distinctPairs':len(values),'votes':dict(votes),
                           'soiliePreferenceShareIncludingHalfTies':statistics.fmean(values),
                           'pairBootstrap95PctInterval':[samples[int(.025*(resamples-1))],samples[int(.975*(resamples-1))]],
                           'reversedControls':condition_controls['reversedControls'],
                           'consistentReversedControls':condition_controls['consistentReversedControls'],
                           'method':'Percentile bootstrap over whole scene pairs, retaining all ten responses together. Descriptive uncertainty for these sampled cases, not calibrated reviewer accuracy or a confirmatory population test.'})
    shared_sampling = reports[0].get('sampling') if reports and all(
        report.get('sampling') == reports[0].get('sampling') for report in reports) else None
    return {'schemaVersion':1,'respondentType':'ai_pilot','humanParticipants':0,'studyVersions':sorted(versions),
            'reviewerSessions':sessions,'promptProfilesPerWave':10,'mainJudgements':len(cases)*10,
            'reversedControls':repeats,'consistentReversedControls':agreements,'conditions':conditions,
            'byPromptFocus':[dict(evidenceMode=key[0],baseline=key[1],profile=key[2],votes=dict(votes)) for key,votes in sorted(profiles.items())],
            'pairs':cases,'sampling':shared_sampling,'bootstrapSeed':seed,'bootstrapResamples':resamples,
            'limitations':'Fresh contexts can share one underlying model. Neither different prompts nor consistent repeats establish correctness. Waves are not independent human reviewers. Never treat all votes as independent scene samples.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reports',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    summary = combine([json.loads(path.read_bytes()) for path in args.reports])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(summary,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps({'waves':len(summary['studyVersions']),'judgements':summary['mainJudgements']}))


if __name__ == '__main__':
    main()
