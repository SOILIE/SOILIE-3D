"""SQS renderer handler that orchestrates Blender and publishes immutable results."""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
from PIL import Image, ImageSequence

from serverless.common.engine import json_dumps
from serverless.common.v4_runtime import (
    SCENE_SEED_STEP,
    V4GenerationError,
    animate_v4_output,
    generate_v4_inputs,
    load_v4_provenance,
    select_v4_objects,
)


TABLE_NAME = os.environ["JOB_TABLE"]
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "soilie3d-data")
OUTPUT_BASE_URL = os.environ.get("OUTPUT_BASE_URL", "https://soilie3d.com/data")
V4_RUNTIME_DIR = Path(os.environ.get("V4_RUNTIME_DIR", "/var/task/v4"))
BLENDER_PATH = os.environ.get("BLENDER_PATH", "/opt/blender/blender")

dynamodb = boto3.client("dynamodb")
s3 = boto3.client("s3")


def _key(job_id: str, sk: str) -> dict[str, dict[str, str]]:
    return {"pk": {"S": f"job#{job_id}"}, "sk": {"S": sk}}


def _scene_is_terminal(job_id: str, scene_index: int) -> bool:
    """Avoid rerendering a scene when SQS redelivers an old timed-out message."""
    response = dynamodb.get_item(
        TableName=TABLE_NAME,
        Key=_key(job_id, f"scene#{scene_index + 1:03d}"),
        ProjectionExpression="#status",
        ExpressionAttributeNames={"#status": "status"},
        ConsistentRead=True,
    )
    status = response.get("Item", {}).get("status", {}).get("S")
    return status in {"complete", "failed"}


def _stage(job_id: str, scene_index: int, status: str) -> None:
    now = int(time.time())
    dynamodb.update_item(
        TableName=TABLE_NAME,
        Key=_key(job_id, f"scene#{scene_index + 1:03d}"),
        UpdateExpression="SET #status = :status, updatedAt = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": {"S": status}, ":now": {"N": str(now)}},
    )
    if status in {"queued", "placing", "rendering", "animating"}:
        dynamodb.update_item(
            TableName=TABLE_NAME,
            Key=_key(job_id, "meta"),
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": {"S": status}},
        )


def _webp_from_v4_gif(source: Path, destination: Path) -> None:
    """Resize V4's completed GIF without replacing its frame sequence."""

    with Image.open(source) as animation:
        duration = int(animation.info.get("duration", 67))
        frames = []
        for frame in ImageSequence.Iterator(animation):
            resized = frame.convert("RGB")
            resized.thumbnail((512, 512), Image.Resampling.LANCZOS)
            frames.append(resized.copy())
    if not frames:
        raise RuntimeError("SOILIE V4 did not produce animation frames.")
    frames[0].save(
        destination,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        method=6,
        quality=78,
    )
    for frame in frames:
        frame.close()


def _parse_v4_result(stdout: str) -> dict[str, Any]:
    """Extract V4's result line without assuming it is Blender's last output.

    Blender may append shutdown diagnostics after the original renderer prints
    its JSON document. Reading the final matching line keeps the adapter thin
    while avoiding any change to V4's output behavior.
    """

    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate.startswith('{"status":'):
            continue
        try:
            document = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict) and "filename" in document and "data" in document:
            return document
    raise RuntimeError("SOILIE V4 did not return its result document.")


def _validate_room_boundary_result(render_result: dict[str, Any]) -> None:
    """Reject a manifest if visible V4 boundaries disagree with room metadata."""

    room = render_result.get("room", {})
    actual = room.get("actual", {})
    rows = {row.get("obj_name"): row for row in render_result.get("data", [])}
    required = {"FLOOR", "LEFT_WALL", "RIGHT_WALL", "FRONT_WALL", "BACK_WALL"}
    if not required <= rows.keys() or not {"widthM", "depthM", "heightM"} <= actual.keys():
        raise RuntimeError("The V4 room-fit extension returned incomplete boundary evidence.")

    checks = (
        (rows["FLOOR"]["dim_x"], actual["widthM"]),
        (rows["FLOOR"]["dim_y"], actual["depthM"]),
        (rows["LEFT_WALL"]["dim_x"], actual["widthM"]),
        (rows["RIGHT_WALL"]["dim_x"], actual["widthM"]),
        (rows["FRONT_WALL"]["dim_y"], actual["depthM"]),
        (rows["BACK_WALL"]["dim_y"], actual["depthM"]),
    )
    checks += tuple((rows[name]["dim_z"], actual["heightM"]) for name in required - {"FLOOR"})
    if any(not math.isclose(float(observed), float(expected), abs_tol=1e-4) for observed, expected in checks):
        raise RuntimeError("The visible V4 floor or walls disagree with the reported room dimensions.")


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        fieldnames = list(rows[0]) if rows else []
        writer = csv.DictWriter(destination, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _upload(
    output_dir: Path,
    job_id: str,
    scene_index: int,
    request: dict[str, Any],
    render_result: dict[str, Any],
    scene_seed: int,
) -> dict[str, Any]:
    prefix = f"generated/{job_id}/{scene_index + 1}"
    content_types = {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".csv": "text/csv; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }
    artifacts = {}
    names = {
        "ordinary.png": "ordinaryImage",
        "imagined.png": "imaginedImage",
        "formation.webp": "animatedPreview",
        "formation.gif": "animatedGif",
        "placements.csv": "placementsCsv",
        "request.json": "request",
    }
    for filename, label in names.items():
        path = output_dir / filename
        key = f"{prefix}/{filename}"
        s3.upload_file(
            str(path), OUTPUT_BUCKET, key,
            ExtraArgs={"ContentType": content_types[path.suffix], "CacheControl": "public, max-age=300, must-revalidate"},
        )
        artifacts[label] = f"{OUTPUT_BASE_URL}/{key}"

    manifest = {
        "schemaVersion": 2,
        "jobId": job_id,
        "sceneIndex": scene_index,
        "objects": [
            row["obj_name"].split(".")[0].lower()
            for row in render_result["data"]
            if row["obj_name"].lower()
            not in {"left_wall", "right_wall", "front_wall", "back_wall", "floor", "camera"}
        ],
        "room": render_result["room"],
        "seed": scene_seed,
        "model": {
            "name": "SOILIE-3D V4",
            "version": "24.07.05",
            "implementation": "original",
            "provenanceSha256": load_v4_provenance(V4_RUNTIME_DIR)["files"]["modules/render.py"]["sha256"],
        },
        "animationModel": "original-v4-change_imagination_focus",
        "formationViewpoint": {"mode": "original_v4_third_person", "gaze": "object_sequence"},
        "roomFit": {"stage": "after_v4_placement", "interiorPlacementChanged": False},
        "artifacts": artifacts,
        "expiresAt": (datetime.now(UTC) + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    key = f"{prefix}/manifest.json"
    s3.upload_file(
        str(manifest_path), OUTPUT_BUCKET, key,
        ExtraArgs={"ContentType": content_types[".json"], "CacheControl": "public, max-age=300, must-revalidate"},
    )
    artifacts["manifest"] = f"{OUTPUT_BASE_URL}/{key}"
    manifest["artifacts"] = artifacts
    return manifest


def _finish_job(job_id: str) -> None:
    response = dynamodb.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": f"job#{job_id}"}},
        ConsistentRead=True,
    )
    statuses = [
        item["status"]["S"] for item in response.get("Items", []) if item.get("entity", {}).get("S") == "scene"
    ]
    if statuses and all(status in {"complete", "failed"} for status in statuses):
        # A multi-scene request is successful only when every promised scene is
        # available. Individual successful artifacts remain visible if a peer
        # scene fails, but the job-level status stays truthful.
        overall = "failed" if any(status == "failed" for status in statuses) else "complete"
        dynamodb.update_item(
            TableName=TABLE_NAME,
            Key=_key(job_id, "meta"),
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": {"S": overall}},
        )


def _complete_scene(job_id: str, scene_index: int, result: dict[str, Any]) -> None:
    dynamodb.update_item(
        TableName=TABLE_NAME,
        Key=_key(job_id, f"scene#{scene_index + 1:03d}"),
        UpdateExpression="SET #status = :complete, resultJson = :result, updatedAt = :now REMOVE errorJson",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":complete": {"S": "complete"},
            ":result": {"S": json_dumps(result)},
            ":now": {"N": str(int(time.time()))},
        },
    )
    _finish_job(job_id)


def _fail_scene(job_id: str, scene_index: int, code: str, message: str) -> None:
    error = {"code": code, "message": message}
    dynamodb.update_item(
        TableName=TABLE_NAME,
        Key=_key(job_id, f"scene#{scene_index + 1:03d}"),
        UpdateExpression="SET #status = :failed, errorJson = :error, updatedAt = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":failed": {"S": "failed"},
            ":error": {"S": json_dumps(error)},
            ":now": {"N": str(int(time.time()))},
        },
    )
    print(json_dumps({"event": "generation_failed", "jobId": job_id, "sceneIndex": scene_index, "code": code}))
    _finish_job(job_id)


def _process(message: dict[str, Any], receive_count: int) -> None:
    job_id = message["jobId"]
    scene_index = int(message["sceneIndex"])
    if _scene_is_terminal(job_id, scene_index):
        print(json_dumps({"event": "generation_redelivery_skipped", "jobId": job_id, "sceneIndex": scene_index}))
        return
    request = message["request"]
    work_dir = Path("/tmp") / job_id / str(scene_index + 1)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    started = time.monotonic()
    try:
        _stage(job_id, scene_index, "placing")
        scene_seed = request["seed"] + scene_index * SCENE_SEED_STEP
        objects = select_v4_objects(V4_RUNTIME_DIR, request, scene_index)
        v4_inputs = generate_v4_inputs(V4_RUNTIME_DIR, objects, scene_seed)
        request_path = work_dir / "request.json"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        (work_dir / "v4-input.json").write_text(json.dumps(v4_inputs, indent=2), encoding="utf-8")

        for name in ("assets", "data", "modules", "suggested_setup.blend"):
            os.symlink(V4_RUNTIME_DIR / name, work_dir / name, target_is_directory=(name != "suggested_setup.blend"))
        (work_dir / "output").mkdir()

        _stage(job_id, scene_index, "rendering")
        environment = os.environ.copy()
        environment["SOILIE_ROOM_REQUEST"] = json.dumps(request["room"], separators=(",", ":"))
        result = subprocess.run(
            [
                BLENDER_PATH,
                "--background",
                "suggested_setup.blend",
                "--python-expr",
                f"import random; random.seed({scene_seed})",
                "--python",
                str(Path(__file__).with_name("v4_entrypoint.py")),
                "--",
                json.dumps(v4_inputs, separators=(",", ":")),
            ],
            capture_output=True,
            text=True,
            timeout=840,
            cwd=work_dir,
            env=environment,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Blender exited with code {result.returncode}: {result.stderr[-1500:]}")
        render_result = _parse_v4_result(result.stdout)
        _validate_room_boundary_result(render_result)
        v4_output = Path(render_result["path"])

        _stage(job_id, scene_index, "animating")
        original_gif = animate_v4_output(V4_RUNTIME_DIR, render_result["filename"], v4_output)
        shutil.copy2(v4_output / "01_birds_eye_view.png", work_dir / "ordinary.png")
        shutil.copy2(v4_output / "02_birds_eye_view.png", work_dir / "imagined.png")
        shutil.copy2(original_gif, work_dir / "formation.gif")
        _webp_from_v4_gif(original_gif, work_dir / "formation.webp")
        _write_csv(render_result["data"], work_dir / "placements.csv")
        manifest = _upload(work_dir, job_id, scene_index, request, render_result, scene_seed)
        _complete_scene(job_id, scene_index, manifest)
        print(
            json_dumps(
                {"event": "generation_complete", "jobId": job_id, "sceneIndex": scene_index, "durationMs": round((time.monotonic() - started) * 1000)}
            )
        )
    except V4GenerationError as error:
        _fail_scene(job_id, scene_index, "V4_GENERATION_FAILED", str(error))
    except Exception as error:
        if receive_count < 2:
            # The first failed attempt will be redelivered by SQS. Reflect that
            # state in the API instead of leaving the job stuck at rendering.
            _stage(job_id, scene_index, "queued")
            print(json_dumps({"event": "generation_retry", "jobId": job_id, "sceneIndex": scene_index, "errorType": type(error).__name__}))
            raise
        _fail_scene(job_id, scene_index, "RENDER_FAILED", "The renderer could not complete this scene after two attempts.")
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    for record in event.get("Records", []):
        _process(json.loads(record["body"]), int(record.get("attributes", {}).get("ApproximateReceiveCount", "1")))
    return {"batchItemFailures": []}
