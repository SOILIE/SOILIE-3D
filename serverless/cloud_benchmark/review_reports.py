"""Assemble verified focused reviews without rewriting their source records.

Private study sessions are read-only inputs. Public case IDs are namespaced to
avoid collisions across protocols; original IDs and versions remain auditable.
Preview exports can inspect completed subsets, but cannot certify final coverage.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from serverless.benchmark.stimuli import digest
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.review_work import audit_protocol, selected_assignment_ids
from serverless.cloud_benchmark.reviews import PLAN
from serverless.study.export_pilot import aggregate, focused_dimension_results, public_summary
from serverless.study.store import SQLiteStudyStore

ROOMS = ('bedroom', 'living_room')
FILES = {'layoutgpt': 'ai-pilot', 'infinigen_controlled': 'ai-pilot-infinigen'}


class SelectedStore:
    """Read-only projection of selected cases and their repeated presentations."""
    def __init__(self, store, protocol, room=None):
        self.store, self.protocol, self.room = store, protocol, room
        self.allowed = {}

    def sessions(self):
        for session in self.store.sessions():
            if (session.get('studyVersion') != self.protocol['studyVersion']
                    or session.get('respondentType') != 'ai_pilot'):
                continue
            selected = selected_assignment_ids(self.protocol, session['assignments'], self.room)
            self.allowed[session['sessionId']] = selected
            yield {**session, 'assignments': [row for row in session['assignments'] if row['caseId'] in selected]}

    def responses(self, session_id):
        return [row for row in self.store.responses(session_id) if row['caseId'] in self.allowed[session_id]]


def source_report(root, name, scenes, room=None):
    original = audit_protocol(root, name, scenes)
    protocol = deepcopy(original)
    if room:
        selected = {row['caseId'] for row in protocol['stimulusEvidence'] if row['matchingStratum'][0] == room}
        protocol['cases'] = [row for row in protocol['cases'] if row['id'] in selected]
        protocol['stimulusEvidence'] = [row for row in protocol['stimulusEvidence'] if row['caseId'] in selected]
    database = root / name / 'private/pilot.sqlite3'
    if not database.is_file():
        raise ValueError('Missing recorded review database')
    store = SQLiteStudyStore(database)
    report = aggregate(SelectedStore(store, original, room), protocol)
    report['sourceProtocolSha256'] = hashlib.sha256((root / name / 'protocol.json').read_bytes()).hexdigest()
    return report


def combine_focused(reports, cohort_sha, *, target_per_room=120, preview=False):
    """Combine only complete selected tasks with identical questions and model.

    Repeats remain consistency evidence, never extra preference votes. Scene
    reuse is rejected within a baseline comparison, including across protocols.
    Each dimension is recomputed from individual saved votes, not averaged CIs.
    """
    if not reports:
        raise ValueError('No completed focused review reports')
    result = deepcopy(reports[0])
    result.update(responses=[], stimuli=[], stimulusEvidence=[], reviewers=[])
    versions, used_scenes, reviewer_rows, sources = set(), set(), {}, []
    baseline = None
    stable_reviewer = ('profile', 'model', 'reportedModel', 'reportedReasoningEffort',
                       'promptHash', 'reviewPrompt', 'decisionRubric', 'dimensionRubric',
                       'evidenceRubric', 'interfaceEmphasis')
    matching_rules = ('minimumSemanticSimilarity', 'maximumFurnitureDensityDifference',
                      'semanticFamilyPolicy', 'qualityScoresUsed')
    for report in reports:
        version = report['studyVersion']
        if (version in versions or report.get('decisionScope') != 'focus_only'
                or report.get('evidenceMode') != 'visual_only'
                or report.get('respondentType') != 'ai_pilot' or report.get('humanParticipants') != 0
                or report.get('reviewersCompleted') != 10
                or report.get('reviewerConfiguration') != {'model': 'GPT-5.6 Sol', 'reasoningEffort': 'Extra High'}):
            raise ValueError('Distinct, complete ten-reviewer focused AI tasks required')
        versions.add(version)
        if any(report['sampling'].get(key) != reports[0]['sampling'].get(key) for key in matching_rules):
            raise ValueError('Matching rules differ across selected tasks')
        roster = {row['reviewerId']: row for row in report['reviewers']}
        expected_roster = {f'reviewer-{i+1:02d}': profile for i, profile in enumerate(PLAN)}
        if len(report['reviewers']) != 10 or {key: row['profile'] for key, row in roster.items()} != expected_roster:
            raise ValueError('The registered two-reviewers-per-dimension roster must be complete')
        case_ids = {row['caseId'] for row in report['stimuli']}
        evidence = {row['caseId']: row for row in report['stimulusEvidence']}
        if not case_ids or len(case_ids) != len(report['stimuli']) or set(evidence) != case_ids:
            raise ValueError('Missing or duplicated stimulus evidence')
        if len(evidence) != len(report['stimulusEvidence']):
            raise ValueError('Repeated stimulus evidence')
        by_case = {key: [] for key in case_ids}
        seen_responses = set()
        for row in report['responses']:
            key = (row['reviewerId'], row['caseId'])
            reviewer = roster.get(row['reviewerId'])
            if (key in seen_responses or not reviewer or row['studyVersion'] != version
                    or row['promptProfile'] != reviewer['profile'] or row['promptHash'] != reviewer['promptHash']
                    or row.get('respondentType') != 'ai_pilot' or row.get('evidenceMode') != 'visual_only'):
                raise ValueError('Duplicate or inconsistent response provenance')
            seen_responses.add(key)
            if row['repeatOf'] is None:
                if row['caseId'] not in by_case:
                    raise ValueError('Response outside selected cases')
                by_case[row['caseId']].append(row)
            elif row['repeatOf'] not in case_ids:
                raise ValueError('Repeat outside selected cases')
        for key, rows in by_case.items():
            if len(rows) != 10 or {row['reviewerId'] for row in rows} != set(roster):
                raise ValueError('Every selected pair requires all ten saved judgements')
        prefix = digest(version)[:20] + ':'
        for stimulus in report['stimuli']:
            condition = stimulus['baseline']
            if condition not in FILES or (baseline is not None and condition != baseline):
                raise ValueError('Do not pool distinct baseline comparisons')
            baseline = condition
            entry = evidence[stimulus['caseId']]
            if entry['matchingStratum'][0] not in ROOMS:
                raise ValueError('Missing frozen room type')
            for side in ('soilieScene', 'baselineScene'):
                identity = (side, entry[side])
                if identity in used_scenes:
                    raise ValueError('Scene reused across selected pairs')
                used_scenes.add(identity)
            result['stimuli'].append({**stimulus, 'caseId': prefix + stimulus['caseId'],
                                      'sourceCaseId': stimulus['caseId'], 'sourceStudyVersion': version})
            result['stimulusEvidence'].append({**entry, 'caseId': prefix + entry['caseId'],
                                              'sourceCaseId': entry['caseId'], 'sourceStudyVersion': version})
        for row in report['responses']:
            if row['comparisonCondition'] != baseline or {row['leftCondition'], row['rightCondition']} != {'soilie', baseline}:
                raise ValueError('Response side assignment has the wrong comparison')
            result['responses'].append({**row, 'sourceCaseId': row['caseId'], 'sourceRepeatOf': row['repeatOf'],
                'caseId': prefix + row['caseId'], 'repeatOf': prefix + row['repeatOf'] if row['repeatOf'] else None})
        for identity, reviewer in roster.items():
            if not reviewer['complete'] or hashlib.sha256(reviewer['reviewPrompt'].encode()).hexdigest() != reviewer['promptHash']:
                raise ValueError('Incomplete reviewer or changed prompt')
            if identity not in reviewer_rows:
                reviewer_rows[identity] = {**deepcopy(reviewer), 'responses': 0, 'votes': {},
                    'repeatComparisons': 0, 'agreements': 0, 'sourceStudyVersions': []}
            combined = reviewer_rows[identity]
            if any(combined[key] != reviewer[key] for key in stable_reviewer):
                raise ValueError('Reviewer configuration or exact prompt differs across tasks')
            for key in ('responses', 'repeatComparisons', 'agreements'):
                combined[key] += reviewer[key]
            combined['votes'] = dict(Counter(combined['votes']) + Counter(reviewer['votes']))
            combined['sourceStudyVersions'].append(version)
        sources.append({'studyVersion': version, 'protocolSha256': report['sourceProtocolSha256'],
                        'selectedCaseIds': sorted(case_ids), 'sampling': report['sampling']})
    counts = Counter(row['matchingStratum'][0] for row in result['stimulusEvidence'])
    complete = counts == {room: target_per_room for room in ROOMS}
    if not preview and not complete:
        raise ValueError(f'Final coverage requires {target_per_room} pairs per room type; found {dict(counts)}')
    result['reviewers'] = [reviewer_rows[key] for key in sorted(reviewer_rows)]
    protocol = {'decisionScope': 'focus_only', 'reviewerPlan': PLAN,
                'cases': [{'id': row['caseId'], 'comparisonCondition': baseline} for row in result['stimuli']],
                'stimulusEvidence': result['stimulusEvidence']}
    result.update(schemaVersion=2, studyVersion='focused-composite-' + digest(sources)[:20],
        cohortSha256=cohort_sha, sourceStudies=sources, complete=complete,
        releaseEligible=complete and not preview,
        roomTypePairs={room: counts[room] for room in ROOMS},
        sampling={'selection': 'Disjoint frozen scene pairs; source sampling rules retained in sourceStudies.'},
        dimensionResults=focused_dimension_results(protocol, result['responses'], result['reviewers']),
        dimensionsByRoomType={room: focused_dimension_results(protocol, result['responses'], result['reviewers'], room) for room in ROOMS},
        stimulusVersionDigest=digest(result['stimuli']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('evidence', 'original', 'extension', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--extra-export', type=Path, action='append', default=[])
    parser.add_argument('--additional', type=Path, action='append', default=[])
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    cohort = json.loads((args.evidence / 'cohort.json').read_bytes())
    raw = (args.evidence / 'measured-scenes.json').read_bytes()
    if not cohort['complete'] or hashlib.sha256(raw).hexdigest() != cohort['measurementsSha256']:
        raise ValueError('Complete audited geometry cohort required')
    scenes = {row['scene']['id']: row['scene'] for row in json.loads(raw)['rows']}
    for path in args.extra_export:
        for scene in json.loads(path.read_bytes())['scenes']:
            if scene['id'] in scenes and digest(scenes[scene['id']]) != digest(scene):
                raise ValueError('Conflicting source geometry')
            scenes[scene['id']] = scene
    args.output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for name, baseline in (('set-a', 'layoutgpt'), ('set-b', 'infinigen_controlled')):
        sources = [(args.original, 'bedroom' if name == 'set-a' else None), (args.extension, None)]
        sources += [(root, None) for root in args.additional if (root / name / 'protocol.json').exists()]
        selected_reports = [source_report(root, name, scenes, room) for root, room in sources]
        report = combine_focused(selected_reports,
                                 cohort['measurementsSha256'], preview=args.preview)
        stem = FILES[baseline]
        write_json(args.output / (stem + '-responses.json'), report)
        write_json(args.output / (stem + '-summary.json'), public_summary(report, stem + '-responses.json'))
        # The public example viewer needs only the selected, content-addressed
        # SVGs. Never copy private sessions or reviewer working directories.
        image_folder = args.output / 'stimuli'
        image_folder.mkdir(exist_ok=True)
        for (root, _room), selected in zip(sources, selected_reports):
            for stimulus in selected['stimuli']:
                for field in ('soilieImage', 'baselineImage'):
                    url = stimulus[field]
                    source = root / 'site' / url.lstrip('/')
                    destination = image_folder / Path(url).name
                    if not destination.exists():
                        shutil.copyfile(source, destination)
                    if hashlib.sha256(destination.read_bytes()).hexdigest()[:24] != destination.stem:
                        raise ValueError('Copied stimulus checksum differs')
        print(json.dumps({'baseline': baseline, 'pairs': report['roomTypePairs'],
                          'savedResponsesIncludingRepeats': len(report['responses']),
                          'releaseEligible': report['releaseEligible']}), flush=True)
        artifacts[baseline] = {'pairs': report['roomTypePairs'], 'releaseEligible': report['releaseEligible'],
            'files': {stem + suffix: hashlib.sha256((args.output / (stem + suffix)).read_bytes()).hexdigest()
                      for suffix in ('-responses.json', '-summary.json')}}
    write_json(args.output / 'review-manifest.json', {'schemaVersion': 1,
        'cohortSha256': cohort['measurementsSha256'], 'comparisons': artifacts,
        'releaseEligible': all(item['releaseEligible'] for item in artifacts.values())})


if __name__ == '__main__': main()
