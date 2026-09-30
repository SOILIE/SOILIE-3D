"""Freeze, collect and audit the approved 32-pair pilot without publishing it.

Routing and previous answers are never placed in reviewer packets. Selection
uses only frozen pair IDs/strata and excludes all inspected control pairs.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from serverless.study.clarified_rubric import VERSION, PROFILES, prompt
from serverless.study.store import SQLiteStudyStore

SEED = "functional-use-v1-pilot-20260926"
MODEL = "gpt-5.6-sol"
EFFORT = "xhigh"
STRATA = tuple((baseline, room) for baseline in ("layoutgpt", "infinigen_controlled")
               for room in ("bedroom", "living_room"))
SYSTEM = "You are a blinded evaluator of room arrangements. Apply the supplied rubric to the attached image only. Do not use tools, inspect files, or consult external information. Return only the requested JSON object."
OUTPUT_INSTRUCTION = "Return JSON with judgement (left/tie/right), errorChoice (left/right/both/neither/uncertain), confidence (integer 1-5), and note (at most 500 characters)."
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"judgement": {"type": "string", "enum": ["left", "tie", "right"]},
                         "errorChoice": {"type": "string", "enum": ["left", "right", "both", "neither", "uncertain"]},
                         "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
                         "note": {"type": "string", "maxLength": 500}},
          "required": ["judgement", "errorChoice", "confidence", "note"]}


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Readers must see either no record or the entire immutable record. A hard
    # link installs the completed file atomically and fails if it already exists.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                     dir=path.parent, suffix='.pending', delete=False) as stream:
        pending = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    try:
        os.link(pending, path)
    finally:
        pending.unlink()


def ordered(rows, salt, seed=SEED):
    return sorted(rows, key=lambda row: digest([seed, salt, row["pairId"]]))


def select_pairs(cases, excluded, seed=SEED):
    selected = []
    for stratum in STRATA:
        available = [row for row in cases if (row["baseline"], row["roomType"]) == stratum
                     and row["pairId"] not in excluded]
        if len(available) < 8:
            raise ValueError("Insufficient uninspected pairs in a stratum")
        selected.extend(ordered(available, "pilot-selection", seed)[:8])
    if len({row["pairId"] for row in selected}) != 32:
        raise ValueError("Duplicate selected pair")
    return selected


def assignments(selected, reviewer, profile, version=VERSION, seed=SEED):
    result = []
    for stratum in STRATA:
        group = ordered([row for row in selected if (row["baseline"], row["roomType"]) == stratum], reviewer + ":sides", seed)
        originals = []
        for index, case in enumerate(group):
            images = case.get("profileImages", {}).get(profile, case)
            left, right = ("baseline", "soilie") if index % 2 else ("soilie", "baseline")
            row = {"assignmentId": digest([version, reviewer, case["pairId"], "main"])[:24],
                   "reviewerId": reviewer, "profile": profile, "pairId": case["pairId"],
                   "baseline": case["baseline"], "roomType": case["roomType"], "title": case["title"],
                   "leftCondition": case["baseline"] if left == "baseline" else "soilie",
                   "rightCondition": case["baseline"] if right == "baseline" else "soilie",
                   "leftSource": images["comparisonImage" if left == "baseline" else "relationImage"],
                   "rightSource": images["comparisonImage" if right == "baseline" else "relationImage"],
                   "repeatOf": None}
            originals.append(row)
            result.append(row)
        # Two controls per baseline x room, frozen independently of outcomes.
        for original in ordered(originals, reviewer + ":controls", seed)[:2]:
            row = deepcopy(original)
            row.update(assignmentId=digest([version, reviewer, original["pairId"], "repeat"])[:24], repeatOf=original["assignmentId"])
            for a, b in (("leftCondition", "rightCondition"), ("leftSource", "rightSource")):
                row[a], row[b] = row[b], row[a]
            result.append(row)
    return sorted(result, key=lambda row: digest([seed, "order", row["assignmentId"]]))


def prepare(source, output, version=VERSION, previous=None):
    from serverless.study import structured_rubric as v2
    if version not in (VERSION, v2.VERSION):
        raise ValueError('Unknown pilot rubric version')
    structured = version == v2.VERSION
    seed = v2.SEED if structured else SEED
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new pilot directory; frozen protocols are never overwritten")
    if not read(source / "preflight.json")["passed"]:
        raise ValueError("Source input preflight failed")
    cases, excluded, retained, source_hashes, source_evidence = [], set(), [], {}, {}
    for name in ("set-a", "set-b"):
        protocol_path = source / name / "protocol.json"
        protocol = read(protocol_path)
        source_evidence.update({name + ':' + row['caseId']: row for row in protocol.get('stimulusEvidence', [])})
        source_hashes[name + "/protocol.json"] = digest(protocol_path.read_bytes())
        store = SQLiteStudyStore(source / name / "private/pilot.sqlite3")
        for session in store.sessions():
            for row in session["assignments"]:
                if row["repeatOf"]:
                    excluded.add(name + ":" + row["repeatOf"])
            if session["promptProfile"] == "room_function":
                responses = store.responses(session["sessionId"])
                if len(responses) != 242:
                    raise ValueError("Complete room-function source required")
                packet = read(source / "packets" / session["reviewerId"] / (name + ".json"))
                exact = packet["prompt"]
                if hashlib.sha256(exact.encode()).hexdigest() != session["promptHash"]:
                    raise ValueError("Retained room-function prompt hash changed")
                safe_responses = [{key: value for key, value in row.items() if key not in ("sessionId", "sessionToken", "invitation")}
                                  for row in responses]
                retained.append({"set": name, "reviewerId": session["reviewerId"], "profile": "room_function",
                                 "studyVersion": session["studyVersion"], "promptHash": session["promptHash"],
                                 "reviewPrompt": exact, "collectionStatus": "retained_original",
                                 "model": session["model"], "responses": safe_responses})
        for row in protocol["cases"]:
            cases.append({**row, "pairId": name + ":" + row["id"], "baseline": row["comparisonCondition"],
                          "roomType": row["balanceStratum"], "sourceStudyVersion": protocol["studyVersion"]})
    if len(cases) != 480 or len(retained) != 4:
        raise ValueError("Expected 480 frozen pairs and two retained reviewers per baseline")
    if structured:
        if previous is None:
            raise ValueError('V2 requires its completed v1 development checkpoint')
        prior = load_frozen(previous)
        prior_report = read(Path(previous) / 'pilot-results.json')
        if (prior.get('rubricVersion') != VERSION or not prior_report['complete']
                or prior_report['protocolSha256'] != digest(prior)
                or prior['retainedRoomFunctionSha256'] != digest(retained)
                or prior['fullPairSetSha256'] != digest(sorted(row['pairId'] for row in cases))):
            raise ValueError('Development checkpoint does not match the fixed sample')
        excluded.update(prior['selectedPairs'])
        excluded.update(prior['excludedCalibrationPairs'])
        if len(excluded) != 70:
            raise ValueError('The approved v2 exclusion set must contain 70 development pairs')
    selected = select_pairs(cases, excluded, seed)
    source_images, inventories, scene_provenance = {}, {}, {}
    if structured:
        from serverless.benchmark.numbered_evidence import inventory
        from serverless.benchmark.stimuli import diagram
        scenes_path = source / 'source-scenes.json'
        scene_source = read(scenes_path)
        scenes = {row['id']: row for row in scene_source['scenes']}
        source_hashes['source-scenes.json'] = digest(scenes_path.read_bytes())
        for case in selected:
            evidence = source_evidence[case['pairId']]
            case.pop('profileImages', None)
            for role, field in (('soilie', 'relationImage'), ('baseline', 'comparisonImage')):
                scene = scenes[evidence[role + 'Scene']]
                if digest(scene) != evidence[role + 'Digest']:
                    raise ValueError('Scene geometry/provenance changed since pair selection')
                content = diagram(scene, numbered=True).encode('utf-8')
                url = '/benchmarks/stimuli/' + digest(content)[:24] + '.svg'
                target = output / 'source-images' / Path(url).name
                target.parent.mkdir(parents=True, exist_ok=True)
                if url not in source_images:
                    with target.open('xb') as stream:
                        stream.write(content)
                    source_images[url] = {'file': 'source-images/' + target.name, 'sha256': digest(content)}
                    inventories[url] = inventory(scene)
                scene_provenance[case['pairId'] + ':' + role] = {'sceneId': scene['id'], 'sceneSha256': digest(scene), 'image': url}
                case[field] = url
    all_assignments, reviewers = [], []
    for index, profile in enumerate(profile for profile in PROFILES for _ in range(2)):
        reviewer = f"reviewer-{index + 1:02}"
        exact = prompt(profile, version) + "\n\n" + (v2.CHECKLIST if structured else OUTPUT_INSTRUCTION)
        system = SYSTEM.replace('attached image only', 'attached image and supplied neutral case evidence only') if structured else SYSTEM
        reviewers.append({"reviewerId": reviewer, "profile": profile, "rubricVersion": version,
                          "reviewPrompt": exact, "promptHash": hashlib.sha256(exact.encode()).hexdigest(),
                          "systemPrompt": system, "systemPromptHash": hashlib.sha256(system.encode()).hexdigest(),
                          "reportedModel": "GPT-5.6 Sol", "reportedReasoningEffort": "Extra High",
                          "model": MODEL, "reasoningEffort": EFFORT, "collectionStatus": "new_pilot"})
        if structured:
            reviewers[-1].update(checklistInstructions=v2.CHECKLIST, responseSchema=v2.schema(SCHEMA),
                                 stimulusVersion=v2.STIMULUS_VERSION, evidenceInstruction=v2.EVIDENCE_INSTRUCTION)
        all_assignments.extend(assignments(selected, reviewer, profile, version, seed))
    # Copy only the neutral SVG inputs required for the pilot. No source scenes,
    # generator names, prior answers or private routing enter an execution folder.
    sources = sorted({row[field] for row in all_assignments for field in ("leftSource", "rightSource")})
    for url in sources:
        if structured:
            continue
        if not url.startswith("/benchmarks/stimuli/") or Path(url).suffix != ".svg":
            raise ValueError("Unexpected source image")
        data = (source / "site" / url.lstrip("/")).read_bytes()
        if digest(data)[:24] != Path(url).stem or b"Judge " in data:
            raise ValueError("Source image changed or contains a task instruction")
        target = output / "source-images" / Path(url).name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        source_images[url] = {"file": "source-images/" + target.name, "sha256": digest(data)}
    manifest = {"schemaVersion": 2 if structured else 1, "rubricVersion": version, "stage": "pilot",
                "seed": seed, "sourceProtocolHashes": source_hashes, "sourceRoot": str(source),
                "selectedPairs": [row["pairId"] for row in selected], "excludedCalibrationPairs": sorted(excluded),
                "fullPairIds": [row["pairId"] for row in cases], "fullPairSetSha256": digest(sorted(row["pairId"] for row in cases)),
                "reviewers": reviewers, "assignments": all_assignments, "sourceImages": source_images,
                "acceptance": {"perDimensionAgreementsRequired": 15, "perDimensionComparisons": 16},
                "expectedMainJudgements": 256, "expectedControls": 64, "model": MODEL, "reasoningEffort": EFFORT,
                "outputSchema": v2.schema(SCHEMA) if structured else SCHEMA, "retainedRoomFunctionSha256": digest(retained),
                "releaseEligible": False, "fullCampaignAuthorized": False}
    if structured:
        from serverless.benchmark.numbered_evidence import task_evidence
        manifest.update(stimulusVersion=v2.STIMULUS_VERSION, sceneProvenance=scene_provenance,
                        inventories=inventories, evidenceInstruction=v2.EVIDENCE_INSTRUCTION,
                        developmentProtocolSha256=digest(prior))
        for row in all_assignments:
            row['evidence'] = {side: task_evidence(inventories[row[side + 'Source']], row['profile']) for side in ('left', 'right')}
            row['evidence']['requiredObservations'] = [{'side': side, 'objects': list(ids)}
                                                      for side, ids in v2.checklist_keys(row['evidence'], row['profile'])]
            row['evidenceSha256'] = digest(row['evidence'])
            row['deliveryPromptHash'] = digest(delivery_prompt(manifest, row).encode('utf-8'))
        for reviewer in reviewers:
            example = next(row for row in all_assignments if row['reviewerId'] == reviewer['reviewerId'] and not row['repeatOf'])
            reviewer['exampleDelivery'] = {'assignmentId': example['assignmentId'],
                                          'prompt': delivery_prompt(manifest, example),
                                          'promptHash': example['deliveryPromptHash'],
                                          'packetPath': 'packets/' + example['assignmentId'] + '.png'}
    write_new(output / "private/retained-room-function.json", retained)
    write_new(output / "protocol.json", manifest)
    write_new(output / "protocol-sha256.json", {"sha256": digest(manifest)})
    return manifest


def delivery_prompt(protocol, assignment):
    reviewer = next(row for row in protocol['reviewers'] if row['reviewerId'] == assignment['reviewerId'])
    result = reviewer['reviewPrompt']
    if 'evidence' in assignment:
        result += '\n\n' + protocol['evidenceInstruction'] + '\n' + json.dumps(assignment['evidence'], sort_keys=True, ensure_ascii=True)
    return result


def load_frozen(root):
    root = Path(root)
    protocol = read(root / "protocol.json")
    if digest(protocol) != read(root / "protocol-sha256.json")["sha256"]:
        raise ValueError("Frozen protocol changed")
    if digest(read(root / "private/retained-room-function.json")) != protocol["retainedRoomFunctionSha256"]:
        raise ValueError("Retained responses changed")
    return protocol


def validate_answer(answer, version=VERSION, evidence=None, profile=None):
    if version in ('functional-use-v2', 'functional-use-v3', 'functional-use-v4'):
        from serverless.study.structured_rubric import validate_observations
        if not isinstance(answer, dict) or set(answer) != set(SCHEMA['required']) | {'observations'}:
            raise ValueError('Invalid structured response fields')
        validate_observations(answer, evidence, profile)
        answer = {key: answer[key] for key in SCHEMA['required']}
    elif version != VERSION:
        raise ValueError('Unknown answer schema version')
    if (not isinstance(answer, dict) or set(answer) != set(SCHEMA["required"])
            or answer["judgement"] not in SCHEMA["properties"]["judgement"]["enum"]
            or answer["errorChoice"] not in SCHEMA["properties"]["errorChoice"]["enum"]
            or type(answer["confidence"]) is not int or not 1 <= answer["confidence"] <= 5
            or not isinstance(answer["note"], str) or not answer["note"].strip() or len(answer["note"]) > 500):
        raise ValueError("Invalid isolated judgment")


def validate_receipt(receipt, protocol, reviewer, packet, assignment=None):
    if (receipt["model"] != protocol["model"] or receipt["reasoningEffort"] != protocol["reasoningEffort"]
            or receipt["promptHash"] != reviewer["promptHash"] or receipt["systemPromptHash"] != reviewer["systemPromptHash"]
            or receipt["imageSha256"] != packet["imageSha256"] or receipt.get("isolatedContext") is not True
            or not receipt.get("threadId")):
        raise ValueError("Judgment delivery/provenance mismatch")
    if assignment and 'deliveryPromptHash' in assignment:
        for field in ('deliveryPromptHash', 'evidenceSha256'):
            if receipt.get(field) != assignment[field]:
                raise ValueError('Case-specific delivery provenance mismatch')


def record(root, assignment_id, answer, receipt):
    protocol = load_frozen(root)
    assignment = next(row for row in protocol["assignments"] if row["assignmentId"] == assignment_id)
    reviewer = next(row for row in protocol["reviewers"] if row["reviewerId"] == assignment["reviewerId"])
    version = reviewer.get('rubricVersion', protocol.get('rubricVersion', VERSION))
    validate_answer(answer, version, assignment.get('evidence'), assignment['profile'])
    packet = read(Path(root) / "packets/index.json")[assignment_id]
    validate_receipt(receipt, protocol, reviewer, packet, assignment)
    path = Path(root) / "responses" / (assignment_id + ".json")
    write_new(path, {**assignment, **answer, "rubricVersion": version, "promptHash": reviewer["promptHash"], "receipt": receipt})


def results(root):
    protocol = load_frozen(root)
    version = protocol.get('rubricVersion', VERSION)
    fields = protocol.get('outputSchema', SCHEMA)['required']
    assignments_by_id = {row["assignmentId"]: row for row in protocol["assignments"]}
    reviewers = {row['reviewerId']: row for row in protocol['reviewers']}
    packets = read(Path(root) / 'packets/index.json') if (Path(root) / 'packets/index.json').exists() else {}
    answers, threads = {}, set()
    for path in sorted((Path(root) / "responses").glob("*.json")):
        row = read(path)
        key = row["assignmentId"]
        if key not in assignments_by_id or key in answers or any(row[field] != value for field, value in assignments_by_id[key].items()):
            raise ValueError("Unknown, duplicate or changed response assignment")
        validate_answer({field: row[field] for field in fields}, version, row.get('evidence'), row['profile'])
        validate_receipt(row['receipt'], protocol, reviewers[row['reviewerId']], packets[key], row)
        if row['promptHash'] != reviewers[row['reviewerId']]['promptHash'] or row['rubricVersion'] != version:
            raise ValueError('Saved prompt provenance changed')
        if row['receipt'].get('runner') == 'codex exec --ephemeral':
            # Verify saved answers against actual runner output, not just values
            # copied into a result row. This detects post-collection edits.
            execution = Path(root) / 'execution' / key
            # subprocess text mode normalizes newlines before hashing; Windows
            # text files can store CRLF. Validate the same logical UTF-8 text.
            raw_events = (execution / 'events.jsonl').read_text(encoding='utf-8')
            if digest(raw_events.encode('utf-8')) != row['receipt']['eventsSha256']:
                raise ValueError('Runner transcript changed')
            if read(execution / 'answer.json') != {field: row[field] for field in fields}:
                raise ValueError('Saved judgment differs from model output')
            events = [json.loads(line) for line in raw_events.splitlines() if line.strip().startswith('{')]
            messages = [event['item']['text'] for event in events if event.get('type') == 'item.completed'
                        and event.get('item', {}).get('type') == 'agent_message']
            if not messages or json.loads(messages[-1]) != read(execution / 'answer.json'):
                raise ValueError('Answer file differs from the recorded model response')
            invocation = read(execution / 'invocation.json')
            for field in ('model', 'reasoningEffort', 'promptHash', 'systemPromptHash', 'imageSha256'):
                if invocation[field] != row['receipt'][field]:
                    raise ValueError('Invocation provenance differs from saved judgment')
            if 'deliveryPromptHash' in row:
                expected_prompt = delivery_prompt(protocol, row)
                if (digest(expected_prompt.encode('utf-8')) != row['deliveryPromptHash']
                        or (execution / 'prompt.txt').read_text(encoding='utf-8') != expected_prompt
                        or invocation.get('deliveryPromptHash') != row['deliveryPromptHash']
                        or invocation.get('evidenceSha256') != row['evidenceSha256']):
                    raise ValueError('Delivered case prompt differs from frozen evidence')
        thread = row["receipt"]["threadId"]
        if thread in threads:
            raise ValueError("Judgment contexts were reused")
        threads.add(thread)
        answers[key] = row
    controls = []
    for row in answers.values():
        if not row["repeatOf"] or row["repeatOf"] not in answers:
            continue
        original = answers[row["repeatOf"]]
        if original["repeatOf"] or any(row[left] != original[right] or row[right] != original[left]
                                     for left, right in (("leftCondition", "rightCondition"), ("leftSource", "rightSource"))):
            raise ValueError("Control did not exchange the same rooms")
        choose = lambda value: "tie" if value["judgement"] == "tie" else value[value["judgement"] + "Condition"]
        controls.append({"assignmentId": row["assignmentId"], "repeatOf": row["repeatOf"], "profile": row["profile"],
                         "reviewerId": row["reviewerId"], "baseline": row["baseline"], "roomType": row["roomType"],
                         "agreed": choose(row) == choose(original)})
        if version == 'functional-use-v2':
            first, second = choose(original), choose(row)
            controls[-1]['transition'] = ('same_tie' if first == second == 'tie' else 'same_winner' if first == second
                                         else 'tie_preference_change' if 'tie' in (first, second) else 'winner_reversal')

    def group(field):
        values = defaultdict(list)
        for row in controls:
            values[row[field]].append(row["agreed"])
        return {key: {"agreements": sum(rows), "comparisons": len(rows), "agreementPct": 100 * sum(rows) / len(rows)}
                for key, rows in sorted(values.items())}

    dimensions = group("profile")
    main_votes = {}
    for profile in PROFILES:
        main_votes[profile] = {}
        for baseline in ('layoutgpt', 'infinigen_controlled'):
            selected = [row for row in answers.values() if row['profile'] == profile
                        and row['baseline'] == baseline and not row['repeatOf']]
            votes = Counter('tie' if row['judgement'] == 'tie' else row[row['judgement'] + 'Condition'] for row in selected)
            main_votes[profile][baseline] = {'judgements': len(selected), 'distinctPairs': len({row['pairId'] for row in selected}),
                                             'votes': {key: votes[key] for key in ('soilie', baseline, 'tie')}}
    complete = len(answers) == len(assignments_by_id) == 320
    accepted = complete and all(dimensions.get(profile, {}).get("comparisons") == 16
                                and dimensions[profile]["agreements"] >= 15 for profile in PROFILES)
    summary = {"rubricVersion": version, "complete": complete, "pilotAccepted": accepted,
            "releaseEligible": False, "responses": len(answers), "mainJudgements": sum(not row["repeatOf"] for row in answers.values()),
            "agreements": sum(row["agreed"] for row in controls), "comparisons": len(controls),
            "byDimension": dimensions, "byReviewer": group("reviewerId"), "byBaseline": group("baseline"),
            "byRoomType": group("roomType"), "controls": controls,
            "mainVotesByDimensionByBaseline": main_votes,
            "remainingAssignmentIds": [key for key in assignments_by_id if key not in answers],
            "retention": "All main pilot judgments may be retained only if accepted and the protocol is unchanged; controls never add preference votes.",
            "interpretation": "Repeat consistency measures observed stability, not correctness or population reliability. No full campaign or publication is authorized."}
    if version == 'functional-use-v2':
        def transitions(rows):
            counts = Counter(row['transition'] for row in rows)
            decisive = counts['same_winner'] + counts['winner_reversal']
            return {**{key: counts[key] for key in ('same_tie', 'same_winner', 'tie_preference_change', 'winner_reversal')},
                    'comparisons': len(rows), 'decisiveComparisons': decisive,
                    'decisiveAgreementPct': 100 * counts['same_winner'] / decisive if decisive else None}
        summary['repeatTransitions'] = {'overall': transitions(controls)}
        for field in ('profile', 'reviewerId', 'baseline'):
            summary['repeatTransitions'][field] = {key: transitions([row for row in controls if row[field] == key])
                                                     for key in sorted({row[field] for row in controls})}
    return summary


def finalize(root):
    root = Path(root)
    from serverless.cloud_benchmark.run_staged_pilot import preflight
    preflight(root)
    summary = results(root)
    if not summary["complete"]:
        raise ValueError("Pilot is incomplete; resume only the remaining assignments")
    protocol = load_frozen(root)
    all_responses = [read(path) for path in sorted((root / "responses").glob("*.json"))]
    report = {**summary, "protocolSha256": digest(protocol), "reviewers": protocol["reviewers"],
              "judgements": all_responses,
              "responseFileHashes": {path.name: digest(path.read_bytes()) for path in sorted((root / "responses").glob("*.json"))},
              "retainedRoomFunctionSha256": protocol["retainedRoomFunctionSha256"],
              "fullPairSetSha256": protocol['fullPairSetSha256'],
              "selection": {"seed": protocol['seed'], 'pairs': protocol['selectedPairs'],
                            'excludedCalibrationPairs': protocol['excludedCalibrationPairs'], 'qualityScoresUsed': False},
              "retainedRoomFunction": [{key: value for key, value in row.items() if key != "responses"}
                                       for row in read(root / "private/retained-room-function.json")]}
    if protocol.get('rubricVersion') == 'functional-use-v2':
        report.update(stimulusVersion=protocol['stimulusVersion'], responseSchema=protocol['outputSchema'],
                      evidenceInstruction=protocol['evidenceInstruction'], sceneProvenance=protocol['sceneProvenance'],
                      developmentProtocolSha256=protocol['developmentProtocolSha256'])
    # This is a checkpoint, NOT an AI release. The existing publication gate is
    # deliberately untouched. All answers survive even when calibration fails.
    write_new(root / "pilot-results.json", report)
    return summary


def continuation_inventory(root):
    """Identify reusable main votes without authorizing or starting continuation.

    A passing pilot contributes all 256 main votes. No question/room preference
    is cherry-picked; altered prompts invalidate reuse rather than rewriting it.
    """
    root = Path(root)
    report = read(root / 'pilot-results.json')
    protocol = load_frozen(root)
    if not report['pilotAccepted'] or report['protocolSha256'] != digest(protocol):
        raise ValueError('An accepted unchanged protocol is required for reuse')
    for filename, expected in report['responseFileHashes'].items():
        if digest((root / 'responses' / filename).read_bytes()) != expected:
            raise ValueError('A frozen pilot response changed')
    if not results(root)['pilotAccepted']:
        raise ValueError('Pilot no longer validates')
    return {'authorized': False, 'reusableMainJudgements': 256,
            'retainedRoomFunctionSha256': protocol['retainedRoomFunctionSha256'],
            'remainingPairsByReviewer': {reviewer['reviewerId']: sorted(set(protocol['fullPairIds']) - set(protocol['selectedPairs']))
                                         for reviewer in protocol['reviewers']},
            'promptHashes': {reviewer['reviewerId']: reviewer['promptHash'] for reviewer in protocol['reviewers']},
            'fullPairSetSha256': protocol['fullPairSetSha256']}


def checkpoint(root, destination):
    """Export compact committed evidence, without private context identifiers.

    Failed pilots are exported too. This does not turn a checkpoint into a
    public release and never invokes the website compiler or deployment.
    """
    root = Path(root)
    protocol = load_frozen(root)
    report = read(root / 'pilot-results.json')
    current = results(root)
    if not current['complete'] or any(report[key] != current[key] for key in current):
        raise ValueError('Completed checkpoint differs from saved judgments')
    if report['protocolSha256'] != digest(protocol):
        raise ValueError('Checkpoint protocol differs')
    for filename, expected in report['responseFileHashes'].items():
        if digest((root / 'responses' / filename).read_bytes()) != expected:
            raise ValueError('Checkpoint response changed')
    saved = [read(path) for path in sorted((root / 'responses').glob('*.json'))]
    if report['judgements'] != saved or report['reviewers'] != protocol['reviewers']:
        raise ValueError('Checkpoint evidence differs from frozen collection')
    document = deepcopy(report)
    document.pop('remainingAssignmentIds', None)
    document['artifactType'] = 'internal-ai-calibration-checkpoint'
    document['sourceProtocolHashes'] = protocol['sourceProtocolHashes']
    document['pixelPreflight'] = read(root / 'preflight.json')
    for row in document['judgements']:
        receipt = row['receipt']
        receipt['contextSha256'] = digest(receipt.pop('threadId'))
    write_new(destination, document)
    return {'written': str(destination), 'judgements': len(document['judgements']),
            'pilotAccepted': document['pilotAccepted'], 'releaseEligible': False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "status", "finalize", "checkpoint"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument('--version', choices=(VERSION, 'functional-use-v2'), default=VERSION)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.source, args.root, args.version, args.previous)
        print(json.dumps({"pairs": len(result["selectedPairs"]), "assignments": len(result["assignments"])}))
    elif args.action == 'checkpoint':
        if not args.output:
            parser.error('checkpoint requires --output')
        print(json.dumps(checkpoint(args.root, args.output)))
    else:
        result = results(args.root) if args.action == "status" else finalize(args.root)
        print(json.dumps({key: value for key, value in result.items() if key not in ("controls", "remainingAssignmentIds")}))
