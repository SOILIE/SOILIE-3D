"""Checkpointed original Indoors single-room construction, without fast_solve.

The official coarse task includes solving, procedural meshes and preparation of
the scene file. It does not render an image. Timings must retain that stage label
and cannot be presented as equivalent to LayoutGPT's bounding-box response.
"""
import argparse
from datetime import datetime, UTC
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

COMMIT = "fb7991e06580639202a4687937082cb63e931eb0"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository",type=Path,required=True)
    parser.add_argument("--site-packages",type=Path,required=True)
    parser.add_argument("--blender",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--per-room",type=int,default=20)
    parser.add_argument("--timeout",type=int,default=21600)
    parser.add_argument("--max-attempts",type=int,default=0)
    args = parser.parse_args()
    for name in ("repository","site_packages","blender","output"):
        setattr(args,name,getattr(args,name).resolve())
    revision = subprocess.check_output(["git","rev-parse","HEAD"],cwd=args.repository,text=True).strip()
    if revision != COMMIT:
        raise RuntimeError("Expected the pinned initial Indoors release")
    changed = subprocess.check_output(["git","diff","--name-only","HEAD"],cwd=args.repository,text=True)
    if changed.strip():
        raise RuntimeError("Original Infinigen source must be unchanged")
    with run_lock(args.output), record_session(args.output, "infinigen-original-coarse"):
        config = {"schemaVersion":1,"model":"infinigen","commit":COMMIT,"tag":"indoors-initial",
                  "targetPerRoom":args.per_room,"timeoutSeconds":args.timeout,"configs":["singleroom.gin"],
                  "fastSolve":False,"terrainEnabled":False,"blenderThreads":4,
                  "stage":"original coarse task: solving, procedural meshes and scene serialization; no image rendering",
                  "hardware":platform.platform(),"cpuThreadsAvailable":os.cpu_count()}
        manifest = args.output/"run.json"
        if manifest.exists() and json.loads(manifest.read_text()) != config:
            raise RuntimeError("Resume configuration changed")
        write_json(manifest,config)
        attempted = 0
        for room_type,parent,offset in (("bedroom","Bedroom",0), ("living_room","LivingRoom",100)):
            for index in range(args.per_room):
                checkpoint = args.output/f"attempt-{room_type}-{index:03d}.json"
                attempted += 1
                if checkpoint.exists():
                    continue
                if args.max_attempts and attempted > args.max_attempts:
                    return
                if shutil.disk_usage(args.output).free < 15*1024**3:
                    raise RuntimeError("Paused before disk space falls below 15 GiB")
                # Infinigen interprets seed strings as hexadecimal. Record both.
                seed = format(offset+index,"x")
                work = args.output/f"scene-{room_type}-{index:03d}"
                work.mkdir(exist_ok=True)
                overrides = ["compose_indoors.terrain_enabled=False",f"restrict_solving.restrict_parent_rooms=['{parent}']"]
                command = [str(args.blender),"--background","--threads","4","--python-use-system-env","--python-exit-code","2",
                           "--python",str(args.repository/"infinigen_examples/generate_indoors.py"),"--","--seed",seed,"--task","coarse",
                           "--output_folder",str(work),"-g","singleroom.gin","-p",*overrides]
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(args.site_packages)+os.pathsep+str(args.repository)
                environment["PYTHONHASHSEED"] = "0"
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


if __name__ == "__main__":
    main()
