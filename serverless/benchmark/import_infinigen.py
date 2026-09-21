"""Export completed official scenes after construction timing has stopped."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from serverless.benchmark.geometry import measure
from serverless.benchmark.run_batch import write_json
from serverless.benchmark.run_infinigen import COMMIT
from serverless.benchmark.timing import session_summary
from serverless.benchmark.supervise import command as supervised


def sha256(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            checksum.update(block)
    return checksum.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--repository",type=Path,required=True)
    parser.add_argument("--blender",type=Path,required=True)
    parser.add_argument("--site-packages",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    for name in ("run","repository","blender","site_packages","output"):
        setattr(args,name,getattr(args,name).resolve())
    config = json.loads((args.run/"run.json").read_text())
    revision = subprocess.check_output(["git","rev-parse","HEAD"],cwd=args.repository,text=True).strip()
    if revision != COMMIT or config["commit"] != COMMIT or config.get("profile", "default") not in {
        "default", "tutorial-fast", "matched-furniture-fast"
    }:
        raise ValueError("Expected a documented profile from the pinned original Indoors release")
    if subprocess.check_output(["git","diff","--name-only","HEAD"],cwd=args.repository,text=True).strip():
        raise ValueError("The source used for export must also remain unchanged")
    environment = dict(os.environ,PYTHONPATH=str(args.site_packages)+os.pathsep+str(args.repository),PWD=str(args.repository))
    scenes, invalid, attempts, original_attempts = [], [], [], []
    exporter = Path(__file__).with_name("export_infinigen.py")
    for path in sorted(args.run.glob("attempt-*.json")):
        attempt = json.loads(path.read_text())
        original_attempts.append(attempt)
        attempts.append({key:attempt[key] for key in ("id","status","roomType","seed","generationSeconds")})
        if attempt["status"] != "complete":
            attempts[-1]["errorCode"] = attempt["errorCode"]
            continue
        directory = Path(attempt["outputDirectory"])
        artifact = directory/"geometry.json"
        command = [str(args.blender),"--background",str(directory/"scene.blend"),"--threads","4",
                   "--python-use-system-env","--python-exit-code","2","--python",str(exporter),"--",
                   "--state",str(directory/"solve_state.json"),"--room-type",attempt["roomType"],
                   "--id",attempt["id"],"--output",str(artifact)]
        # A new exporter revision must never accidentally reuse an older capture.
        expected = {"exporterSha256":sha256(exporter),"metadataImporterSha256":sha256(exporter.with_name("infinigen_metadata.py")),
                    "supportSamplerSha256":sha256(exporter.with_name("mesh_support.py")),
                    "blendSha256":sha256(directory/"scene.blend")}
        receipt = directory/"geometry-receipt.json"
        cached = artifact.exists() and receipt.exists() and json.loads(receipt.read_text()) == expected
        if not cached:
            try:
                with (directory/"export.log").open("wb") as log:
                    result = subprocess.run(supervised(command),cwd=args.repository,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=900)
            except subprocess.TimeoutExpired:
                invalid.append({"id":attempt["id"],"reason":"GEOMETRY_EXPORT_TIMEOUT"})
                continue
            if result.returncode:
                invalid.append({"id":attempt["id"],"reason":"GEOMETRY_EXPORT_FAILED", "exitCode":result.returncode})
                continue
            write_json(receipt,expected)
        scene = json.loads(artifact.read_text())
        scene["provenance"].update(expected,commit=COMMIT)
        try:
            measure(scene)
        except (ValueError,KeyError,TypeError) as error:
            invalid.append({"id":attempt["id"],"reason":str(error)})
            continue
        scenes.append(scene)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    write_json(args.output,{"scenes":scenes,"invalidArtifacts":invalid,"attempts":attempts,"configuration":config,
                           "sessionTiming":session_summary(args.run,original_attempts)})
    print(json.dumps({"scenes":len(scenes),"invalidGeometry":len(invalid),"attempts":len(attempts)}))


if __name__ == "__main__":
    main()
