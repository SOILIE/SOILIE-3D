"""Explain the earlier shorter selections using observed original sampler calls.

This is a configuration check, not a scene-quality score. The observer records
the sampler's returned list without changing the list or its random state.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

from serverless.common.v4_runtime import _load_original_modules, select_v4_objects


def audit(runtime, source):
    raw = source.read_bytes()
    previous = json.loads(raw)
    _, combinations, _ = _load_original_modules(runtime)
    original = combinations.load
    calls = []
    def observed(*args,**kwargs):
        value = original(*args,**kwargs)
        calls.append(list(value))
        return value
    rows = []
    combinations.load = observed
    try:
        for prior in previous["rows"]:
            request = {"mode":"random" if prior["mode"]=="random" else "room_type", "roomType":prior["mode"],
                       "seed":prior["seed"],"objectCount":prior["requestedObjectCount"], "allowDuplicates":False,
                       "sameObjectsAcrossScenes":True}
            calls.clear()
            selected = select_v4_objects(runtime,request,0)
            sampled = calls[-1]
            if len(selected) != prior["selectedObjectCount"] or ("selectedObjects" in prior and Counter(selected) != Counter(prior["selectedObjects"])):
                raise RuntimeError(f"Original selection replay differs for {prior['mode']} seed {prior['seed']}; check the pinned runtime and PYTHONHASHSEED")
            shorter = len(selected) < request["objectCount"]
            explained = len(sampled)==request["objectCount"] and set(sampled)==set(selected) and len(set(sampled))==len(selected)
            enabled = select_v4_objects(runtime,dict(request,allowDuplicates=True),0)
            rows.append({"mode":prior["mode"],"seed":prior["seed"],"requested":request["objectCount"],
                         "sampledBeforeDeduplication":sampled,"selectedWithDuplicatesDisabled":selected,
                         "selectedWithDuplicatesEnabled":enabled,"shorter":shorter,"explainedByDeduplication":explained})
    finally:
        combinations.load = original
    consolidated = [row for row in previous["rows"] if "window" in row.get("selectedObjects",[]) and "blinds" in row.get("selectedObjects",[])
                    and sum(label in {"window","blinds","curtain"} for label in row.get("returnedObjects",[])) == 1]
    shorter = [row for row in rows if row["shorter"]]
    return {"schemaVersion":1,"sourceAuditSha256":hashlib.sha256(raw).hexdigest(),"sourceCommit":previous["model"]["baselineCommit"],
            "shorterSelections":len(shorter),"auditedSelections":len(rows),"allCausedByDuplicateRemoval":all(row["explainedByDeduplication"] for row in shorter),
            "architecturalConsolidationCases":[{"seed":row["seed"],"selected":row["selectedObjects"],"returned":row["returnedObjects"]} for row in consolidated],
            "rows":rows,"interpretation":"Duplicate-disabled selection intentionally collapses repeated labels. This is expected configuration behavior, not a quality metric."}


def main():
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise RuntimeError("Run the historical selection replay with PYTHONHASHSEED=0, matching its recorded set ordering")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime",type=Path,required=True)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    result = audit(args.runtime.resolve(),args.source)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({key:result[key] for key in ("shorterSelections","auditedSelections","allCausedByDuplicateRemoval")}))


if __name__ == "__main__":
    main()
