"""Read-only terminal progress bars for a frozen pilot or authoritative cohort."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import time

from serverless.cloud_benchmark.authoritative import campaign_status, load_plan


def render(status, width=32):
    lines = [f"SOILIE {status['phase']}  {status['planSha256'][:12]}"]
    for cell in status['cells']:
        fraction = min(1, cell['complete'] / cell['target']) if cell['target'] else 0
        filled = int(fraction * width)
        bar = '#' * filled + '-' * (width - filled)
        name = f"{cell['platform']} {cell['roomType'].replace('_', ' ')}"
        lines.append(f"{name:<19} [{bar}] {cell['complete']:>4}/{cell['target']:<4} {fraction:6.1%}")
    lines.append(f"Attempts {status['attempts']} | failed {status['failedAttempts']} | "
                 f"unreconciled {status['unknownAttempts']}")
    lines.append(f"Lambda all pilots + campaign: ${status['costPlusReservedUSD']:.4f} / "
                 f"${status['budgetUSD']:.2f} (includes in-flight reservations)")
    lines.append('Complete' if status['complete'] else 'Ctrl+C closes this monitor; workers continue.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--budget-root', type=Path,
                        help='Read the same budget directory through a different Windows/WSL mount path')
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not .2 <= args.interval <= 60:
        parser.error('Use a refresh interval between 0.2 and 60 seconds')
    plan = load_plan(args.root, verify_runtime=False)
    if args.budget_root:
        plan = deepcopy(plan)
        plan['configuration']['budgetRoot'] = str(args.budget_root.resolve())
    previous_lines = 0
    try:
        while True:
            # Atomic writers may briefly conflict with Windows virus scanners.
            # A transient read failure does not mutate or retry benchmark work.
            try:
                output = render(campaign_status(args.root, plan))
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
