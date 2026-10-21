"""Offline package identity, mixed-source detection and bounded local version checks."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("dots_ts_version_test", str(ROOT / "dots-ts"))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


class PackageVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="package-version-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.metadata = {"schema_version": 1, "package_version": "0.4.0-dev", "files": {}}
        for source, installed in tool.CORE_FILES.items():
            path = self.directory / installed
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = ("fixture for " + source).encode()
            path.write_bytes(payload)
            self.metadata["files"][source] = hashlib.sha256(payload).hexdigest()
        self.write_manifest()

    def write_manifest(self):
        (self.directory / tool.MANIFEST).write_text(json.dumps(self.metadata))

    def test_matching_package_and_unknown_revision(self):
        report = tool.package_report(self.directory)
        self.assertEqual(report["integrity"], "match")
        self.assertEqual(report["declared_version"], "0.4.0-dev")
        self.assertIsNone(report["source_revision"])

    def test_modified_and_mixed_sources_do_not_claim_match(self):
        for payload in (b"local customization", (ROOT / "dots_proxy.py").read_bytes()):
            (self.directory / "dots_proxy.py").write_bytes(payload)
            report = tool.package_report(self.directory)
            self.assertEqual(report["integrity"], "mismatch")
            self.assertEqual(report["reason"], "modified_or_mixed")
            self.assertEqual(report["files"]["dots_proxy.py"], "mismatch")

    def test_missing_core_and_legacy_metadata(self):
        (self.directory / "dots-ts").unlink()
        self.assertEqual(tool.package_report(self.directory)["integrity"], "incomplete")
        (self.directory / tool.MANIFEST).unlink()
        report = tool.package_report(self.directory)
        self.assertEqual(report["integrity"], "unknown")
        self.assertEqual(report["reason"], "metadata_missing")
        self.assertIsNone(report["declared_version"])

    def test_code_and_manifest_restore_together_after_update(self):
        path = self.directory / "dots_proxy.py"
        old_code = path.read_bytes()
        old_manifest = (self.directory / tool.MANIFEST).read_bytes()
        path.write_bytes(b"next release fixture")
        self.assertEqual(tool.package_report(self.directory)["integrity"], "mismatch")
        self.metadata["files"]["dots_proxy.py"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.metadata["package_version"] = "0.4.1"
        self.write_manifest()
        self.assertEqual(tool.package_report(self.directory)["integrity"], "match")
        path.write_bytes(old_code)
        self.assertEqual(tool.package_report(self.directory)["integrity"], "mismatch")
        (self.directory / tool.MANIFEST).write_bytes(old_manifest)
        self.assertEqual(tool.package_report(self.directory)["integrity"], "match")
        self.assertEqual(tool.package_report(self.directory)["declared_version"], "0.4.0-dev")

    def test_manifest_rejects_bad_schema_paths_duplicates_and_oversize(self):
        original = json.dumps(self.metadata)
        for raw in ('{"schema_version":1,"schema_version":1}', '{', '[]',
                    original.replace('"schema_version": 1', '"schema_version": true'),
                    original.replace('"dots-ts":', '"state/tailscaled.state":'),
                    original.replace('0.4.0-dev', 'PRIVATE\\nTOKEN'), ' ' * 16385):
            (self.directory / tool.MANIFEST).write_text(raw)
            report = tool.package_report(self.directory)
            self.assertEqual(report["integrity"], "unknown")
            self.assertIsNone(report["declared_version"])
            self.assertNotIn("PRIVATE", json.dumps(report))

    def test_symlinked_core_parent_and_manifest_are_not_followed(self):
        shim = self.directory / "shim"
        shim.rename(self.directory / "elsewhere")
        shim.symlink_to(self.directory / "elsewhere", target_is_directory=True)
        self.assertEqual(tool.package_report(self.directory)["files"]["shim.py"], "unavailable")
        manifest = self.directory / tool.MANIFEST
        manifest.rename(self.directory / "other.json")
        manifest.symlink_to(self.directory / "other.json")
        self.assertEqual(tool.package_report(self.directory)["reason"], "metadata_unavailable")

    def test_fifo_cannot_block_core_read(self):
        path = self.directory / "dots-ts"
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(tool.package_report(self.directory)["files"]["dots-ts"], "unavailable")

    def test_source_layout_and_checked_in_manifest(self):
        self.assertEqual(tool.package_report(ROOT, "source")["integrity"], "match")
        subprocess.run([sys.executable, str(ROOT / "scripts/source_manifest.py"), "--check"], check=True)

    def binary(self, body):
        path = self.directory / "tailscale"
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o700)

    def test_local_tailscale_is_separate_and_output_bounded(self):
        self.binary("print('1.102.4')\nprint('long build description')\n")
        self.assertEqual(tool.tailscale_version(self.directory), {"version": "1.102.4", "status": "available"})
        self.binary("print('x' * 10000)\n")
        self.assertEqual(tool.tailscale_version(self.directory)["status"], "invalid_response")
        self.binary("import time\ntime.sleep(10)\n")
        with mock.patch.object(tool, "MANAGEMENT_TIMEOUT", .05):
            self.assertEqual(tool.tailscale_version(self.directory)["status"], "timeout")
        (self.directory / "tailscale").unlink()
        self.assertEqual(tool.tailscale_version(self.directory)["status"], "unavailable")

    def test_json_exit_codes_and_no_network_or_daemon(self):
        for expected_code, mutation in ((0, None), (1, "modify"), (0, "legacy")):
            if mutation == "modify":
                (self.directory / "dots-ts").write_text("mixed")
            elif mutation == "legacy":
                (self.directory / tool.MANIFEST).unlink()
            output = io.StringIO()
            with mock.patch.object(tool, "directories", return_value=(self.directory, self.directory)), \
                    mock.patch.object(tool, "run_cli", side_effect=AssertionError("daemon query")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(tool.main(["version", "--json"]), expected_code)
            report = json.loads(output.getvalue())
            self.assertEqual(report["tailscale"]["status"], "unavailable")
            self.assertNotIn(str(self.directory), output.getvalue())


if __name__ == "__main__":
    unittest.main()
