"""Compact task-owned remote checkpoint view for the authenticated SSH relay."""
import json
from pathlib import Path

if __name__=='__main__':
    root=Path('/mnt/soilie/campaign')
    receipts=[json.loads(p.read_bytes()) for p in sorted(root.glob('*/receipt.json'))]
    finished={r['id'] for r in receipts}
    print(json.dumps({'receipts':receipts,'running':[p.parent.name for p in root.glob('*/started.json')
                                                   if p.parent.name not in finished]}))
