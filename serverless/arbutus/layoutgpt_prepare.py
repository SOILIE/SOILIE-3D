"""Original LayoutGPT retrieval/prompt with a disclosed exact-inventory input.

One joint ledger covers both room types. Author training demonstrations and
held-out floor plans are unchanged. Category requests do not contain SOILIE
positions, sizes, orientations, geometry scores, or reviewer answers.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
import os.path
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

import numpy as np
from PIL import Image
import tiktoken

from serverless.arbutus.inventory_plan import LAYOUTGPT
from serverless.benchmark.layoutgpt_controlled import COMMIT, REPOSITORY, hydrate


def prepare(campaign, output, source, bedroom_cache, living_cache):
    if (output/'requests.json').exists(): raise ValueError('Frozen requests already exist')
    plan_raw=campaign.read_bytes()
    tasks=json.loads(plan_raw)['tasks']
    raw=source.read_text(encoding='utf-8')
    official=urlopen(REPOSITORY+'/run_layoutgpt_3d.py',timeout=60).read().decode()
    if raw.replace('\r\n','\n')!=official.replace('\r\n','\n'):
        raise ValueError('Expected pinned original LayoutGPT source')
    names={'load_room_boxes','load_features','get_closest_room','form_prompt_for_chatgpt'}
    nodes=[n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name in names]
    if len(nodes)!=len(names): raise ValueError('Missing original prompt builders')
    gpt2,gpt4=tiktoken.get_encoding('gpt2'),tiktoken.get_encoding('cl100k_base')
    requests=[]
    for room,cache in [('bedroom',bedroom_cache),('living_room',living_cache)]:
        selected=[t for t in tasks if t['baseline']=='layoutgpt' and t['roomType']==room and t['needsGeneration']]
        target=output/room
        target.mkdir(parents=True,exist_ok=True)
        native_room='livingroom' if room=='living_room' else room
        manifest=hydrate(target,len(selected),20260929,data_cache=cache,room=native_room)
        scope={'np':np,'Image':Image,'op':os.path,'args':SimpleNamespace(room=native_room,normalize=True,
            unit='px',icl_type='k-similar',test=False,gpt_input_length_limit=7000),
            'tokenizer':lambda text:{'input_ids':gpt2.encode(text)}}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),scope)
        stats=json.loads((cache/'dataset_stats.txt').read_bytes())
        prompts,meta={},{}
        for name in manifest['trainingIds']+manifest['targetIds']:
            condition,layout,data=scope['load_room_boxes'](str(cache),name,stats,'px')
            prompts[name]=(condition,layout);meta[name]=data
        features=scope['load_features']({n:meta[n] for n in manifest['trainingIds']})
        target_features=scope['load_features']({n:meta[n] for n in manifest['targetIds']})
        training={n:prompts[n] for n in manifest['trainingIds']}
        for task,name in zip(selected,manifest['targetIds']):
            labels=Counter({LAYOUTGPT[room][label]:count for label,count in task['inventory'].items()})
            if set(labels)-set(stats['object_types']): raise ValueError('Requested category outside native vocabulary')
            messages=scope['form_prompt_for_chatgpt'](prompts[name],8 if room=='bedroom' else 4,
                stats,training,features,target_features[name])
            instruction=('Generate exactly '+str(sum(labels.values()))+' furniture instances with this inventory: '+
                ', '.join(f'{label}: {count}' for label,count in sorted(labels.items()))+
                '. Include no other objects. Use one CSS line per instance; count repeated instances separately.\nLayout:\n')
            messages[-1]['content']=messages[-1]['content'].replace('Layout:\n',instruction)
            tokens=3+sum(3+len(gpt4.encode(m['role']))+len(gpt4.encode(m['content'])) for m in messages)
            if tokens+1024>8192: raise ValueError('GPT-4 context allowance exceeded')
            payload={'model':'gpt-4','messages':messages,'temperature':.7,'max_tokens':1024,
                'top_p':1,'frequency_penalty':0,'presence_penalty':0,'stop':'Condition:','n':1}
            requests.append({'id':task['id'],'roomType':room,'sourceRoomId':name,
                'requestedInventory':task['inventory'],'requestedObjects':sum(labels.values()),
                'task':task,'estimatedInputTokens':tokens,'request':payload,
                'requestSha256':hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()})
    order={task['id']:i for i,task in enumerate(tasks)}
    requests.sort(key=lambda r:order[r['id']])
    document={'schemaVersion':1,'variant':'shared-inventory-gpt4-v1','sourceCommit':COMMIT,
        'campaignSha256':hashlib.sha256(plan_raw).hexdigest(),'budgetUsd':35,'requests':requests,
        'methods':'Original GPT-4 and native K=8 bedroom / K=4 living-room retrieval, authors training examples and held-out room dimensions. An explicit 3–6-object inventory instruction is added. Inputs selected without quality scores; substitutions recorded per task. One US$35 cap covers both room types.'}
    with (output/'requests.json').open('x') as f: json.dump(document,f,indent=2)
    print(json.dumps({'prepared':len(requests),'maxTokenEstimateUsd':sum(
        r['estimatedInputTokens']*.00003+1024*.00006 for r in requests),'budgetUsd':35}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','output','source','bedroom-cache','living-cache'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    prepare(a.campaign,a.output,a.source,a.bedroom_cache,a.living_cache)
