"""Compact evidence that measurement hooks leave ordinary placements unchanged."""
import argparse
import hashlib
import json
from pathlib import Path

from serverless.benchmark.run_batch import write_json


def digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def placement(stage):
    # Support rays are a read-only extra measurement. They are not a placement
    # field, so a support replay can be checked against a non-support run.
    return {"units": stage["units"], "room": stage["room"],
            "objects": [{key: value for key, value in obj.items() if key != "support"}
                        for obj in stage["objects"]]}


def compare(left, right):
    if left["request"] != right["request"] or left.get("selection") != right.get("selection"):
        raise ValueError("Parity requires identical requests and selected instances")
    if left["status"] != right["status"]:
        raise ValueError("The same request had different completion outcomes")
    result = {"seed": left["request"]["seed"], "requestedCount": left["request"]["objectCount"],
              "status": left["status"], "requestSha256": digest(left["request"])}
    if left["status"] == "complete":
        stages = ("beforeSeparation", "afterSeparation", "final")
        result["stageSha256"] = {}
        for name in stages:
            first, second = placement(left["stages"][name]), placement(right["stages"][name])
            if first != second:
                raise ValueError(f"Placement differs at {name} for seed {result['seed']}")
            result["stageSha256"][name] = digest(first)
        result["exactPlacementEquality"] = True
    else:
        if left["errorCode"] != right["errorCode"]:
            raise ValueError("The same request had different failure codes")
        result["errorCode"] = left["errorCode"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--layouts", type=Path, nargs="+", required=True)
    parser.add_argument("--full", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    layouts, full, provenance = {}, [], []
    for kind, folders in (("layout", args.layouts), ("full", args.full)):
        for folder in folders:
            config = json.loads((folder/"run.json").read_text())
            if bool(config["full"]) != (kind == "full") or config["roomFitIncluded"]:
                raise ValueError("Expected ordinary full renders versus placement-only runs, without room fitting")
            provenance.append(digest(config["provenance"]))
            for path in sorted(folder.glob("attempt-*.json")):
                row = json.loads(path.read_text())
                if kind == "layout":
                    layouts[(row["request"]["roomType"], row["request"]["seed"])] = row
                else:
                    full.append(row)
    if len(set(provenance)) != 1:
        raise ValueError("Runtime assets or source differ between parity runs")
    results = [compare(layouts[(row["request"]["roomType"], row["request"]["seed"])], row) for row in full]
    counts = {row["requestedCount"] for row in results if row["status"] == "complete"}
    if counts != {3, 4, 5, 6}:
        raise ValueError("Full-render equality must cover successful requested counts 3 through 6")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, {"schemaVersion": 1, "runtimeProvenanceSha256": provenance[0], "checks": results,
                             "method": "Exact equality of original room boundary and every object transform and box, at all three observation stages"})
    print(json.dumps({"checks": len(results), "successfulRequestedCounts": sorted(counts), "output": str(args.output)}))


if __name__ == "__main__":
    main()
