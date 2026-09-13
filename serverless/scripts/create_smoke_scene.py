"""Create an exact V4 Blender input document for container smoke tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from serverless.common.v4_runtime import generate_v4_inputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=Path(".codex/runtime/v4"))
    parser.add_argument("--objects", nargs="+", default=["telephone", "office_chair", "bed"])
    parser.add_argument("--seed", type=int, default=41021)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    document = generate_v4_inputs(args.runtime.resolve(), args.objects, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"objects": args.objects, "seed": args.seed, "output": str(args.output)}))


if __name__ == "__main__":
    main()
