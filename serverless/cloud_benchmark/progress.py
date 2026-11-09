"""Read-only terminal progress bars for a frozen pilot or authoritative cohort."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import os
from pathlib import Path
import shutil
import sys
import time

from serverless.cloud_benchmark.authoritative import campaign_status, load_plan


def render(status, width=32, *, color=False):
    """Keep plain log output stable; color is only a terminal presentation choice."""
    lines = [f"SOILIE {status['phase']}  {status['planSha256'][:12]}"]
    for cell in status['cells']:
        fraction = min(1, cell['complete'] / cell['target']) if cell['target'] else 0
        filled = int(fraction * width)
        if color:
            shade = 81 if cell['platform'] == 'local' else 141
            bar = (f'\033[38;5;{shade}m' + '█' * filled + '\033[38;5;238m'
                   + '░' * (width - filled) + '\033[0m')
        else:
            bar = '#' * filled + '-' * (width - filled)
        name = f"{cell['platform']} {cell['roomType'].replace('_', ' ')}"
        lines.append(f"{name:<19} [{bar}] {cell['complete']:>4}/{cell['target']:<4} {fraction:6.1%}")
    lines.append(f"Attempts {status['attempts']} | failed {status['failedAttempts']} | "
                 f"unreconciled {status['unknownAttempts']}")
    lines.append(f"Lambda all pilots + campaign: ${status['costPlusReservedUSD']:.4f} / "
                 f"${status['budgetUSD']:.2f} (includes in-flight reservations)")
    if status['complete'] and status.get('parentEvidenceReadback'):
        lines.append('Generation complete; evidence verification reported separately.')
    else:
        lines.append('Complete' if status['complete'] else 'Ctrl+C closes this monitor; workers continue.')
        if status.get('parentEvidenceReadback'):
            lines.append('Status uses ledger metadata; parent evidence readback was not performed.')
    return '\n'.join(lines)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--budget-root', type=Path,
                        help='Read the same budget directory through a different Windows/WSL mount path')
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--color', choices=('auto', 'always', 'never'), default='auto',
                        help='ANSI terminal colors; redirected logs remain plain in auto mode')
    args = parser.parse_args()
    if not .2 <= args.interval <= 60:
        parser.error('Use a refresh interval between 0.2 and 60 seconds')
    plan = load_plan(args.root, verify_runtime=False)
    if args.budget_root:
        plan = deepcopy(plan)
        plan['configuration']['budgetRoot'] = str(args.budget_root.resolve())
    previous_lines = 0
    color = args.color == 'always' or (args.color == 'auto' and sys.stdout.isatty()
                                      and 'NO_COLOR' not in os.environ)
    try:
        while True:
            # Atomic writers may briefly conflict with Windows virus scanners.
            # A transient read failure does not mutate or retry benchmark work.
            try:
                width = max(10, min(40, shutil.get_terminal_size((100, 24)).columns - 42))
                output = render(campaign_status(args.root, plan), width=width, color=color)
            except (PermissionError, FileNotFoundError) as error:
                output = f'Waiting for checkpoint: {error}'
            if previous_lines and sys.stdout.isatty():
                print(f'\033[{previous_lines}F\033[J', end='')
            elif previous_lines:
                print(datetime.now().isoformat(timespec='seconds'))
            print(output, flush=True)
            previous_lines = output.count('\n') + 1
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nMonitor closed. Generation workers were not changed.', flush=True)


if __name__ == '__main__':
    main()
