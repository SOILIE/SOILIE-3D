"""Replace an owned serial controller only after its active stage finishes.

The controller is paused, not its worker. A successfully checkpointed worker
is never terminated, and a new controller cannot race it into the next stage.
This is orchestration only: no generator settings or RNG state are changed.
"""
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import signal
import time

from serverless.benchmark.run_batch import write_json


def identity(pid):
    try:
        base = Path(f'/proc/{pid}')
        # Fields after the parenthesized process name begin with field 3.
        fields = (base/'stat').read_text().rsplit(')',1)[1].split()
        return {'state':fields[0], 'ppid':int(fields[1]), 'start':fields[19],
                'args':(base/'cmdline').read_bytes().decode().strip('\0').split('\0')}
    except FileNotFoundError:
        return None


def alive(pid, original):
    current = identity(pid)
    return bool(current and current['start'] == original['start'] and current['state'] not in {'Z','X'})


def after_stage(pid, root, journal):
    if os.name != 'posix' or pid <= 1 or pid == os.getpid():
        raise ValueError('Expected another owned Linux controller')
    original = identity(pid)
    if not original or 'serverless.benchmark.run_campaign' not in original['args'] or Path(f'/proc/{pid}/cwd').resolve() != root.resolve():
        raise ValueError('PID is not this project\'s campaign controller')
    children_path = Path(f'/proc/{pid}/task/{pid}/children')
    history = {'controllerPid':pid,'startedAt':datetime.now(UTC).isoformat(),
               'reason':'Add separately labelled diversity and current mesh-support observations at a safe stage boundary',
               'status':'waiting-for-active-stage'}
    paused = False
    try:
        os.kill(pid,signal.SIGSTOP)
        paused = True
        while alive(pid,original) and identity(pid)['state'] not in {'T','t'}:
            time.sleep(.02)
        children = [int(value) for value in children_path.read_text().split()]
        observed = {child:identity(child) for child in children}
        if any(not data or data['ppid'] != pid or not any(v.startswith('serverless.benchmark.') for v in data['args']) for data in observed.values()):
            raise ValueError('Controller owns an unexpected child; leaving the existing plan intact')
        history['stagePids'] = children
        write_json(journal,history)
        print(json.dumps({'status':history['status'],'stagePids':children}),flush=True)
        while any(alive(child,data) for child,data in observed.items()):
            time.sleep(2)
        # Worker is finished, so the old controller's signal handler cannot
        # interrupt model work. Its generator-owned checkpoint is resumable.
        os.kill(pid,signal.SIGTERM)
        os.kill(pid,signal.SIGCONT)
        paused = False
        deadline = time.monotonic()+30
        while alive(pid,original):
            if time.monotonic() > deadline:
                raise RuntimeError('Old controller has not exited; refusing a second campaign')
            time.sleep(.1)
        history['status'] = 'complete'
    finally:
        if paused and alive(pid,original):
            os.kill(pid,signal.SIGCONT)
        history['endedAt'] = datetime.now(UTC).isoformat()
        write_json(journal,history)
