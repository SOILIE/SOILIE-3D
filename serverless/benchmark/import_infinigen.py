"""Export completed official scenes after construction timing has stopped."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess

from serverless.benchmark.geometry import measure
from serverless.benchmark.run_batch import write_json
from serverless.benchmark.run_infinigen import COMMIT, PROFILES
from serverless.benchmark.infinigen_task import (CONTROLLED_PROFILES, INVENTORY_VERSION,
                                                validate_controlled_output)
from serverless.benchmark.infinigen_metadata import generated_instances
from serverless.benchmark.timing import session_summary
from serverless.benchmark.supervise import command as supervised


def sha256(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            checksum.update(block)
    return checksum.hexdigest()


def require_complete_sample(scenes, invalid, configuration):
    """Reject a publication export until every requested completed scene is usable."""
    expected = configuration["targetPerRoom"]
    counts = Counter(scene["roomType"] for scene in scenes)
    missing = {room_type: expected - counts.get(room_type, 0)
               for room_type in configuration.get('roomTypes', ("bedroom", "living_room"))
               if counts.get(room_type, 0) != expected}
    unexpected = set(counts) - set(configuration.get('roomTypes', ('bedroom', 'living_room')))
    if invalid or missing or unexpected:
        raise RuntimeError(
            f"Infinigen publication requires {expected} valid scenes per room type; "
            f"counts={dict(counts)}, invalidArtifacts={len(invalid)}, differences={missing}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--repository",type=Path,required=True)
    parser.add_argument("--blender",type=Path,required=True)
    parser.add_argument("--site-packages",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--timing-exclusions",type=Path,
                        help="Internal ledger of completed scene IDs whose stopwatches were not isolated")
    parser.add_argument("--timing-ineligible",action="store_true",
                        help="Mark every completed scene in this geometry-only shard as ineligible for latency summaries")
    parser.add_argument("--workers",type=int,default=1,
                        help="Independent Blender exporters to run concurrently after generation timing has stopped")
    parser.add_argument('--native-isolation-plan', type=Path,
                        help='Frozen credential-free Docker boundary for native Blender only')
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    for name in ("run","repository","blender","site_packages","output"):
        setattr(args,name,getattr(args,name).resolve())
    if args.timing_exclusions:
        args.timing_exclusions = args.timing_exclusions.resolve()
    config = json.loads((args.run/"run.json").read_text())
    revision = subprocess.check_output(["git","rev-parse","HEAD"],cwd=args.repository,text=True).strip()
    if revision != COMMIT or config["commit"] != COMMIT or config.get("profile", "default") not in PROFILES:
        raise ValueError("Expected a documented profile from the pinned original Indoors release")
    if subprocess.check_output(["git","diff","--name-only","HEAD"],cwd=args.repository,text=True).strip():
        raise ValueError("The source used for export must also remain unchanged")
    environment = dict(os.environ,PYTHONPATH=str(args.site_packages)+os.pathsep+str(args.repository),PWD=str(args.repository))
    timing_exclusions = set()
    if args.timing_exclusions:
        timing_exclusions = set(json.loads(args.timing_exclusions.read_text())["sceneIds"])
    seen_timing_exclusions = set()
    scenes, invalid, attempts, original_attempts, completed_attempts = [], [], [], [], []
    exporter = Path(__file__).with_name("export_infinigen.py")
    for path in sorted(args.run.glob("attempt-*.json")):
        attempt = json.loads(path.read_text())
        original_attempts.append(attempt)
        summary = {key:attempt[key] for key in ("id","status","roomType","seed","generationSeconds")}
        if args.timing_ineligible and attempt["status"] == "complete":
            summary["timingEligible"] = False
        if attempt["id"] in timing_exclusions:
            if attempt["status"] != "complete":
                raise ValueError(f"A timing exclusion must identify a completed scene: {attempt['id']}")
            summary["timingEligible"] = False
            seen_timing_exclusions.add(attempt["id"])
        attempts.append(summary)
        if attempt["status"] != "complete":
            attempts[-1]["errorCode"] = attempt["errorCode"]
            continue
        completed_attempts.append(attempt)

    if seen_timing_exclusions != timing_exclusions:
        raise ValueError(f"Unknown timing exclusion IDs: {sorted(timing_exclusions-seen_timing_exclusions)}")

    def export_attempt(attempt):
        directory = Path(attempt["outputDirectory"])
        artifact = directory/"geometry.json"
        if config.get('profile') == 'controlled-inventory-fast':
            if config.get('inventoryVersion') != INVENTORY_VERSION:
                return None, {'id': attempt['id'], 'reason': 'Unknown controlled inventory version'}
            try:
                roles = validate_controlled_output(directory, attempt['roomType'],
                                                   config['objectCount'], config['inventoryVersion'])
                declared = config['controlledRoleCountsByRoom'][attempt['roomType']]
                if Counter(roles) != Counter({role: n for role, n in declared.items() if n}):
                    raise ValueError('Run inventory does not match the pinned inventory contract')
            except (OSError, ValueError, KeyError, TypeError) as error:
                return None, {'id': attempt['id'], 'reason': str(error)}
        command = [str(args.blender),"--background",str(directory/"scene.blend"),"--threads",str(config.get('blenderThreads', 4)),
                   "--python-use-system-env","--python-exit-code","2","--python",str(exporter),"--",
                   "--state",str(directory/"solve_state.json"),"--room-type",attempt["roomType"],
                   "--id",attempt["id"],"--output",str(artifact)]
        # A new exporter revision must never accidentally reuse an older capture.
        expected = {"exporterSha256":sha256(exporter),"metadataImporterSha256":sha256(exporter.with_name("infinigen_metadata.py")),
                    "supportSamplerSha256":sha256(exporter.with_name("mesh_support.py")),
                    "blendSha256":sha256(directory/"scene.blend"),
                    "stateSha256":sha256(directory/"solve_state.json")}
        receipt = directory/"geometry-receipt.json"
        if args.native_isolation_plan:
            from serverless.benchmark.native_isolation import wrapped_command
            isolation_plan = json.loads(args.native_isolation_plan.read_bytes())
            expected['nativeIsolationPlanSha256'] = isolation_plan['planSha256']
            command = wrapped_command(command, args.native_isolation_plan.resolve(), directory,
                                      args.run/f'export-native-isolation-{directory.name}.json', 870)
        cached = artifact.exists() and receipt.exists() and json.loads(receipt.read_text()) == expected
        if not cached:
            try:
                export_log = (args.run/f'native-export-{directory.name}.log'
                              if args.native_isolation_plan else directory/'export.log')
                with export_log.open("wb") as log:
                    result = subprocess.run(supervised(command),cwd=args.repository,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=900)
                if args.native_isolation_plan:
                    from serverless.benchmark.native_isolation import workspace_bytes
                    workspace_bytes(directory)  # Before JSON/provenance reads.
            except subprocess.TimeoutExpired:
                return None, {"id":attempt["id"],"reason":"GEOMETRY_EXPORT_TIMEOUT"}
            if result.returncode:
                return None, {"id":attempt["id"],"reason":"GEOMETRY_EXPORT_FAILED", "exitCode":result.returncode}
            write_json(receipt,expected)
        scene = json.loads(artifact.read_text())
        if config.get("profile") in CONTROLLED_PROFILES:
            scene["sourceSceneId"] = scene["id"]
            scene["id"] = "controlled-" + scene["id"]
            scene["model"] = "infinigen_controlled"
            scene["benchmarkVariant"] = config['profile']
            if config['profile'] == 'controlled-count-fast':
                scene['provenance']['controlledObjectCount'] = config['objectCount']
                scene['provenance']['controlledRoleCounts'] = config['controlledRoleCounts']
                scene['provenance']['controlImplementation'] = config['controlImplementation']
            elif config['profile'] == 'controlled-inventory-fast':
                # Count semantic roots, not the component meshes making up an
                # asset. Every declared root must survive geometry export once.
                records = json.loads((directory/'solve_state.json').read_text())['objs']
                _room, instances = generated_instances(records, attempt['roomType'])
                expected_ids = {identifier for identifier, _record in instances}
                objects = scene.get('objects', [])
                observed_ids = [obj.get('id') for obj in objects if obj.get('kind') == 'furniture']
                if (len(observed_ids) != config['objectCount'] or len(objects) != len(observed_ids)
                        or len(set(observed_ids)) != len(observed_ids) or set(observed_ids) != expected_ids):
                    return None, {'id': attempt['id'], 'reason': 'Exported semantic inventory differs from solver inventory'}
                scene['provenance'].update({
                    'inventoryVersion': config['inventoryVersion'],
                    'controlledObjectCount': config['objectCount'],
                    'controlledRoleCounts': config['controlledRoleCountsByRoom'][attempt['roomType']],
                    'inventoryScope': config['inventoryScope'],
                    'controlImplementation': config['controlImplementation'],
                })
        scene["provenance"].update(expected,commit=COMMIT)
        try:
            measure(scene)
        except (ValueError,KeyError,TypeError) as error:
            return None, {"id":attempt["id"],"reason":str(error)}
        return scene, None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for scene, error in pool.map(export_attempt, completed_attempts):
            if error:
                invalid.append(error)
            else:
                scenes.append(scene)
    require_complete_sample(scenes, invalid, config)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    write_json(args.output,{"scenes":scenes,"invalidArtifacts":invalid,"attempts":attempts,"configuration":config,
                           "sessionTiming":session_summary(args.run,original_attempts)})
    print(json.dumps({"scenes":len(scenes),"invalidGeometry":len(invalid),"attempts":len(attempts)}))


if __name__ == "__main__":
    main()
