"""Resumable local V4 placement batches; one fresh process per attempt.

Use --worker only internally. Failed attempts are retained and consume time;
they are never silently replaced in the first-10,000-attempt summary.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

from serverless.benchmark.supervise import command as supervised

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def run_lock(directory):
    """OS-owned lock: survives neither a crash nor an IDE restart as a stale lock."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "run.lock").open("a+b") as handle:
        if os.name == "posix":
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(handle, fcntl.LOCK_UN)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def checkpoint_rows(directory):
    paths = sorted(directory.glob("attempt-*.json"))
    rows = [json.loads(path.read_text()) for path in paths]
    if [row["attempt"] for row in rows] != list(range(len(rows))):
        raise RuntimeError("Attempt checkpoint contains a gap or duplicate; do not overwrite evidence")
    return rows


def write_json(path, data):
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def terminate_tree(process):
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGKILL)
    else:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    process.wait()


def worker(args):
    from serverless.common.v4_runtime import generate_v4_inputs, select_v4_objects
    request = json.loads((args.work/"request.json").read_text())
    started = time.perf_counter()
    objects = select_v4_objects(args.runtime, request, 0)
    write_json(args.work/"selection.json", {"objects": objects, "request": request})
    inputs = generate_v4_inputs(args.runtime, objects, request["seed"])
    write_json(args.work/"input.json", inputs)
    selection_seconds = time.perf_counter()-started
    for name in ("assets", "modules", "data", "suggested_setup.blend"):
        (args.work/name).symlink_to(args.runtime/name, target_is_directory=name != "suggested_setup.blend")
    (args.work/"output").mkdir()
    cmd = [str(args.blender), "--background", "suggested_setup.blend", "--threads", "4", "--python-exit-code", "2",
           "--python", str(Path(__file__).with_name("capture_v4.py")), "--",
           "--input", str(args.work/"input.json"), "--output", str(args.work/"capture.json"), "--seed", str(request["seed"])]
    if args.full:
        cmd.append("--full")
    if args.support:
        cmd.append("--support")
    result = subprocess.run(supervised(cmd), cwd=args.work)
    if result.returncode:
        raise RuntimeError(f"Blender exited {result.returncode}")
    if not (args.work/"capture.json").exists():
        raise RuntimeError("No final-placement capture; output is not successful")
    write_json(args.work/"worker.json", {"selectionSeconds": selection_seconds})


def batch(args):
    args.output.mkdir(parents=True, exist_ok=True)
    plan = load_request_plan(args.request_plan) if args.request_plan else None
    manifest = args.output/"run.json"
    config = {"schemaVersion": 1, "model": "soilie", "runtime": str(args.runtime), "seed": args.seed,
              "roomType": args.room_type, "allowDuplicates": not args.no_duplicates, "objectCounts": [args.object_count] if args.object_count else [3,4,5,6],
              "timeoutSeconds": args.timeout, "targetCompletions": args.target, "blender": str(args.blender),
              "full": args.full, "support": args.support, "pythonVersion": platform.python_version(),
              "hardware": platform.platform(), "cpu": platform.processor(), "cpuThreadsAvailable": os.cpu_count(),
              "roomFitIncluded": False, "provenance": json.loads((args.runtime/"v4-provenance.json").read_text())}
    if plan:
        # A distinct finite exploration workload, never mixed into the fixed
        # bedroom throughput run. Hash the inputs, not a machine-specific path.
        config.update(cohort='diversity', requestPlanSha256=hashlib.sha256(args.request_plan.read_bytes()).hexdigest(),
                      plannedAttempts=len(plan), targetCompletions=len(plan),roomType='mixed',allowDuplicates='per-request')
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if previous != config:
            raise RuntimeError("Resume configuration differs; use a new output directory")
    else:
        write_json(manifest, config)
    rows = checkpoint_rows(args.output)
    completed = sum(row["status"] == "complete" for row in rows)
    attempted = len(rows)
    while (attempted < len(plan) if plan else completed < args.target) and (not args.max_attempts or attempted < args.max_attempts):
        if shutil.disk_usage(args.output).free < 15*1024**3:
            raise RuntimeError("Paused before disk space falls below 15 GiB; checkpoint is resumable")
        index = attempted
        work = args.output/f"work-{index:05d}"
        if work.exists():
            # Only the exact interrupted attempt directory owned by this run.
            shutil.rmtree(work)
        work.mkdir()
        request = dict(plan[index]) if plan else {"mode": "room_type", "roomType": args.room_type, "objectCount": args.object_count or 3+index%4,
                   "seed": args.seed+index*997, "allowDuplicates": not args.no_duplicates,
                   "sameObjectsAcrossScenes": True}
        write_json(work/"request.json", request)
        command = [sys.executable, "-m", "serverless.benchmark.run_batch", "--worker", "--work", str(work),
                   "--runtime", str(args.runtime), "--blender", str(args.blender)]
        if args.full:
            command.append("--full")
        if args.support:
            command.append("--support")
        started = time.perf_counter()
        room_type = request.get('roomType', 'unspecified')
        identity = f"soilie-diversity-{index:05d}-{request['seed']}" if plan else f"soilie-{args.room_type}-{request['seed']}"
        row = {"id": identity, "attempt": index, "request": request,
               "startedAt": datetime.now(UTC).isoformat(), "status": "failed"}
        if plan:
            row['cohort'] = 'diversity'
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = "0"
        environment.pop("SOILIE_ROOM_REQUEST", None)
        with (work/"process.log").open("wb") as log:
            process = subprocess.Popen(supervised(command), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       env=environment, start_new_session=os.name == "posix")
            try:
                process.wait(timeout=args.timeout)
                row["errorCode"] = "GENERATION_FAILED"
                if process.returncode == 0:
                    row.update(json.loads((work/"capture.json").read_text()))
                    row.update(json.loads((work/"worker.json").read_text()))
                    row["status"] = "complete"
                    row.pop("errorCode", None)
                    for stage in row["stages"].values():
                        stage.update({"id": row["id"], "model": "soilie", "roomType": room_type})
                        if plan:
                            stage['cohort'] = 'diversity'
                    completed += 1
            except subprocess.TimeoutExpired:
                terminate_tree(process)
                row["errorCode"] = "TIMEOUT"
            except BaseException:
                if process.poll() is None:
                    terminate_tree(process)
                raise
        row["wallSeconds"] = time.perf_counter()-started
        row["generationSeconds"] = row["wallSeconds"]-row.get("observationSeconds", 0)
        if (work/"selection.json").exists():
            row["selection"] = json.loads((work/"selection.json").read_text())["objects"]
        if row["status"] != "complete":
            log_text = (work/"process.log").read_text(errors="replace")
            trace_start = log_text.find("Traceback")
            row["errorTrace"] = log_text[trace_start:trace_start+2400] if trace_start >= 0 else ""
            row["errorTail"] = log_text[-1200:]
        write_json(args.output/f"attempt-{index:05d}.json", row)
        attempted += 1
        print(json.dumps({"attempts": attempted, "completed": completed, "lastStatus": row["status"],
                          "lastSeconds": round(row["wallSeconds"],2)}), flush=True)
        if not args.full:
            shutil.rmtree(work)


def load_request_plan(path):
    document = json.loads(path.read_text())
    rows = document.get('requests')
    if document.get('schemaVersion') != 1 or document.get('cohort') != 'diversity' or not rows:
        raise ValueError('Expected a nonempty frozen diversity request plan')
    for row in rows:
        allowed = {'mode','roomType','objectCount','objects','seed','allowDuplicates','sameObjectsAcrossScenes'}
        if set(row)-allowed or type(row.get('seed')) is not int or not 0 <= row['seed'] < 2**32:
            raise ValueError('Invalid exploration request fields or seed')
        if row.get('mode') == 'objects':
            objects = row.get('objects', [])
            if not 3 <= len(objects) <= 6 or not all(isinstance(v,str) and v.strip() for v in objects):
                raise ValueError('Explicit exploration inputs require three to six labels')
            if 'roomType' in row or 'objectCount' in row:
                raise ValueError('Explicit scenes cannot be assigned an inferred room preset')
        elif row.get('mode') in {'random','room_type'}:
            if row.get('objectCount') not in (3,4,5,6) or 'objects' in row:
                raise ValueError('Invalid random/preset count')
            if row['mode'] == 'room_type' and row.get('roomType') not in {'bedroom','living_room','kitchen','bathroom'}:
                raise ValueError('Unknown preset')
            if row['mode'] == 'random' and 'roomType' in row:
                raise ValueError('Random scenes have no preset room label')
            if type(row.get('allowDuplicates')) is not bool:
                raise ValueError('Duplicate policy must be explicit')
        else:
            raise ValueError('Unknown generation mode')
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT/".codex/benchmark/soilie-bedroom")
    parser.add_argument("--target", type=int, default=10000)
    parser.add_argument("--max-attempts", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--room-type", choices=["bedroom","living_room","kitchen","bathroom"], default="bedroom")
    parser.add_argument('--request-plan', type=Path)
    parser.add_argument("--object-count", type=int, choices=[3,4,5,6])
    parser.add_argument("--no-duplicates", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--support", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    for name in ("runtime","blender","output","work","request_plan"):
        if getattr(args,name) is not None:
            setattr(args,name,getattr(args,name).resolve())
    if args.worker:
        worker(args)
    else:
        from serverless.benchmark.timing import record_session
        with run_lock(args.output):
            with record_session(args.output, "soilie-full-parity" if args.full else "soilie-final-placement"):
                batch(args)


if __name__ == "__main__":
    main()
