"""Prepare a disclosed count-conditioned LayoutGPT pilot; no inference calls.

The original prompt builder is executed from a pinned source checkout. Only
the final requested-instance-count sentence is added. Training demonstrations
come from the authors' data, never from generated rooms or judgement scores.
"""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import random
import re
import struct
from types import SimpleNamespace
import urllib.parse
import urllib.request
import zipfile
import zlib

import numpy as np
from PIL import Image
import tiktoken

from serverless.cloud_benchmark.checkpoint import write_json

COMMIT = 'fc31954962553e5b65bf267a904a6930d50b1f5e'
REPOSITORY = 'https://raw.githubusercontent.com/UCSB-AI/LayoutGPT/' + COMMIT
DATA_ID = '1NV3pmRpWcehPO5iKJPmShsRp_lNbxJuK'
DATA_URL = 'https://drive.google.com/file/d/' + DATA_ID + '/view'


def download_url():
    url = 'https://drive.usercontent.google.com/download?id=' + DATA_ID + '&export=download'
    with urllib.request.urlopen(url, timeout=60) as response:
        html = response.read(100000).decode()
    action = re.search(r'<form[^>]*action="([^"]+)"', html)[1]
    if urllib.parse.urlparse(action).hostname != 'drive.usercontent.google.com':
        raise ValueError('Unexpected download host')
    fields = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))
    return action + '?' + urllib.parse.urlencode(fields)


def ranged(url, start, end):
    request = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'})
    with urllib.request.urlopen(request, timeout=60) as response:
        expected = f'bytes {start}-{end}/'
        if response.status != 206 or not response.headers.get('Content-Range', '').startswith(expected):
            raise ValueError('Unexpected archive byte range')
        data = response.read(end - start + 2)
    if len(data) != end - start + 1:
        raise ValueError('Incomplete archive byte range')
    return data


class RemoteArchive(io.RawIOBase):
    def __init__(self, url, size):
        self.url, self.size, self.position = url, size, 0

    def seekable(self): return True

    def tell(self): return self.position

    def seek(self, offset, whence=0):
        self.position = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        return self.position

    def read(self, n=-1):
        n = min(n if n >= 0 else self.size, self.size - self.position)
        if n <= 0: return b''
        data = ranged(self.url, self.position, self.position + n - 1)
        self.position += len(data)
        return data


def hydrate(output, count, seed, start=0, data_cache=None):
    with urllib.request.urlopen(REPOSITORY + '/dataset/3D/livingroom_splits.json', timeout=60) as response:
        raw_splits = response.read()
    splits = json.loads(raw_splits)
    # Unique held-out room dimensions, not 120 repetitions of 53 floor plans.
    # LayoutGPT's prompt communicates rectangular max length/width; this
    # controlled task does not assess reconstruction of irregular outlines.
    targets = sorted(splits['test'])
    random.Random(seed).shuffle(targets)
    targets = targets[start:start + count]
    if len(targets) != count or set(targets) & set(splits['rect_train']):
        raise ValueError('Need distinct held-out targets outside the training demonstrations')
    names = {f'data_output/livingroom/{name}/boxes.npz' for name in targets + splits['rect_train']}
    names.add('data_output/livingroom/dataset_stats.txt')
    url = download_url()
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=-1'}), timeout=60) as response:
        size = int(response.headers['Content-Range'].split('/')[-1])
    with zipfile.ZipFile(RemoteArchive(url, size)) as archive:
        entries = {row.filename: row for row in archive.infolist() if row.filename in names}
    if set(entries) != names:
        raise ValueError('Authors archive lacks requested livingroom metadata')
    directory = data_cache or output / 'data'
    directory.mkdir(parents=True, exist_ok=True)

    def one(entry):
        relative = Path(*Path(entry.filename).parts[2:])
        target = (directory / relative).resolve()
        if not target.is_relative_to(directory.resolve()):
            raise ValueError('Invalid dataset path')
        if target.exists():
            data = target.read_bytes()
        else:
            header = ranged(url, entry.header_offset, entry.header_offset + 29)
            if header[:4] != b'PK\x03\x04': raise ValueError('Invalid ZIP local header')
            name_size, extra_size = struct.unpack_from('<HH', header, 26)
            start = entry.header_offset + 30 + name_size + extra_size
            compressed = ranged(url, start, start + entry.compress_size - 1)
            if entry.compress_type == zipfile.ZIP_DEFLATED: data = zlib.decompress(compressed, -15)
            elif entry.compress_type == zipfile.ZIP_STORED: data = compressed
            else: raise ValueError('Unsupported ZIP compression')
            if len(data) != entry.file_size or zlib.crc32(data) != entry.CRC:
                raise ValueError('Dataset CRC or length mismatch')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if len(data) != entry.file_size or zlib.crc32(data) != entry.CRC:
            raise ValueError('Cached dataset CRC or length mismatch')
        return {'file': relative.as_posix(), 'sha256': hashlib.sha256(data).hexdigest()}

    with ThreadPoolExecutor(max_workers=6) as pool:
        hashes = list(pool.map(one, entries.values()))
    plan = {'sourceCommit': COMMIT, 'dataSource': DATA_URL, 'archiveBytes': size,
            'splitSha256': hashlib.sha256(raw_splits).hexdigest(), 'seed': seed,
            'trainingIds': splits['rect_train'], 'targetIds': targets, 'files': hashes}
    write_json(output / 'data-manifest.json', plan)
    print(json.dumps({'hydratedFiles': len(hashes), 'targets': len(targets)}), flush=True)
    return plan


def prepare(output, source, count=120, seed=20260925, start=0, previous_batch=None):
    if (count, start) not in ((120, 0), (1, 120)):
        raise ValueError('Only the approved 120 proposals or one next held-out supplement may be prepared')
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'requests.json').exists():
        raise ValueError('Do not overwrite a prepared inference plan')
    previous = None
    if previous_batch:
        previous_batch = previous_batch.resolve()
        ledger_raw = (previous_batch / 'inference-ledger.json').read_bytes()
        ledger = json.loads(ledger_raw)
        if len(ledger['entries']) != 120 or any(row['status'] != 'complete' for row in ledger['entries'].values()):
            raise ValueError('The full original batch must be settled before its supplement')
        previous = {'folder': str(previous_batch), 'ledgerSha256': hashlib.sha256(ledger_raw).hexdigest(),
                    'planSha256': ledger['planSha256'],
                    'accountedUsd': sum(row['actualUsd'] for row in ledger['entries'].values())}
    if bool(previous) != bool(start):
        raise ValueError('A supplement must carry forward the settled batch spending')
    manifest = hydrate(output, count, seed, start, previous_batch / 'data' if previous_batch else None)
    # Extract unchanged pure functions, avoiding the legacy script's CLI,
    # model downloads and top-level API call. No prompt is hand-reconstructed.
    raw = source.read_text(encoding='utf-8')
    with urllib.request.urlopen(REPOSITORY + '/run_layoutgpt_3d.py', timeout=60) as response:
        official = response.read().decode()
    if raw.replace('\r\n', '\n') != official.replace('\r\n', '\n'):
        raise ValueError('Prompt source differs from pinned official source')
    functions = {'load_room_boxes', 'load_features', 'get_closest_room', 'form_prompt_for_chatgpt'}
    nodes = [node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef) and node.name in functions]
    if len(nodes) != len(functions): raise ValueError('Missing original prompt function')
    gpt2, gpt4 = tiktoken.get_encoding('gpt2'), tiktoken.get_encoding('cl100k_base')
    args = SimpleNamespace(room='livingroom', normalize=True, unit='px', icl_type='k-similar',
                           test=False, gpt_input_length_limit=7000)
    scope = {'np': np, 'Image': Image, 'op': __import__('os').path, 'args': args,
             'tokenizer': lambda text: {'input_ids': gpt2.encode(text)}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), scope)
    directory = previous_batch / 'data' if previous_batch else output / 'data'
    stats = json.loads((directory / 'dataset_stats.txt').read_text())
    prompts, meta = {}, {}
    for name in manifest['trainingIds'] + manifest['targetIds']:
        condition, layout, data = scope['load_room_boxes'](str(directory), name, stats, 'px')
        prompts[name] = (condition, layout); meta[name] = data
    features = scope['load_features']({key: meta[key] for key in manifest['trainingIds']})
    target_features = scope['load_features']({key: meta[key] for key in manifest['targetIds']})
    training = {key: prompts[key] for key in manifest['trainingIds']}
    requests = []
    for index, name in enumerate(manifest['targetIds'], start=start):
        messages = scope['form_prompt_for_chatgpt'](prompts[name], 4, stats, training,
                                                  features, target_features[name])
        requested = 3 + index % 4
        messages[-1]['content'] = messages[-1]['content'].replace('Layout:\n',
            f'Generate exactly {requested} furniture instances. Use one CSS line per instance; '
            'count repeated furniture instances separately.\nLayout:\n')
        tokens = 3 + sum(3 + len(gpt4.encode(row['role'])) + len(gpt4.encode(row['content'])) for row in messages)
        if tokens + 1024 > 8192: raise ValueError('Prompt exceeds GPT-4 context allowance')
        payload = {'model': 'gpt-4', 'messages': messages, 'temperature': .7, 'max_tokens': 1024,
                   'top_p': 1, 'frequency_penalty': 0, 'presence_penalty': 0, 'stop': 'Condition:', 'n': 1}
        requests.append({'id': f'controlled-living-{index:03d}', 'sourceRoomId': name,
                         'requestedObjects': requested, 'estimatedInputTokens': tokens,
                         'request': payload, 'requestSha256': hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()})
    write_json(output / 'requests.json', {'schemaVersion': 1, 'variant': 'living-room-count-conditioned',
        'sourceCommit': COMMIT, 'requests': requests, 'budgetUsd': 35, 'previousBatch': previous,
        'methods': 'Original K=4 retrieved rectangular-training examples and GPT-4 CSS prompt; one added count instruction cycling 3–6. Unique held-out room dimensions from the full test split use the original rectangular max-length/width prompt. No quality-based selection.'})
    print(json.dumps({'prepared': len(requests), 'minimumInputTokens': min(r['estimatedInputTokens'] for r in requests),
        'maximumInputTokens': max(r['estimatedInputTokens'] for r in requests),
        'maximumEstimatedTokenCostUsd': sum(r['estimatedInputTokens'] * .00003 + 1024 * .00006 for r in requests)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--previous-batch', type=Path)
    args = parser.parse_args()
    prepare(args.output, args.source, count=1 if args.previous_batch else 120,
            start=120 if args.previous_batch else 0, previous_batch=args.previous_batch)


if __name__ == '__main__': main()
