"""Finite cloud dispatch with worst-case in-flight cost reservations.

Local successful artifacts remain immutable. Cloud records and timing are a
separate source in the same frozen bedroom/living-room corpus. Local workers
must own a disjoint seed allocation and fixed-seed parity must have passed.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, UTC
import argparse
import json
import math
from pathlib import Path
import signal
import time

import boto3
from botocore.config import Config

from serverless.benchmark.run_batch import run_lock
from serverless.cloud_benchmark.checkpoint import write_json
from serverless.cloud_benchmark.design import freeze
from serverless.cloud_benchmark.pilot import invoke_and_download

GB_SECOND_USD = .0000166667  # AWS Canada Central x86 tier 1, no free tier assumed.


def compute_cost(seconds, memory_mb):
    # Request, extra /tmp storage, and conservative small-log allowance included.
    return seconds*(memory_mb/1024*GB_SECOND_USD + .5*.0000000309) + .00003


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    parity = json.loads(args.parity.read_text())
    if not parity.get('passed') or {(row['roomType'],row['objectCount']) for row in parity.get('scenes',[])} != {
            (room,count) for room in ('bedroom','living_room') for count in range(3,7)}:
        raise ValueError('Model-parity probes must pass before cloud dispatch')
    configured = boto3.Session(profile_name=args.profile, region_name=args.region).client('lambda').get_function(FunctionName=args.function)
    config = configured['Configuration']
    if configured.get('Concurrency',{}).get('ReservedConcurrentExecutions',0) < args.concurrency:
        raise ValueError('Explicitly allocate the requested temporary concurrency before dispatch')
    if (parity.get('function') != args.function or parity.get('image') != configured['Code']['ResolvedImageUri']
            or config['MemorySize'] != args.memory_mb or parity.get('memoryMB') != args.memory_mb
            or config['Timeout'] > 900 or config.get('EphemeralStorage', {}).get('Size',512) > 1024
            or config.get('Architectures') != ['x86_64']):
        raise ValueError('Lambda differs from the verified pilot or cost reservation assumptions')
    plan = freeze(args.campaign, args.output)
    ledger_path = args.output/'ledger.json'
    reserve = compute_cost(915, args.memory_mb)  # 900s watchdog plus init margin.
    base_spend = sum(compute_cost(row['billedSeconds'], args.memory_mb) for row in parity['scenes'])
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {'entries': {}, 'setupAllowanceUSD': 1.0,
                'pilotEstimatedUSD': base_spend, 'budgetUSD': args.budget_usd, 'memoryMB': args.memory_mb,
                'function': args.function, 'bucket': args.bucket, 'perCallReserveUSD': reserve}
    for key, value in [('budgetUSD',args.budget_usd),('memoryMB',args.memory_mb),('function',args.function),('bucket',args.bucket)]:
        if ledger[key] != value:
            raise ValueError('Cost ledger/configuration mismatch')
    if any(row['status'] != 'complete' for row in ledger['entries'].values()):
        raise ValueError('Reconcile ambiguous in-flight calls from S3 before resuming; do not pay for duplicates')
    tasks = [row for row in plan['requests'] if str(row['seed']) not in ledger['entries']]
    active = {}
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def spent():
        return ledger['setupAllowanceUSD']+ledger['pilotEstimatedUSD']+sum(row.get('costUSD', reserve) for row in ledger['entries'].values())
    # Construct thread-safe low-level clients once, before starting threads.
    # Hundreds of independent SDK sessions otherwise duplicate service models
    # and credential parsing, wasting several GB of the local machine's RAM.
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    clients = {'lambda':session.client('lambda',config=Config(read_timeout=920,connect_timeout=15,
                        max_pool_connections=args.concurrency,retries={'total_max_attempts':1})),
               's3':session.client('s3',config=Config(max_pool_connections=args.concurrency))}
    def dispatch(task):
        return invoke_and_download(None, args.function, args.bucket,
                                   {key:task[key] for key in ('seed','objectCount','roomType')}, args.output/'downloads',
                                   require_success=False, clients=clients)
    started = time.monotonic()
    last_report = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        while tasks or active:
            completed_before_dispatch = sum(row['status']=='complete' for row in ledger['entries'].values())
            # Start with a small real-work wave before filling hundreds of cold
            # environments. Initial failures stop spending before scaling out.
            limit = min(args.concurrency,16) if completed_before_dispatch < 16 else args.concurrency
            while tasks and not stopping and len(active) < limit and spent()+reserve <= args.budget_usd:
                task = tasks.pop(0)
                ledger['entries'][str(task['seed'])] = {'status': 'dispatched', 'task': task,
                                                       'at': datetime.now(UTC).isoformat()}
                # Persist dispatch intent BEFORE contacting AWS. A crash cannot
                # silently make the next controller submit that seed again.
                write_json(ledger_path, ledger)
                active[pool.submit(dispatch, task)] = task
            if not active:
                break
            done, _ = wait(active, timeout=2, return_when=FIRST_COMPLETED)
            for future in done:
                task = active.pop(future)
                entry = ledger['entries'][str(task['seed'])]
                try:
                    row, receipt = future.result()
                    billed = receipt.get('billedSeconds')
                    if billed is None:
                        raise RuntimeError('Missing billed duration; reconcile before more invocations')
                    entry.update(status=row['status'], costUSD=compute_cost(billed,args.memory_mb),
                                 billedSeconds=billed, sha256=receipt['response']['sha256'], key=receipt['response']['key'])
                    if row['status'] != 'complete':
                        stopping = True
                except Exception as error:
                    entry.update(status='unknown', costUSD=reserve, error=str(error))
                    stopping = True
                write_json(ledger_path, ledger)
            if time.monotonic()-last_report > 15 or not active:
                completed = sum(row['status'] == 'complete' for row in ledger['entries'].values())
                failures = sum(row['status'] in ('failed','unknown') for row in ledger['entries'].values())
                state = {'cloudComplete':completed,
                         'cloudBedroomsComplete':sum(row['status']=='complete' and row['task']['roomType']=='bedroom' for row in ledger['entries'].values()),
                         'cloudLivingRoomsComplete':sum(row['status']=='complete' and row['task']['roomType']=='living_room' for row in ledger['entries'].values()),
                         'active':len(active), 'pending':len(tasks), 'failures':failures,
                         'costPlusReservedUSD':round(spent(),4), 'budgetUSD':args.budget_usd,
                         'elapsedSeconds':round(time.monotonic()-started,1)}
                write_json(args.output/'progress.json',state)
                print(json.dumps(state),flush=True)
                last_report=time.monotonic()
    complete = len(ledger['entries']) == len(plan['requests']) and all(row['status'] == 'complete' for row in ledger['entries'].values())
    write_json(args.output/'completion.json', {'complete':complete, 'costWithSetupAllowanceUSD':spent(),
                                             'downloadedCloudScenes':sum(row['status']=='complete' for row in ledger['entries'].values())})
    if not complete:
        raise RuntimeError('Campaign paused at budget or an unresolved invocation; existing evidence is retained')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','output','parity'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--function',required=True)
    parser.add_argument('--bucket',required=True)
    parser.add_argument('--memory-mb',type=int,default=4096)
    parser.add_argument('--concurrency',type=int,default=500)
    parser.add_argument('--budget-usd',type=float,default=25)
    parser.add_argument('--profile',default='darkest')
    parser.add_argument('--region',default='ca-central-1')
    args=parser.parse_args()
    if not 1 <= args.concurrency <= 500 or not math.isfinite(args.budget_usd) or args.budget_usd <= 1:
        parser.error('Use 1-500 concurrent calls and a finite budget above setup allowance')
    with run_lock(args.output):
        run(args)


if __name__=='__main__':
    main()
