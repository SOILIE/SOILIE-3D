"""Compile the frozen full counterbalanced review, without collecting new answers.

Public exports are allowlisted. Provider transcripts, credentials and local paths
stay private. Opposite-side responses are combined once per pair and dimension;
win-plus-tie is directional support, never described as unanimous agreement.
"""
import argparse
from collections import Counter
from pathlib import Path

from serverless.cloud_benchmark.full_review import results
from serverless.cloud_benchmark.staged_pilot import read, load_frozen, digest, delivery_prompt
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.benchmark.archive import packed

PROFILES = ('orientation', 'proportions', 'relationships', 'access', 'room_function')
BASELINES = {'layoutgpt': ('LayoutGPT', 'ai-pilot'), 'infinigen_controlled': ('Infinigen · controlled', 'ai-pilot-infinigen')}


def response_digest(root):
    return digest({p.name:digest(p.read_bytes()) for p in sorted((Path(root)/'responses').glob('*.json'))})


def audit_executions(root):
    """Prove exports match isolated completed calls, not just response metadata."""
    from serverless.cloud_benchmark.retain_identical_reviews import verify_execution
    from serverless.cloud_benchmark.run_staged_pilot import preflight
    root=Path(root); protocol=load_frozen(root)
    report=results(root)
    if not report['complete']: raise ValueError('Incomplete collection')
    retained={r['assignmentId']:r for r in read(root/'retention-report.json')['records']}
    old=Path(read(root/'private/retained-execution-source.json')['sourceRoot'])
    previous=load_frozen(old)
    for n,path in enumerate(sorted((root/'responses').glob('*.json')),1):
        row=read(path); location=root; frozen=protocol
        if path.stem in retained:
            location=old; frozen=previous
            row=read(old/'responses'/(retained[path.stem]['sourceAssignmentId']+'.json'))
        verify_execution(location,frozen,row)
        execution=location/'execution'/row['assignmentId']
        reviewer=next(r for r in frozen['reviewers'] if r['reviewerId']==row['reviewerId'])
        if ((execution/'system.txt').read_text(encoding='utf-8')!=reviewer['systemPrompt']
            or read(execution/'schema.json')!=frozen['outputSchema']):
            raise ValueError('Delivered system/schema differs')
        if n%400==0: print({'verified':n},flush=True)
    pixels=preflight(root)
    audit={'passed':True,'protocolSha256':digest(protocol),'verifiedExecutions':report['responses'],
        'newExecutions':report['responses']-len(retained),'identicalRetainedExecutions':len(retained),
        'isolatedContexts':report['responses'],'pixelIdenticalSwaps':pixels['pixelIdenticalSwaps'],
        'completeChecklistCoverage':True,'sourceImages':len(protocol['sourceImages']),
        'responsesSha256':response_digest(root),'packetIndexSha256':pixels['packetIndexSha256']}
    write_json(root/'release-audit.json',audit)
    return audit


def counts(pairs):
    kinds = Counter(p['bucket'] for p in pairs)
    exact = sum(p['subtype'] in ('win_win', 'same_tie') for p in pairs)
    sub = Counter((p['bucket'], p['subtype']) for p in pairs)
    return {'pairs': len(pairs), 'responses': 2 * len(pairs),
            **{k: kinds[v] for k, v in [('soilie', 'model_a'), ('baseline', 'model_b'), ('tie', 'tie'), ('disagreement', 'disagreement')]},
            'subtypes': {k: {'bothPrefer': sub[v, 'win_win'], 'preferenceAndTie': sub[v, 'win_tie']}
                         for k, v in [('soilie', 'model_a'), ('baseline', 'model_b')]},
            'exactAgreements': exact, 'tiePreferenceChanges': sum(p['subtype'] == 'win_tie' for p in pairs),
            'oppositeWinners': kinds['disagreement']}


def compile_release(root, output):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    protocol, report = load_frozen(root), results(root)
    if not report['complete'] or report['responses'] != 4800 or len(report['pairs']) != 2400:
        raise ValueError('The complete frozen 480-pair review is required')
    audit = read(root / 'release-audit.json')
    if not audit['passed'] or audit['protocolSha256'] != report['protocolSha256'] or audit['verifiedExecutions'] != 4800:
        raise ValueError('Full execution and pixel audit is required')
    if audit.get('responsesSha256') != response_digest(root):
        raise ValueError('Responses changed since execution audit')
    responses = {f.stem: read(f) for f in (root / 'responses').glob('*.json')}
    packets = read(root / 'packets/index.json')
    image_prefix = '/data/files/outputs/website-comparisons/review/' + report['protocolSha256'][:16] + '/'
    matching = read(Path(protocol['sourceRoot']) / 'matching.json')['pairs']
    methods = {
        'pairing': 'Same room type, instance count and bed/sofa count; normalized category inventories differ by at most one substitution. Pairing does not consult preferences or quality scores.',
        'density': 'Density is the sum of individual furniture footprint areas divided by floor area (overlap counted separately). The absolute between-room density difference is at most 0.25 for LayoutGPT and 1.0 for Infinigen; these are not identical-density workloads.',
        'selection': 'Baseline inventories were requested to match available SOILIE inventories. Results describe this selected sample, not unrestricted generator output.',
        'evidence': 'Each isolated judgment receives one paired image with plan, oblique and bird’s-eye box views, a dimension-specific prompt, and structured case evidence. Non-size tasks receive computed geometry facts, including distances and potential obstructions. Size receives all within-room volume ratios and an illustrative sourced size-reference catalog.',
        'aggregation': 'For each question, two independent contexts see opposite left/right orders. Same winner or winner plus tie supports that model; two ties form Tie; opposite winners form Disagreement. Every pair contributes once. Win plus tie is not unanimous agreement.',
        'limits': 'One AI model, not human validation. Geometry hints and supplied reference values can influence judgments. Shared SOILIE rooms make pairs dependent; counts are descriptive, not a population confidence interval or an overall model ranking.',
        'inventoryCounts': dict(Counter(str(p['substitutions']) for p in matching)),
        'uniqueScenes': len({v['sceneId'] for v in protocol['sceneProvenance'].values()}),
        'uniqueSoilieScenes': len({v['sceneId'] for k,v in protocol['sceneProvenance'].items() if k.endswith(':soilie')}),
        'referenceCatalog': protocol['referenceCatalog'], 'referenceCatalogSha256': protocol['referenceCatalogSha256'],
    }
    examples = []
    hashes = {}
    for baseline, (label, stem) in BASELINES.items():
        pairs = [p for p in report['pairs'] if p['baseline'] == baseline]
        selected_ids = {room: min(p['pairId'] for p in pairs if p['roomType'] == room) for room in ('bedroom', 'living_room')}
        public_rows = []
        for row in responses.values():
            if row['baseline'] != baseline:
                continue
            packet = packets[row['assignmentId']]
            public_rows.append({k: row[k] for k in ('assignmentId','reviewerId','profile','pairId','baseline','roomType','leftCondition','rightCondition','presentation','pairedWith','evidenceSha256','deliveryPromptHash','judgement','errorChoice','confidence','note','observations','promptHash','rubricVersion')}
                               | {'image': image_prefix + packet['file'], 'imageSha256': packet['imageSha256']})
        reviewers = []
        for reviewer in protocol['reviewers']:
            sample = responses[next(r['assignmentId'] for r in protocol['assignments'] if r['baseline'] == baseline and r['reviewerId'] == reviewer['reviewerId'])]
            dimension_pairs = [p for p in pairs if p['profile'] == reviewer['profile']]
            reviewers.append(reviewer | {'exampleDelivery': {'prompt': delivery_prompt(protocol, sample), 'promptHash': sample['deliveryPromptHash']},
                                         'pairedComparisons': len(dimension_pairs), 'agreements': counts(dimension_pairs)['exactAgreements']})
        summary = {'schemaVersion': 5, 'protocol': 'full_counterbalanced', 'protocolSha256': report['protocolSha256'],
            'stimulusVersionDigest': report['protocolSha256'], 'releaseEligible': True, 'decisionScope': 'focus_only',
            'evidenceMode': 'diagrams_and_dimension_specific_facts', 'reviewersCompleted': 10,
            'roomTypePairs': {room: len({p['pairId'] for p in pairs if p['roomType'] == room}) for room in selected_ids},
            'dimensionsByRoomType': {room: [dict(id=profile, **counts([p for p in pairs if p['profile']==profile and p['roomType']==room])) for profile in PROFILES] for room in selected_ids},
            'dimensionResults': [dict(id=profile, **counts([p for p in pairs if p['profile']==profile])) for profile in PROFILES],
            'consistency': counts(pairs), 'overallConsistency': counts(report['pairs']),
            'consistencyByDimension': {profile: counts([p for p in report['pairs'] if p['profile']==profile]) for profile in PROFILES},
            'methods': methods, 'reviewers': reviewers, 'audit': audit}
        response_export = {'schemaVersion': 5, 'protocolSha256': report['protocolSha256'], 'pairs': pairs, 'responses': public_rows}
        for name, document in [(stem+'-summary.json', summary), (stem+'-responses.json', response_export)]:
            packed(document)  # Refuse private keys/paths before writing public output.
            write_json(output/name, document)
            hashes[name] = digest((output/name).read_bytes())
        for room, pair_id in selected_ids.items():
            example_reviews = []
            for reviewer in protocol['reviewers']:
                row = next(r for r in responses.values() if r['pairId']==pair_id and r['reviewerId']==reviewer['reviewerId'])
                packet = packets[row['assignmentId']]
                example_reviews.append({'reviewerId': reviewer['reviewerId'], 'dimension': reviewer['profile'],
                    'model': reviewer['reportedModel'], 'reasoningEffort': reviewer['reportedReasoningEffort'],
                    'prompt': delivery_prompt(protocol,row), 'promptHash': row['deliveryPromptHash'],
                    'systemPrompt': reviewer['systemPrompt'], 'responseSchema': reviewer['responseSchema'],
                    'image': {'url': image_prefix+packet['file'], 'sha256': packet['imageSha256']},
                    'evidence': row['evidence'],
                    'recordedOutcome': {'leftMethod': 'SOILIE-3D' if row['leftCondition']=='soilie' else label,
                        'rightMethod': 'SOILIE-3D' if row['rightCondition']=='soilie' else label,
                        'preference':row['judgement'], 'clearerProblem':row['errorChoice'], 'confidence':row['confidence'],
                        'explanation':row['note'], 'observations':row['observations']}})
            examples.append({'label':label, 'caseId':pair_id, 'roomType':room, 'studyVersion':protocol['rubricVersion'],
                'stimulusVersionDigest':report['protocolSha256'], 'source':'/benchmarks/'+stem+'-responses.json', 'reviews':example_reviews})
    write_json(output/'review-examples.json', {'schemaVersion':2, 'selection':'First frozen pair ID in each baseline × room-type group, selected without consulting judgments.', 'examples':examples})
    hashes['review-examples.json'] = digest((output/'review-examples.json').read_bytes())
    write_json(output/'review-manifest.json', {'schemaVersion':5, 'protocolSha256':report['protocolSha256'],
        'reviewedPairs':480, 'responses':4800, 'pairedComparisons':2400, 'files':hashes, 'audit':audit})
    return {'responses':len(responses), 'consistency':counts(report['pairs']), 'files':list(hashes)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--audit', action='store_true')
    args=parser.parse_args()
    if args.audit: print(audit_executions(args.root))
    if args.output: print(compile_release(args.root,args.output))
