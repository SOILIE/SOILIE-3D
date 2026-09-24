"""Import independent reviewers' answers through the real immutable service."""
import argparse
from collections import Counter
import json
from pathlib import Path

from serverless.study.service import StudyService
from serverless.study.store import SQLiteStudyStore


def submit(root, reviewer):
    folder=root/'packets'/reviewer
    assignments=json.loads((folder/'cases.json').read_bytes())
    answers=json.loads((folder/'answers.json').read_bytes())
    required={(case['set'],case['caseId']) for case in assignments}
    supplied=Counter((row['set'],row['caseId']) for row in answers)
    if set(supplied)!=required or any(count!=1 for count in supplied.values()):
        raise ValueError('Exactly one judgement is required per assigned case, including repeats')
    for name in ('set-a','set-b'):
        cohort=root/name; state=cohort/'private'
        protocol=json.loads((cohort/'protocol.json').read_bytes())
        session=json.loads((state/(reviewer+'.json')).read_bytes())
        service=StudyService(protocol,SQLiteStudyStore(state/'pilot.sqlite3'),
                             (state/'session-secret').read_bytes(),enabled=True)
        for row in answers:
            if row['set']==name:
                service.respond(session['sessionId'],{key:value for key,value in
                    {**row,'sessionToken':session['sessionToken']}.items() if key!='set'})
        result=service.resume(session['sessionId'],{'sessionToken':session['sessionToken']})
        if len(result['completedCaseIds'])!=len(result['cases']):
            raise ValueError('Incomplete persisted reviewer session')
    print(json.dumps({'reviewer':reviewer,'saved':len(answers)}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--reviewer',required=True)
    args=parser.parse_args()
    if args.reviewer not in {f'reviewer-{index:02}' for index in range(1,11)}:
        raise ValueError('Unregistered reviewer')
    submit(args.root,args.reviewer)


if __name__=='__main__':
    main()
