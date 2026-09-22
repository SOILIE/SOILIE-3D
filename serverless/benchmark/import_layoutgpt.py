"""Fetch pinned author-released layouts and preserve their native pixel units."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.request import urlopen

from serverless.benchmark.geometry import box_corners

COMMIT = "fc31954962553e5b65bf267a904a6930d50b1f5e"
FILES = {"bedroom": "gpt4.bedroom.k-similar.k_8.px_regular.json",
         "living_room": "gpt4.livingroom.k-similar.k_4.px_regular.json"}
CHECKSUMS = {
    "bedroom": "7ce88e5cb79d81616b4f938e5237fd3f8aaf8c2ad010c0be0b46e7412cd8bcaa",
    "living_room": "c1b01d6ba24193377165e38ee13e70a03f3e5979c30d732b1ebd52862d73ad04",
}


def verify_source(raw, room_type):
    checksum = hashlib.sha256(raw).hexdigest()
    if checksum != CHECKSUMS[room_type]:
        raise ValueError("Released artifact differs from the pinned, verified source checksum")
    return checksum


def normalize(layout, room_type, index, checksum):
    matches = re.search(r"max length\s+([0-9.]+)px,\s+max width\s+([0-9.]+)px", layout["prompt"], re.I)
    if not matches:
        raise ValueError("Missing room dimensions in released prompt")
    width, depth = map(float, matches.groups())
    objects = []
    for number, (label, box) in enumerate(layout["object_list"]):
        center = [float(box[key]) for key in ("left", "top", "depth")]
        size = [float(box[key]) for key in ("length", "width", "height")]
        if min(size) <= 0:
            raise ValueError("Non-positive released object dimensions")
        yaw = -float(box["orientation"])
        angle = math.radians(yaw)
        objects.append({"id": f"object-{number:03d}", "label": label, "kind": "furniture",
                        "corners": box_corners(center, size, yaw),
                        "frontDirection": [math.cos(angle), math.sin(angle)],
                        "frontConvention": "released local +X orientation heading"})
    return {"schemaVersion": 1, "id": f"layoutgpt-{room_type}-{index:04d}", "model": "layoutgpt",
            "roomType": room_type, "units": "px", "stage": "released-final-layout", "objects": objects,
            "room": {"polygon": [[0,0],[width,0],[width,depth],[0,depth]], "floorZ": 0},
            "provenance": {"commit": COMMIT, "sha256": checksum, "row": index,
                           "file": FILES[room_type], "inferenceRerun": False}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows, failures, sources = [], [], []
    for room_type, filename in FILES.items():
        url = f"https://raw.githubusercontent.com/UCSB-AI/LayoutGPT/{COMMIT}/llm_output/3D/{filename}"
        raw_path = args.output/filename
        if not raw_path.exists():
            raw_path.write_bytes(urlopen(url, timeout=60).read())
        raw = raw_path.read_bytes()
        checksum = verify_source(raw, room_type)
        layouts = json.loads(raw)
        sources.append({"url": url, "sha256": checksum, "rows": len(layouts)})
        for index, layout in enumerate(layouts):
            try:
                rows.append(normalize(layout, room_type, index, checksum))
            except (ValueError, KeyError, TypeError) as error:
                failures.append({"roomType": room_type, "row": index, "error": str(error)})
    document = {"sources": sources, "scenes": rows, "invalidArtifacts": failures,
                "sourceCount": sum(source["rows"] for source in sources)}
    (args.output/"layouts.json").write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"layouts": len(rows), "invalidArtifacts": len(failures), "sources": sources}))


if __name__ == "__main__":
    main()
