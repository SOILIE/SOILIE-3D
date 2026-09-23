"""Resumable local V4 placement batches; one fresh process per attempt.

Use --worker only internally. Failed attempts are retained for reliability
reporting, while successful-layout timing excludes arbitrary watchdog waits.
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


def runtime_implementation(runtime):
    """Capture the exact code used by an attempt without charging benchmark time."""
    package = json.loads((runtime/"package.json").read_text(encoding="utf-8"))
    render_path = runtime/"modules"/"render.py"
    commit = "local"
    try:
        result = subprocess.run(
            ["git", "-C", str(runtime), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        commit = result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    return {
        "modelVersion": str(package["version"]),
        "sourceCommit": commit,
        "renderSha256": hashlib.sha256(render_path.read_bytes()).hexdigest(),
    }


def _without_provenance(config):
    return {key: value for key, value in config.items() if key not in {"provenance", "provenanceSegments"}}


def _compatible_render_maintenance(previous, current):
    """Allow only a render-module repair within the same model release."""
    if any(previous.get(key) != current.get(key) for key in ("model", "version", "channel")):
        return False
    first = json.loads(json.dumps(previous))
    second = json.loads(json.dumps(current))
    for value in (first, second):
        value.pop("baselineCommit", None)
        value.pop("totalBytes", None)
        value.get("files", {}).pop("modules/render.py", None)
    return first == second


def prepare_manifest(manifest, config, rows, compatible_maintenance=False):
    """Validate a resume and record the exact implementation from each boundary."""
    if not manifest.exists():
        config["provenanceSegments"] = [{"firstAttempt": 0, "provenance": config["provenance"]}]
        write_json(manifest, config)
        return config, 0

    previous = json.loads(manifest.read_text())
    if _without_provenance(previous) != _without_provenance(config):
        raise RuntimeError("Resume configuration differs; use a new output directory")
    segments = previous.get("provenanceSegments") or [
        {"firstAttempt": 0, "provenance": previous["provenance"]}
    ]
    if segments[-1]["provenance"] != config["provenance"]:
        if not compatible_maintenance or not _compatible_render_maintenance(segments[-1]["provenance"], config["provenance"]):
            raise RuntimeError("Resume implementation differs; use a new output directory")
        segments.append({"firstAttempt": len(rows), "provenance": config["provenance"]})
        previous["provenance"] = config["provenance"]
    previous["provenanceSegments"] = segments
    write_json(manifest, previous)
    return previous, len(segments)-1


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
    cmd = [str(args.blender), "--background", "suggested_setup.blend", "--threads", str(args.blender_threads), "--python-exit-code", "2",
           "--python", str(Path(__file__).with_name("capture_v4.py")), "--",
           "--input", str(args.work/"input.json"), "--output", str(args.work/"capture.json"), "--seed", str(request["seed"])]
    if args.full:
        cmd.append("--full")
    if args.support:
        cmd.append("--support")
    if args.solid_mesh_overlap:
        cmd.append("--solid-mesh-overlap")
    result = subprocess.run(supervised(cmd), cwd=args.work)
    if result.returncode:
        raise RuntimeError(f"Blender exited {result.returncode}")
    if not (args.work/"capture.json").exists():
        raise RuntimeError("No final-placement capture; output is not successful")
    write_json(args.work/"worker.json", {"selectionSeconds": selection_seconds})


def batch(args):
    from serverless.common.v4_runtime import load_v4_provenance

    args.output.mkdir(parents=True, exist_ok=True)
    plan_document = load_request_plan(args.request_plan) if args.request_plan else None
    plan = plan_document["requests"] if plan_document else None
    manifest = args.output/"run.json"
    config = {"schemaVersion": 1, "model": "soilie", "runtime": str(args.runtime), "seed": args.seed,
              "roomType": args.room_type, "allowDuplicates": not args.no_duplicates, "objectCounts": [args.object_count] if args.object_count else [3,4,5,6],
              "timeoutSeconds": args.timeout, "targetCompletions": args.target, "blender": str(args.blender),
              "full": args.full, "support": args.support, "solidMeshOverlap": args.solid_mesh_overlap,
              "pythonVersion": platform.python_version(),
              "hardware": platform.platform(), "cpu": platform.processor(), "cpuThreadsAvailable": os.cpu_count(),
              "roomFitIncluded": False, "provenance": load_v4_provenance(args.runtime)}
    if args.seed_step != 997 or args.blender_threads != 4 or args.parallel_workers != 1:
        config['execution'] = {'seedStep': args.seed_step, 'blenderThreads': args.blender_threads,
                               'parallelWorkers': args.parallel_workers,
                               'timingScope': 'parallel workload; not an uncontended serial timing sample'}
    if plan:
        # Finite evaluation workloads remain separate from the controlled
        # bedroom throughput run. Hash the inputs, not a machine-specific path.
        config.update(cohort=plan_document['cohort'],
                      requestPlanSha256=hashlib.sha256(args.request_plan.read_bytes()).hexdigest(),
                      plannedAttempts=len(plan), targetCompletions=len(plan),
                      roomType=plan_document.get('roomType', 'mixed'), allowDuplicates='per-request')
        if plan_document['cohort'] == 'paired_comparison':
            config['baseline'] = plan_document['baseline']
            config['baselineSceneIds'] = plan_document['baselineSceneIds']
    rows = checkpoint_rows(args.output)
    config, provenance_segment = prepare_manifest(
        manifest, config, rows, args.compatible_maintenance_resume,
    )
    completed = sum(row["status"] == "complete" for row in rows)
    attempted = len(rows)
    implementation = runtime_implementation(args.runtime)
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
                   "seed": args.seed+index*args.seed_step, "allowDuplicates": not args.no_duplicates,
                   "sameObjectsAcrossScenes": True}
        write_json(work/"request.json", request)
        command = [sys.executable, "-m", "serverless.benchmark.run_batch", "--worker", "--work", str(work),
                   "--runtime", str(args.runtime), "--blender", str(args.blender),
                   "--blender-threads", str(args.blender_threads)]
        if args.full:
            command.append("--full")
        if args.support:
            command.append("--support")
        if args.solid_mesh_overlap:
            command.append("--solid-mesh-overlap")
        started = time.perf_counter()
        room_type = request.get('roomType', config['roomType'] if plan else args.room_type)
        identity = (f"soilie-{config['cohort'].replace('_', '-')}-{index:05d}-{request['seed']}"
                    if plan else f"soilie-{args.room_type}-{request['seed']}")
        row = {"id": identity, "attempt": index, "request": request,
               "implementation": implementation,
               "provenanceSegment": provenance_segment,
               "startedAt": datetime.now(UTC).isoformat(), "status": "failed"}
        if plan:
            row['cohort'] = config['cohort']
            if config['cohort'] == 'paired_comparison':
                row['baselineSceneId'] = config['baselineSceneIds'][index]
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = "0"
        if args.parallel_workers > 1:
            # BLAS defaults may consume every CPU inside each process. Limit
            # inner parallelism; independent scenes supply the outer parallelism.
            for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
                environment[variable] = '1'
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
        log_text = (work/"process.log").read_text(errors="replace")
        row["collisionRecoveryActivated"] = "Object-aware collision recovery activated" in log_text
        row["collisionRecoveryMoves"] = log_text.count("---| Object-aware move")
        row["boundaryContainmentActivated"] = "Room containment repair activated" in log_text
        if (work/"selection.json").exists():
            row["selection"] = json.loads((work/"selection.json").read_text())["objects"]
        if row["status"] != "complete":
            if "Object-aware collision recovery repeated a geometric state" in log_text:
                row["errorCode"] = "COLLISION_RECOVERY_ASSERTION"
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
    cohort = document.get('cohort')
    if document.get('schemaVersion') != 1 or cohort not in {'diversity', 'paired_comparison'} or not rows:
        raise ValueError('Expected a nonempty frozen evaluation request plan')
    if cohort == 'paired_comparison':
        if document.get('roomType') not in {'bedroom', 'living_room'} or document.get('baseline') != 'layoutgpt':
            raise ValueError('Paired comparison plans require a supported room type and baseline')
        baseline_ids = document.get('baselineSceneIds')
        if not isinstance(baseline_ids, list) or len(baseline_ids) != len(rows) or len(set(baseline_ids)) != len(rows):
            raise ValueError('Paired comparison plans require one unique baseline scene per request')
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
    if cohort == 'paired_comparison' and any(row.get('mode') != 'objects' for row in rows):
        raise ValueError('Paired comparisons use explicit object inventories only')
    return document


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT/".codex/benchmark/soilie-bedroom")
    parser.add_argument("--target", type=int, default=10000)
    parser.add_argument("--max-attempts", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument('--seed-step', type=int, default=997)
    parser.add_argument('--blender-threads', type=int, default=4)
    parser.add_argument('--parallel-workers', type=int, default=1)
    parser.add_argument("--room-type", choices=["bedroom","living_room","kitchen","bathroom"], default="bedroom")
    parser.add_argument('--request-plan', type=Path)
    parser.add_argument("--object-count", type=int, choices=[3,4,5,6])
    parser.add_argument("--no-duplicates", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--support", action="store_true")
    parser.add_argument("--solid-mesh-overlap", action="store_true")
    parser.add_argument("--compatible-maintenance-resume", action="store_true",
                        help="Resume only when provenance differs by modules/render.py within the same version")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    if args.seed_step < 1 or args.blender_threads < 1 or args.parallel_workers < 1:
        parser.error('Seed step, Blender threads and parallel workers must be positive')
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
