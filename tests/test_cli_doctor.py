"""Behavior checks for the installed CLI and shareable diagnostics."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import signal
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


CLI = pathlib.Path(__file__).resolve().parents[1] / "dots-ts"
loader = importlib.machinery.SourceFileLoader("dots_ts_test", str(CLI))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


class CliDoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cli-")
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name)
        self.runtime = self.directory / "r"
        self.runtime.mkdir()
        self.binary = self.directory / "tailscale"
        payload = self.directory / "fake-tailscale.py"
        payload.write_text("import os,sys,time,signal,json\n"
                               "mode=os.environ.get('FAKE_MODE','forward')\n"
                               "if mode=='long': time.sleep(24); print('completed'); sys.exit(0)\n"
                               "if mode=='signal': print('ready',flush=True); time.sleep(60)\n"
                               "if mode=='forward': print(json.dumps(sys.argv[1:])); sys.stdout.flush(); sys.stdout.buffer.write(sys.stdin.buffer.read()); print('private-stderr',file=sys.stderr); sys.exit(7)\n")
        self.binary.write_text("#!/bin/sh\nexec {} {} \"$@\"\n".format(
            shlex.quote(sys.executable), shlex.quote(str(payload))))
        self.binary.chmod(0o700)
        self.processes = {name: "running" for name in ("shim", "tailscaled", "sshd")}

    def child(self, args, **kwargs):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", DOTS_TS_DIR=str(self.directory))
        env.update(kwargs.pop("env", {}))
        return subprocess.Popen([sys.executable, str(CLI)] + args,
                                env=env, **kwargs)

    def test_cli_forwards_arguments_stdin_stdout_stderr_and_exit(self):
        process = self.child(["cli", "unknown-command", "--flag=a b", "--help"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(b"stdin-data", timeout=5)
        self.assertEqual(process.returncode, 7)
        first, remaining = stdout.split(b"\n", 1)
        self.assertEqual(json.loads(first), ["--socket=/tmp/dots-tailscale-{}/tailscaled.sock".format(os.getuid()), "unknown-command", "--flag=a b", "--help"])
        self.assertEqual(remaining, b"stdin-data")
        self.assertEqual(stderr, b"private-stderr\n")

    def test_raw_cli_is_not_subject_to_twenty_second_timeout(self):
        started = time.monotonic()
        process = self.child(["cli", "long-running"], env={"FAKE_MODE": "long"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(stdout, b"completed\n")
        self.assertGreater(time.monotonic() - started, 20)

    def test_raw_cli_receives_signals_directly(self):
        process = self.child(["cli", "wait"], env={"FAKE_MODE": "signal"}, stdout=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.assertEqual(process.stdout.readline(), b"ready\n")
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)
        process.stdout.close()

    def populate(self):
        for name in ("installed", "setup.sh", "start.sh", "dots-ts", "dots_proxy.py", "tailscaled", "shim/shim.py", "shim/ca.crt",
                     "shim/ca.key", "shim/bundle.crt", "sshd/sshd_config", "sshd/ssh_host_ed25519_key",
                     "sshd/authorized_keys", "state/tailscaled.state"):
            path = self.directory / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_text("PRIVATE-SECRET-CONTENTS")
        manifest = {"schema_version": 1, "package_version": "0.4.0-dev",
                    "files": {source: hashlib.sha256((self.directory / installed).read_bytes()).hexdigest()
                              for source, installed in tool.CORE_FILES.items()}}
        (self.directory / tool.MANIFEST).write_text(json.dumps(manifest))

    def diagnose(self, state="Running", *, result=None, error=None, target=None, processes=None):
        self.populate()
        if result is None:
            result = types.SimpleNamespace(returncode=0, stdout=json.dumps({"BackendState": state,
                     "AuthURL": "https://private-login/?secret=AUTH", "Health": ["PRIVATE-HEALTH"],
                     "Self": {"HostName": "PRIVATE-HOST", "TailscaleIPs": ["100.1.2.3"]}}).encode())
        proxy = types.SimpleNamespace(inspect_config=lambda d, r: {"desired": {"socks_port": 0, "http_port": 0},
                "running": {"socks_port": 0, "http_port": 0}, "restart_required": False, "daemon_running": True})
        with mock.patch.object(tool, "process_checks", return_value=processes or self.processes), \
                mock.patch.object(tool, "socket_status", return_value="present"), \
                mock.patch.object(tool, "socket_connection", return_value="connectable"), \
                mock.patch.object(tool, "run_cli", return_value=result, side_effect=error), \
                mock.patch.dict(sys.modules, {"dots_proxy": proxy}):
            return tool.doctor(self.directory, self.runtime, target)

    def test_doctor_status_and_authentication_are_distinct_and_sanitized(self):
        for backend, expected, code in (("Running", "authenticated", 0), ("NeedsLogin", "needs_login", 1),
                                        ("NoState", "no_state", 1), ("NeedsMachineAuth", "needs_approval", 1)):
            report, actual = self.diagnose(backend)
            self.assertEqual(actual, code)
            self.assertEqual(report["checks"]["authentication"], expected)
            serialized = json.dumps(report)
            for secret in ("PRIVATE", "AuthURL", "100.1.2.3", "https://", "AUTH", str(self.directory)):
                self.assertNotIn(secret, serialized)
            self.assertEqual(report["checks"]["target_tcp"], "skipped")

    def test_doctor_management_timeout_and_bad_response(self):
        report, code = self.diagnose(error=subprocess.TimeoutExpired(["PRIVATE"], 5, output=b"SECRET"))
        self.assertEqual(report["checks"]["backend"], "timeout")
        self.assertEqual(code, 1)
        report, code = self.diagnose(result=types.SimpleNamespace(returncode=0, stdout=b"SECRET-invalid"))
        self.assertEqual(report["checks"]["backend"], "invalid_response")
        self.assertNotIn("SECRET", json.dumps(report))

    def test_management_calls_have_a_real_timeout(self):
        started = time.monotonic()
        with mock.patch.dict(os.environ, {"FAKE_MODE": "long"}):
            with self.assertRaises(subprocess.TimeoutExpired):
                tool.run_cli(self.directory, self.runtime, ["status", "--json"], timeout=0.05)
        self.assertLess(time.monotonic() - started, 2)

    def test_process_identity_rejects_editor_and_recognizes_python_script(self):
        entry = mock.MagicMock()
        entry.name = "42"
        entry.stat.return_value.st_uid = os.getuid()
        def component(name):
            if name == "cmdline":
                return types.SimpleNamespace(read_bytes=lambda: b"python3\0" + str(self.directory / "shim/shim.py").encode() + b"\0")
            return "/proc/42/exe"
        entry.__truediv__.side_effect = component
        real_readlink = os.readlink
        def readlink_as(executable):
            return lambda path, *a, **kw: executable if str(path) == "/proc/42/exe" else real_readlink(path, *a, **kw)
        with mock.patch.object(pathlib.Path, "iterdir", return_value=iter([entry])), \
                mock.patch.object(os, "readlink", side_effect=readlink_as("/usr/bin/vim")):
            self.assertEqual(tool.process_checks(self.directory)["shim"], "not_running")
        with mock.patch.object(pathlib.Path, "iterdir", return_value=iter([entry])), \
                mock.patch.object(os, "readlink", side_effect=readlink_as("/usr/bin/python3")):
            self.assertEqual(tool.process_checks(self.directory)["shim"], "running")

    def test_process_inspection_unavailable_is_unknown(self):
        with mock.patch.object(pathlib.Path, "iterdir", side_effect=PermissionError("SECRET")):
            self.assertEqual(set(tool.process_checks(self.directory).values()), {"unknown"})

    def test_target_timeout_does_not_claim_connection_success(self):
        with mock.patch.object(tool, "probe_tcp", side_effect=socket.timeout("PRIVATE")):
            report, code = self.diagnose(target="private-host:22")
        self.assertEqual(report["checks"]["target_tcp"], "timeout")
        self.assertEqual(report["overall"], "unhealthy")
        self.assertEqual(code, 1)
        self.assertFalse(report["healthy"])
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_unknown_process_inspection_is_degraded(self):
        report, code = self.diagnose(processes={name: "unknown" for name in self.processes})
        self.assertEqual(report["overall"], "degraded")
        self.assertFalse(report["healthy"])
        self.assertEqual(code, 0)

    def test_default_target_never_probes_network(self):
        with mock.patch.object(tool, "probe_tcp", side_effect=AssertionError("unexpected network")):
            self.diagnose()

    def test_ambiguous_proxy_inspection_does_not_claim_no_restart_needed(self):
        with mock.patch.dict(sys.modules, {"dots_proxy": types.SimpleNamespace(
                inspect_config=mock.Mock(side_effect=RuntimeError("PRIVATE-CONFIG-SECRET")))}):
            # diagnose supplies its own benign proxy; exercise the actual doctor
            # once using the complete fixture and a failing optional inspection.
            self.populate()
            with mock.patch.object(tool, "process_checks", return_value=self.processes), \
                    mock.patch.object(tool, "socket_status", return_value="present"), \
                mock.patch.object(tool, "socket_connection", return_value="connectable"), \
                    mock.patch.object(tool, "run_cli", return_value=types.SimpleNamespace(
                        returncode=0, stdout=b'{"BackendState":"Running"}')):
                report, code = tool.doctor(self.directory, self.runtime)
        self.assertIsNone(report["checks"]["proxy"]["restart_required"])
        self.assertEqual(report["checks"]["proxy"]["inspection"], "unavailable")
        self.assertFalse(report["healthy"])
        self.assertEqual(report["overall"], "degraded")
        self.assertNotIn("PRIVATE", json.dumps(report))

    def test_missing_companion_is_unhealthy_and_empty_state_is_distinct(self):
        self.populate()
        original_lstat = pathlib.Path.lstat
        original_stat = pathlib.Path.stat
        for companion in ("dots-ts", "dots_proxy.py"):
            def lstat(path, *args, **kwargs):
                if path == self.directory / companion:
                    raise FileNotFoundError()
                return original_lstat(path, *args, **kwargs)
            with mock.patch.object(pathlib.Path, "lstat", lstat):
                report, code = self.diagnose()
            self.assertEqual(report["checks"]["files"][companion], "missing")
            self.assertEqual(code, 1)
        def stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == self.directory / "state/tailscaled.state":
                return types.SimpleNamespace(st_mode=result.st_mode, st_size=0)
            return result
        with mock.patch.object(pathlib.Path, "stat", stat):
            report, code = self.diagnose()
        self.assertEqual(report["checks"]["state"], "empty")
        self.assertEqual(code, 1)

    def test_doctor_does_not_read_key_or_state_contents(self):
        self.populate()
        original = tool.bounded_code_read
        allowed = {tool.MANIFEST} | set(tool.CORE_FILES.values())
        def guarded(directory, relative, limit):
            self.assertIn(relative, allowed)
            return original(directory, relative, limit)
        with mock.patch.object(tool, "bounded_code_read", side_effect=guarded):
            self.diagnose()

    def test_readonly_mount_overrides_optimistic_access_and_gives_context_advice(self):
        with mock.patch.object(os, "statvfs", return_value=types.SimpleNamespace(f_flag=os.ST_RDONLY)), \
                mock.patch.object(os, "access", return_value=True):
            report, code = self.diagnose()
        self.assertEqual(report["context"]["installation"]["filesystem"], "read_only")
        self.assertEqual(report["context"]["installation"]["writable"], "denied")
        self.assertEqual(report["overall"], "degraded")
        self.assertEqual(code, 0)
        self.assertIn("independent_gui_terminal", report["advice"])
        self.assertEqual(report["context"]["scope"], "current_execution_context")
        self.assertEqual(report["context"]["process_scope"], "visible_processes_only")

    def test_directory_permission_denial_and_unknown_mount_are_distinct(self):
        with mock.patch.object(pathlib.Path, "stat", side_effect=PermissionError("PRIVATE-PATH")):
            result = tool.directory_context(self.directory)
        self.assertEqual(result["visibility"], "access_denied")
        self.assertEqual(result["filesystem"], "unknown")
        with mock.patch.object(os, "statvfs", side_effect=PermissionError("PRIVATE-MOUNT")), \
                mock.patch.object(os, "access", return_value=False):
            result = tool.directory_context(self.directory)
        self.assertEqual(result["visibility"], "present")
        self.assertEqual(result["filesystem"], "unknown")
        self.assertEqual(result["readable"], "denied")
        self.assertEqual(result["writable"], "denied")
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_socket_missing_denied_and_wrong_type_are_distinct(self):
        path = self.runtime / "tailscaled.sock"
        self.assertEqual(tool.socket_status(path), "missing")
        path.write_text("not a socket")
        self.assertEqual(tool.socket_status(path), "not_socket")
        with mock.patch.object(pathlib.Path, "lstat", side_effect=PermissionError("PRIVATE")):
            self.assertEqual(tool.socket_status(path), "access_denied")
        with mock.patch.object(pathlib.Path, "lstat", side_effect=OSError("PRIVATE")):
            self.assertEqual(tool.socket_status(path), "unknown")

    def test_local_socket_connection_distinguishes_permission_and_unresponsive(self):
        for error, expected in ((PermissionError("PRIVATE"), "access_denied"),
                                (ConnectionRefusedError("PRIVATE"), "unavailable"),
                                (socket.timeout("PRIVATE"), "timeout"),
                                (FileNotFoundError("PRIVATE"), "missing")):
            connection = mock.MagicMock()
            connection.__enter__.return_value = connection
            connection.connect.side_effect = error
            with mock.patch.object(tool.socket, "socket", return_value=connection):
                self.assertEqual(tool.socket_connection(self.runtime), expected)

    def test_invisible_socket_is_unhealthy_here_and_skips_backend(self):
        self.populate()
        for visibility in ("missing", "access_denied", "unknown"):
            with mock.patch.object(tool, "process_checks", return_value=self.processes), \
                    mock.patch.object(tool, "socket_status", return_value=visibility), \
                    mock.patch.object(tool, "socket_connection", side_effect=AssertionError("no connect")), \
                    mock.patch.object(tool, "run_cli", side_effect=AssertionError("no CLI")), \
                    mock.patch.dict(sys.modules, {"dots_proxy": types.SimpleNamespace(inspect_config=lambda d, r: {})}):
                report, code = tool.doctor(self.directory, self.runtime)
            self.assertEqual(report["checks"]["socket"], visibility)
            self.assertEqual(report["checks"]["backend"], "skipped")
            self.assertIn("backend", report["skipped_checks"])
            self.assertIn("independent_gui_terminal", report["advice"])
            self.assertEqual(code, 0 if visibility == "unknown" else 1)
            self.assertFalse(report["healthy"])
            self.assertNotIn(str(self.directory), json.dumps(report))

    def test_unresponsive_socket_does_not_run_cli_and_gives_safe_human_advice(self):
        self.populate()
        for result in ("access_denied", "unavailable", "timeout"):
            stream = io.StringIO()
            with mock.patch.object(tool, "directories", return_value=(self.directory, self.runtime)), \
                    mock.patch.object(tool, "process_checks", return_value=self.processes), \
                    mock.patch.object(tool, "socket_status", return_value="present"), \
                    mock.patch.object(tool, "socket_connection", return_value=result), \
                    mock.patch.object(tool, "run_cli", side_effect=AssertionError("no CLI")), \
                    mock.patch.dict(sys.modules, {"dots_proxy": types.SimpleNamespace(inspect_config=lambda d, r: {})}), \
                    contextlib.redirect_stdout(stream):
                code = tool.main(["doctor"])
            self.assertEqual(code, 1)
            output = stream.getvalue()
            self.assertIn(result, output)
            self.assertIn("independent GUI terminal", output)
            self.assertIn("only this execution context", output)
            for private in (str(self.directory), "PRIVATE", "sudo", "chmod", "reinstall"):
                self.assertNotIn(private, output)

    def test_healthy_context_has_no_peer_probes_or_recovery_advice(self):
        with mock.patch.object(os, "statvfs", return_value=types.SimpleNamespace(f_flag=0)), \
                mock.patch.object(os, "access", return_value=True), \
                mock.patch.object(tool, "probe_tcp", side_effect=AssertionError("no peer probe")):
            report, code = self.diagnose()
        self.assertEqual(report["overall"], "healthy")
        self.assertEqual(code, 0)
        self.assertEqual(report["skipped_checks"], ["target_tcp"])
        self.assertNotIn("independent_gui_terminal", report["advice"])
        self.assertEqual(report["context"]["access_method"], "metadata_and_access_only")

    def test_proxy_command_delegates_with_directory_contract(self):
        proxy_main = mock.Mock(return_value=0)
        proxy = types.SimpleNamespace(main=proxy_main, ProxyError=RuntimeError)
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.runtime)), \
                mock.patch.dict(sys.modules, {"dots_proxy": proxy}):
            self.assertEqual(tool.main(["proxy", "show"]), 0)
        proxy_main.assert_called_once_with(["show"], self.directory, self.runtime)

    def test_proxy_cli_preserves_actionable_static_errors(self):
        class ProxyError(RuntimeError):
            pass
        for message in ("configured proxy port is unavailable", "another operation is running",
                        "proxy ports must be integers 1024..65535, excluding transport port 18089"):
            error = ProxyError(message)
            error.__cause__ = OSError("PRIVATE-TOKEN /private/secret/path")
            proxy = types.SimpleNamespace(main=mock.Mock(side_effect=error), ProxyError=ProxyError)
            stream = io.StringIO()
            with mock.patch.object(tool, "directories", return_value=(self.directory, self.runtime)), \
                    mock.patch.dict(sys.modules, {"dots_proxy": proxy}), contextlib.redirect_stderr(stream):
                self.assertEqual(tool.main(["proxy", "configure", "--socks-port", "1080"]), 1)
            self.assertIn(message, stream.getvalue())
            self.assertNotIn("PRIVATE", stream.getvalue())
            self.assertNotIn("/private", stream.getvalue())

    def test_proxy_cli_os_error_is_redacted(self):
        proxy = types.SimpleNamespace(main=mock.Mock(side_effect=OSError("PRIVATE-TOKEN /private/secret/path")), ProxyError=RuntimeError)
        stream = io.StringIO()
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.runtime)), \
                mock.patch.dict(sys.modules, {"dots_proxy": proxy}), contextlib.redirect_stderr(stream):
            self.assertEqual(tool.main(["proxy", "show"]), 2)
        self.assertNotIn("PRIVATE", stream.getvalue())
        self.assertNotIn("/private", stream.getvalue())

    def test_relative_installation_directory_is_rejected(self):
        with mock.patch.dict(os.environ, {"DOTS_TS_DIR": "relative-private-directory"}):
            with self.assertRaises(ValueError):
                tool.directories()

    def test_localapi_error_never_counts_as_success_or_discloses_body(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = b"HTTP/1.1 500 Error\r\nContent-Length: 20\r\n\r\nPRIVATE-ERROR-BODY"
        with mock.patch.object(tool.socket, "socket", return_value=connection):
            self.assertEqual(tool.probe_tcp(self.runtime, "private-host", "22"), "failed")

    def test_target_is_validated_and_not_in_shared_output(self):
        with mock.patch.object(tool, "probe_tcp", return_value="reachable") as probe:
            report, code = self.diagnose(target="private-host:22")
        probe.assert_called_once_with(self.runtime, "private-host", "22")
        self.assertEqual(report["checks"]["target_tcp"], "reachable")
        self.assertEqual(report["target_scope"], "tcp_only")
        self.assertNotIn("private-host", json.dumps(report))
        for invalid in ("host:0", "host:65536", "host\r\nSecret:22", "[::1\r\nx]:22", "-host:22"):
            with self.assertRaises(ValueError):
                tool.target_parts(invalid)

    def test_localapi_probe_requires_successful_dial_and_sends_no_peer_payload(self):
        # A real Unix server checks the full wire request and data after upgrade.
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.runtime / "tailscaled.sock"))
        except OSError as exc:
            server.close()
            self.skipTest("Unix sockets unavailable: " + exc.__class__.__name__)
        server.listen(1)
        self.addCleanup(server.close)
        received = []
        def serve():
            connection, _ = server.accept()
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += connection.recv(4096)
                received.append(request)
                connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: ts-dial\r\n\r\n")
                received.append(connection.recv(4096))
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        self.assertEqual(tool.probe_tcp(self.runtime, "private-host", "22"), "reachable")
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertIn(b"POST /localapi/v0/dial HTTP/1.1\r\n", received[0])
        self.assertIn(b"Dial-Network: tcp\r\n", received[0])
        self.assertEqual(received[1], b"")

    def test_argument_errors_do_not_echo_private_values(self):
        stream = io.StringIO()
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.runtime)), contextlib.redirect_stderr(stream):
            self.assertEqual(tool.main(["doctor", "--target", "PRIVATE-invalid"]), 2)
        self.assertNotIn("PRIVATE", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
