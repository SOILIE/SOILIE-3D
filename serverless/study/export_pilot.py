"""Publish an AI-only export without session IDs, bearer tokens or invitations."""
import argparse
from collections import Counter
import hashlib
import hmac
import json
import math
from pathlib import Path
import random
import re
from copy import deepcopy

from serverless.study.service import (EVIDENCE_RUBRICS, FOCUS_PROFILES, FOCUS_ONLY_RUBRIC,
                                      PROFILES, RUBRIC, prompt_text)
from serverless.study.store import SQLiteStudyStore

LABELS = {"layoutgpt":"LayoutGPT", "infinigen":"Infinigen Indoors",
          "infinigen_controlled":"Infinigen Indoors"}
DIAGNOSTIC_THEMES = {
    "crowdingOrCollision": r"overlap|collid|crowd|cluster|cramp|obstruct|clearance",
    "boundaryOrWallUse": r"wall|boundar|outside|edge|corner",
    "functionalRelationships": r"orient|facing|face |relation|paired|pairing|bedside|nightstand|coher|functional",
    "relativeScale": r"scale|proportion|oversi|undersi|too large|too small|larger|smaller",
}


def preference(row):
    return "tie" if row["judgement"] == "tie" else row[row["judgement"]+"Condition"]


def _majority(votes, baseline):
    values = {"soilie": votes["soilie"], "tie": votes["tie"], baseline: votes[baseline]}
    best = max(values.values(), default=0)
    winners = [name for name, value in values.items() if value == best]
    return winners[0] if len(winners) == 1 else "tie"


def _decisive_share(rows):
    votes = Counter(preference(row) for row in rows)
    decisive = votes["soilie"] + sum(value for key, value in votes.items() if key not in {"soilie", "tie"})
    return 100 * votes["soilie"] / decisive if decisive else None


def _cluster_bootstrap(case_rows, seed=20260920, samples=5000):
    """Resample scene pairs, keeping correlated reviewer ratings together."""
    if not case_rows:
        return None
    generator = random.Random(seed)
    values = []
    for _ in range(samples):
        sampled = [generator.choice(case_rows) for _ in case_rows]
        share = _decisive_share([response for group in sampled for response in group])
        if share is not None:
            values.append(share)
    if not values:
        return None
    values.sort()
    return {"lowPct": values[int(.025 * (len(values) - 1))],
            "highPct": values[int(.975 * (len(values) - 1))],
            "method": f"{samples}-resample percentile interval clustered by scene pair"}


def _pair_majority_sign_test(majorities, baseline):
    """Exact two-sided test after collapsing correlated ratings to one result per pair."""
    soilie = majorities["soilie"]
    other = majorities[baseline]
    decisive = soilie + other
    if not decisive:
        return {"decisivePairs": 0, "soiliePairs": 0, "baselinePairs": 0,
                "soiliePairSharePct": None, "twoSidedExactP": None}
    tail = min(soilie, other)
    probability = min(1.0, 2 * sum(math.comb(decisive, i) for i in range(tail + 1)) / (2 ** decisive))
    return {"decisivePairs": decisive, "soiliePairs": soilie, "baselinePairs": other,
            "soiliePairSharePct": 100 * soilie / decisive, "twoSidedExactP": probability,
            "method": "two-sided exact binomial sign test over distinct-pair majorities; tied pair majorities excluded"}


def _profile_diagnostics(rows, baseline):
    """Post-hoc descriptive coding of the reviewers' own rationales.

    Themes deliberately overlap and are never treated as independent votes or
    causal explanations. They make the interpretation auditable without asking
    readers to infer why a focus row differs from its label.
    """
    diagnostics = []
    for profile in ("orientation", "proportions"):
        selected = [row for row in rows if row["promptProfile"] == profile
                    and preference(row) == baseline]
        diagnostics.append({
            "profile": profile,
            "baselinePreferredResponses": len(selected),
            "themes": {name: sum(bool(re.search(pattern, row.get("note", ""), re.I)) for row in selected)
                       for name, pattern in DIAGNOSTIC_THEMES.items()},
        })
    return {"rows":diagnostics,"countsOverlap":True,
            "method":"Post-hoc case-insensitive keyword coding of baseline-preference rationales. One rationale can count in several themes; counts describe reviewer explanations and do not prove model defects."}


def _metric_dominance(case, baseline):
    """Identify strict Pareto dominance on the displayed lower-is-better metrics."""
    left = {row["id"]: row for row in case.get("relationMetrics", [])
            if row.get("availability") == "measured" and row.get("direction") == "lower"}
    right = {row["id"]: row for row in case.get("comparisonMetrics", [])
             if row.get("availability") == "measured" and row.get("direction") == "lower"}
    identifiers = sorted(set(left) & set(right))
    if not identifiers:
        return "unavailable"
    comparisons = []
    for identifier in identifiers:
        difference = float(left[identifier]["value"]) - float(right[identifier]["value"])
        comparisons.append(0 if abs(difference) <= 1e-9 else (-1 if difference < 0 else 1))
    if all(value == 0 for value in comparisons):
        return "equal"
    if all(value <= 0 for value in comparisons):
        return "soilie"
    if all(value >= 0 for value in comparisons):
        return baseline
    return "tradeoff"


def _metric_alignment(protocol, grouped, baseline):
    if protocol.get("evidenceMode", "visual_only") == "visual_only":
        return None
    cases = {case["id"]: case for case in protocol["cases"]
             if case["comparisonCondition"] == baseline}
    dominance = {case_id: _metric_dominance(case, baseline) for case_id, case in cases.items()}
    counts = Counter(dominance.values())
    by_relation = {name: Counter() for name in ("soilie", baseline, "equal", "tradeoff", "unavailable")}
    aligned = opposed = tied = 0
    for case_id, rows in grouped.items():
        preferred = dominance[case_id]
        by_relation[preferred].update(preference(row) for row in rows)
        if preferred not in {"soilie", baseline}:
            continue
        for row in rows:
            choice = preference(row)
            if choice == "tie":
                tied += 1
            elif choice == preferred:
                aligned += 1
            else:
                opposed += 1
    return {"pairDominance":{"soilie":counts["soilie"],"baseline":counts[baseline],
                              "equal":counts["equal"],"tradeoff":counts["tradeoff"],
                              "unavailable":counts["unavailable"]},
            "preferencesByNumericRelation":[
                {"relation":name,"pairs":counts[name],"soilie":votes["soilie"],
                 "tie":votes["tie"],"baseline":votes[baseline]}
                for name, votes in by_relation.items() if counts[name]
            ],
            "ratingsOnDominatedPairs":aligned+opposed+tied,"aligned":aligned,"opposed":opposed,"tie":tied,
            "interpretation":"Dominance means one room is no worse on every displayed lower-is-better measurement and strictly better on at least one. Alignment describes whether an overall AI preference follows that narrow numeric result; disagreement can reflect the visual arrangement rather than an arithmetic error."}


def condition_result(condition, protocol, responses):
    evidence = {row["caseId"]: row for row in protocol.get("stimulusEvidence", [])}
    case_ids = [case["id"] for case in protocol["cases"] if case["comparisonCondition"] == condition]
    grouped = {case_id: [row for row in responses
                         if row["comparisonCondition"] == condition
                         and row["repeatOf"] is None and row["caseId"] == case_id]
               for case_id in case_ids}
    rows = [row for values in grouped.values() for row in values]
    votes = Counter(preference(row) for row in rows)
    pair_results = []
    for case_id, values in grouped.items():
        pair_votes = Counter(preference(row) for row in values)
        pair_results.append({"caseId": case_id, "responses": len(values),
                             "soilie": pair_votes["soilie"], "tie": pair_votes["tie"],
                             "baseline": pair_votes[condition], "majority": _majority(pair_votes, condition),
                             "semanticSimilarity": evidence.get(case_id, {}).get("semanticSimilarity")})
    majorities = Counter(row["majority"] for row in pair_results)
    strata = []
    for identifier, label, predicate in (
        ("high_match", "At least two-thirds of object roles match", lambda value: value is not None and value >= 2/3),
        ("broader_match", "40% to less than two-thirds of object roles match", lambda value: value is not None and .4 <= value < 2/3),
    ):
        selected_ids = {row["caseId"] for row in pair_results if predicate(row["semanticSimilarity"])}
        selected = [row for row in rows if row["caseId"] in selected_ids]
        selected_votes = Counter(preference(row) for row in selected)
        strata.append({"id": identifier, "label": label, "pairs": len(selected_ids),
                       "responses": len(selected), "soilie": selected_votes["soilie"],
                       "tie": selected_votes["tie"], "baseline": selected_votes[condition],
                       "soilieDecisivePreferencePct": _decisive_share(selected)})
    return {"id":condition,"label":LABELS[condition],"cases":len(case_ids),
            "responses":len(rows),"soilie":votes["soilie"],"tie":votes["tie"],"baseline":votes[condition],
            "soilieDecisivePreferencePct":_decisive_share(rows),
            "pairClustered95PctInterval":_cluster_bootstrap(list(grouped.values())),
            "pairMajorities":{"soilie":majorities["soilie"],"tie":majorities["tie"],
                              "baseline":majorities[condition]},
            "pairMajoritySignTest":_pair_majority_sign_test(majorities, condition),
            "semanticStrata":strata,"pairResults":pair_results,
            "profileDiagnostics":_profile_diagnostics(rows, condition),
            "metricAlignment":_metric_alignment(protocol, grouped, condition)}


def focused_dimension_results(protocol, responses, reviewers, room_type=None):
    """Keep visual dimensions separate instead of inventing one composite rank."""
    if protocol.get("decisionScope") != "focus_only":
        return []
    results = []
    ordered_profiles = list(dict.fromkeys(protocol.get("reviewerPlan", [])))
    baseline = next(iter(sorted({case["comparisonCondition"] for case in protocol["cases"]})))
    case_ids = {case["id"] for case in protocol["cases"] if case["comparisonCondition"] == baseline}
    if room_type is not None:
        # Room membership comes from frozen matching evidence, never a
        # reviewer's wording or inferred preference.
        case_ids &= {row['caseId'] for row in protocol.get('stimulusEvidence', [])
                     if row['matchingStratum'][0] == room_type}
    for profile in ordered_profiles:
        rows = [row for row in responses if row["promptProfile"] == profile
                and row["repeatOf"] is None and row["caseId"] in case_ids]
        grouped = [[row for row in rows if row["caseId"] == case_id] for case_id in sorted(case_ids)]
        votes = Counter(preference(row) for row in rows)
        majorities = Counter(_majority(Counter(preference(row) for row in group), baseline)
                             for group in grouped)
        results.append({"id":profile,"label":PROFILES[profile],
                        "reviewers":sum(row["profile"] == profile for row in reviewers),
                        "pairs":len(case_ids),"responses":len(rows),
                        "soilie":votes["soilie"],"tie":votes["tie"],"baseline":votes[baseline],
                        "soilieDecisivePreferencePct":_decisive_share(rows),
                        "pairClustered95PctInterval":_cluster_bootstrap(grouped),
                        "pairMajorities":{"soilie":majorities["soilie"],"tie":majorities["tie"],
                                          "baseline":majorities[baseline]}})
    return results


def matching_description(protocol):
    sampling = protocol.get('sampling') or {}
    sources = sampling.get('sourceSampling')
    if sources:
        rules = {(row['minimumSemanticSimilarity'], row['maximumFurnitureDensityDifference']) for row in sources}
        if len(rules) != 1:
            raise ValueError('Multiple matching policies require an explicit stratified methods description')
        similarity, density = next(iter(rules))
        sampling = {**sampling, 'minimumSemanticSimilarity': similarity, 'maximumFurnitureDensityDifference': density}
    similarity = sampling.get('minimumSemanticSimilarity', .4)
    density = sampling.get('maximumFurnitureDensityDifference', .25)
    return (f'Pairs share room type, exact furniture-instance count and room-anchor count '
            f'(beds in bedrooms; sofas in living rooms). At least {similarity * 100:.1f}% of '
            f'duplicate-aware normalized object roles agree. Summed-footprint density differs '
            f'by at most {density:g}; density is summed floor-supported furniture footprint '
            f'area divided by floor area. Only present objects are judged.')


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
            stable_keys = ("respondentType","studyVersion","reviewerId","promptProfile","model","promptHash")
            if (any(row.get(key) != session[key] for key in stable_keys)
                or row.get("evidenceMode", "visual_only") != session.get("evidenceMode", "visual_only")):
                raise ValueError("Response provenance differs from server-controlled session")
        main = [row for row in rows if row["repeatOf"] is None]
        by_case = {row["caseId"]:row for row in rows}
        controls = [row for row in rows if row["repeatOf"] in by_case]
        votes = Counter(preference(row) for row in main)
        published_prompt = prompt_text(protocol, session["promptProfile"])
        if not hmac.compare_digest(session["promptHash"], hashlib.sha256(published_prompt.encode()).hexdigest()):
            raise ValueError("Published reviewer prompt differs from the immutable session prompt")
        configuration = protocol.get("reviewerConfiguration") or {}
        reviewers.append({"reviewerId":reviewer,"profile":session["promptProfile"],"model":session["model"],
                          "reportedModel":configuration.get("model", session["model"]),
                          "reportedReasoningEffort":configuration.get("reasoningEffort"),
                          "promptHash":session["promptHash"],"reviewPrompt":published_prompt,
                          "decisionRubric":FOCUS_ONLY_RUBRIC if protocol.get("decisionScope") == "focus_only" else RUBRIC,
                          "dimensionRubric":FOCUS_PROFILES.get(session["promptProfile"]),
                          "evidenceRubric":EVIDENCE_RUBRICS[session.get("evidenceMode", "visual_only")],
                          "interfaceEmphasis":PROFILES[session["promptProfile"]],
                          "responses":len(main),"votes":dict(votes),
                          "complete":len(rows)==len(assignments),"repeatComparisons":len(controls),
                          "agreements":sum(preference(row)==preference(by_case[row["repeatOf"]]) for row in controls)})
        responses.extend(rows)
    focused = protocol.get("decisionScope") == "focus_only"
    # A vote made under an orientation-only instruction is not commensurate
    # with one made under a proportions-only instruction. Never collapse the
    # focused study into the legacy overall-preference total.
    conditions = [] if focused else [condition_result(condition, protocol, responses)
                                     for condition in sorted({case["comparisonCondition"] for case in protocol["cases"]})]
    safe_keys = {"caseId","judgement","errorChoice","confidence","note","respondentType","reviewerId","promptProfile","evidenceMode",
                 "model","promptHash","studyVersion","leftCondition","rightCondition","comparisonCondition","repeatOf"}
    safe_rows = [{key:row[key] for key in sorted(safe_keys) if key in row} for row in responses]
    reviewer_plan = protocol.get("reviewerPlan") or list(PROFILES)
    limitations = ([
        "Two review contexts judge each visual dimension; their ratings are correlated within each frozen scene pair and are not independent scene samples.",
        "Review contexts may share an underlying model; separate context and prompt assignment do not establish independent model architectures or calibrated accuracy.",
        matching_description(protocol),
        "Bounding-box views preserve final placement, rotation and dimensions but omit mesh detail and independently fit each room to the canvas.",
        "The proportions question compares which room has more believable pairwise size relationships among its present objects, using within-room bounding-box volumes normalized to the smallest object. It does not assess shape, aspect ratio or physical-size ground truth.",
        "Reversed-side repeats assess response consistency, not correctness.",
    ] if focused else [
        "Multiple reviewers rate the same frozen pairs; rating counts are not counts of independent scene pairs.",
        "Reviewers may share an underlying model; prompt variation is not evidence of calibrated accuracy.",
        "The matched cohort fixes room type, furniture count, bed count and density, but only requires 40% normalized object-role agreement; high-match results are therefore reported separately.",
        "Bounding-box views omit mesh detail and can have overlapping labels in crowded arrangements.",
        "Reversed-side repeats assess response consistency, not correctness.",
    ])
    return {"schemaVersion":1,"studyVersion":protocol["studyVersion"],"respondentType":"ai_pilot",
            "humanParticipants":0,"reviewersPlanned":len(reviewer_plan),
            "reviewersCompleted":sum(row["complete"] for row in reviewers),"conditions":conditions,
            "decisionScope":protocol.get("decisionScope", "overall"),
            "aggregateAcrossDimensions":not focused,
            "dimensionResults":focused_dimension_results(protocol, responses, reviewers),
            "dimensionsByRoomType":{room: focused_dimension_results(protocol, responses, reviewers, room)
                                    for room in sorted({row['matchingStratum'][0]
                                                        for row in protocol.get('stimulusEvidence', [])})},
            "reviewers":sorted(reviewers,key=lambda row:row["reviewerId"]),"responses":safe_rows,
            "rubric":FOCUS_ONLY_RUBRIC if protocol.get("decisionScope") == "focus_only" else RUBRIC,
            "promptProfiles":{profile:PROFILES[profile] for profile in dict.fromkeys(reviewer_plan)},
            "reviewerConfiguration":protocol.get("reviewerConfiguration"),
            "sampling":protocol.get("sampling"),
            "matchingDescription":matching_description(protocol),
            "evidenceMode":protocol.get("evidenceMode", "visual_only"),
            "presentationPolicy":protocol.get('presentationPolicy'),
            "frontPolicy":protocol.get('frontPolicy'),
            "labelPolicy":protocol.get('labelPolicy'),
            "stimulusEvidence":protocol.get("stimulusEvidence",[]),
            "stimuli":[{"caseId":case["id"],"soilieImage":case["relationImage"],
                        "baselineImage":case["comparisonImage"],"baseline":case["comparisonCondition"],
                        **({'profileImages': {profile: {'soilieImage': images['relationImage'], 'baselineImage': images['comparisonImage']}
                                              for profile, images in case['profileImages'].items()}} if case.get('profileImages') else {})}
                       for case in protocol["cases"]],
            "stimulusVersionDigest":hashlib.sha256(json.dumps(protocol,sort_keys=True).encode()).hexdigest(),
            "limitations":limitations,
            "interpretation":"Exploratory AI opinions and integration-test evidence only. Not human validation, independent model architectures, or a calibrated measure of accuracy. Repeated cases are excluded from preference totals."}


def public_summary(result, download_name="ai-pilot-responses.json"):
    """Return the lightweight document consumed by the public result pages.

    The complete export remains downloadable for audit. The browser only needs
    aggregate results, reviewer prompts and provenance, so transferring every
    per-case note and repeated stimulus reference would slow the Research page
    without changing anything it renders.
    """
    compact = deepcopy(result)
    responses = compact.pop("responses", [])
    stimuli = compact.pop("stimuli", [])
    evidence = compact.pop("stimulusEvidence", [])
    compact["detailedEvidence"] = {
        "responseRows": len(responses),
        "stimulusPairs": len(stimuli),
        "stimulusEvidenceRows": len(evidence),
        "download": download_name,
    }
    return compact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database",type=Path,required=True)
    parser.add_argument("--protocol",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--compact-output",type=Path)
    parser.add_argument("--download-name",default="ai-pilot-responses.json",
                        help="Public filename of the corresponding complete response export")
    parser.add_argument("--require-complete",action="store_true")
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    result = aggregate(SQLiteStudyStore(args.database),protocol)
    expected_plan = protocol.get("reviewerPlan") or list(PROFILES)
    if args.require_complete and (result["reviewersCompleted"] != len(expected_plan)
                                  or Counter(row["profile"] for row in result["reviewers"]) != Counter(expected_plan)):
        raise RuntimeError("All ten registered reviewers must complete the frozen task before final publication")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding="utf-8")
    if args.compact_output:
        args.compact_output.parent.mkdir(parents=True,exist_ok=True)
        args.compact_output.write_text(json.dumps(public_summary(result,args.download_name),indent=2),encoding="utf-8")
    print(json.dumps({"reviewersCompleted":result["reviewersCompleted"],"responses":len(result["responses"])}))


if __name__ == "__main__":
    main()
