"""Predetermined supplementary draws, sharing the original US$35 ledger cap.

Repeat only the original inventory-conditioned request for each unmatched room,
under new proposal IDs. Keep every result and use matching eligibility, never
visual quality or reviewer preferences, to accept the first feasible proposals.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

from serverless.cloud_benchmark.staged_pilot import read, write_new, digest


def fresh_inventories(root, output, matching):
    """New unused SOILIE inventory targets, selected without geometry scores.

    Repeating saturated rare inventories cannot create new one-to-one matches.
    Keep native demonstration/floor-plan prompts, change only the already
    authorized inventory input. No SOILIE coordinates or sizes enter a request.
    """
    import tiktoken
    from serverless.arbutus.inventory_plan import target_inventory, LAYOUTGPT
    from serverless.arbutus.review_cohort import BASE
    root,output=Path(root).resolve(),Path(output).resolve()
    parent=root/'layoutgpt-supplement'
    parent_raw=(parent/'inference-ledger.json').read_bytes()
    ledger=read(parent/'inference-ledger.json')
    if output.exists() or (parent/'inference.lock').exists() or any(r['status']!='complete' for r in ledger['entries'].values()):
        raise ValueError('Fresh destination and settled parent required')
    def cost(row):
        return row.get('actualUsd',row.get('reservedUsd',0))+sum(cost(r) for r in row.get('previousAttempts',[]))
    accounted=ledger.get('previousBatchUsd',0)+sum(cost(r) for r in ledger['entries'].values())
    used={p['soilieScene'] for p in read(matching)['matchedPairs'] if p['baseline']=='layoutgpt'}
    candidates=[]
    for row in read(BASE/'evidence/measured-scenes.json')['rows']:
        scene=row['scene']
        if scene['model']!='soilie' or scene['roomType']!='bedroom' or scene['id'] in used: continue
        target=target_inventory(scene,'layoutgpt')
        if target: candidates.append((len(target['substitutions']),digest(['inventory-topup-v1',scene['id']]),scene,target))
    candidates.sort(key=lambda x:x[:2])
    templates=[r for r in read(root/'layoutgpt/requests.json')['requests'] if r['roomType']=='bedroom']
    requests=[]
    tokenizer=tiktoken.get_encoding('cl100k_base')
    for index,(_,_,scene,target) in enumerate(candidates[:10]):
        row=deepcopy(templates[index])
        identifier=f'layoutgpt-bedroom-{140+index:03d}'
        labels={LAYOUTGPT['bedroom'][key]:count for key,count in target['inventory'].items()}
        instruction=('Generate exactly '+str(sum(labels.values()))+' furniture instances with this inventory: '+
                     ', '.join(f'{label}: {count}' for label,count in sorted(labels.items()))+
                     '. Include no other objects. Use one CSS line per instance; count repeated instances separately.\nLayout:\n')
        messages=row['request']['messages']
        text,count=re.subn(r'Generate exactly .*?\nLayout:\n',lambda _:instruction,messages[-1]['content'],flags=re.S)
        if count!=1: raise ValueError('Original inventory instruction not found exactly once')
        messages[-1]['content']=text
        tokens=3+sum(3+len(tokenizer.encode(m['role']))+len(tokenizer.encode(m['content'])) for m in messages)
        row.update(id=identifier,requestedInventory=target['inventory'],requestedObjects=sum(labels.values()),
            estimatedInputTokens=tokens,requestSha256=hashlib.sha256(json.dumps(row['request'],sort_keys=True).encode()).hexdigest(),
            task={'id':identifier,'baseline':'layoutgpt','roomType':'bedroom','needsGeneration':True,
                  'soilieSceneId':scene['id'],'soilieSceneSha256':digest(scene),**target})
        requests.append(row)
    original=read(root/'layoutgpt/requests.json')
    document={**original,'requests':requests,'selectionSeed':'inventory-topup-v1',
        'supplementSelection':'Unused supported SOILIE inventories, exact first then fixed hash order; no quality scores.',
        'previousBatch':{'folder':str(parent),'ledgerSha256':digest(parent_raw),'planSha256':ledger['planSha256'],
                         'expectedRequests':len(ledger['entries']),'accountedUsd':accounted}}
    write_new(output/'requests.json',document)
    return {'requests':len(requests),'previousAccountedUsd':accounted,'totalBudgetUsd':35}


def prepare(root, output, source_audit, rounds=3):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or rounds not in (1,2,3):
        raise ValueError('New destination and at most three fixed rounds required')
    parent = root / 'layoutgpt'
    plan = read(parent / 'requests.json')
    ledger = read(parent / 'inference-ledger.json')
    if (parent / 'inference.lock').exists() or any(r['status']!='complete' for r in ledger['entries'].values()):
        raise ValueError('The parent paid batch must be finished')
    def cost(row):
        return row.get('actualUsd',row.get('reservedUsd',0)) + sum(cost(r) for r in row.get('previousAttempts',[]))
    accounted = ledger.get('previousBatchUsd',0) + sum(cost(r) for r in ledger['entries'].values())
    audit = read(source_audit)
    accepted = {r['taskId'] for r in audit['matchedPairs']}
    missing = sorted(r['taskId'] for r in audit['pairs'] if r['taskId'] not in accepted)
    if any(not key.startswith('layoutgpt-bedroom-') for key in missing) or len(missing)!=5:
        raise ValueError('Expected the five audited unmatched bedrooms')
    originals = {r['id']:r for r in plan['requests']}
    requests = []
    for attempt in range(rounds):
        for key in missing:
            row = deepcopy(originals[key])
            row.update(id=f'layoutgpt-bedroom-{120+len(requests):03d}', supplementsTask=key, draw=attempt+1)
            row['task'] = dict(row['task'], id=row['id'])
            requests.append(row)
    result = {**plan, 'requests':requests, 'supplementSelection':'First eligible proposals in frozen round-robin order, without quality scores.',
              'matchingAuditSha256':digest(Path(source_audit).read_bytes()),
              'previousBatch':{'folder':str(parent),'ledgerSha256':digest((parent/'inference-ledger.json').read_bytes()),
                    'planSha256':ledger['planSha256'],'expectedRequests':len(ledger['entries']),'accountedUsd':accounted}}
    write_new(output / 'requests.json', result)
    return {'requests':len(requests), 'previousAccountedUsd':accounted, 'totalBudgetUsd':35}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--audit',type=Path,required=True)
    p.add_argument('--fresh-inventories',action='store_true')
    a=p.parse_args()
    print((fresh_inventories if a.fresh_inventories else prepare)(a.root,a.output,a.audit),flush=True)
