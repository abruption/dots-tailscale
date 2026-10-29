#!/usr/bin/env python3
"""Build a tag-bound release candidate for review; never publish a release."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("tailscale-setup.sh", "shim.py", "dots-ts", "dots_proxy.py")
REPOSITORY = "abruption/dots-tailscale"
WORKFLOW = ".github/workflows/release-provenance.yml"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    options = parser.parse_args()
    if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", options.tag):
        parser.error("a stable version tag is required")
    if not re.fullmatch(r"[0-9a-f]{40}", options.commit):
        parser.error("an exact source commit is required")
    version = options.tag[1:]
    if tuple(int(part) for part in version.split(".")) < (0, 8, 0):
        parser.error("the attested release policy starts at v0.8.0")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != options.commit or (ROOT / "VERSION").read_text().strip() != version:
        parser.error("tag, checked-out commit and VERSION must agree")
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT):
        parser.error("tracked source tree must be clean")
    subprocess.run(["python3", str(ROOT / "scripts/source_manifest.py"), "--check"], check=True, cwd=ROOT)
    destination = ROOT / "dist"
    destination.mkdir(mode=0o700)
    for name in SOURCES + ("source-manifest.json",):
        source = ROOT / name
        if source.is_symlink() or not source.is_file():
            parser.error("release source must be a regular file")
        shutil.copyfile(source, destination / name)
    for archive_format, suffix in (("tar.gz", "tar.gz"), ("zip", "zip")):
        subprocess.run(["git", "archive", "--format=" + archive_format,
                        "--output=" + str(destination / ("dots-tailscale-" + version + "." + suffix)), options.commit],
                       check=True, cwd=ROOT)
    metadata = {"schema_version": 1, "repository": REPOSITORY, "workflow": WORKFLOW,
                "tag": options.tag, "source_commit": options.commit,
                "manifest_sha256": hashlib.sha256((destination / "source-manifest.json").read_bytes()).hexdigest()}
    (destination / "release-provenance.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    checksums = "".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
                        for path in sorted(destination.iterdir()))
    (destination / "SHA256SUMS").write_text(checksums)
    print("Prepared exact source candidate; attestation and maintainer publication remain separate.")


if __name__ == "__main__":
    main()
