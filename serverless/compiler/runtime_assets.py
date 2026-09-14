"""Create and verify the immutable non-code inputs used by SOILIE-3D V4."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from serverless.compiler.build_runtime import V4_DATA_FILES


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_paths(repository: Path) -> list[Path]:
    files = [repository / "assets" / "asset_rotations.csv"]
    files.extend(sorted((repository / "assets").glob("*.obj")))
    files.extend(repository / "data" / name for name in V4_DATA_FILES)
    return files


def build_manifest(repository: Path) -> dict:
    files = expected_paths(repository)
    if len([path for path in files if path.suffix == ".obj"]) != 191:
        raise RuntimeError("The V4 runtime manifest requires exactly 191 OBJ meshes")
    rows = []
    for path in files:
        if not path.is_file():
            raise RuntimeError(f"Required runtime input is missing: {path}")
        relative = path.relative_to(repository).as_posix()
        rows.append(
            {
                "path": relative,
                "sourceKey": f"files/{relative}",
                "bytes": path.stat().st_size,
                "sha256": checksum(path),
            }
        )
    return {"schemaVersion": 1, "files": rows}


def verify(repository: Path, manifest: dict) -> None:
    if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("Unsupported runtime asset manifest")
    expected = {path.relative_to(repository).as_posix() for path in expected_paths(repository)}
    declared = {row.get("path") for row in manifest["files"]}
    if declared != expected:
        raise ValueError("Runtime manifest file set does not match the V4 inputs")
    for row in manifest["files"]:
        path = (repository / row["path"]).resolve()
        if not path.is_relative_to(repository.resolve()) or not path.is_file():
            raise ValueError(f"Runtime input is unavailable: {row['path']}")
        if path.stat().st_size != row["bytes"] or checksum(path) != row["sha256"]:
            raise ValueError(f"Runtime input checksum differs: {row['path']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create", "verify"))
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=Path("serverless/runtime-assets.json"))
    args = parser.parse_args()
    repository = args.repository.resolve()
    manifest_path = args.manifest.resolve()
    if args.command == "create":
        document = build_manifest(repository)
        manifest_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"files": len(document["files"]), "manifest": str(manifest_path)}))
    else:
        verify(repository, json.loads(manifest_path.read_text(encoding="utf-8")))
        print(json.dumps({"verified": True, "manifest": str(manifest_path)}))


if __name__ == "__main__":
    main()
