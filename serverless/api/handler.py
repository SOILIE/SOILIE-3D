"""API Gateway v2 handler for asynchronous SOILIE scene jobs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import boto3
from botocore.exceptions import ClientError

from serverless.common.engine import (
    RequestError,
    json_dumps,
    open_runtime,
    public_catalog_document,
    validate_request,
)


TABLE_NAME = os.environ["JOB_TABLE"]
QUEUE_URL = os.environ["QUEUE_URL"]
HMAC_SECRET = os.environ["VISITOR_HMAC_SECRET"].encode("utf-8")
RUNTIME_DB = os.environ.get("RUNTIME_DB", "/var/task/runtime/relations.sqlite3")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "soilie3d-data")
OUTPUT_BASE_URL = os.environ.get("OUTPUT_BASE_URL", "https://soilie3d.com/data").rstrip("/")
DISTRIBUTION_ID = os.environ.get("DISTRIBUTION_ID", "E233HJHDZKRH4")
GALLERY_AUTH_BUCKET = os.environ["GALLERY_AUTH_BUCKET"]
RESULT_TTL_DAYS = 7
STATUS_TTL_DAYS = 8
VISITOR_DAILY_LIMIT = 6
ACCOUNT_DAILY_LIMIT = 20
ACCOUNT_MONTHLY_LIMIT = 600

dynamodb = boto3.client("dynamodb")
sqs = boto3.client("sqs")
s3 = boto3.client("s3")
cloudfront = boto3.client("cloudfront")
_connection: sqlite3.Connection | None = None
_catalog_cache: dict[str, Any] | None = None
_study_document = json.loads((Path(__file__).parents[1] / "study" / "cases.json").read_text(encoding="utf-8"))
STUDY_COLLECTION_ENABLED = (
    os.environ.get("STUDY_COLLECTION_ENABLED", "false").lower() == "true"
    and _study_document.get("collectionEnabled") is True
)

ARTIFACT_FILES = {
    "ordinary.png": ("ordinaryImage", "image/png"),
    "imagined.png": ("imaginedImage", "image/png"),
    "formation.webp": ("animatedPreview", "image/webp"),
    "formation.gif": ("animatedGif", "image/gif"),
    "placements.csv": ("placementsCsv", "text/csv; charset=utf-8"),
    "request.json": ("request", "application/json; charset=utf-8"),
    "manifest.json": ("manifest", "application/json; charset=utf-8"),
}


def _db() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        _connection = open_runtime(RUNTIME_DB)
    return _connection


def _response(status: int, body: dict[str, Any], cache_control: str = "no-store") -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json; charset=utf-8", "cache-control": cache_control},
        "body": json_dumps(body),
    }


def _item_key(pk: str, sk: str = "meta") -> dict[str, dict[str, str]]:
    return {"pk": {"S": pk}, "sk": {"S": sk}}


def _get(pk: str, sk: str = "meta") -> dict[str, Any] | None:
    response = dynamodb.get_item(TableName=TABLE_NAME, Key=_item_key(pk, sk), ConsistentRead=True)
    return response.get("Item")


def _visitor_key(source_ip: str, day: str) -> str:
    digest = hmac.new(HMAC_SECRET, f"{day}:{source_ip}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"quota#visitor#{day}#{digest}"


def _quota_update(pk: str, amount: int, limit: int, expires_at: int) -> dict[str, Any]:
    return {
        "Update": {
            "TableName": TABLE_NAME,
            "Key": _item_key(pk),
            "UpdateExpression": "SET accepted = if_not_exists(accepted, :zero) + :amount, expiresAt = :expires",
            "ConditionExpression": "attribute_not_exists(accepted) OR accepted <= :threshold",
            "ExpressionAttributeValues": {
                ":zero": {"N": "0"},
                ":amount": {"N": str(amount)},
                ":threshold": {"N": str(limit - amount)},
                ":expires": {"N": str(expires_at)},
            },
        }
    }


def _management_token(job_id: str) -> str:
    """Derive a stable bearer token without storing the token itself."""
    return hmac.new(HMAC_SECRET, f"manage:{job_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def _study_token(session_id: str) -> str:
    return hmac.new(HMAC_SECRET, f"study:{session_id}".encode("utf-8"), hashlib.sha256).hexdigest()


def _study_assignment(session_id: str, case: dict[str, Any]) -> dict[str, Any]:
    """Return opaque, deterministically balanced sides without revealing conditions."""
    flip = hmac.new(HMAC_SECRET, f"{session_id}:{case['id']}".encode("utf-8"), hashlib.sha256).digest()[0] % 2 == 1
    comparison = case.get("comparisonImage", case.get("ablationImage"))
    left = comparison if flip else case["relationImage"]
    right = case["relationImage"] if flip else comparison
    return {"caseId": case["id"], "objects": case["objects"], "leftImage": left, "rightImage": right}


def _study_case(case_id: str) -> dict[str, Any] | None:
    return next((case for case in _study_document["cases"] if case["id"] == case_id), None)


def _study_parse_body(event: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        body = json.loads(event.get("body") or "{}")
        if not isinstance(body, dict):
            raise ValueError
        return body, None
    except (json.JSONDecodeError, ValueError):
        return None, _response(400, {"error": {"code": "INVALID_JSON", "message": "The request body is not valid JSON."}})


def _study_unavailable() -> dict[str, Any]:
    return _response(503, {
        "error": {
            "code": "STUDY_NOT_COLLECTING",
            "message": "The comparison protocol is prepared, but participant data collection is not open.",
        }
    })


def _study_start(event: dict[str, Any]) -> dict[str, Any]:
    if not STUDY_COLLECTION_ENABLED:
        return _study_unavailable()
    body, error = _study_parse_body(event)
    if error:
        return error
    participant = " ".join(str(body.get("participantLabel", "")).split())
    if not 2 <= len(participant) <= 60 or any(ord(character) < 32 for character in participant):
        return _response(400, {"error": {"code": "INVALID_PARTICIPANT_LABEL", "message": "Enter a participant name or assigned code between 2 and 60 characters."}})
    if body.get("consent") is not True or body.get("invited") is not True:
        return _response(400, {"error": {"code": "STUDY_ACKNOWLEDGEMENT_REQUIRED", "message": "Both study acknowledgements are required."}})

    session_id = str(uuid.uuid4())
    now = int(time.time())
    expires_at = int((datetime.now(UTC) + timedelta(days=365)).timestamp())
    dynamodb.put_item(
        TableName=TABLE_NAME,
        Item={
            "pk": {"S": f"study-session#{session_id}"},
            "sk": {"S": "meta"},
            "entity": {"S": "study-session"},
            "participantLabel": {"S": participant},
            "studyVersion": {"S": _study_document["studyVersion"]},
            "consentVersion": {"S": "2026-09-12"},
            "createdAt": {"N": str(now)},
            "expiresAt": {"N": str(expires_at)},
        },
        ConditionExpression="attribute_not_exists(pk)",
    )
    print(json_dumps({"event": "study_session_started", "sessionId": session_id, "studyVersion": _study_document["studyVersion"]}))
    return _response(201, {
        "sessionId": session_id,
        "sessionToken": _study_token(session_id),
        "studyVersion": _study_document["studyVersion"],
        "cases": [_study_assignment(session_id, case) for case in _study_document["cases"]],
        "completedCaseIds": [],
    })


def _study_session(event: dict[str, Any], session_id: str) -> dict[str, Any]:
    if not STUDY_COLLECTION_ENABLED:
        return _study_unavailable()
    try:
        session_id = str(uuid.UUID(session_id))
    except ValueError:
        return _response(404, {"error": {"code": "STUDY_SESSION_NOT_FOUND", "message": "That study session was not found."}})
    body, error = _study_parse_body(event)
    if error:
        return error
    if not hmac.compare_digest(str(body.get("sessionToken", "")), _study_token(session_id)):
        return _response(403, {"error": {"code": "STUDY_SESSION_FORBIDDEN", "message": "This browser cannot open that study session."}})
    records = dynamodb.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": f"study-session#{session_id}"}},
        ConsistentRead=True,
    ).get("Items", [])
    if not any(record["sk"]["S"] == "meta" for record in records):
        return _response(404, {"error": {"code": "STUDY_SESSION_NOT_FOUND", "message": "That study session was not found."}})
    completed = sorted(record["caseId"]["S"] for record in records if record.get("entity", {}).get("S") == "study-response")
    return _response(200, {
        "sessionId": session_id,
        "studyVersion": _study_document["studyVersion"],
        "cases": [_study_assignment(session_id, case) for case in _study_document["cases"]],
        "completedCaseIds": completed,
    })


def _study_response(event: dict[str, Any], session_id: str) -> dict[str, Any]:
    if not STUDY_COLLECTION_ENABLED:
        return _study_unavailable()
    body, error = _study_parse_body(event)
    if error:
        return error
    try:
        session_id = str(uuid.UUID(session_id))
    except ValueError:
        return _response(404, {"error": {"code": "STUDY_SESSION_NOT_FOUND", "message": "That study session was not found."}})
    if not hmac.compare_digest(str(body.get("sessionToken", "")), _study_token(session_id)):
        return _response(403, {"error": {"code": "STUDY_SESSION_FORBIDDEN", "message": "This browser cannot save to that study session."}})
    if not _get(f"study-session#{session_id}"):
        return _response(404, {"error": {"code": "STUDY_SESSION_NOT_FOUND", "message": "That study session was not found."}})
    case = _study_case(str(body.get("caseId", "")))
    judgement = body.get("judgement")
    error_choice = body.get("errorChoice")
    confidence = body.get("confidence")
    note = str(body.get("note", "")).strip()
    if case is None or judgement not in {"left", "tie", "right"} or error_choice not in {"left", "neither", "both", "right"}:
        return _response(400, {"error": {"code": "INVALID_STUDY_RESPONSE", "message": "Choose a plausibility judgement and an error judgement for a valid case."}})
    if not isinstance(confidence, int) or not 1 <= confidence <= 5 or len(note) > 280:
        return _response(400, {"error": {"code": "INVALID_STUDY_RESPONSE", "message": "Confidence must be 1–5 and a note may contain at most 280 characters."}})

    assignment = _study_assignment(session_id, case)
    comparison_condition = case.get("comparisonCondition", "ablation")
    left_condition = "relation" if assignment["leftImage"] == case["relationImage"] else comparison_condition
    now = int(time.time())
    expires_at = int((datetime.now(UTC) + timedelta(days=365)).timestamp())
    item = {
        "pk": {"S": f"study-session#{session_id}"},
        "sk": {"S": f"response#{case['id']}"},
        "entity": {"S": "study-response"},
        "caseId": {"S": case["id"]},
        "judgement": {"S": judgement},
        "errorChoice": {"S": error_choice},
        "confidence": {"N": str(confidence)},
        "leftCondition": {"S": left_condition},
        "rightCondition": {"S": comparison_condition if left_condition == "relation" else "relation"},
        "updatedAt": {"N": str(now)},
        "expiresAt": {"N": str(expires_at)},
    }
    if note:
        item["note"] = {"S": note}
    dynamodb.put_item(TableName=TABLE_NAME, Item=item)
    print(json_dumps({"event": "study_response_saved", "sessionId": session_id, "caseId": case["id"]}))
    return _response(200, {"saved": True, "caseId": case["id"]})


def _job_response(job_id: str, expires_at: int, replayed: bool = False) -> dict[str, Any]:
    return {
        "jobId": job_id,
        "status": "queued",
        "statusUrl": f"/generations/{job_id}",
        "expiresAt": datetime.fromtimestamp(expires_at, UTC).isoformat().replace("+00:00", "Z"),
        "managementToken": _management_token(job_id),
        "retention": {"mode": "temporary", "expiresAt": datetime.fromtimestamp(expires_at, UTC).isoformat().replace("+00:00", "Z")},
        "replayed": replayed,
    }


def _existing_request(client_request_id: str) -> dict[str, Any] | None:
    item = _get(f"request#{client_request_id}")
    if not item:
        return None
    result_expiry = int(item.get("resultExpiresAt", item["expiresAt"])["N"])
    return _job_response(item["jobId"]["S"], result_expiry, True)


def _submit(event: dict[str, Any]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": {"code": "INVALID_JSON", "message": "The request body is not valid JSON."}})

    try:
        request = validate_request(_db(), body)
    except RequestError as error:
        return _response(400, {"error": {"code": error.code, "message": error.message, "details": error.details}})

    existing = _existing_request(request["clientRequestId"])
    if existing:
        return _response(202, existing)

    now = datetime.now(UTC)
    now_epoch = int(now.timestamp())
    expires_at = int((now + timedelta(days=STATUS_TTL_DAYS)).timestamp())
    result_expires_at = int((now + timedelta(days=RESULT_TTL_DAYS)).timestamp())
    quota_expiry = int((now + timedelta(days=40)).timestamp())
    day = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    source_ip = event.get("requestContext", {}).get("http", {}).get("sourceIp", "unknown")
    scene_count = request["sceneCount"]
    job_id = str(uuid.uuid4())

    job_item = {
        "pk": {"S": f"job#{job_id}"},
        "sk": {"S": "meta"},
        "entity": {"S": "job"},
        "jobId": {"S": job_id},
        "requestJson": {"S": json_dumps(request)},
        "status": {"S": "queued"},
        "sceneCount": {"N": str(scene_count)},
        "createdAt": {"N": str(now_epoch)},
        "expiresAt": {"N": str(expires_at)},
        "resultExpiresAt": {"N": str(result_expires_at)},
    }
    transactions: list[dict[str, Any]] = [
        {
            "Put": {
                "TableName": TABLE_NAME,
                "Item": {
                    "pk": {"S": f"request#{request['clientRequestId']}"},
                    "sk": {"S": "meta"},
                    "entity": {"S": "idempotency"},
                    "jobId": {"S": job_id},
                    "expiresAt": {"N": str(expires_at)},
                    "resultExpiresAt": {"N": str(result_expires_at)},
                },
                "ConditionExpression": "attribute_not_exists(pk)",
            }
        },
        {"Put": {"TableName": TABLE_NAME, "Item": job_item, "ConditionExpression": "attribute_not_exists(pk)"}},
        _quota_update(_visitor_key(source_ip, day), scene_count, VISITOR_DAILY_LIMIT, quota_expiry),
        _quota_update(f"quota#account-day#{day}", scene_count, ACCOUNT_DAILY_LIMIT, quota_expiry),
        _quota_update(f"quota#account-month#{month}", scene_count, ACCOUNT_MONTHLY_LIMIT, quota_expiry),
    ]
    for scene_index in range(scene_count):
        transactions.append(
            {
                "Put": {
                    "TableName": TABLE_NAME,
                    "Item": {
                        "pk": {"S": f"job#{job_id}"},
                        "sk": {"S": f"scene#{scene_index + 1:03d}"},
                        "entity": {"S": "scene"},
                        "sceneIndex": {"N": str(scene_index)},
                        "status": {"S": "queued"},
                        "updatedAt": {"N": str(now_epoch)},
                        "expiresAt": {"N": str(expires_at)},
                        "resultExpiresAt": {"N": str(result_expires_at)},
                    },
                }
            }
        )

    try:
        dynamodb.transact_write_items(TransactItems=transactions)
    except ClientError as error:
        replay = _existing_request(request["clientRequestId"])
        if replay:
            return _response(202, replay)
        if error.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            return _response(
                429,
                {
                    "error": {
                        "code": "GENERATION_LIMIT_REACHED",
                        "message": "The public research demo has reached a generation limit. Please try again after the next UTC reset.",
                        "resetAt": f"{(now + timedelta(days=1)).date().isoformat()}T00:00:00Z",
                    }
                },
            )
        raise

    try:
        for scene_index in range(scene_count):
            message = {
                "jobId": job_id,
                "sceneIndex": scene_index,
                "request": request,
            }
            sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json_dumps(message))
    except Exception:
        dynamodb.update_item(
            TableName=TABLE_NAME,
            Key=_item_key(f"job#{job_id}"),
            UpdateExpression="SET #status = :failed, errorJson = :error",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":failed": {"S": "failed"},
                ":error": {"S": json_dumps({"code": "QUEUE_UNAVAILABLE", "message": "The job could not be queued."})},
            },
        )
        return _response(503, {"error": {"code": "QUEUE_UNAVAILABLE", "message": "The job could not be queued."}})

    print(json_dumps({"event": "generation_accepted", "jobId": job_id, "mode": request["mode"], "sceneCount": scene_count}))
    return _response(202, _job_response(job_id, result_expires_at))


def _deserialize_scene(item: dict[str, Any]) -> dict[str, Any]:
    scene: dict[str, Any] = {"sceneIndex": int(item["sceneIndex"]["N"]), "status": item["status"]["S"]}
    if "resultJson" in item:
        scene.update(json.loads(item["resultJson"]["S"]))
    if "errorJson" in item:
        scene["error"] = json.loads(item["errorJson"]["S"])
    if "retentionJson" in item:
        scene["retention"] = json.loads(item["retentionJson"]["S"])
    elif scene["status"] == "complete":
        expiry = scene.get("expiresAt")
        if not expiry and "resultExpiresAt" in item:
            expiry = datetime.fromtimestamp(int(item["resultExpiresAt"]["N"]), UTC).isoformat().replace("+00:00", "Z")
        scene["retention"] = {"mode": "temporary", "expiresAt": expiry}
    return scene


def _status(job_id: str) -> dict[str, Any]:
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        return _response(404, {"error": {"code": "JOB_NOT_FOUND", "message": "No generation job was found."}})
    response = dynamodb.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": f"job#{job_id}"}},
        ConsistentRead=True,
    )
    items = response.get("Items", [])
    meta = next((item for item in items if item["sk"]["S"] == "meta"), None)
    if not meta:
        return _response(404, {"error": {"code": "JOB_NOT_FOUND", "message": "No generation job was found."}})
    scenes = sorted(
        (_deserialize_scene(item) for item in items if item.get("entity", {}).get("S") == "scene"),
        key=lambda scene: scene["sceneIndex"],
    )
    body: dict[str, Any] = {
        "jobId": job_id,
        "status": meta["status"]["S"],
        "request": json.loads(meta["requestJson"]["S"]),
        "scenes": scenes,
        "expiresAt": datetime.fromtimestamp(int(meta.get("resultExpiresAt", meta["expiresAt"])["N"]), UTC).isoformat().replace("+00:00", "Z"),
    }
    if "errorJson" in meta:
        body["error"] = json.loads(meta["errorJson"]["S"])
    return _response(200, body)


def _retention_path(path: str) -> tuple[str, int] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[0] != "generations" or parts[2] != "scenes" or parts[4] != "retention":
        return None
    try:
        scene_number = int(parts[3])
        if scene_number < 1:
            return None
        return str(uuid.UUID(parts[1])), scene_number - 1
    except (ValueError, TypeError):
        return None


def _gallery_id(job_id: str, scene_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://soilie3d.com/generations/{job_id}/scenes/{scene_index + 1}"))


def _artifact_urls(prefix: str) -> dict[str, str]:
    return {label: f"{OUTPUT_BASE_URL}/{prefix}/{filename}" for filename, (label, _content_type) in ARTIFACT_FILES.items()}


def _delete_prefix(prefix: str) -> None:
    s3.delete_objects(
        Bucket=OUTPUT_BUCKET,
        Delete={"Objects": [{"Key": f"{prefix}/{filename}"} for filename in ARTIFACT_FILES], "Quiet": True},
    )


def _invalidate(paths: list[str]) -> None:
    if not paths:
        return
    try:
        cloudfront.create_invalidation(
            DistributionId=DISTRIBUTION_ID,
            InvalidationBatch={
                "Paths": {"Quantity": len(paths), "Items": paths},
                "CallerReference": f"retention-{uuid.uuid4()}",
            },
        )
    except ClientError as error:
        # Storage deletion is authoritative. A failed CDN invalidation is logged
        # for operations, while cached copies age out under their short TTL.
        print(json_dumps({"event": "retention_invalidation_failed", "code": error.response.get("Error", {}).get("Code", "UNKNOWN")}))


def _gallery_record(item: dict[str, Any]) -> dict[str, Any]:
    record = {
        "galleryId": item["galleryId"]["S"],
        "createdAt": datetime.fromtimestamp(int(item["createdAt"]["N"]), UTC).isoformat().replace("+00:00", "Z"),
        "publishedAt": datetime.fromtimestamp(int(item["publishedAt"]["N"]), UTC).isoformat().replace("+00:00", "Z"),
        "displayName": item.get("displayName", {"S": "Anonymous"})["S"],
        "objects": json.loads(item["objectsJson"]["S"]),
        "room": json.loads(item["roomJson"]["S"]),
        "seed": int(item["seed"]["N"]),
        "mode": item["mode"]["S"],
        "artifacts": json.loads(item["artifactsJson"]["S"]),
        "model": {"name": "SOILIE-3D V4", "implementation": item["modelImplementation"]["S"]},
    }
    if "roomType" in item:
        record["roomType"] = item["roomType"]["S"]
    return record


def _gallery(event: dict[str, Any]) -> dict[str, Any]:
    query = parse_qs(event.get("rawQueryString", ""))
    try:
        limit = min(100, max(1, int(query.get("limit", ["60"])[0])))
    except ValueError:
        limit = 60
    arguments: dict[str, Any] = {
        "TableName": TABLE_NAME,
        "KeyConditionExpression": "pk = :pk",
        "ExpressionAttributeValues": {":pk": {"S": "gallery"}},
        "ScanIndexForward": False,
        "Limit": limit,
    }
    cursor = query.get("cursor", [None])[0]
    if cursor:
        try:
            encoded_cursor = cursor.encode("ascii")
            encoded_cursor += b"=" * (-len(encoded_cursor) % 4)
            sk = base64.b64decode(encoded_cursor, altchars=b"-_", validate=True).decode("utf-8")
            arguments["ExclusiveStartKey"] = _item_key("gallery", sk)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return _response(400, {"error": {"code": "INVALID_CURSOR", "message": "The gallery cursor is invalid."}})
    response = dynamodb.query(**arguments)
    # Records created by the retired prototype renderer did not identify an
    # original-model implementation. Keep them out of the V4 gallery instead
    # of presenting approximate scenes as SOILIE-3D output.
    exact_items = [
        item
        for item in response.get("Items", [])
        if item.get("modelImplementation", {}).get("S") == "original"
    ]
    body: dict[str, Any] = {"items": [_gallery_record(item) for item in exact_items]}
    if response.get("LastEvaluatedKey"):
        sk = response["LastEvaluatedKey"]["sk"]["S"]
        body["nextCursor"] = base64.urlsafe_b64encode(sk.encode("utf-8")).decode("ascii").rstrip("=")
    return _response(200, body, "public, max-age=30, stale-while-revalidate=60")


def _display_name(value: Any) -> str:
    if value is None or not str(value).strip():
        return "Anonymous"
    name = " ".join(str(value).split())
    if len(name) > 40 or any(ord(character) < 32 for character in name):
        raise ValueError("A gallery name must be 40 characters or fewer.")
    return name


def _password_verifier(password: Any) -> dict[str, Any]:
    if not isinstance(password, str) or len(password) < 10 or len(password) > 128:
        raise ValueError("Use a password between 10 and 128 characters.")
    salt = os.urandom(16)
    iterations = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {
        "algorithm": "pbkdf2-sha256",
        "iterations": iterations,
        "salt": base64.b64encode(salt).decode("ascii"),
        "passwordHash": base64.b64encode(digest).decode("ascii"),
    }


def _password_matches(password: Any, verifier: dict[str, Any]) -> bool:
    if not isinstance(password, str) or verifier.get("algorithm") != "pbkdf2-sha256":
        return False
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(verifier["salt"]),
            int(verifier["iterations"]),
        )
        return hmac.compare_digest(digest, base64.b64decode(verifier["passwordHash"]))
    except (KeyError, ValueError, TypeError, binascii.Error):
        return False


def _auth_key(gallery_id: str) -> str:
    return f"gallery-auth/{gallery_id}.json"


def _write_gallery_auth(gallery_id: str, verifier: dict[str, Any]) -> None:
    document = {"schemaVersion": 1, "galleryId": gallery_id, **verifier}
    s3.put_object(
        Bucket=GALLERY_AUTH_BUCKET,
        Key=_auth_key(gallery_id),
        Body=f"{json.dumps(document, separators=(',', ':'))}\n".encode("utf-8"),
        ContentType="application/json; charset=utf-8",
        CacheControl="no-store",
        ServerSideEncryption="AES256",
    )


def _publish_scene(
    job_id: str,
    scene_index: int,
    meta: dict[str, Any],
    scene: dict[str, Any],
    display_name: str,
    verifier: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = json.loads(scene["resultJson"]["S"])
    if result.get("model", {}).get("implementation") != "original":
        raise ValueError("Only scenes produced by the original SOILIE-3D V4 renderer can be published.")
    request = json.loads(meta["requestJson"]["S"])
    gallery_id = _gallery_id(job_id, scene_index)
    source_prefix = f"generated/{job_id}/{scene_index + 1}"
    gallery_prefix = f"gallery/{gallery_id}"
    artifacts = _artifact_urls(gallery_prefix)
    try:
        for filename, (_label, content_type) in ARTIFACT_FILES.items():
            if filename == "manifest.json":
                continue
            s3.copy_object(
                Bucket=OUTPUT_BUCKET,
                CopySource={"Bucket": OUTPUT_BUCKET, "Key": f"{source_prefix}/{filename}"},
                Key=f"{gallery_prefix}/{filename}",
                MetadataDirective="REPLACE",
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable",
            )
        now = int(time.time())
        public_manifest = {
            **result,
            "schemaVersion": 2,
            "galleryId": gallery_id,
            "displayName": display_name,
            "artifacts": artifacts,
            "retention": {"mode": "public", "publishedAt": datetime.fromtimestamp(now, UTC).isoformat().replace("+00:00", "Z")},
        }
        public_manifest.pop("expiresAt", None)
        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=f"{gallery_prefix}/manifest.json",
            Body=f"{json.dumps(public_manifest, indent=2)}\n".encode("utf-8"),
            ContentType="application/json; charset=utf-8",
            CacheControl="public, max-age=31536000, immutable",
        )
    except ClientError:
        _delete_prefix(gallery_prefix)
        raise

    created_at = int(meta["createdAt"]["N"])
    gallery_sk = f"{created_at:010d}#{gallery_id}"
    item = {
        "pk": {"S": "gallery"},
        "sk": {"S": gallery_sk},
        "entity": {"S": "gallery-scene"},
        "galleryId": {"S": gallery_id},
        "createdAt": {"N": str(created_at)},
        "publishedAt": {"N": str(now)},
        "displayName": {"S": display_name},
        "objectsJson": {"S": json_dumps(result["objects"])},
        "roomJson": {"S": json_dumps(result["room"])},
        "seed": {"N": str(result["seed"])},
        "mode": {"S": request["mode"]},
        "artifactsJson": {"S": json_dumps(artifacts)},
        "modelImplementation": {"S": "original"},
    }
    if request.get("roomType"):
        item["roomType"] = {"S": request["roomType"]}
    pointer = {
        "pk": {"S": f"gallery-id#{gallery_id}"},
        "sk": {"S": "meta"},
        "entity": {"S": "gallery-pointer"},
        "galleryId": {"S": gallery_id},
        "gallerySk": {"S": gallery_sk},
        "jobId": {"S": job_id},
        "sceneIndex": {"N": str(scene_index)},
    }
    try:
        _write_gallery_auth(gallery_id, verifier)
        dynamodb.transact_write_items(
            TransactItems=[
                {"Put": {"TableName": TABLE_NAME, "Item": item}},
                {"Put": {"TableName": TABLE_NAME, "Item": pointer, "ConditionExpression": "attribute_not_exists(pk)"}},
            ]
        )
    except ClientError:
        _delete_prefix(gallery_prefix)
        try:
            s3.delete_object(Bucket=GALLERY_AUTH_BUCKET, Key=_auth_key(gallery_id))
        except ClientError:
            pass
        raise
    retention = {
        "mode": "public",
        "galleryId": gallery_id,
        "gallerySk": gallery_sk,
        "displayName": display_name,
        "publishedAt": datetime.fromtimestamp(now, UTC).isoformat().replace("+00:00", "Z"),
    }
    return retention, _gallery_record(item)


def _remove_public_gallery(retention: dict[str, Any]) -> None:
    gallery_id = retention["galleryId"]
    _delete_prefix(f"gallery/{gallery_id}")
    s3.delete_object(Bucket=GALLERY_AUTH_BUCKET, Key=_auth_key(gallery_id))
    dynamodb.transact_write_items(
        TransactItems=[
            {"Delete": {"TableName": TABLE_NAME, "Key": _item_key("gallery", retention["gallerySk"])}},
            {"Delete": {"TableName": TABLE_NAME, "Key": _item_key(f"gallery-id#{gallery_id}")}},
        ]
    )


def _restore_scene_after_gallery_removal(pointer: dict[str, Any], gallery_id: str) -> None:
    """Keep the originating job truthful after password-based removal elsewhere."""
    job_id = pointer.get("jobId", {}).get("S")
    scene_index = int(pointer.get("sceneIndex", {"N": "-1"})["N"])
    if not job_id or scene_index < 0:
        return
    scene = _get(f"job#{job_id}", f"scene#{scene_index + 1:03d}")
    if not scene or "retentionJson" not in scene:
        return
    current = json.loads(scene["retentionJson"]["S"])
    if current.get("mode") != "public" or current.get("galleryId") != gallery_id:
        return
    now = int(time.time())
    expiry_epoch = int(scene.get("resultExpiresAt", scene["expiresAt"])["N"])
    retention = (
        {"mode": "temporary", "expiresAt": datetime.fromtimestamp(expiry_epoch, UTC).isoformat().replace("+00:00", "Z")}
        if expiry_epoch > now
        else {"mode": "discarded", "discardedAt": datetime.fromtimestamp(now, UTC).isoformat().replace("+00:00", "Z")}
    )
    dynamodb.update_item(
        TableName=TABLE_NAME,
        Key=_item_key(f"job#{job_id}", f"scene#{scene_index + 1:03d}"),
        UpdateExpression="SET retentionJson = :retention, updatedAt = :now",
        ExpressionAttributeValues={
            ":retention": {"S": json_dumps(retention)},
            ":now": {"N": str(now)},
        },
    )


def _retention(event: dict[str, Any], job_id: str, scene_index: int) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": {"code": "INVALID_JSON", "message": "The request body is not valid JSON."}})
    action = body.get("action")
    if action not in {"temporary", "public", "discard"}:
        return _response(400, {"error": {"code": "INVALID_RETENTION_ACTION", "message": "Choose temporary, public, or discard."}})
    if not hmac.compare_digest(str(body.get("managementToken", "")), _management_token(job_id)):
        return _response(403, {"error": {"code": "RETENTION_FORBIDDEN", "message": "This browser cannot manage that result."}})

    meta = _get(f"job#{job_id}")
    scene = _get(f"job#{job_id}", f"scene#{scene_index + 1:03d}") if scene_index >= 0 else None
    if not meta or not scene:
        return _response(404, {"error": {"code": "JOB_NOT_FOUND", "message": "No generation scene was found."}})
    if scene.get("status", {}).get("S") != "complete" or "resultJson" not in scene:
        return _response(409, {"error": {"code": "RESULT_NOT_READY", "message": "Only a completed scene can change retention."}})

    expiry_epoch = int(scene.get("resultExpiresAt", meta.get("resultExpiresAt", meta["expiresAt"]))["N"])
    current = json.loads(scene.get("retentionJson", {"S": json_dumps({"mode": "temporary", "expiresAt": datetime.fromtimestamp(expiry_epoch, UTC).isoformat().replace("+00:00", "Z")})})["S"])
    target_mode = "discarded" if action == "discard" else action
    if current.get("mode") == target_mode:
        return _response(200, {"retention": current})
    if current.get("mode") == "discarded":
        return _response(409, {"error": {"code": "RESULT_DISCARDED", "message": "A discarded result cannot be restored."}})

    source_prefix = f"generated/{job_id}/{scene_index + 1}"
    invalidations: list[str] = []
    try:
        if action == "public":
            try:
                display_name = _display_name(body.get("displayName"))
                verifier = _password_verifier(body.get("password"))
            except ValueError as error:
                return _response(400, {"error": {"code": "INVALID_GALLERY_PROFILE", "message": str(error)}})
            retention, gallery_record = _publish_scene(job_id, scene_index, meta, scene, display_name, verifier)
            response_body: dict[str, Any] = {"retention": retention, "galleryItem": gallery_record}
        else:
            if current.get("mode") == "public":
                gallery_id = current["galleryId"]
                _remove_public_gallery(current)
                invalidations.append(f"/data/gallery/{gallery_id}/*")
            if action == "discard":
                _delete_prefix(source_prefix)
                invalidations.append(f"/data/{source_prefix}/*")
                retention = {"mode": "discarded", "discardedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z")}
            else:
                retention = {"mode": "temporary", "expiresAt": datetime.fromtimestamp(expiry_epoch, UTC).isoformat().replace("+00:00", "Z")}
            response_body = {"retention": retention}
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "STORAGE_UNAVAILABLE")
        status = 410 if code in {"NoSuchKey", "404"} else 503
        return _response(status, {"error": {"code": "RESULT_EXPIRED" if status == 410 else "RETENTION_UNAVAILABLE", "message": "The result is no longer available." if status == 410 else "The retention choice could not be saved."}})

    dynamodb.update_item(
        TableName=TABLE_NAME,
        Key=_item_key(f"job#{job_id}", f"scene#{scene_index + 1:03d}"),
        UpdateExpression="SET retentionJson = :retention, updatedAt = :now",
        ExpressionAttributeValues={
            ":retention": {"S": json_dumps(retention)},
            ":now": {"N": str(int(time.time()))},
        },
    )
    _invalidate(invalidations)
    print(json_dumps({"event": "retention_changed", "jobId": job_id, "sceneIndex": scene_index, "mode": retention["mode"]}))
    return _response(200, response_body)


def _delete_gallery(event: dict[str, Any], gallery_id: str) -> dict[str, Any]:
    try:
        gallery_id = str(uuid.UUID(gallery_id))
        body = json.loads(event.get("body") or "{}")
    except (ValueError, json.JSONDecodeError):
        return _response(400, {"error": {"code": "INVALID_GALLERY_REQUEST", "message": "The gallery removal request is invalid."}})
    pointer = _get(f"gallery-id#{gallery_id}")
    if not pointer:
        return _response(404, {"error": {"code": "GALLERY_ITEM_NOT_FOUND", "message": "That public scene is no longer in the gallery."}})
    try:
        auth_object = s3.get_object(Bucket=GALLERY_AUTH_BUCKET, Key=_auth_key(gallery_id))
        verifier = json.loads(auth_object["Body"].read())
    except (ClientError, json.JSONDecodeError):
        return _response(503, {"error": {"code": "GALLERY_AUTH_UNAVAILABLE", "message": "The removal password could not be checked."}})
    if not _password_matches(body.get("password"), verifier):
        return _response(403, {"error": {"code": "GALLERY_PASSWORD_INCORRECT", "message": "That removal password does not match."}})

    retention = {
        "mode": "public",
        "galleryId": gallery_id,
        "gallerySk": pointer["gallerySk"]["S"],
    }
    try:
        _remove_public_gallery(retention)
    except ClientError:
        return _response(503, {"error": {"code": "GALLERY_DELETE_UNAVAILABLE", "message": "The scene could not be removed just now."}})
    try:
        _restore_scene_after_gallery_removal(pointer, gallery_id)
    except (ClientError, ValueError, KeyError, json.JSONDecodeError) as error:
        # The public removal is authoritative even if an expired job record
        # cannot be reconciled. Log the stale metadata for operational review.
        print(json_dumps({"event": "gallery_source_reconcile_failed", "galleryId": gallery_id, "code": type(error).__name__}))
    _invalidate([f"/data/gallery/{gallery_id}/*"])
    print(json_dumps({"event": "gallery_removed", "galleryId": gallery_id}))
    return _response(200, {"removed": True, "galleryId": gallery_id})


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    global _catalog_cache
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    if method == "OPTIONS":
        return _response(204, {})
    if method == "GET" and path == "/catalog":
        if _catalog_cache is None:
            _catalog_cache = public_catalog_document(_db())
        return _response(200, _catalog_cache, "public, max-age=3600")
    if method == "POST" and path == "/generations":
        return _submit(event)
    if method == "POST" and path == "/study/sessions":
        return _study_start(event)
    if method == "POST" and path.startswith("/study/sessions/") and path.endswith("/responses"):
        return _study_response(event, path.split("/")[3])
    if method == "POST" and path.startswith("/study/sessions/"):
        return _study_session(event, path.rsplit("/", 1)[-1])
    if method == "GET" and path == "/gallery":
        return _gallery(event)
    if method == "DELETE" and path.startswith("/gallery/"):
        return _delete_gallery(event, path.rsplit("/", 1)[-1])
    retention_path = _retention_path(path)
    if method == "POST" and retention_path:
        return _retention(event, *retention_path)
    if method == "GET" and path.startswith("/generations/"):
        return _status(path.rsplit("/", 1)[-1])
    return _response(404, {"error": {"code": "NOT_FOUND", "message": "The requested endpoint does not exist."}})
