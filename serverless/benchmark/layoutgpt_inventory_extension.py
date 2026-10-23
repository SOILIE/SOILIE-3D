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
import hashlib
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace

from serverless.cloud_benchmark.checkpoint import write_json


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


def freeze_design(output, campaign_id, per_cell, budget_usd, seed, retained_campaign_sha256):
    """Pin attempt counts before generation; no quality-based replenishment."""
    output = scratch_path(output)
    if (not re.fullmatch(r'[a-z][a-z0-9-]{2,39}', campaign_id) or
            type(per_cell) is not int or not 1 <= per_cell <= 60 or
            type(seed) is not int or seed < 0 or seed + 4 * per_cell > 2**53 - 1 or
            not isinstance(budget_usd, (int, float)) or not math.isfinite(budget_usd) or
            budget_usd < 4 * per_cell * RESERVATION_USD or
            not re.fullmatch(r'[a-f0-9]{64}', retained_campaign_sha256)):
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
              'publicOutputPrefix': 'files/outputs/2026-10-23/',
              'inventoryPolicy': 'explicit model-native mapping; do not assert identical cross-model inventories',
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
        neutral = Counter()
        for label, count in task['nativeInventory'].items():
            neutral[presentation_label(label)] += count
        if dict(neutral) != task['inventory']:
            raise ValueError('Native categories do not realize the declared neutral inventory')
        origin = task.get('inventoryOrigin')
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


def prepare(design_path, tasks_path, source, output):
    from serverless.benchmark.run_batch import run_lock

    output = scratch_path(output)
    with run_lock(output):
        return _prepare(design_path, tasks_path, source, output)


def _prepare(design_path, tasks_path, source, output):
    from serverless.benchmark.layoutgpt_controlled import hydrate
    import os

    output = scratch_path(output)
    design_raw, tasks_raw = design_path.read_bytes(), tasks_path.read_bytes()
    design, task_document = json.loads(design_raw), json.loads(tasks_raw)
    if design.get('schema') != 'soilie.layoutgpt-inventory-extension/v1':
        raise ValueError('An immutable seven/eight extension design is required')
    freeze_design(design_path, design['campaignId'], design['perCell'], design['budgetUsd'],
                  design['selectionSeed'], design['retainedCampaignSha256'])
    if task_document.get('designSha256') != hashlib.sha256(design_raw).hexdigest():
        raise ValueError('Bound inventories refer to a different frozen design')
    tasks = validate_tasks(design, task_document['tasks'])
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
    plan = {'schemaVersion': 2, 'variant': VARIANT, 'campaignId': design['campaignId'],
            'sourceCommit': AUTHOR_COMMIT, 'authorPromptSha256': AUTHOR_PROMPT_SHA256,
            'expectedResponseModel': 'gpt-4-0613',
            'promptProtocol': PROMPT_PROTOCOL, 'perCell': design['perCell'], 'budgetUsd': design['budgetUsd'],
            'campaignSha256': hashlib.sha256(design_raw).hexdigest(),
            'retainedCampaignSha256': design['retainedCampaignSha256'],
            'taskManifestSha256': hashlib.sha256(tasks_raw).hexdigest(), 'dataManifestSha256': manifests,
            'publicOutputPrefix': design['publicOutputPrefix'], 'requests': requests,
            'methods': ('New seven/eight-object inventory extension; original GPT-4, authors CSS prompt, '
                        'K=8 bedroom / K=4 living-room retrieval, training examples and held-out room dimensions. '
                        'Only the original explicit inventory sentence is added. Equal fixed attempts per room/count; '
                        'no quality-based replenishment. Model-native category mappings and inventory origins are '
                        'recorded per task; identical cross-model inventories are not assumed. '
                        'Retain all parse/inventory failures and uncertain charges. This is not mesh realization.')}
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
    preparation = commands.add_parser('prepare', help='Bind explicit inventories and hydrate authors metadata; no inference')
    for name in ('design', 'tasks', 'source', 'output'):
        preparation.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'design':
        result = freeze_design(args.output, args.campaign_id, args.per_cell, args.budget_usd,
                               args.seed, args.retained_campaign_sha256)
        print(json.dumps({'frozenTasks': len(result['tasks']), 'budgetUsd': result['budgetUsd'],
                          'inferenceStarted': False}))
    else:
        print(json.dumps(prepare(args.design, args.tasks, args.source, args.output)))


if __name__ == '__main__':
    main()
