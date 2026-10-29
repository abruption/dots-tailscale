"""Offline baseline evidence must not infer a running version from a CLI file."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("dots_ts_baseline_test", str(ROOT / "dots-ts"))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


class BaselineDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="baseline-diagnostic-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.runtime = self.directory / "runtime"
        self.runtime.mkdir()
        for installed in tool.CORE_FILES.values():
            path = self.directory / installed
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (self.directory / "setup.sh").write_text("#!/bin/bash\nTS_VERSION=1.104.4\n")
        self.binary("tailscale", "version", "1.104.4")
        self.binary("tailscaled", "--version", "1.104.4")
        self.manifest()

    def binary(self, name, argument, version):
        path = self.directory / name
        path.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c " + shlex.quote(
            "import sys; assert sys.argv[1:] == [" + repr(argument) + "]; print(" + repr(version) + ")") + " \"$@\"\n")
        path.chmod(0o700)

    def manifest(self):
        data = {"schema_version": 1, "package_version": "0.8.0-dev",
                "files": {source: hashlib.sha256((self.directory / installed).read_bytes()).hexdigest()
                          for source, installed in tool.CORE_FILES.items()}}
        (self.directory / tool.MANIFEST).write_text(json.dumps(data))

    def report(self, version=None, **kwargs):
        if version is not None:
            self.binary("tailscale", "version", version)
            self.binary("tailscaled", "--version", version)
        return tool.runtime_baseline_report(self.directory, **kwargs)

    def doctor(self, status=None, error=None, socket_state="present", connection="connectable"):
        for name in ("installed", "start.sh", "shim/ca.crt", "shim/ca.key", "shim/bundle.crt",
                     "sshd/sshd_config", "sshd/ssh_host_ed25519_key", "sshd/authorized_keys", "state/tailscaled.state"):
            path = self.directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
        proxy = types.SimpleNamespace(inspect_config=lambda d, r: {"desired": {}, "running": {},
                                                                 "restart_required": False, "daemon_running": True})
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(status or {
            "BackendState": "Running", "Version": "1.104.4-t27047635f"}).encode())
        with mock.patch.object(tool, "process_checks", return_value={name: "running" for name in ("shim", "tailscaled", "sshd")}), \
                mock.patch.object(tool, "socket_status", return_value=socket_state), \
                mock.patch.object(tool, "socket_connection", return_value=connection), \
                mock.patch.object(tool, "run_cli", return_value=result, side_effect=error) as run, \
                mock.patch.dict(sys.modules, {"dots_proxy": proxy}):
            report, code = tool.doctor(self.directory, self.runtime)
        return report, code, run

    def test_equal_above_and_below_binary_pair(self):
        for version, comparison, assessment in (("1.104.4", "equal", "local_binaries_meet_baseline"),
                                                 ("1.106.0", "above", "local_binaries_meet_baseline"),
                                                 ("1.102.4", "below", "below_baseline")):
            report = self.report(version)
            self.assertEqual(report["declaration"]["version"], "1.104.4")
            self.assertEqual(report["cli"]["comparison"], comparison)
            self.assertEqual(report["daemon_binary"]["comparison"], comparison)
            self.assertEqual(report["assessment"], assessment)
            self.assertEqual(report["backend"]["status"], "not_queried")
            self.assertEqual(report["scope"], "local_binary_pair_only")
            self.assertEqual(report["advisory_policy"], "not_evaluated")

    def test_installed_declaration_not_a_universal_constant(self):
        (self.directory / "setup.sh").write_text('TS_VERSION="1.102.4"\n')
        self.manifest()
        report = self.report("1.102.4")
        self.assertEqual(report["declaration"]["version"], "1.102.4")
        self.assertEqual(report["assessment"], "local_binaries_meet_baseline")
        self.assertNotIn("vulnerable", json.dumps(report))
        self.assertNotIn("secure", json.dumps(report))

    def test_missing_invalid_duplicate_and_unreadable_declarations(self):
        path = self.directory / "setup.sh"
        for body in ("fixture", "TS_VERSION=PRIVATE\n", "TS_VERSION=1.104.4\nTS_VERSION=1.102.4\n",
                     "TS_VERSION=01.104.4\n", "TS_VERSION=$(PRIVATE)\n", "TS_VERSION=1.104.4-rc.1\n",
                     "TS_VERSION=1.104.4\nTS_VERSION=$(PRIVATE)\n", "TS_VERSION=1.104.4\n export TS_VERSION=1.102.4\n"):
            path.write_text(body)
            self.manifest()
            report = self.report()
            self.assertIsNone(report["declaration"]["version"])
            self.assertEqual(report["declaration"]["status"], "invalid_declaration")
            self.assertEqual(report["assessment"], "unknown_baseline")
            self.assertNotIn("PRIVATE", json.dumps(report))
        path.unlink()
        report = self.report()
        self.assertEqual(report["declaration"]["status"], "missing")
        elsewhere = self.directory / "elsewhere"
        elsewhere.write_text("TS_VERSION=1.104.4\n")
        path.symlink_to(elsewhere)
        self.assertEqual(self.report()["declaration"]["status"], "unavailable")
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(self.report()["declaration"]["status"], "unavailable")

    def test_missing_or_modified_metadata_does_not_trust_a_declaration(self):
        (self.directory / tool.MANIFEST).unlink()
        report = self.report()
        self.assertEqual(report["declaration"]["package_integrity"], "unknown")
        self.assertEqual(report["assessment"], "unverified_declaration")
        self.manifest()
        (self.directory / "setup.sh").write_text("TS_VERSION=1.106.0\n")
        report = self.report()
        self.assertEqual(report["declaration"]["package_integrity"], "mismatch")
        self.assertEqual(report["assessment"], "unverified_declaration")

    def test_daemon_binary_and_backend_do_not_stand_in_for_each_other(self):
        self.binary("tailscaled", "--version", "1.102.4")
        report = self.report(backend={"version": "1.104.4", "status": "available"})
        self.assertEqual(report["daemon_binary"]["version"], "1.102.4")
        self.assertEqual(report["backend"]["version"], "1.104.4")
        self.assertEqual(report["consistency"], "mismatch")
        self.assertEqual(report["assessment"], "version_mismatch")
        self.binary("tailscaled", "--version", "1.104.4")
        report = self.report(backend={"version": "1.102.4", "status": "available"})
        self.assertEqual(report["backend"]["comparison"], "below")
        self.assertEqual(report["assessment"], "version_mismatch")

    def test_release_build_suffix_is_not_mistaken_for_prerelease(self):
        report = self.report(backend={"version": "1.104.4-t27047635f", "status": "available"})
        self.assertEqual(report["consistency"], "match")
        self.assertEqual(report["backend"]["release_kind"], "release_build")
        self.assertEqual(report["backend"]["comparison"], "equal")
        self.assertEqual(report["assessment"], "meets_declared_baseline")
        self.assertEqual(report["backend"]["version"], "1.104.4-t27047635f")

    def test_prerelease_is_explicit_and_not_ordered_as_stable(self):
        for version in ("1.104.4-rc.1", "1.106.0-dev", "1.104.4-tNOTHEX"):
            report = self.report(version)
            self.assertEqual(report["cli"]["release_kind"], "prerelease")
            self.assertEqual(report["cli"]["comparison"], "not_comparable")
            self.assertEqual(report["assessment"], "nonstable_version")

    def test_unavailable_invalid_backend_and_binary_timeout(self):
        for version in (None, "PRIVATE", "1.104.4\nPRIVATE", 1044):
            report = self.report(backend={"version": version, "status": "available"})
            self.assertEqual(report["backend"]["status"], "invalid_response")
            self.assertIsNone(report["backend"]["version"])
            self.assertEqual(report["assessment"], "unknown")
            self.assertNotIn("PRIVATE", json.dumps(report))
        (self.directory / "tailscaled").unlink()
        report = self.report()
        self.assertEqual(report["daemon_binary"]["status"], "unavailable")
        self.assertEqual(report["assessment"], "unknown")
        path = self.directory / "tailscaled"
        path.write_text("#!" + sys.executable + "\nimport time; time.sleep(10)\n")
        path.chmod(0o700)
        with mock.patch.object(tool, "MANAGEMENT_TIMEOUT", 0.05):
            report = self.report()
        self.assertEqual(report["daemon_binary"]["status"], "timeout")
        self.assertEqual(report["assessment"], "unknown")

    def test_binary_version_probe_refuses_symlink_and_bounds_invalid_output(self):
        path = self.directory / "tailscaled"
        path.rename(self.directory / "other-daemon")
        path.symlink_to(self.directory / "other-daemon")
        with mock.patch.object(tool.subprocess, "Popen", side_effect=AssertionError("symlink execution")):
            self.assertEqual(tool.local_binary_version(self.directory, "tailscaled", "--version")["status"], "unavailable")
        path.unlink()
        self.binary("tailscaled", "--version", "PRIVATE" * 1000)
        report = self.report()
        self.assertEqual(report["daemon_binary"]["status"], "invalid_response")
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_doctor_human_output_explains_its_separate_assessment(self):
        report, code, _ = self.doctor()
        stream = io.StringIO()
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.runtime)), \
                mock.patch.object(tool, "doctor", return_value=(report, code)), contextlib.redirect_stdout(stream):
            self.assertEqual(tool.main(["doctor"]), 0)
        output = stream.getvalue()
        self.assertIn("Tailscale daemon binary: 1.104.4", output)
        self.assertIn("Tailscale running backend: 1.104.4-t27047635f", output)
        self.assertIn("runtime baseline assessment: meets_declared_baseline", output)
        self.assertIn("security advisory policy: not evaluated", output)

    def test_version_keeps_daemon_free_offline_contract_and_exit_compatibility(self):
        self.report("1.102.4")
        for as_json in (True, False):
            stream = io.StringIO()
            with mock.patch.object(tool, "directories", return_value=(self.directory, self.runtime)), \
                    mock.patch.object(tool, "run_cli", side_effect=AssertionError("must not query daemon")), \
                    mock.patch.object(tool, "fetch_bytes", side_effect=AssertionError("must not fetch")), \
                    contextlib.redirect_stdout(stream):
                code = tool.main(["version"] + (["--json"] if as_json else []))
            self.assertEqual(code, 0)
            if as_json:
                report = json.loads(stream.getvalue())
                self.assertEqual(report["runtime_baseline"]["assessment"], "below_baseline")
                self.assertEqual(report["runtime_baseline"]["backend"]["status"], "not_queried")
                self.assertEqual(report["tailscale"], {"version": "1.102.4", "status": "available"})
            else:
                self.assertIn("declared installation baseline: 1.104.4", stream.getvalue())
                self.assertIn("running backend: not_queried", stream.getvalue())
                self.assertIn("assessment: below_baseline", stream.getvalue())
                self.assertIn("not evaluated", stream.getvalue())
            self.assertNotIn(str(self.directory), stream.getvalue())

    def test_doctor_reuses_actual_backend_and_preserves_exit_meaning(self):
        report, code, run = self.doctor()
        run.assert_called_once_with(self.directory, self.runtime, ["status", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(report["checks"]["runtime_baseline"]["assessment"], "meets_declared_baseline")
        report, code, _ = self.doctor({"BackendState": "Running", "Version": "1.102.4", "AuthURL": "PRIVATE"})
        self.assertEqual(code, 0)  # Existing exit code remains connectivity/integrity, not security policy.
        self.assertEqual(report["checks"]["runtime_baseline"]["assessment"], "version_mismatch")
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_doctor_missing_and_unavailable_backend_never_uses_cli_version(self):
        report, _, _ = self.doctor({"BackendState": "Running"})
        self.assertEqual(report["checks"]["runtime_baseline"]["backend"]["status"], "missing")
        self.assertEqual(report["checks"]["runtime_baseline"]["assessment"], "unknown")
        for error, expected in ((subprocess.TimeoutExpired("PRIVATE", 5), "timeout"), (OSError("PRIVATE"), "unavailable")):
            report, code, _ = self.doctor(error=error)
            self.assertEqual(code, 1)
            self.assertEqual(report["checks"]["runtime_baseline"]["backend"]["status"], expected)
            self.assertEqual(report["checks"]["runtime_baseline"]["assessment"], "unknown")
        report, _, run = self.doctor(socket_state="missing")
        run.assert_not_called()
        self.assertEqual(report["checks"]["runtime_baseline"]["backend"]["status"], "skipped")
        self.assertEqual(report["checks"]["runtime_baseline"]["assessment"], "unknown")


if __name__ == "__main__":
    unittest.main()
