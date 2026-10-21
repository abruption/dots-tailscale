#!/usr/bin/env python3
"""Explicitly regenerate package metadata, or check it without writes in CI."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("tailscale-setup.sh", "shim.py", "dots-ts", "dots_proxy.py")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = {"schema_version": 1, "package_version": (ROOT / "VERSION").read_text().strip(),
                "files": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}}
    destination = ROOT / "source-manifest.json"
    serialized = json.dumps(expected, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not destination.is_file() or destination.read_text() != serialized:
            parser.exit(1, "source manifest is stale; run python3 scripts/source_manifest.py\n")
    else:
        destination.write_text(serialized)

if __name__ == "__main__":
    main()
