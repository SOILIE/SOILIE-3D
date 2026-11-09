"""Run core/serverless tests with fake service clients and real Blender checks."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def source_fingerprint():
    """Bind results to the exact tested source, including an uncommitted suite."""
    paths = {ROOT / 'imagine.py', ROOT / 'package.json', ROOT / 'requirements.txt',
             ROOT / 'serverless/runtime-assets.json', ROOT / 'serverless/requirements-ci.txt'}
    for folder in ('modules', 'serverless', 'tests'):
        paths.update((ROOT / folder).rglob('*.py'))
    files = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(paths)}
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reject_network(*_args, **_kwargs):
    raise AssertionError("Tests must use a fake client; live network access is disabled")


def unit_suite():
    suite = unittest.TestSuite()
    for directory in (ROOT / "tests", ROOT / "serverless/tests"):
        # Separate loaders prevent the discovery root leaking between packages.
        suite.addTests(unittest.TestLoader().discover(
            str(directory), pattern="test_*.py", top_level_dir=str(ROOT)))
    return suite


def blender_command(command, fixture, timeout):
    """Retain earlier passes and partial diagnostics if one process fails."""
    try:
        completed = subprocess.run(command, cwd=ROOT, timeout=timeout,
                                   check=False, text=True, capture_output=True)
        stdout, stderr = completed.stdout, completed.stderr
        record = {"fixture": fixture, "returnCode": completed.returncode}
    except subprocess.TimeoutExpired as error:
        # TimeoutExpired can hold bytes even when text=True was requested.
        stdout, stderr = error.stdout or "", error.stderr or ""
        record = {"fixture": fixture, "returnCode": 1,
                  "error": f"Timed out after {timeout} seconds"}
    except OSError as error:
        stdout = stderr = ""
        record = {"fixture": fixture, "returnCode": 1, "error": str(error)}
    stdout = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
    stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
    print(stdout, end="")
    print(stderr, end="", file=sys.stderr)
    if record["returnCode"]:
        record["stdoutTail"] = stdout[-4000:]
        record["stderrTail"] = stderr[-4000:]
    return record, stdout


def asset_smoke_receipt(stdout):
    """A zero exit alone does not establish that all registered assets ran."""
    manifest = json.loads((ROOT / "serverless/runtime-assets.json").read_text(encoding="utf-8"))
    expected = {item["path"] for item in manifest["files"]
                if item["path"].startswith("assets/") and item["path"].endswith(".obj")}
    receipts = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("assetSmoke") == "passed":
            receipts.append(value)
    if not expected or len(receipts) != 1 or receipts[0].get("count") != len(expected):
        raise ValueError("Asset smoke did not confirm the complete registered mesh count")
    return receipts[0]


def run_blender(executable):
    from serverless.benchmark.run_batch import run_lock

    fixtures = [
        ROOT / "serverless/tests/blender_collision_fixtures.py",
        ROOT / "serverless/tests/blender_window_chisel_fixtures.py",
        ROOT / "serverless/tests/blender_settlement_fixtures.py",
        ROOT / "serverless/tests/blender_support_fixtures.py",
        ROOT / "serverless/tests/blender_saved_support_fixtures.py",
        ROOT / "serverless/tests/blender_solid_overlap_fixtures.py",
        *sorted((ROOT / "tests/blender").glob("test_*.py")),
    ]
    results = []
    # Never contaminate concurrently timed local generation.
    with run_lock(ROOT / ".codex/benchmark/local-timing-scheduler"):
        for fixture in fixtures:
            command = [str(executable), "--background", "--factory-startup",
                       "--threads", "1", "--python-exit-code", "2",
                       "--python", str(fixture)]
            print(f"\nBlender fixture: {fixture.relative_to(ROOT)}", flush=True)
            record, _ = blender_command(command, fixture.relative_to(ROOT).as_posix(), 180)
            results.append(record)
        # Import every selectable real OBJ without rendering a new room corpus.
        command = [str(executable), "--background", "--factory-startup",
                   "--threads", "1", "--python-exit-code", "2", "--python",
                   str(ROOT / "serverless/renderer/asset_smoke.py"), "--", str(ROOT)]
        print("\nBlender selectable-asset smoke check", flush=True)
        # Full OBJ imports from a Windows-mounted checkout can exceed ten
        # minutes. This is a test watchdog, not a benchmark timing limit.
        record, stdout = blender_command(command, "serverless/renderer/asset_smoke.py", 1800)
        if not record["returnCode"]:
            try:
                record["receipt"] = asset_smoke_receipt(stdout)
            except ValueError as error:
                record.update(returnCode=1, error=str(error))
        results.append(record)
    return results


def main(argv=None):
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="Require compiled runtime and real Blender fixtures")
    parser.add_argument("--blender", type=Path, default=os.environ.get("SOILIE_BLENDER"))
    parser.add_argument("--report", type=Path, help="JSON result under repository .codex/")
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    if args.report:
        args.report = args.report.resolve()
        if not args.report.is_relative_to((ROOT / ".codex").resolve()):
            parser.error("--report must stay under the repository .codex directory")
        args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.all:
        if not args.blender or not args.blender.is_file():
            parser.error("--all requires --blender PATH (or SOILIE_BLENDER)")
        database = Path(os.environ.get("SOILIE_RUNTIME_DB", ROOT / ".codex/runtime/relations.sqlite3"))
        if not database.is_file():
            parser.error("--all requires the compiled runtime (SOILIE_RUNTIME_DB)")
    base = ROOT / ".codex/tests"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="suite-", dir=base) as directory, ExitStack() as stack:
        scratch = Path(directory)
        stack.enter_context(patch.object(tempfile, "tempdir", str(scratch)))
        stack.enter_context(patch.dict(os.environ, {
            "TMPDIR": str(scratch), "TEMP": str(scratch), "TMP": str(scratch),
            "MPLCONFIGDIR": str(scratch / "matplotlib"),
            "AWS_EC2_METADATA_DISABLED": "true", "AWS_ACCESS_KEY_ID": "offline-test",
            "AWS_SECRET_ACCESS_KEY": "offline-test", "AWS_SESSION_TOKEN": "offline-test",
            "AWS_DEFAULT_REGION": "ca-central-1", "PYTHONDONTWRITEBYTECODE": "1",
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
        }))
        stack.enter_context(patch.object(socket, "create_connection", reject_network))
        stack.enter_context(patch.object(socket.socket, "connect", reject_network))
        stack.enter_context(patch.object(socket.socket, "connect_ex", reject_network))
        fingerprint = source_fingerprint()
        result = unittest.TextTestRunner(verbosity=2).run(unit_suite())
        report = {"testsRun": result.testsRun, "passed": result.wasSuccessful(),
                  "sourceFingerprint": fingerprint, "pythonVersion": sys.version.split()[0],
                  "failures": [{"test": case.id(), "traceback": detail} for case, detail in result.failures],
                  "errors": [{"test": case.id(), "traceback": detail} for case, detail in result.errors],
                  "skipped": [{"test": case.id(), "reason": reason} for case, reason in result.skipped],
                  "blender": []}
        if args.all:
            try:
                report["blender"] = run_blender(args.blender.resolve())
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
                # A launch error or timeout must leave an explicit failed gate.
                report["blender"] = [{"fixture": "Blender gate",
                                      "returnCode": 1, "error": str(error)}]
                print(f"Blender gate failed: {error}", file=sys.stderr)
            report["passed"] = report["passed"] and all(
                item["returnCode"] == 0 for item in report["blender"])
        # OS-specific lock/console checks run on their native platform. A
        # missing dataset or dependency must never pass the full gate silently.
        platform_reasons = {"Windows monitor test", "Windows sharing semantics"} if os.name == "posix" else {
            "Linux controller handoff", "Linux parent-death process supervision",
            "Linux measurement worker", "Linux ownership boundary"}
        unexpected_skips = [reason for _case, reason in result.skipped if reason not in platform_reasons]
        if args.all and unexpected_skips:
            report["passed"] = False
            print("Full gate has a skipped check unrelated to platform availability.", file=sys.stderr)
        report["sourceUnchanged"] = source_fingerprint() == fingerprint
        if not report["sourceUnchanged"]:
            report["passed"] = False
            print("Tested source changed during the suite; rerun against stable inputs.", file=sys.stderr)
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
