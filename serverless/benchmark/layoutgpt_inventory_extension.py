"""Freeze and prepare new seven/eight-item LayoutGPT tasks, without inference.

This is a new condition, not an edit to the retired shared-inventory campaign.
The authors' prompt/retrieval functions and original inventory instruction are
preserved. Inventories must be supplied explicitly after the new task design is
frozen; old SOILIE rooms are never silently reused as corrected source rooms.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import copy
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace

from serverless.cloud_benchmark.checkpoint import write_json
from serverless.common.object_counts import (
    OBJECT_COUNT_DEFINITION, OBJECT_COUNT_POLICY, WINDOW_FAMILY, validate_selection,
)


VARIANT = 'inventory-extension-7-8-gpt4-v1'
PROMPT_PROTOCOL = 'authors-css-k8-bedroom-k4-living-room-inventory-v1'
AUTHOR_COMMIT = 'fc31954962553e5b65bf267a904a6930d50b1f5e'
AUTHOR_PROMPT_SHA256 = '8aab0891a1cf60aab64b184f04170a42c7cee2832f299643ecb88a7c38048c24'
RESERVATION_USD = .3072
CELLS = tuple((room, count) for room in ('bedroom', 'living_room') for count in (7, 8))
SCRATCH_ROOT = Path(__file__).resolve().parents[2] / '.codex'


def scratch_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(SCRATCH_ROOT.resolve()):
        raise ValueError('Extension working files must remain inside this project .codex directory')
    return path


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def freeze_design(output, campaign_id, per_cell, budget_usd, seed, retained_campaign_sha256,
                  inventory_policy='explicit-model-native-or-fresh-soilie-v1', archive_date='2026-10-25'):
    """Pin attempt counts before generation; no quality-based replenishment."""
    output = scratch_path(output)
    if date.fromisoformat(archive_date).isoformat() != archive_date:
        raise ValueError('The public output date must be an ISO date')
    if (not re.fullmatch(r'[a-z][a-z0-9-]{2,39}', campaign_id) or
            type(per_cell) is not int or not 1 <= per_cell <= 60 or
            type(seed) is not int or seed < 0 or seed + 4 * per_cell > 2**53 - 1 or
            not isinstance(budget_usd, (int, float)) or not math.isfinite(budget_usd) or
            budget_usd < 4 * per_cell * RESERVATION_USD or
            not re.fullmatch(r'[a-f0-9]{64}', retained_campaign_sha256) or
            inventory_policy not in ('explicit-model-native-or-fresh-soilie-v1', 'fresh-soilie-first-scheduled-local-v1')):
        raise ValueError('Invalid frozen allocation, seed range, or explicit budget')
    tasks = []
    for cell_index, (room, count) in enumerate(CELLS):
        for index in range(per_cell):
            tasks.append({'id': f'layoutgpt-{campaign_id}-{room}-{count}-{index:03d}',
                          'roomType': room, 'requestedObjects': count,
                          'seed': seed + cell_index * per_cell + index})
    design = {'schema': 'soilie.layoutgpt-inventory-extension/v1', 'campaignId': campaign_id,
              'perCell': per_cell, 'budgetUsd': budget_usd, 'selectionSeed': seed,
              'retainedCampaignSha256': retained_campaign_sha256,
              'qualitySelection': False, 'attemptPolicy': 'one-request-per-task; no ambiguous automatic retry',
              'publicOutputPrefix': f'files/outputs/{archive_date}/',
              'inventoryPolicy': inventory_policy,
              'objectCountDefinition': OBJECT_COUNT_DEFINITION,
              'targetReusePolicy': 'held-out dimensions may repeat across campaigns; unique within this extension',
              'seedMeaning': 'task identity and held-out dimension selection, not a provider RNG guarantee',
              'tasks': tasks}
    if output.exists():
        if json.loads(output.read_bytes()) != design:
            raise ValueError('Frozen extension design differs; never overwrite it')
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, design)
    return design


def validate_tasks(design, tasks):
    """Require exact multiplicities and honest, inspectable source provenance."""
    from serverless.benchmark.review_annotations import presentation_label

    if design.get('objectCountDefinition') != OBJECT_COUNT_DEFINITION:
        raise ValueError('Freeze a new LayoutGPT design for the non-window object-count protocol')

    if len(tasks) != len(design['tasks']) or len({row['id'] for row in tasks}) != len(tasks):
        raise ValueError('Every frozen task must be bound exactly once')
    expected = {row['id']: row for row in design['tasks']}
    ordered = []
    for task in tasks:
        slot = expected.get(task['id'])
        if slot is None or any(task.get(key) != value for key, value in slot.items()):
            raise ValueError('Task identity, seed, room type, or object count changed')
        for key in ('inventory', 'nativeInventory'):
            inventory = task.get(key)
            if (not isinstance(inventory, dict) or not inventory or
                    any(not isinstance(label, str) or not re.fullmatch(r'[a-z][a-z_ ]*', label) or
                        type(count) is not int or count < 1 for label, count in inventory.items()) or
                    sum(inventory.values()) != slot['requestedObjects']):
                raise ValueError('Task must contain exact positive instance multiplicities')
            if any(re.sub(r'[-\s]+', '_', label.strip().lower()) in WINDOW_FAMILY for label in inventory):
                raise ValueError('Window extras cannot consume the controlled LayoutGPT object quota')
        neutral = Counter()
        for label, count in task['nativeInventory'].items():
            neutral[presentation_label(label)] += count
        if dict(neutral) != task['inventory']:
            raise ValueError('Native categories do not realize the declared neutral inventory')
        origin = task.get('inventoryOrigin')
        if design['inventoryPolicy'] == 'fresh-soilie-first-scheduled-local-v1' and origin != 'fresh-soilie':
            raise ValueError('This design requires the first scheduled local fresh SOILIE inventories, not native replacements')
        allowed = {*slot, 'inventory', 'nativeInventory', 'inventoryOrigin'}
        if origin == 'fresh-soilie':
            allowed.update(('soilieSceneId', 'soilieSceneSha256', 'soilieCampaignSha256',
                            'sourceInventory', 'substitutions'))
            if (not isinstance(task.get('soilieSceneId'), str) or
                    not re.fullmatch(r'[A-Za-z0-9_.-]+', task['soilieSceneId']) or
                    not re.fullmatch(r'[a-f0-9]{64}', task.get('soilieSceneSha256', '')) or
                    not re.fullmatch(r'[a-f0-9]{64}', task.get('soilieCampaignSha256', ''))):
                raise ValueError('Fresh SOILIE inventory requires scene and campaign receipts')
            original = task.get('sourceInventory')
            changes = task.get('substitutions')
            if (not isinstance(original, dict) or not original or
                    any(not isinstance(label, str) or not re.fullmatch(r'[a-z][a-z_ ]*', label) or
                        type(n) is not int or n < 1 for label, n in original.items()) or
                    sum(original.values()) != slot['requestedObjects'] or
                    not isinstance(changes, list) or len(changes) > 1):
                raise ValueError('Preserve the original inventory and at most one disclosed instance substitution')
            mapped = Counter(original)
            for change in changes:
                if (not isinstance(change, dict) or set(change) != {'from', 'to'} or
                        not isinstance(change['from'], str) or not isinstance(change['to'], str) or
                        mapped[change['from']] < 1):
                    raise ValueError('Invalid disclosed inventory substitution')
                mapped[change['from']] -= 1
                mapped[change['to']] += 1
            if +mapped != Counter(task['inventory']):
                raise ValueError('Disclosed substitutions do not yield the requested inventory')
        elif origin != 'predeclared-model-native':
            raise ValueError('Declare fresh-soilie or predeclared-model-native inventory provenance')
        if set(task) != allowed:
            raise ValueError('Unexpected task fields; keep private host/account/path data outside scene inputs')
        ordered.append(task)
    order = {row['id']: index for index, row in enumerate(design['tasks'])}
    return sorted(ordered, key=lambda row: order[row['id']])


def source_slots(design_path, campaign, output):
    """Freeze source request IDs before inspecting any generated room quality."""
    from serverless.cloud_benchmark.authoritative import load_plan
    design_raw = Path(design_path).read_bytes()
    design = json.loads(design_raw)
    if design.get('inventoryPolicy') != 'fresh-soilie-first-scheduled-local-v1':
        raise ValueError('Source scheduling requires the shared fresh-SOILIE policy')
    if design.get('objectCountDefinition') != OBJECT_COUNT_DEFINITION:
        raise ValueError('Source scheduling requires a newly frozen non-window-count design')
    plan = load_plan(Path(campaign), verify_runtime=False)
    if plan['phase'] != 'authoritative' or plan['maxObjectCount'] != 8:
        raise ValueError('Bind only the new authoritative three-to-eight campaign')
    if (plan['configuration'].get('objectCountDefinition') != OBJECT_COUNT_DEFINITION
            or plan['configuration'].get('objectCountPolicy') != OBJECT_COUNT_POLICY):
        raise ValueError('Do not bind legacy total-count source rooms to the new non-window condition')
    rows = []
    for room, count in CELLS:
        scheduled = [row for row in plan['requests'] if row['platform'] == 'local'
                     and row['request']['roomType'] == room and row['request']['objectCount'] == count]
        targets = [row for row in design['tasks'] if row['roomType'] == room and row['requestedObjects'] == count]
        if len(scheduled) < len(targets):
            raise ValueError('Source plan lacks the fixed first scheduled requests')
        for task, request in zip(targets, scheduled):
            rows.append({'taskId': task['id'], 'sourceRequestId': request['id'],
                         'sourceRequest': request['request'], 'soilieSceneId': 'soilie-' + request['id']})
    document = {'schemaVersion': 1, 'policy': design['inventoryPolicy'],
                'objectCountDefinition': OBJECT_COUNT_DEFINITION,
                'designSha256': hashlib.sha256(design_raw).hexdigest(),
                'soilieCampaignSha256': plan['planSha256'], 'sourcePlatform': 'local',
                'failurePolicy': 'Keep these source seeds; never skip failed or unsupported inventories',
                'tasks': rows}
    output = scratch_path(output)
    if output.exists():
        if json.loads(output.read_bytes()) != document:
            raise ValueError('Frozen source request slots changed')
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, document)
    return document


def _source_render_normalizer(runtime):
    """Extract only the frozen renderer's pure input-normalization function."""
    path = SCRATCH_ROOT.parent / 'modules/render.py'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != runtime['files']['modules/render.py']:
        raise ValueError('Source renderer normalization differs from the frozen SOILIE runtime')
    tree = ast.parse(raw)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'load_variables']
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'SYNONYMS' for target in node.targets)]
    if len(functions) != 1 or len(assignments) != 1:
        raise ValueError('Expected one pure source normalizer and literal synonym assignment')
    function, assignment = functions[0], assignments[0]
    args = function.args
    if (function.decorator_list or function.returns is not None or args.posonlyargs or args.kwonlyargs or args.defaults
            or args.vararg or args.kwarg or [value.arg for value in args.args] != ['inputs']
            or args.args[0].annotation is not None
            or len(function.body) != 5
            or [type(node) for node in function.body] != [ast.Assign] * 4 + [ast.Return]
            or len(assignment.targets) != 1 or not isinstance(assignment.targets[0], ast.Name)):
        raise ValueError('Unexpected source normalization shape; review rather than guessing semantics')
    aliases = ast.literal_eval(assignment.value)
    if not isinstance(aliases, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                            for key, value in aliases.items()):
        raise ValueError('Source synonyms must remain a literal string mapping')
    scope = {'copy': copy}
    # No Blender import, unrelated renderer statement or top-level side effect
    # is executed. Both compiled nodes come from the exact pinned human source.
    exec(compile(ast.Module(body=[assignment, function], type_ignores=[]), str(path), 'exec'), scope)
    return scope['load_variables']


def verify_source_artifacts(design_path, slots_path, tasks, tasks_sha, campaign):
    """Bind inventories to exact production artifacts, never caller assertions."""
    from serverless.benchmark.geometry import ARCHITECTURE, canonical_label, furniture
    from serverless.benchmark.review_annotations import presentation_label
    from serverless.cloud_benchmark.authoritative import load_plan, validate_artifact, verify_attempt_evidence

    campaign = scratch_path(campaign)
    slots = source_slots(design_path, campaign, slots_path)
    plan = load_plan(campaign, verify_runtime=False)
    runtime = plan['configuration']['runtimeSnapshot']
    core = SCRATCH_ROOT.parent / 'modules/prepare_data.py'
    if hashlib.sha256(core.read_bytes()).hexdigest() != runtime['files']['modules/prepare_data.py']:
        raise ValueError('Source instance-normalization helpers differ from the frozen SOILIE runtime')
    from modules import prepare_data
    normalize_inputs = _source_render_normalizer(runtime)
    ledger_raw = (campaign / 'local-ledger.json').read_bytes()
    ledger = json.loads(ledger_raw)
    if ledger['planSha256'] != plan['planSha256']:
        raise ValueError('Source attempt ledger belongs to another campaign')
    allocated = {row['id']: row for row in plan['requests'] if row['platform'] == 'local'}
    bindings = []
    for task, slot in zip(tasks, slots['tasks']):
        expected = allocated[slot['sourceRequestId']]
        attempts = [row for row in ledger['attempts'] if row['requestId'] == slot['sourceRequestId']]
        complete = [row for row in attempts if row['status'] == 'complete']
        if (len(complete) != 1 or any(row['status'] not in ('complete', 'failed') for row in attempts)
                or sorted(row['attempt'] for row in attempts) != list(range(1, len(attempts) + 1))):
            raise ValueError('Every fixed source request needs exactly one complete attempt and all failed attempts retained')
        originals = {}
        for attempt in attempts:
            if (attempt.get('planSha256') != plan['planSha256'] or attempt['request'] != expected['request']):
                raise ValueError('Source attempt differs from its fixed local request')
            for name in ('artifact', 'receipt', 'log', 'invocationIdentity'):
                if attempt.get(name) and not (campaign / attempt[name]).resolve().is_relative_to(campaign):
                    raise ValueError('Source execution evidence escaped its campaign')
            # Includes exact original S3-backed bytes, execution-log validation
            # and declared receipt/log hashes, without restoring an evicted file.
            artifact = verify_attempt_evidence(campaign, attempt, plan)
            if artifact is None:
                # finish_local can record a failed startup/worker exit before
                # any terminal JSON exists. Preserve this real failure with
                # its exact nonempty controller log, not an invented artifact.
                if (attempt['status'] != 'failed' or 'artifact' in attempt or 'sha256' in attempt
                        or not re.fullmatch(r'Worker exited -?\d+ without terminal evidence', attempt.get('error', ''))
                        or not re.fullmatch('[a-f0-9]{64}', attempt.get('logSha256', ''))
                        or not attempt.get('log') or (campaign / attempt['log']).stat().st_size == 0):
                    raise ValueError('Missing source terminal evidence is not a verifiable retained worker failure')
                originals[attempt['attempt']] = None
                continue
            if validate_artifact(artifact, expected, plan)['status'] != attempt['status']:
                raise ValueError('Source terminal artifact or status differs from the ledger')
            originals[attempt['attempt']] = artifact
        attempt = complete[0]
        artifact = originals[attempt['attempt']]
        if (task['soilieSceneSha256'] != attempt['sha256']
                or task['soilieCampaignSha256'] != plan['planSha256']
                or task['soilieSceneId'] != slot['soilieSceneId']):
            raise ValueError('Declared source scene SHA, campaign or selected request changed')
        selection = artifact['selection']
        counts = validate_selection(expected['request'], selection)
        if counts['countedObjectCount'] != task['requestedObjects']:
            raise ValueError('Source primary selected instance count differs from the task')
        counted_selection = [label for label in selection if label not in WINDOW_FAMILY]
        # LayoutGPT controls the N non-window source instances. Keep selected
        # window extras in this private binding and in final-geometry checks,
        # rather than silently dropping them from the original source evidence.
        inventory = dict(sorted(Counter(presentation_label(canonical_label(label)) for label in counted_selection).items()))
        if inventory != task['sourceInventory']:
            raise ValueError('Declared source inventory differs from the exact SOILIE selection bytes')
        # Reuse the human production helpers, including additive companion
        # windows for coverings. These dummy values are never scene geometry.
        primary = prepare_data.suffix_duplicates(selection)
        coordinates = {name: [0., 0., 0.] for name in primary}
        sizes = {name: [1., 0] for name in primary}
        normalized, _ = prepare_data.ensure_window_assemblies(coordinates, sizes)
        _original, normalized, _parameters = normalize_inputs(normalized)
        expected_final = {name: canonical_label(name) for name in normalized}
        final = artifact['stages']['final']
        objects = final['objects']
        actual_final = {row['id']: canonical_label(row['label']) for row in objects}
        if len(actual_final) != len(objects) or actual_final != expected_final:
            raise ValueError('Final source identities/labels differ from production instance/window normalization')
        for row in objects:
            if row['kind'] != ('architecture' if canonical_label(row['label']) in ARCHITECTURE else 'furniture'):
                raise ValueError('Final source kind differs from the production label classification')
        bindings.append({'taskId': task['id'], 'taskSha256': digest(task),
            'sourceRequestId': slot['sourceRequestId'], 'soilieSceneId': task['soilieSceneId'],
            'sourceArtifactSha256': attempt['sha256'], 'sourceSelectedCount': len(selection),
            **counts, 'sourceSelectedObjects': selection,
            'sourceWindowExtras': [label for label in selection if label in WINDOW_FAMILY],
            'sourceFullInventory': dict(sorted(Counter(presentation_label(canonical_label(label)) for label in selection).items())),
            'companionWindowCount': artifact['companionWindowCount'],
            'renderedWindowCount': artifact['renderedWindowCount'],
            'sourceInventory': inventory,
            'normalizedFinalInventory': dict(sorted(Counter(presentation_label(row['label']) for row in objects).items())),
            'furnitureInventory': dict(sorted(Counter(presentation_label(row['label']) for row in furniture(final)).items())),
            'attempts': [{**{key: row[key] for key in ('attempt', 'status')},
                          **{key: row[key] for key in ('error', 'logSha256', 'receiptSha256', 'invocationIdentitySha256') if key in row},
                          **({'artifactSha256': row['sha256']} if row.get('sha256') else {})} for row in attempts]})
    return {'schemaVersion': 1, 'policy': slots['policy'], 'sourcePlatform': 'local',
            'sourcePlanSha256': plan['planSha256'], 'sourceRuntimeSha256': digest(runtime),
            'sourceProvenanceSha256': runtime['provenanceSha256'],
            'sourceLedgerSha256': hashlib.sha256(ledger_raw).hexdigest(),
            'taskManifestSha256': tasks_sha, 'sourceSlotsSha256': hashlib.sha256(Path(slots_path).read_bytes()).hexdigest(),
            'countDefinition': OBJECT_COUNT_DEFINITION,
            'objectCountPolicy': OBJECT_COUNT_POLICY,
            'tasks': bindings}


def validate_source_binding_receipt(root, plan):
    """Check the exact prepared byte-bound receipt before paid dispatch."""
    from serverless.benchmark.geometry import canonical_label
    from serverless.benchmark.review_annotations import presentation_label

    raw = (Path(root) / 'source-binding.json').read_bytes()
    receipt = json.loads(raw)
    slots_raw = (Path(root) / 'source-slots.json').read_bytes()
    slots = json.loads(slots_raw)
    if (hashlib.sha256(raw).hexdigest() != plan.get('sourceBindingSha256')
            or receipt.get('schemaVersion') != 1 or receipt.get('policy') != plan['inventoryPolicy']
            or receipt.get('sourcePlatform') != 'local'
            or receipt.get('taskManifestSha256') != plan['taskManifestSha256']
            or receipt.get('sourceSlotsSha256') != plan['sourceSlotsSha256']
            or hashlib.sha256(slots_raw).hexdigest() != plan['sourceSlotsSha256']
            or receipt.get('countDefinition') != OBJECT_COUNT_DEFINITION
            or receipt.get('objectCountPolicy') != OBJECT_COUNT_POLICY
            or slots.get('objectCountDefinition') != OBJECT_COUNT_DEFINITION
            or len(receipt['tasks']) != len(plan['requests'])
            or len(slots['tasks']) != len(plan['requests'])):
        raise ValueError('Source artifact binding receipt changed')
    for request, binding, slot in zip(plan['requests'], receipt['tasks'], slots['tasks']):
        task = request['task']
        counts = validate_selection(slot['sourceRequest'], binding['sourceSelectedObjects'])
        counted_selection = [label for label in binding['sourceSelectedObjects'] if label not in WINDOW_FAMILY]
        inventory = dict(sorted(Counter(presentation_label(canonical_label(label)) for label in counted_selection).items()))
        full_inventory = dict(sorted(Counter(presentation_label(canonical_label(label))
                                             for label in binding['sourceSelectedObjects']).items()))
        if (binding['taskId'] != task['id'] or binding['taskSha256'] != digest(task)
                or slot['taskId'] != task['id'] or binding['sourceRequestId'] != slot['sourceRequestId']
                or binding['sourceArtifactSha256'] != task['soilieSceneSha256']
                or binding['soilieSceneId'] != task['soilieSceneId']
                or receipt['sourcePlanSha256'] != task['soilieCampaignSha256']
                or binding['sourceInventory'] != task['sourceInventory']
                or inventory != binding['sourceInventory']
                or full_inventory != binding['sourceFullInventory']
                or binding['sourceWindowExtras'] != [label for label in binding['sourceSelectedObjects'] if label in WINDOW_FAMILY]
                or any(type(binding.get(key)) is not type(value) or binding.get(key) != value
                       for key, value in counts.items())
                or counts['countedObjectCount'] != task['requestedObjects']
                or binding['sourceSelectedCount'] != counts['selectedTotalCount']
                or type(binding.get('companionWindowCount')) is not int
                or binding['companionWindowCount'] < 0
                or type(binding.get('renderedWindowCount')) is not int
                or binding['renderedWindowCount'] != counts['selectedWindowCount'] + binding['companionWindowCount']):
            raise ValueError('Prepared task differs from its verified original source artifact')
    return receipt


def author_scope(source, room):
    """Execute only unchanged pure upstream functions, never its top-level API."""
    import numpy as np
    from PIL import Image
    import tiktoken

    raw = source.read_text(encoding='utf-8')
    if hashlib.sha256(raw.encode()).hexdigest() != AUTHOR_PROMPT_SHA256:
        raise ValueError('Prompt source is not the pinned authors implementation')
    functions = {'load_room_boxes', 'load_features', 'get_closest_room', 'form_prompt_for_chatgpt'}
    nodes = [node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef) and node.name in functions]
    if len(nodes) != len(functions):
        raise ValueError('Missing original prompt function')
    gpt2 = tiktoken.get_encoding('gpt2')
    args = SimpleNamespace(room=room, normalize=True, unit='px', icl_type='k-similar',
                           test=False, gpt_input_length_limit=7000)
    scope = {'np': np, 'Image': Image, 'op': __import__('os').path, 'args': args,
             'tokenizer': lambda text: {'input_ids': gpt2.encode(text)}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
    return scope


def build_requests(tasks, manifest, directory, source, room_type):
    """Use the original retrieved demonstrations; add only its inventory sentence."""
    import tiktoken

    room = 'livingroom' if room_type == 'living_room' else room_type
    scope = author_scope(source, room)
    stats = json.loads((directory / 'dataset_stats.txt').read_bytes())
    if manifest['sourceCommit'] != AUTHOR_COMMIT or len(manifest['targetIds']) != len(tasks):
        raise ValueError('Authors data manifest differs from the frozen task count')
    # The manifest's file checksums bind every training/target metadata input.
    for entry in manifest['files']:
        path = (directory / entry['file']).resolve()
        if not path.is_relative_to(directory.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Authors training/held-out metadata checksum changed')
    prompts, metadata = {}, {}
    for name in manifest['trainingIds'] + manifest['targetIds']:
        condition, layout, data = scope['load_room_boxes'](str(directory), name, stats, 'px')
        prompts[name] = (condition, layout)
        metadata[name] = data
    features = scope['load_features']({key: metadata[key] for key in manifest['trainingIds']})
    target_features = scope['load_features']({key: metadata[key] for key in manifest['targetIds']})
    training = {key: prompts[key] for key in manifest['trainingIds']}
    gpt4 = tiktoken.get_encoding('cl100k_base')
    requests = []
    for task, name in zip(tasks, manifest['targetIds']):
        if any(label not in stats['object_types'] for label in task['nativeInventory']):
            raise ValueError('Native inventory contains a category absent from the authors room vocabulary')
        messages = scope['form_prompt_for_chatgpt'](prompts[name], 8 if room_type == 'bedroom' else 4,
                                                  stats, training, features, target_features[name])
        if not messages[-1]['content'].endswith('Layout:\n'):
            raise ValueError('Authors final prompt delimiter changed')
        inventory = ', '.join(f'{label}: {count}' for label, count in sorted(task['nativeInventory'].items()))
        instruction = (f'Generate exactly {task["requestedObjects"]} furniture instances with this inventory: {inventory}. '
                       'Include no other objects. Use one CSS line per instance; count repeated instances separately.\nLayout:\n')
        messages[-1]['content'] = messages[-1]['content'][:-len('Layout:\n')] + instruction
        tokens = 3 + sum(3 + len(gpt4.encode(row['role'])) + len(gpt4.encode(row['content'])) for row in messages)
        if tokens + 1024 > 8192:
            raise ValueError('Prompt exceeds the original GPT-4 context allowance')
        payload = {'model': 'gpt-4', 'messages': messages, 'temperature': .7, 'max_tokens': 1024,
                   'top_p': 1, 'frequency_penalty': 0, 'presence_penalty': 0, 'stop': 'Condition:', 'n': 1}
        requests.append({'id': task['id'], 'roomType': room_type, 'sourceRoomId': name,
                         'requestedInventory': task['inventory'], 'nativeInventory': task['nativeInventory'],
                         'requestedObjects': task['requestedObjects'], 'task': task, 'taskSha256': digest(task),
                         'estimatedInputTokens': tokens, 'request': payload, 'requestSha256': digest(payload)})
    return requests


def prepare(design_path, tasks_path, source, output, source_slots_path=None, node_path=None, source_campaign=None):
    from serverless.benchmark.run_batch import run_lock

    output = scratch_path(output)
    with run_lock(output):
        return _prepare(design_path, tasks_path, source, output, source_slots_path, node_path, source_campaign)


def _prepare(design_path, tasks_path, source, output, source_slots_path=None, node_path=None, source_campaign=None):
    from serverless.benchmark.layoutgpt_controlled import hydrate
    import os

    output = scratch_path(output)
    design_raw, tasks_raw = design_path.read_bytes(), tasks_path.read_bytes()
    design, task_document = json.loads(design_raw), json.loads(tasks_raw)
    if design.get('schema') != 'soilie.layoutgpt-inventory-extension/v1':
        raise ValueError('An immutable seven/eight extension design is required')
    freeze_design(design_path, design['campaignId'], design['perCell'], design['budgetUsd'],
                  design['selectionSeed'], design['retainedCampaignSha256'], design['inventoryPolicy'],
                  archive_date=design['publicOutputPrefix'].removeprefix('files/outputs/').removesuffix('/'))
    if task_document.get('designSha256') != hashlib.sha256(design_raw).hexdigest():
        raise ValueError('Bound inventories refer to a different frozen design')
    tasks = validate_tasks(design, task_document['tasks'])
    source_slot_sha = None
    source_binding = None
    if design['inventoryPolicy'] == 'fresh-soilie-first-scheduled-local-v1':
        if source_slots_path is None or source_campaign is None:
            raise ValueError('Shared inventories require source-slots and the exact source-campaign evidence')
        source_raw = Path(source_slots_path).read_bytes()
        source_slot_sha = hashlib.sha256(source_raw).hexdigest()
        slots = json.loads(source_raw)
        if (slots.get('policy') != design['inventoryPolicy'] or slots.get('sourcePlatform') != 'local'
                or slots.get('designSha256') != hashlib.sha256(design_raw).hexdigest()
                or task_document.get('sourceSlotsSha256') != source_slot_sha
                or len(slots['tasks']) != len(tasks)):
            raise ValueError('Source request schedule differs from the frozen shared-inventory design')
        for task, slot in zip(tasks, slots['tasks']):
            if (task['id'] != slot['taskId'] or task['soilieSceneId'] != slot['soilieSceneId']
                    or task['soilieCampaignSha256'] != slots['soilieCampaignSha256']):
                raise ValueError('Do not skip failed/unsupported source seeds or substitute platform duplicates')
        source_binding = verify_source_artifacts(design_path, source_slots_path, tasks,
                                                hashlib.sha256(tasks_raw).hexdigest(), source_campaign)
    if (output / 'requests.json').exists():
        raise ValueError('Do not overwrite a prepared inference plan')
    os.environ['TIKTOKEN_CACHE_DIR'] = str((output / 'tokenizer-cache').resolve())
    requests, manifests = [], {}
    for offset, room in enumerate(('bedroom', 'living_room')):
        selected = [task for task in tasks if task['roomType'] == room]
        directory = output / room
        directory.mkdir(parents=True, exist_ok=True)
        manifest = hydrate(directory, len(selected), design['selectionSeed'] + offset,
                           room='livingroom' if room == 'living_room' else room)
        requests.extend(build_requests(selected, manifest, directory / 'data', source, room))
        manifests[room] = hashlib.sha256((directory / 'data-manifest.json').read_bytes()).hexdigest()
    from serverless.benchmark.layoutgpt_extension_store import configuration, runtime_snapshot
    snapshot = runtime_snapshot(node_path)
    plan = {'schemaVersion': 2, 'variant': VARIANT, 'campaignId': design['campaignId'],
            'sourceCommit': AUTHOR_COMMIT, 'authorPromptSha256': AUTHOR_PROMPT_SHA256,
            'expectedResponseModel': 'gpt-4-0613',
            'promptProtocol': PROMPT_PROTOCOL, 'perCell': design['perCell'], 'budgetUsd': design['budgetUsd'],
            'campaignSha256': hashlib.sha256(design_raw).hexdigest(),
            'retainedCampaignSha256': design['retainedCampaignSha256'],
            'taskManifestSha256': hashlib.sha256(tasks_raw).hexdigest(), 'dataManifestSha256': manifests,
            'publicOutputPrefix': design['publicOutputPrefix'], 'requests': requests,
            'outputArchive': configuration(archive_date=design['publicOutputPrefix'].removeprefix('files/outputs/').removesuffix('/')),
            'inventoryPolicy': design['inventoryPolicy'],
            'objectCountDefinition': OBJECT_COUNT_DEFINITION,
            'sourceSlotsSha256': source_slot_sha,
            'sourceBindingSha256': hashlib.sha256((canonical(source_binding) + '\n').encode()).hexdigest()
                                  if source_binding is not None else None,
            'runtimeSnapshot': snapshot, 'runtimeSnapshotSha256': digest(snapshot),
            'methods': ('New seven/eight-object inventory extension; original GPT-4, authors CSS prompt, '
                        'K=8 bedroom / K=4 living-room retrieval, training examples and held-out room dimensions. '
                        'Only the original explicit inventory sentence is added. Controlled counts refer to '
                        'non-window selected instances; SOILIE window extras and renderer companions remain '
                        'in the source-artifact binding but do not consume the LayoutGPT inventory quota. '
                        'Equal fixed attempts per room/count; '
                        'no quality-based replenishment. Model-native category mappings and inventory origins are '
                        'recorded per task; shared inventories use the first scheduled local source requests '
                        'with no failed/unsupported-seed substitution; native mappings remain disclosed. '
                        'Retain all parse/inventory failures and uncertain charges. This is not mesh realization.')}
    if source_slot_sha is not None:
        (output / 'source-slots.json').write_bytes(source_raw)
        (output / 'source-binding.json').write_bytes((canonical(source_binding) + '\n').encode())
    write_json(output / 'requests.json', plan)
    return {'prepared': len(requests), 'budgetUsd': design['budgetUsd'],
            'maximumReservedUsd': len(requests) * RESERVATION_USD, 'inferenceStarted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    design = commands.add_parser('design', help='Freeze attempt allocation only; no source downloads or API calls')
    design.add_argument('--output', type=Path, required=True)
    design.add_argument('--campaign-id', required=True)
    design.add_argument('--per-cell', type=int, required=True)
    design.add_argument('--budget-usd', type=float, required=True)
    design.add_argument('--seed', type=int, required=True)
    design.add_argument('--retained-campaign-sha256', required=True)
    design.add_argument('--archive-date', default='2026-10-25')
    design.add_argument('--inventory-policy', default='explicit-model-native-or-fresh-soilie-v1',
                        choices=('explicit-model-native-or-fresh-soilie-v1', 'fresh-soilie-first-scheduled-local-v1'))
    preparation = commands.add_parser('prepare', help='Bind explicit inventories and hydrate authors metadata; no inference')
    for name in ('design', 'tasks', 'source', 'output'):
        preparation.add_argument('--' + name, type=Path, required=True)
    preparation.add_argument('--source-slots', type=Path)
    preparation.add_argument('--node-path', type=Path)
    preparation.add_argument('--source-campaign', type=Path)
    scheduling = commands.add_parser('source-slots', help='Freeze the first scheduled local source seeds, without quality filtering')
    for name in ('design', 'campaign', 'output'):
        scheduling.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'design':
        result = freeze_design(args.output, args.campaign_id, args.per_cell, args.budget_usd,
                               args.seed, args.retained_campaign_sha256, args.inventory_policy, args.archive_date)
        print(json.dumps({'frozenTasks': len(result['tasks']), 'budgetUsd': result['budgetUsd'],
                          'inferenceStarted': False}))
    elif args.command == 'source-slots':
        result = source_slots(args.design, args.campaign, args.output)
        print(json.dumps({'sourceSlots': len(result['tasks']), 'sourcePlatform': result['sourcePlatform'],
                          'inferenceStarted': False}))
    else:
        print(json.dumps(prepare(args.design, args.tasks, args.source, args.output, args.source_slots,
                                 args.node_path, args.source_campaign)))


if __name__ == '__main__':
    main()
