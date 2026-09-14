"""Single model identity shared by API, renderer, and benchmark artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _package_version() -> str:
    candidates = (
        Path(__file__).resolve().parents[2] / "package.json",
        Path("/var/task/package.json"),
    )
    for path in candidates:
        if path.exists():
            return str(json.loads(path.read_text(encoding="utf-8"))["version"])
    raise RuntimeError("SOILIE-3D package version is unavailable")


MODEL_VERSION = os.environ.get("SOILIE_MODEL_VERSION") or _package_version()
SOURCE_COMMIT = os.environ.get("SOILIE_SOURCE_COMMIT", "local")
ASSET_MANIFEST_SHA256 = os.environ.get("SOILIE_ASSET_MANIFEST_SHA256", "local")
IMPLEMENTATION_CHANNEL = os.environ.get("SOILIE_IMPLEMENTATION_CHANNEL", "publication-2026")


def model_document() -> dict[str, str]:
    return {
        "name": "SOILIE-3D V4",
        "version": MODEL_VERSION,
        "implementation": "original",
        "sourceCommit": SOURCE_COMMIT,
        "assetManifestSha256": ASSET_MANIFEST_SHA256,
        "channel": IMPLEMENTATION_CHANNEL,
    }
