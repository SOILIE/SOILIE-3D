"""Parse paid LayoutGPT responses with its original parser; never fix layouts."""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
from string import digits

from serverless.benchmark.geometry import measure
from serverless.benchmark.import_layoutgpt import normalize
from serverless.cloud_benchmark.checkpoint import write_json


def load_parser(path):
    raw = path.read_text(encoding='utf-8')
    nodes = [node for node in ast.parse(raw).body
             if isinstance(node, ast.FunctionDef) and node.name == 'parse_3D_layout']
    if len(nodes) != 1: raise ValueError('Original LayoutGPT parser required')
    scope = {'digits': digits}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope['parse_3D_layout']


def compile_responses(folder, parser_path):
    plan_raw = (folder / 'requests.json').read_bytes()
    plan = json.loads(plan_raw)
    ledger = json.loads((folder / 'inference-ledger.json').read_bytes())
    if ledger['planSha256'] != hashlib.sha256(plan_raw).hexdigest():
        raise ValueError('Inference plan changed')
    parser = load_parser(parser_path)
    scenes, rows, attempts = [], [], []
    for index, request in enumerate(plan['requests']):
        entry = ledger['entries'].get(request['id'])
        if entry is None: continue
        attempt = {'id': request['id'], 'status': entry['status'],
                   'requestedObjects': request['requestedObjects'], 'wallSeconds': entry.get('wallSeconds'),
                   'usd': entry.get('actualUsd'), 'usage': entry.get('usage')}
        attempts.append(attempt)
        if entry['status'] != 'complete': continue
        raw = (folder / 'responses' / (request['id'] + '.json')).read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if checksum != entry['responseSha256']: raise ValueError('Response checksum differs')
        response = json.loads(raw)
        objects, unparsed = [], []
        content = response['choices'][0]['message']['content']
        for line in content.splitlines():
            if not line.strip(): continue
            try:
                label, box = parser(line, 'px')
                if label is not None: objects.append((label, box))
                else: unparsed.append(line)
            except (ValueError, KeyError, TypeError): unparsed.append(line)
        attempt.update(parsedObjects=len(objects), unparsedLines=len(unparsed),
                       countSatisfied=len(objects) == request['requestedObjects'],
                       finishReason=response['choices'][0]['finish_reason'])
        try:
            scene = normalize({'prompt': request['request']['messages'][-1]['content'], 'object_list': objects},
                              'living_room', index, checksum)
            scene['id'] = 'layoutgpt-' + request['id']
            scene['stage'] = 'api-final-layout'
            scene['benchmarkVariant'] = plan['variant']
            scene['provenance'] = {'commit': plan['sourceCommit'], 'inferenceRerun': True,
                'responseSha256': checksum, 'requestSha256': request['requestSha256'],
                'sourceRoomId': request['sourceRoomId'], 'model': response['model'],
                'requestedObjects': request['requestedObjects']}
            metric = measure(scene)
        except (ValueError, KeyError, TypeError):
            attempt['geometryStatus'] = 'invalid'
            continue
        attempt['geometryStatus'] = 'complete'
        scenes.append(scene); rows.append({'scene': scene, 'metrics': metric})
    result = {'schemaVersion': 1, 'variant': plan['variant'], 'methods': plan['methods'],
              'planSha256': ledger['planSha256'], 'complete': len(attempts) == len(plan['requests']),
              'scenes': scenes, 'rows': rows, 'attempts': attempts,
              'actualApiUsd': sum(row.get('actualUsd', 0) for row in ledger['entries'].values()),
              'reservedUncertainUsd': sum(row['reservedUsd'] for row in ledger['entries'].values() if row.get('actualUsd') is None)}
    write_json(folder / 'export.json', result)
    print(json.dumps({'attempted': len(attempts), 'validGeometry': len(scenes),
        'requestedCountSatisfied': sum(row.get('countSatisfied', False) for row in attempts),
        'unparsedLines': sum(row.get('unparsedLines', 0) for row in attempts),
        'furnitureCounts': dict(Counter(row['metrics']['objectCount'] for row in rows)),
        'actualApiUsd': result['actualApiUsd']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--parser', dest='parser_path', type=Path, required=True)
    args = parser.parse_args()
    compile_responses(args.folder, args.parser_path)


if __name__ == '__main__': main()
