#!/usr/bin/env python3
"""Run the pinned Python correctness gate on tracked source files."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile


RUFF_VERSION = "0.17.3"
ROOT = Path(__file__).resolve().parent.parent


def source_paths(tracked_paths):
    """Explicitly include the extensionless entry point and research sources."""
    selected = []
    for name in tracked_paths:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("unexpected tracked source path")
        if name == "dots-ts" or path.suffix == ".py":
            selected.append(name)
    return sorted(set(selected))


def fixture_rejected(returncode, output):
    """Tool crashes and configuration errors are not successful gate tests."""
    if returncode != 1:
        return False
    try:
        diagnostics = json.loads(output)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(diagnostics, list)
        and len(diagnostics) == 1
        and isinstance(diagnostics[0], dict)
        and diagnostics[0].get("code") == "F821"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="verify the gate rejects an undefined-name fixture")
    args = parser.parse_args(argv)
    version = subprocess.run(
        [sys.executable, "-m", "ruff", "--version"], capture_output=True, text=True, cwd=ROOT
    )
    if version.returncode or version.stdout.strip() != "ruff " + RUFF_VERSION:
        print("Install requirements-static.txt in this Python environment first.", file=sys.stderr)
        return 2
    command = [sys.executable, "-m", "ruff", "check", "--no-cache", "--config", str(ROOT / "ruff.toml")]
    if args.self_test:
        # Exercise a real extensionless file through the same explicit path flow.
        with tempfile.TemporaryDirectory(prefix="dots-static-") as directory:
            fixture = Path(directory) / "dots-ts"
            fixture.write_text("print(static_gate_undefined_name)\n")
            probe = subprocess.run(command + ["--output-format", "json", "--", str(fixture)],
                                   capture_output=True, text=True, cwd=ROOT)
        if not fixture_rejected(probe.returncode, probe.stdout):
            print("Static gate did not reject the undefined-name fixture as expected.", file=sys.stderr)
            return 1
        print("Static gate self-test passed: extensionless dots-ts fixture rejected with F821.")
        return 0
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    paths = source_paths(name for name in tracked if name)
    if "dots-ts" not in paths or not any(name.startswith("research/") for name in paths):
        print("Required source groups missing from tracked-file selection.", file=sys.stderr)
        return 2
    print("Checking {} tracked Python sources, including dots-ts and research.".format(len(paths)), flush=True)
    return subprocess.run(command + ["--"] + paths, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
