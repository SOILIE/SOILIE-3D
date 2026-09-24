"""Atomic checkpoints tolerant of brief Windows reader/antivirus file locks."""
import json
import time


def write_json(path, document):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(document,separators=(',',':')),encoding='utf-8')
    # Windows readers do not always grant FILE_SHARE_DELETE. Retrying only the
    # rename cannot duplicate an invocation or expose a partially written file.
    for attempt in range(101):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 100:
                raise
            time.sleep(.05)
