"""Reuse only byte-identical delivered inputs, never preference-based selection."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path

from serverless.cloud_benchmark.full_review import results
from serverless.cloud_benchmark.staged_pilot import digest, load_frozen, read, record, write_new, delivery_prompt
from serverless.cloud_benchmark.run_staged_pilot import parse_events


def verify_execution(source, protocol, row):
    """Check the saved model output, not only its copied response metadata."""
    execution=Path(source)/'execution'/row['assignmentId']
    if row['receipt'].get('runner')!='codex exec --ephemeral':
        raise ValueError('Retention requires an auditable isolated execution')
    events=(execution/'events.jsonl').read_text(encoding='utf-8')
    thread,_=parse_events(events)
    if digest(events.encode())!=row['receipt']['eventsSha256'] or thread!=row['receipt']['threadId']:
        raise ValueError('Original reviewer transcript changed')
    answer={k:row[k] for k in protocol['outputSchema']['required']}
    if read(execution/'answer.json')!=answer:
        raise ValueError('Saved response differs from original answer')
    if (execution/'prompt.txt').read_text(encoding='utf-8')!=delivery_prompt(protocol,row):
        raise ValueError('Original delivered prompt changed')
    invocation=read(execution/'invocation.json')
    for key in ('model','reasoningEffort','promptHash','systemPromptHash','imageSha256','deliveryPromptHash','evidenceSha256'):
        if invocation.get(key)!=row['receipt'].get(key):
            raise ValueError('Original invocation changed: '+key)
    if digest((execution/'pair.png').read_bytes())!=row['receipt']['imageSha256']:
        raise ValueError('Original delivered image changed')


def fingerprint(protocol, row, packet):
    reviewer=next(r for r in protocol['reviewers'] if r['reviewerId']==row['reviewerId'])
    # Screen orientation, case-specific data and dimension all participate.
    # A reused room in a new pair or a revised catalog cannot match this key.
    return digest([row['profile'],reviewer['model'],reviewer['reasoningEffort'],
                   reviewer['promptHash'],reviewer['systemPromptHash'],
                   protocol['outputSchema'],row['deliveryPromptHash'],row['evidenceSha256'],
                   packet['imageSha256']])


def retain(source, destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if source==destination or any((destination/'responses').glob('*.json')):
        raise ValueError('Retention requires a fresh destination')
    previous,current=load_frozen(source),load_frozen(destination)
    old_report=results(source)  # Verify every old response, not only matches.
    old_index,new_index=read(source/'packets/index.json'),read(destination/'packets/index.json')
    available=defaultdict(list)
    for file in sorted((source/'responses').glob('*.json')):
        row=read(file)
        verify_execution(source,previous,row)
        available[fingerprint(previous,row,old_index[row['assignmentId']])].append(row)
    records=[]
    for row in current['assignments']:
        key=fingerprint(current,row,new_index[row['assignmentId']])
        if not available[key]: continue
        old=available[key].pop(0)
        answer={k:old[k] for k in current['outputSchema']['required']}
        record(destination,row['assignmentId'],answer,old['receipt'])
        records.append({'assignmentId':row['assignmentId'],'sourceAssignmentId':old['assignmentId'],
                        'sourceReviewerId':old['reviewerId'],'profile':row['profile'],
                        'sourceProtocolSha256':digest(previous),'deliveredInputSha256':key,
                        'sourceResponseSha256':digest((source/'responses'/(old['assignmentId']+'.json')).read_bytes()),
                        'sourceEventsSha256':old['receipt']['eventsSha256']})
    report={'retained':len(records),'previousResponsesPreserved':old_report['responses'],
            'byDimension':dict(Counter(r['profile'] for r in records)),'records':records,
            'rule':'Exact prompt, system, schema, model/effort, case evidence and presented-image match. No answer-dependent selection. Retained responses were not newly collected.'}
    write_new(destination/'retention-report.json',report)
    write_new(destination/'private/retained-execution-source.json',{'sourceRoot':str(source)})
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--root',type=Path,required=True)
    a=p.parse_args()
    report=retain(a.source,a.root)
    print({k:v for k,v in report.items() if k!='records'})
