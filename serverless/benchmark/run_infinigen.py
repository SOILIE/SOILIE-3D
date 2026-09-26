"""Checkpointed original Indoors single-room construction.

The official coarse task includes solving, procedural meshes and preparation of
the scene file. It does not render an image. Timings must retain that stage label
and cannot be presented as equivalent to LayoutGPT's bounding-box response.
"""
import argparse
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

from serverless.benchmark.run_batch import run_lock, terminate_tree, write_json
from serverless.benchmark.timing import record_session
from serverless.benchmark.supervise import command as supervised
from serverless.benchmark.infinigen_task import (
    COMMIT, PROFILES, CONTROLLED_PROFILES, controlled_roles,
    profile_command, validate_controlled_output,
)


def revalidate_controlled_checkpoints(rows, output, room_type, object_count=6):
    """Correct old success-only checkpoints before a controlled run resumes."""
    for index, row in enumerate(rows):
        if row.get("status") != "complete":
            continue
        try:
            row["controlledRoles"] = validate_controlled_output(
                output/f"scene-{room_type}-{index:03d}", room_type, object_count
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            row["status"] = "failed"
            row["errorCode"] = "CONTROLLED_COMPOSITION_MISMATCH"
            row["validationError"] = str(error)
        write_json(output/f"attempt-{room_type}-{index:03d}.json", row)
    return rows




def checkpoint_rows(output, room_type):
    """Read completed checkpoint records for one room type in attempt order."""
    rows = []
    for expected_index, path in enumerate(sorted(output.glob(f"attempt-{room_type}-*.json"))):
        if path.stem != f"attempt-{room_type}-{expected_index:03d}":
            raise RuntimeError(f"Checkpoint sequence has a gap before {path.name}")
        row = json.loads(path.read_text())
        if row.get("roomType") != room_type:
            raise RuntimeError(f"Checkpoint {path.name} has the wrong room type")
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository",type=Path,required=True)
    parser.add_argument("--site-packages",type=Path,required=True)
    parser.add_argument("--blender",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--per-room",type=int,default=20,
                        help="Required completed scenes for each room type; unsuccessful seeds advance the deterministic sequence")
    parser.add_argument('--room-types', nargs='+', choices=('bedroom', 'living_room'),
                        default=['bedroom', 'living_room'], help='Generate only the explicitly requested room types')
    parser.add_argument("--timeout",type=int,default=21600)
    parser.add_argument("--max-attempts",type=int,default=0)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="default")
    parser.add_argument('--object-count', type=int, choices=range(3, 7), default=6)
    parser.add_argument('--blender-threads', type=int, choices=range(1, 9), default=4)
    parser.add_argument("--bedroom-seed-offset",type=int,default=0,
                        help="Non-negative deterministic offset used to create disjoint benchmark shards")
    parser.add_argument("--living-room-seed-offset",type=int,default=0,
                        help="Non-negative deterministic offset added after the living-room base seed")
    args = parser.parse_args()
    if args.profile == 'controlled-count-fast' and args.room_types != ['bedroom']:
        parser.error('Variable-count profile is restricted to bedrooms')
    if args.object_count != 6 and args.profile != 'controlled-count-fast':
        parser.error('Non-six counts require the explicit controlled-count-fast profile')
    if (args.per_room < 1 or args.max_attempts < 0 or args.bedroom_seed_offset < 0
            or args.living_room_seed_offset < 0):
        parser.error("counts and seed offsets must be non-negative, and --per-room must be positive")
    for name in ("repository","site_packages","blender","output"):
        setattr(args,name,getattr(args,name).resolve())
    revision = subprocess.check_output(["git","rev-parse","HEAD"],cwd=args.repository,text=True).strip()
    if revision != COMMIT:
        raise RuntimeError("Expected the pinned initial Indoors release")
    changed = subprocess.check_output(["git","diff","--name-only","HEAD"],cwd=args.repository,text=True)
    if changed.strip():
        raise RuntimeError("Original Infinigen source must be unchanged")
    with run_lock(args.output), record_session(args.output, "infinigen-original-coarse"):
        profile_configs, _profile_overrides, profile_description = profile_command(
            args.profile, "bedroom", "Bedroom"
        )
        config = {"schemaVersion":1,"model":"infinigen","commit":COMMIT,"tag":"indoors-initial",
                  "targetPerRoom":args.per_room,"timeoutSeconds":args.timeout,"configs":profile_configs,
                  "profile":args.profile,"fastSolve":args.profile != "default","terrainEnabled":False,"blenderThreads":args.blender_threads,
                  "seedOffsets":{"bedroom":args.bedroom_seed_offset,"living_room":args.living_room_seed_offset},
                  "stage":"coarse task: solving, procedural meshes and scene serialization; no image rendering",
                  "profileDescription":profile_description,
                  "hardware":platform.platform(),"cpuThreadsAvailable":os.cpu_count()}
        # Omit the default field to preserve exact resume checks for existing
        # two-room campaigns. A single-room supplement has its own directory.
        if args.room_types != ['bedroom', 'living_room']:
            config['roomTypes'] = list(dict.fromkeys(args.room_types))
        if args.profile == 'controlled-count-fast':
            config['objectCount'] = args.object_count
            config['controlledRoleCounts'] = controlled_role_counts('bedroom', args.object_count)
            config['controlImplementation'] = {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ('infinigen_controlled_entry.py', 'infinigen_task.py')}
        manifest = args.output/"run.json"
        if manifest.exists() and json.loads(manifest.read_text()) != config:
            raise RuntimeError("Resume configuration changed")
        write_json(manifest,config)
        new_attempts = 0
        for room_type,parent,offset in (
            ("bedroom","Bedroom",args.bedroom_seed_offset),
            ("living_room","LivingRoom",100+args.living_room_seed_offset),
        ):
            if room_type not in args.room_types:
                continue
            existing = checkpoint_rows(args.output, room_type)
            if args.profile in CONTROLLED_PROFILES:
                existing = revalidate_controlled_checkpoints(existing, args.output, room_type, args.object_count)
            completed = sum(row.get("status") == "complete" for row in existing)
            index = len(existing)
            while completed < args.per_room:
                checkpoint = args.output/f"attempt-{room_type}-{index:03d}.json"
                if checkpoint.exists():
                    raise RuntimeError(f"Non-contiguous checkpoint sequence at {checkpoint}")
                if args.max_attempts and new_attempts >= args.max_attempts:
                    return
                new_attempts += 1
                if shutil.disk_usage(args.output).free < 15*1024**3:
                    raise RuntimeError("Paused before disk space falls below 15 GiB")
                # Infinigen interprets seed strings as hexadecimal. Record both.
                seed = format(offset+index,"x")
                work = args.output/f"scene-{room_type}-{index:03d}"
                # An interrupted subprocess may leave a partial directory
                # without a checkpoint. It is not resumable evidence and must
                # not be mixed with the deterministic retry of that seed.
                if work.exists():
                    shutil.rmtree(work)
                work.mkdir(exist_ok=True)
                configs, overrides, description = profile_command(args.profile, room_type, parent)
                if configs != config["configs"] or description != config["profileDescription"]:
                    raise RuntimeError("An Infinigen profile must retain one disclosed configuration across room types")
                entrypoint = (Path(__file__).with_name("infinigen_controlled_entry.py")
                              if args.profile in CONTROLLED_PROFILES
                              else args.repository/"infinigen_examples/generate_indoors.py")
                command = [str(args.blender),"--background","--threads",str(args.blender_threads),"--python-use-system-env","--python-exit-code","2",
                           "--python",str(entrypoint),"--","--seed",seed,"--task","coarse",
                           "--output_folder",str(work),"-g",*configs,"-p",*overrides]
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(args.site_packages)+os.pathsep+str(args.repository)
                environment["PYTHONHASHSEED"] = "0"
                # Set explicitly so an unrelated inherited variable cannot alter
                # the frozen six-object task on a subsequent invocation.
                environment['SOILIE_INFINIGEN_BEDROOM_COUNT'] = str(args.object_count)
                # Standalone Blender resolves some relative resources using PWD,
                # which subprocess cwd alone does not rewrite in the environment.
                environment["PWD"] = str(args.repository)
                row = {"id":f"infinigen-{room_type}-{seed}","roomType":room_type,"seed":offset+index,"seedArgument":seed,
                       "startedAt":datetime.now(UTC).isoformat(),"status":"failed","overrides":overrides,"command":command}
                started = time.perf_counter()
                with (work/"process.log").open("wb") as log:
                    process = subprocess.Popen(supervised(command),cwd=args.repository,env=environment,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    try:
                        process.wait(timeout=args.timeout)
                        if process.returncode == 0 and (work/"scene.blend").exists() and (work/"solve_state.json").exists():
                            if args.profile in CONTROLLED_PROFILES:
                                try:
                                    row["controlledRoles"] = validate_controlled_output(work, room_type, args.object_count)
                                    row["status"] = "complete"
                                except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
                                    row["errorCode"] = "CONTROLLED_COMPOSITION_MISMATCH"
                                    row["validationError"] = str(error)
                            else:
                                row["status"] = "complete"
                        else:
                            row["errorCode"] = "GENERATION_FAILED"
                    except subprocess.TimeoutExpired:
                        terminate_tree(process)
                        row["errorCode"] = "TIMEOUT"
                    except BaseException:
                        if process.poll() is None:
                            terminate_tree(process)
                        raise
                row["generationSeconds"] = time.perf_counter()-started
                row["outputDirectory"] = str(work)
                if row["status"] != "complete":
                    row["errorTail"] = (work/"process.log").read_text(errors="replace")[-4000:]
                write_json(checkpoint,row)
                print(json.dumps({"scene":row["id"],"status":row["status"],"seconds":row["generationSeconds"]}),flush=True)
                if row["status"] == "complete":
                    completed += 1
                index += 1


if __name__ == "__main__":
    main()
