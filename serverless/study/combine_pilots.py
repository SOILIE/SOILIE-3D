"""Combine frozen AI-only waves, using scene pairs, not votes, as clusters."""
from collections import Counter, defaultdict
import random
import statistics


def combine(reports, seed=20260914, resamples=10000):
    versions, used_scenes, cases = set(), set(), []
    profiles = defaultdict(Counter)
    repeats = agreements = sessions = 0
    for report in reports:
        version = report['studyVersion']
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
                identity = (baseline,side,evidence[case][side])
                if identity in used_scenes:
                    raise ValueError('Scene reuse across waves would invalidate pair-level aggregation')
                used_scenes.add(identity)
            votes = Counter()
            for row in rows:
                choice = 'tie' if row['judgement'] == 'tie' else row[row['judgement']+'Condition']
                votes[choice] += 1
                profiles[(baseline,row['promptProfile'])][choice] += 1
            cases.append({'studyVersion':version,'caseId':case,'baseline':baseline,'votes':dict(votes),
                          'soiliePreferenceShareIncludingHalfTies':(votes['soilie']+.5*votes['tie'])/10})
        sessions += len(report['reviewers'])
        repeats += sum(row['repeatComparisons'] for row in report['reviewers'])
        agreements += sum(row['agreements'] for row in report['reviewers'])
    conditions = []
    for baseline in sorted({row['baseline'] for row in cases}):
        selected = [row for row in cases if row['baseline'] == baseline]
        votes = sum((Counter(row['votes']) for row in selected),Counter())
        values = [row['soiliePreferenceShareIncludingHalfTies'] for row in selected]
        randomizer = random.Random(f'{seed}:{baseline}')
        samples = sorted(statistics.fmean(randomizer.choices(values,k=len(values))) for _ in range(resamples))
        conditions.append({'baseline':baseline,'distinctPairs':len(values),'votes':dict(votes),
                           'soiliePreferenceShareIncludingHalfTies':statistics.fmean(values),
                           'pairBootstrap95PctInterval':[samples[int(.025*(resamples-1))],samples[int(.975*(resamples-1))]],
                           'method':'Percentile bootstrap over whole scene pairs, retaining all ten responses together. Descriptive uncertainty for these sampled cases, not calibrated reviewer accuracy or a confirmatory population test.'})
    return {'schemaVersion':1,'respondentType':'ai_pilot','humanParticipants':0,'studyVersions':sorted(versions),
            'reviewerSessions':sessions,'promptProfilesPerWave':10,'mainJudgements':len(cases)*10,
            'reversedControls':repeats,'consistentReversedControls':agreements,'conditions':conditions,
            'byPromptFocus':[dict(baseline=key[0],profile=key[1],votes=dict(votes)) for key,votes in sorted(profiles.items())],
            'pairs':cases,'bootstrapSeed':seed,'bootstrapResamples':resamples,
            'limitations':'Fresh contexts can share one underlying model. Neither different prompts nor consistent repeats establish correctness. Waves are not independent human reviewers. Never treat all votes as independent scene samples.'}
