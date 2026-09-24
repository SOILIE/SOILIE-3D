"""Freeze room-stratified, blinded review packets from the full four-condition cohort."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import secrets

from serverless.benchmark.stimuli import freeze, digest
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.study.service import StudyService, prompt_text
from serverless.study.store import SQLiteStudyStore

PLAN=['orientation','orientation','proportions','proportions','relationships',
      'relationships','access','access','room_function','room_function']


def prepare(evidence, output):
    cohort=json.loads((evidence/'cohort.json').read_bytes())
    if not cohort['complete'] or cohort['soilieScenes']!=10000:
        raise ValueError('Require the complete 10,000-scene cohort, not a rolling subset')
    raw=(evidence/'measured-scenes.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=cohort['measurementsSha256']:
        raise ValueError('Measured scene checksum mismatch')
    rows=json.loads(raw)['rows']
    actual=Counter(row['scene']['roomType'] for row in rows if row['scene']['model']=='soilie')
    if actual!={'bedroom':5000,'living_room':5000} or len({row['scene']['id'] for row in rows})!=len(rows):
        raise ValueError('Final review pool must have 5,000 distinct rooms of each type')
    images=output/'site/benchmarks/stimuli'
    protocols=[]
    for name,baseline,similarity,density in (
            ('set-a','layoutgpt',.4,.25),('set-b','infinigen_controlled',1/3,1)):
        folder=output/name; folder.mkdir(parents=True,exist_ok=True)
        parts=[]
        for room in ('bedroom','living_room'):
            subset=[row for row in rows if row['scene']['roomType']==room]
            parts.append(freeze(subset,images,folder/(room+'.json'),limit=120,
                minimum_semantic_similarity=similarity,maximum_density_difference=density,
                decision_scope='focus_only',reviewer_plan=PLAN,reviewer_model='GPT-5.6 Sol',
                reasoning_effort='Extra High',baselines=(baseline,)))
        document={**parts[0], 'cases':[case for part in parts for case in part['cases']],
                  'stimulusEvidence':[item for part in parts for item in part['stimulusEvidence']]}
        document['sampling']={**parts[0]['sampling'],
            'maximumPairsPerRoomTypePerBaseline':120,
            'maximumPairsPerBaseline':240,
            'cohortScenes':dict(Counter(row['scene']['model'] for row in rows)),
            'cohortSceneIdsSha256':digest(sorted(row['scene']['id'] for row in rows)),
            'roomTypePairs':{room:len(part['cases']) for room,part in zip(('bedroom','living_room'),parts)},
            'soiliePool':'All 10,000 completed scenes: 2,500 in each room-type/platform condition; no completion-order selection.',
            'scope':'The complete fixed four-condition allocation; immutable final corrected placements.',
            'roomProtocols':[part['studyVersion'] for part in parts]}
        document['studyVersion']='balanced-platform-'+digest(document)[:20]
        protocol=folder/'protocol.json'
        if protocol.exists() and json.loads(protocol.read_bytes())!=document:
            raise ValueError('Frozen protocol differs; use a new study directory')
        write_json(protocol,document)
        state=folder/'private'; state.mkdir(exist_ok=True)
        secret=state/'session-secret'
        if not secret.exists():
            secret.write_bytes(secrets.token_bytes(32))
        service=StudyService(document,SQLiteStudyStore(state/'pilot.sqlite3'),secret.read_bytes(),enabled=True)
        for index,profile in enumerate(PLAN):
            reviewer=f'reviewer-{index+1:02}'
            private=state/(reviewer+'.json')
            if private.exists():
                session=json.loads(private.read_bytes())
                service.resume(session['sessionId'],{'sessionToken':session['sessionToken']})
            else:
                session=service.start({'invitation':service.invite(reviewer,profile,'GPT-5.6 Sol (Extra High reasoning effort)')})
                write_json(private,session)
            packet=output/'packets'/reviewer
            packet.mkdir(parents=True,exist_ok=True)
            public={key:value for key,value in session.items() if key not in ('sessionToken','sessionId')}
            public['prompt']=prompt_text(document,profile)
            write_json(packet/(name+'.json'),public)
        protocols.append({'set':name,'baseline':baseline,'pairs':document['sampling']['roomTypePairs'],
                          'studyVersion':document['studyVersion']})
    write_json(output/'manifest.json',{'protocols':protocols,'reviewerPlan':PLAN,
        'cohortSha256':hashlib.sha256((evidence/'cohort.json').read_bytes()).hexdigest()})
    print(json.dumps(protocols),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    prepare(args.evidence,args.output)


if __name__=='__main__':
    main()
