"""Merge a parity-checked mesh observation into a campaign checkpoint.

The command deliberately replaces only the final read-only overlap result. It
cannot change a request, selected instance, placement, timing, or provenance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from serverless.benchmark.run_batch import write_json
from serverless.benchmark.verify_parity import compare


def merge(campaign: dict, replay: dict) -> dict:
    parity = compare(campaign, replay)
    if not parity.get("exactPlacementEquality"):
        raise ValueError("Only complete, exactly equal placements can be merged")

    measurement = replay["stages"]["final"].get("solidMeshOverlap")
    if not measurement or measurement.get("method") != "evaluated-mesh-intersection-v2":
        raise ValueError("Replay does not contain the current mesh-intersection measurement")
    if not measurement.get("complete") or measurement.get("unavailablePairs"):
        raise ValueError("Incomplete mesh measurements cannot enter a published campaign")

    merged = json.loads(json.dumps(campaign))
    merged["stages"]["final"]["solidMeshOverlap"] = measurement
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-attempt", type=Path, required=True)
    parser.add_argument("--replay-attempt", type=Path, required=True)
    args = parser.parse_args()

    campaign = json.loads(args.campaign_attempt.read_text())
    replay = json.loads(args.replay_attempt.read_text())
    merged = merge(campaign, replay)
    write_json(args.campaign_attempt, merged)
    print(json.dumps({
        "scene": merged["id"],
        "request": parity_request(merged),
        "meshMeasurementComplete": True,
    }, separators=(",", ":")))


def parity_request(row: dict) -> dict:
    return {
        "seed": row["request"]["seed"],
        "objectCount": row["request"]["objectCount"],
        "selection": row["selection"],
    }


if __name__ == "__main__":
    main()
