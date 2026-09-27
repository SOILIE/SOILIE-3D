"""One fresh Codex process per judgment; never resume a review conversation.

Uses existing local Codex authentication, not a new API key or paid endpoint.
No model fallback. Invalid answers or tool use halt collection for inspection.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from serverless.cloud_benchmark.staged_pilot import (digest, load_frozen, read, record, results, write_new, delivery_prompt)


def verify_panel_pixels(image, panel, x):
    if panel.size != (720, 1080) or panel.convert('RGBA').tobytes() != image.crop((x, 66, x + 720, 1146)).convert('RGBA').tobytes():
        raise ValueError('Panel pixels changed during composition')


def preflight(root):
    from PIL import Image
    root = Path(root)
    protocol = load_frozen(root)
    for source in protocol['sourceImages'].values():
        if digest((root / source['file']).read_bytes()) != source['sha256']:
            raise ValueError('Frozen source SVG changed')
    index = read(root / 'packets/index.json')
    if set(index) != {row['assignmentId'] for row in protocol['assignments']}:
        raise ValueError('Incomplete or extra reviewer packets')
    checked_packets = set()
    assignments = {row['assignmentId']: row for row in protocol['assignments']}
    for row in protocol['assignments']:
        if 'evidence' in row:
            if digest(row['evidence']) != row['evidenceSha256'] or digest(delivery_prompt(protocol, row).encode('utf-8')) != row['deliveryPromptHash']:
                raise ValueError('Frozen case evidence or prompt changed')
            partner = row.get('pairedWith') or row['repeatOf']
            if partner:
                original_row = assignments[partner]
                if row.get('pairedWith') and (original_row.get('pairedWith') != row['assignmentId']
                    or row['profile'] != original_row['profile'] or row['pairId'] != original_row['pairId']
                    or row['leftCondition'] != original_row['rightCondition']):
                    raise ValueError('Counterbalanced assignment mismatch')
                if any(row['evidence'][side] != original_row['evidence'][other] for side, other in (('left', 'right'), ('right', 'left'))):
                    raise ValueError('Reversed evidence did not swap the same rooms')
        packet = index[row['assignmentId']]
        raw = (root / packet['file']).read_bytes()
        if digest(raw) != packet['imageSha256']:
            raise ValueError('Packet checksum mismatch')
        if packet['file'] in checked_packets:
            # Same bytes are reused across dimensions, but routing/evidence
            # still receive the checks above on every assignment.
            image = None
        else:
            image = Image.open(root / packet['file'])
        if image is not None:
            if image.size != (1480, 1146):
                raise ValueError('Unexpected packet dimensions')
            for side, x in (('left', 10), ('right', 750)):
                path = root / packet[side + 'Panel']
                if digest(path.read_bytes()) != packet[side + 'PanelSha256']:
                    raise ValueError('Panel checksum mismatch')
                with Image.open(path) as panel:
                    verify_panel_pixels(image, panel, x)
            image.close()
            checked_packets.add(packet['file'])
        partner = row.get('pairedWith') or row['repeatOf']
        if partner:
            original = index[partner]
            if packet['leftPanelSha256'] != original['rightPanelSha256'] or packet['rightPanelSha256'] != original['leftPanelSha256']:
                raise ValueError('Repeat panels did not swap exactly')
    repeat_count = sum(bool(row['repeatOf']) for row in protocol['assignments'])
    full = protocol.get('stage') == 'full_counterbalanced'
    if full and (len(index) != 4800 or repeat_count or sum(bool(r.get('pairedWith')) for r in protocol['assignments']) != 4800):
        raise ValueError('The full campaign requires 4800 paired judgments')
    if not full and (len(index) != 320 or repeat_count != 64):
        raise ValueError('The approved pilot requires 320 packets and 64 controls')
    result = {'passed': True, 'protocolSha256': digest(protocol), 'packetIndexSha256': digest(index),
              'packets': len(index), 'pixelIdenticalSwaps': repeat_count}
    if full:
        result['pixelIdenticalSwaps'] = len(index)//2
    target = root / 'preflight.json'
    if target.exists():
        if read(target) != result:
            raise ValueError('Preflight changed')
    else:
        write_new(target, result)
    return result


def command(executable, workspace, reviewer):
    return [executable, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
            '-C', str(workspace), '--model', reviewer['model'], '-c', 'model_reasoning_effort=' + reviewer['reasoningEffort'],
            '-c', 'project_doc_max_bytes=0', '-c', 'web_search=disabled', '-c', 'features.memories=false',
            '-c', 'memories.use_memories=false', '-c', 'memories.generate_memories=false',
            '-c', 'features.multi_agent=false', '-c', 'features.shell_tool=false',
            '-c', 'model_instructions_file=' + json.dumps(str(workspace / 'system.txt')),
            '--output-schema', str(workspace / 'schema.json'), '--image', str(workspace / 'pair.png'),
            '--output-last-message', str(workspace / 'answer.json'), '--json', '-']


def parse_events(raw):
    events = [json.loads(line) for line in raw.splitlines() if line.strip().startswith('{')]
    threads = [row['thread_id'] for row in events if row.get('type') == 'thread.started']
    if len(threads) != 1 or not any(row.get('type') == 'turn.completed' for row in events):
        raise ValueError('A complete isolated model turn is required')
    if any(row.get('type') in ('turn.failed', 'error') for row in events):
        raise ValueError('Model reported an error; no answer may be silently retried')
    for event in events:
        if event.get('type', '').startswith('item.') and event.get('item', {}).get('type') not in ('agent_message', 'reasoning'):
            raise ValueError('Reviewer used a tool; isolate and inspect this attempt')
    return threads[0], next(row.get('usage', {}) for row in reversed(events) if row.get('type') == 'turn.completed')


def run_one(root, protocol, row, executable, stop):
    if stop.is_set():
        return None
    root = Path(root)
    reviewer = next(value for value in protocol['reviewers'] if value['reviewerId'] == row['reviewerId'])
    index = read(root / 'packets/index.json')
    packet = index[row['assignmentId']]
    # Assignment folders contain one neutral image and the instructions only.
    # No prior answer, baseline identity or private routing enters the context.
    workspace = root / 'execution' / row['assignmentId']
    if workspace.exists():
        raise ValueError('An unsubmitted attempt exists; inspect it before any retry')
    workspace.mkdir(parents=True)
    (workspace / 'system.txt').write_text(reviewer['systemPrompt'], encoding='utf-8')
    write_new(workspace / 'schema.json', protocol['outputSchema'])
    shutil.copyfile(root / packet['file'], workspace / 'pair.png')
    if digest((workspace / 'pair.png').read_bytes()) != packet['imageSha256']:
        raise ValueError('Delivery image changed')
    args = command(executable, workspace, reviewer)
    prompt_text = delivery_prompt(protocol, row)
    extra = {field: row[field] for field in ('deliveryPromptHash', 'evidenceSha256') if field in row}
    if extra:
        (workspace / 'prompt.txt').write_text(prompt_text, encoding='utf-8')
    write_new(workspace / 'invocation.json', {**extra, 'model': reviewer['model'], 'reasoningEffort': reviewer['reasoningEffort'],
        'args': args, 'promptHash': reviewer['promptHash'], 'systemPromptHash': reviewer['systemPromptHash'],
        'imageSha256': packet['imageSha256'], 'newConversation': True})
    started = time.monotonic()
    environment = dict(os.environ)
    # Only use the existing Codex account. An unrelated shell API-key setting
    # must not silently switch this evaluation to separately billed API usage.
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY'):
        environment.pop(key, None)
    result = subprocess.run(args, input=prompt_text, capture_output=True, text=True,
                            encoding='utf-8', cwd=workspace, env=environment, timeout=900,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    (workspace / 'events.jsonl').write_text(result.stdout, encoding='utf-8')
    (workspace / 'stderr.log').write_text(result.stderr, encoding='utf-8')
    if result.returncode:
        raise ValueError(f'Codex exited {result.returncode}; inspect the saved attempt')
    thread, usage = parse_events(result.stdout)
    answer = read(workspace / 'answer.json')
    receipt = {**extra, 'model': reviewer['model'], 'reasoningEffort': reviewer['reasoningEffort'],
               'promptHash': reviewer['promptHash'], 'systemPromptHash': reviewer['systemPromptHash'],
               'imageSha256': packet['imageSha256'], 'isolatedContext': True, 'threadId': thread,
               'usage': usage, 'durationSeconds': time.monotonic() - started,
               'eventsSha256': digest(result.stdout.encode()), 'runner': 'codex exec --ephemeral'}
    record(root, row['assignmentId'], answer, receipt)
    return row['assignmentId']


def run(root, executable, workers=3, limit=None):
    root = Path(root).resolve()
    if workers not in (1, 2, 3):
        raise ValueError('Use one to three independent workers')
    preflight(root)
    protocol = load_frozen(root)
    summary = results(root)
    pending = set(summary['remainingAssignmentIds'])
    rows = [row for row in protocol['assignments'] if row['assignmentId'] in pending]
    if limit is not None:
        rows = rows[:limit]
    stop = threading.Event()
    failures = []
    completed = summary['responses']
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, root, protocol, row, executable, stop): row for row in rows}
        for future in as_completed(futures):
            try:
                key = future.result()
                if key:
                    completed += 1
                    print(json.dumps({'saved': completed, 'expected': 320}), flush=True)
            except Exception as error:
                stop.set()
                row = futures[future]
                failures.append({'assignmentId': row['assignmentId'], 'error': str(error)})
                print(json.dumps({'stopped': True, **failures[-1]}), flush=True)
    if failures:
        raise RuntimeError('Collection stopped; saved answers and attempts are preserved')
    return results(root)


def recover_completed(root):
    """Submit already finished isolated output after interruption, without calls.

    Incomplete/error attempts remain untouched and require investigation. This
    never replaces an existing response or asks for a new answer to that case.
    """
    root = Path(root).resolve()
    preflight(root)
    protocol = load_frozen(root)
    pending = set(results(root)['remainingAssignmentIds'])
    recovered = 0
    for row in protocol['assignments']:
        key = row['assignmentId']
        workspace = root / 'execution' / key
        if key not in pending or not workspace.exists():
            continue
        if not all((workspace / name).is_file() for name in ('events.jsonl', 'answer.json', 'invocation.json')):
            raise ValueError('Incomplete execution cannot be recovered or rerun automatically: ' + key)
        raw = (workspace / 'events.jsonl').read_text(encoding='utf-8')
        thread, usage = parse_events(raw)
        invocation = read(workspace / 'invocation.json')
        receipt = {name: invocation[name] for name in ('model', 'reasoningEffort', 'promptHash', 'systemPromptHash', 'imageSha256')}
        receipt.update({name: invocation[name] for name in ('deliveryPromptHash', 'evidenceSha256') if name in invocation})
        receipt.update(isolatedContext=True, threadId=thread, usage=usage,
                       eventsSha256=digest(raw.encode('utf-8')), runner='codex exec --ephemeral', recoveredExistingOutput=True)
        record(root, key, read(workspace / 'answer.json'), receipt)
        recovered += 1
    return {'recovered': recovered, 'modelCalls': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--authorize-review', action='store_true')
    parser.add_argument('--recover-completed', action='store_true')
    parser.add_argument('--codex', default=shutil.which('codex'))
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.preflight_only:
        print(json.dumps(preflight(args.root)))
    elif args.recover_completed:
        print(json.dumps(recover_completed(args.root)))
    elif args.authorize_review and args.codex:
        run(args.root, args.codex, args.workers, args.limit)
    else:
        parser.error('Review execution requires --authorize-review and a local Codex executable')
