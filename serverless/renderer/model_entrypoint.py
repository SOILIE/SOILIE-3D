"""Launch the repository's SOILIE-3D renderer through its existing CLI shape.

Blender treats bare arguments after ``--python`` as files to open, while the
published model script expects its JSON payload in exactly that position. The
serverless launcher accepts the payload after Blender's standard ``--``
separator, then recreates the argv shape the renderer expects before executing
the checked-out script directly.
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
        raise RuntimeError("The SOILIE-3D renderer payload must follow Blender's -- separator.") from error

    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    modules_dir = Path.cwd() / "modules"
    sys.path.insert(0, str(modules_dir))
    sys.argv = ["blender", "--python", "modules/render.py", payload]
    runpy.run_path("modules/render.py", run_name="__main__")


if __name__ == "__main__":
    main()
