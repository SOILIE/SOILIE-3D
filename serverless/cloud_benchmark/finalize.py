"""After local completion, audit the full corpus and prepare (not run) AI reviews.

This unattended step only writes private checkpoint artifacts. It never
publishes, spends cloud budget, starts reviewers, or changes model placement.
"""
import argparse
import json
from pathlib import Path
import time

from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.evidence import compile_full
from serverless.cloud_benchmark.reviews import prepare
from serverless.benchmark.run_batch import run_lock


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('grid','campaign','cloud-evidence','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--wait',action='store_true')
    args=parser.parse_args()
    with run_lock(args.output/'finalizer-lock'):
        while not (args.grid/'local-completion.json').exists():
            if not args.wait:
                raise ValueError('Local campaign is not complete')
            write_json(args.output/'readiness.json',{'stage':'waiting_for_local','reviewersStarted':0})
            time.sleep(30)
        try:
            write_json(args.output/'readiness.json',{'stage':'auditing_and_measuring','reviewersStarted':0})
            compile_full(args.grid,args.campaign,args.cloud_evidence,args.output/'evidence')
            write_json(args.output/'readiness.json',{'stage':'freezing_review_pairs','reviewersStarted':0})
            prepare(args.output/'evidence',args.output/'review')
        except Exception as error:
            write_json(args.output/'readiness.json',{'stage':'failed','reviewersStarted':0,'error':str(error)})
            raise
        write_json(args.output/'readiness.json',{'stage':'ready_for_reviewers','reviewersStarted':0})
        print(json.dumps({'stage':'ready_for_reviewers','reviewersStarted':0}),flush=True)


if __name__=='__main__':
    main()
