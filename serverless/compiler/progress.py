"""Read-only source-rebuild progress; scene receipts are not an audit pass."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_bytes())


def source_status(workspace):
    workspace = Path(workspace).resolve()
    if not workspace.is_relative_to((ROOT / '.codex').resolve()):
        raise ValueError('Source workspace must be below this repository .codex directory')
    recovery = read(workspace / 'sun3d-files.json')
    preprocessing = workspace / 'washington-preprocess.json'
    washington = read(preprocessing)['scenes'] if preprocessing.exists() else []
    sealed_preprocess = len(list((workspace / 'candidate/data/washington').glob('scene_*/preprocess-receipt.json')))
    cells = [{'name': 'Washington preprocess', 'complete': sealed_preprocess, 'target': 14}]
    for dataset, label, target in (
            ('princeton', 'SUN3D collection', sum(len(scene['retainedFrames']) for scene in recovery['scenes'])),
            ('washington', 'Washington collection', sum(len(scene['samples']) for scene in washington) if washington else 420)):
        complete = sum(len(read(path)['frames']) for path in (workspace / 'scene-receipts' / dataset).glob('*.json'))
        cells.append({'name': label, 'complete': complete, 'target': target})
    active = None
    pending = workspace / 'collector-pending.json'
    if pending.exists():
        transaction = read(pending)
        dataset, annotation = transaction['dataset'], transaction['annotation']
        if dataset not in ('princeton', 'washington') or Path(annotation).name != annotation:
            raise ValueError('Unexpected source-controller checkpoint identity')
        log = workspace / 'logs' / dataset / (Path(annotation).stem + '.log')
        # Only a progress observation: these frames are not counted in the
        # completed bar until the whole scene and color contribution are sealed.
        started = len(re.findall(r'Calculating Frame \d+ coordinates', log.read_text(errors='replace'))) if log.exists() else 0
        active = {'dataset': dataset, 'annotation': annotation, 'startedFrames': started}
    continuation = workspace / 'continuation-state.json'
    controller = read(continuation) if continuation.exists() else None
    return {'cells': cells, 'active': active, 'paused': (workspace / 'PAUSE_SOURCE').exists(),
            'gathered': (workspace / 'collection-summary.json').exists(),
            'continuation': controller}


def render(status, *, color=False, width=30):
    lines = ['SOILIE source preparation — not room generation']
    for cell in status['cells']:
        fraction = min(1, cell['complete'] / cell['target']) if cell['target'] else 0
        filled = int(width * fraction)
        bar = '#' * filled + '-' * (width - filled)
        if color:
            bar = '\033[38;5;81m' + '█' * filled + '\033[38;5;238m' + '░' * (width - filled) + '\033[0m'
        lines.append(f"{cell['name']:<22} [{bar}] {cell['complete']:>3}/{cell['target']:<3} {fraction:6.1%}")
    if status['active']:
        active = status['active']
        lines.append(f"Active: {active['dataset']}/{active['annotation']} — {active['startedFrames']} frames started, not yet scene-sealed")
    lines.append('Paused before new scene dispatch.' if status['paused'] else 'Ctrl+C closes only this monitor; source workers continue.')
    lines.append('Triplets gathered; independent audit/catalog gates remain separate.' if status['gathered']
                 else 'Triplet gathering pending. Bars count scene receipts, not scientific acceptance.')
    controller = status.get('continuation')
    if controller:
        lines.append(f"Continuation: {controller['status']} / {controller['phase']}")
        if controller['status'] == 'complete-awaiting-root-review':
            lines.append('Candidate awaits review; no promotion or room generation was started.')
        elif controller['status'] == 'stopped-error':
            lines.append('Continuation stopped. Inspect continuation-state.json and its stage log before resuming.')
    return '\n'.join(lines)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--color', choices=('auto', 'always', 'never'), default='auto')
    parser.add_argument('--interval', type=float, default=2)
    args = parser.parse_args()
    if not .2 <= args.interval <= 60:
        parser.error('Refresh interval must be between 0.2 and 60 seconds')
    color = args.color == 'always' or (args.color == 'auto' and sys.stdout.isatty() and 'NO_COLOR' not in os.environ)
    previous = 0
    try:
        while True:
            try:
                output = render(source_status(args.workspace), color=color,
                                width=max(10, min(35, shutil.get_terminal_size((100, 24)).columns - 43)))
            except (PermissionError, FileNotFoundError) as error:
                output = f'Waiting for a source checkpoint: {error}'
            if previous and sys.stdout.isatty():
                print(f'\033[{previous}F\033[J', end='')
            print(output, flush=True)
            previous = output.count('\n') + 1
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nMonitor closed; source workers were not changed.', flush=True)


if __name__ == '__main__':
    main()
