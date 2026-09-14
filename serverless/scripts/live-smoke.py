"""Verify the deployed catalog and two asynchronous generation paths."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
import uuid


def request(url: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "GET" if payload is None else "POST"
    call = urllib.request.Request(url, data=body, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(call, timeout=30) as response:
        return json.load(response)


def generation(endpoint: str, payload: dict) -> dict:
    accepted = request(f"{endpoint}/generations", payload)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        status = request(f"{endpoint}/generations/{accepted['jobId']}")
        if status["status"] in {"complete", "failed"}:
            if status["status"] != "complete":
                raise RuntimeError(f"Live generation failed: {status}")
            return status
        time.sleep(8)
    raise TimeoutError("Live generation did not finish within 15 minutes")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    endpoint = args.endpoint.rstrip("/")
    catalog = request(f"{endpoint}/catalog")
    if catalog["model"]["version"] != args.version or catalog["model"]["sourceCommit"] != args.commit:
        raise RuntimeError(f"Catalog provenance differs from deployment: {catalog['model']}")

    common = {
        "sceneCount": 1,
        "sameObjectsAcrossScenes": True,
        "allowDuplicates": True,
        "room": {"mode": "auto"},
    }
    ordinary = {
        **common,
        "clientRequestId": str(uuid.uuid4()),
        "mode": "room_type",
        "roomType": "bedroom",
        "objectCount": 3,
        "seed": 20260913,
    }
    formerly_cycling = {
        **common,
        "clientRequestId": str(uuid.uuid4()),
        "mode": "room_type",
        "roomType": "bedroom",
        "objectCount": 3,
        "seed": 20627809,
    }
    results = [generation(endpoint, payload) for payload in (ordinary, formerly_cycling)]
    print(json.dumps({"catalog": catalog["model"], "jobs": [row["jobId"] for row in results]}))


if __name__ == "__main__":
    main()
