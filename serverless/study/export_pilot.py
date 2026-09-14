"""Publish an AI-only export without session IDs, bearer tokens or invitations."""
import argparse
from collections import Counter
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path

from serverless.study.service import PROFILES, RUBRIC
from serverless.study.store import SQLiteStudyStore

LABELS = {"layoutgpt":"LayoutGPT", "infinigen":"Infinigen Indoors"}


def preference(row):
    return "tie" if row["judgement"] == "tie" else row[row["judgement"]+"Condition"]


def aggregate(store, protocol):
    responses, reviewers = [], []
    seen = set()
    for session in store.sessions():
        if session.get("respondentType") != "ai_pilot" or session.get("studyVersion") != protocol["studyVersion"]:
            continue # Human/test/old-version records never enter an AI export.
        reviewer = session["reviewerId"]
        if reviewer in seen or session["promptProfile"] not in PROFILES:
            raise ValueError("Duplicate reviewer identity or unregistered profile")
        seen.add(reviewer)
        rows = store.responses(session["sessionId"])
        assignments = {row["caseId"]:row for row in session["assignments"]}
        if len({row["caseId"] for row in rows}) != len(rows):
            raise ValueError("Duplicate response")
        for row in rows:
            assignment = assignments.get(row["caseId"])
            if assignment is None or any(row.get(key) != assignment[key] for key in ("leftCondition","rightCondition","comparisonCondition","repeatOf")):
                raise ValueError("Response does not match its immutable assignment")
            if any(row.get(key) != session[key] for key in ("respondentType","studyVersion","reviewerId","promptProfile","model","promptHash")):
                raise ValueError("Response provenance differs from server-controlled session")
        main = [row for row in rows if row["repeatOf"] is None]
        by_case = {row["caseId"]:row for row in rows}
        controls = [row for row in rows if row["repeatOf"] in by_case]
        votes = Counter(preference(row) for row in main)
        reviewers.append({"reviewerId":reviewer,"profile":session["promptProfile"],"model":session["model"],
                          "promptHash":session["promptHash"],"responses":len(main),"votes":dict(votes),
                          "complete":len(rows)==len(assignments),"repeatComparisons":len(controls),
                          "agreements":sum(preference(row)==preference(by_case[row["repeatOf"]]) for row in controls)})
        responses.extend(rows)
    conditions = []
    for condition in sorted({case["comparisonCondition"] for case in protocol["cases"]}):
        rows = [row for row in responses if row["comparisonCondition"] == condition and row["repeatOf"] is None]
        votes = Counter(preference(row) for row in rows)
        conditions.append({"id":condition,"label":LABELS[condition],"cases":sum(case["comparisonCondition"]==condition for case in protocol["cases"]),
                           "responses":len(rows),"soilie":votes["soilie"],"tie":votes["tie"],"baseline":votes[condition]})
    safe_keys = {"caseId","judgement","errorChoice","confidence","note","respondentType","reviewerId","promptProfile",
                 "model","promptHash","studyVersion","leftCondition","rightCondition","comparisonCondition","repeatOf","recordedAt"}
    safe_rows = [{key:row[key] for key in sorted(safe_keys) if key in row} for row in responses]
    return {"schemaVersion":1,"studyVersion":protocol["studyVersion"],"respondentType":"ai_pilot",
            "generatedAt":datetime.now(UTC).isoformat(),"humanParticipants":0,"reviewersPlanned":10,
            "reviewersCompleted":sum(row["complete"] for row in reviewers),"conditions":conditions,
            "reviewers":sorted(reviewers,key=lambda row:row["reviewerId"]),"responses":safe_rows,
            "rubric":RUBRIC,"promptProfiles":PROFILES,"sampling":protocol.get("sampling"),
            "stimulusEvidence":protocol.get("stimulusEvidence",[]),
            "stimuli":[{"caseId":case["id"],"soilieImage":case["relationImage"],
                        "baselineImage":case["comparisonImage"],"baseline":case["comparisonCondition"]}
                       for case in protocol["cases"]],
            "stimulusVersionDigest":hashlib.sha256(json.dumps(protocol,sort_keys=True).encode()).hexdigest(),
            "limitations":["Multiple reviewers rate the same frozen pairs; rating counts are not counts of independent scene pairs.",
                           "Reviewers may share an underlying model; prompt variation is not evidence of calibrated accuracy.",
                           "Bounding-box views omit mesh detail and can have overlapping labels in crowded arrangements.",
                           "Reversed-side repeats assess response consistency, not correctness."],
            "interpretation":"Exploratory AI opinions and integration-test evidence only. Not human validation, independent model architectures, or a calibrated measure of accuracy. Repeated cases are excluded from preference totals."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database",type=Path,required=True)
    parser.add_argument("--protocol",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--require-complete",action="store_true")
    args = parser.parse_args()
    result = aggregate(SQLiteStudyStore(args.database),json.loads(args.protocol.read_text()))
    if args.require_complete and (result["reviewersCompleted"] != 10 or {row["profile"] for row in result["reviewers"]} != set(PROFILES)):
        raise RuntimeError("All ten registered reviewers must complete the frozen task before final publication")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({"reviewersCompleted":result["reviewersCompleted"],"responses":len(result["responses"])}))


if __name__ == "__main__":
    main()
