"""Launch the original V4 renderer without changing its command-line parser.

Blender treats bare arguments after ``--python`` as files to open, while the
published V4 script expects its JSON payload in exactly that position.  The
serverless launcher accepts the payload after Blender's standard ``--``
separator, then recreates the argv shape V4 expects before executing the
original script byte-for-byte.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    try:
        separator = sys.argv.index("--")
        payload = sys.argv[separator + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError("The V4 renderer payload must follow Blender's -- separator.") from error

    modules_dir = Path.cwd() / "modules"
    sys.path.insert(0, str(modules_dir))
    sys.argv = ["blender", "--python", "modules/render.py", payload]
    runpy.run_path("modules/render.py", run_name="__main__")


if __name__ == "__main__":
    main()
