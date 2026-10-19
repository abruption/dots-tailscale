"""Local sockets/config files; process control is mocked, never a live daemon."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import signal
import sys
import tempfile
import threading
import unittest
from unittest import mock

import dots_proxy as proxy


class OutboundProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.d = Path(self.temp.name) / "install"
        self.r = Path(self.temp.name) / "runtime"
        self.d.mkdir(mode=0o700)
        self.r.mkdir(mode=0o700)
        # macOS /tmp is a symlink; implementation deliberately rejects symlink
        # parents, so use resolved test-only directories on that platform.
        self.d, self.r = self.d.resolve(), self.r.resolve()
        self.actual_daemon = proxy._daemon
        self.daemon = mock.patch.object(proxy, "_daemon", return_value=None).start()
        self.addCleanup(mock.patch.stopall)

    def write(self, text, mode=0o600):
        path = self.d / proxy.CONFIG
        path.write_text(text)
        path.chmod(mode)
        return path

    def available_port(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def test_missing_config_is_disabled_and_no_flags(self):
        self.assertEqual(proxy.load_config(self.d), proxy.DEFAULT)
        self.assertEqual(proxy.startup_args(self.d, self.r), [])
        self.assertFalse((self.d / proxy.CONFIG).exists())

    def test_strict_ports_and_duplicate_keys(self):
        for value in (True, "1080", 0, 22, 1023, 65536, 18089, 1.5, -1):
            with self.subTest(value=value):
                self.write(json.dumps({"socks_port": value, "http_port": None}))
                with self.assertRaises(proxy.ProxyError):
                    proxy.load_config(self.d)
        for text in ('{}', '[]', '{"socks_port":null,"http_port":null,"extra":1}',
                     '{"socks_port":1080,"http_port":1080}',
                     '{"socks_port":1080,"socks_port":1081,"http_port":null}',
                     '{"socks_port":NaN,"http_port":null}', 'invalid'):
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(proxy.ProxyError):
                    proxy.load_config(self.d)

    def test_private_files_directories_and_symlinks(self):
        path = self.write(json.dumps(proxy.DEFAULT), mode=0o644)
        with self.assertRaises(proxy.ProxyError):
            proxy.load_config(self.d)
        path.unlink()
        path.symlink_to(self.d / "absent")
        with self.assertRaises(proxy.ProxyError):
            proxy.load_config(self.d)
        path.unlink()
        self.d.chmod(0o755)
        with self.assertRaises(proxy.ProxyError):
            proxy.load_config(self.d)
        self.d.chmod(0o700)
        alias = self.d.parent / "alias"
        alias.symlink_to(self.d)
        with self.assertRaises(proxy.ProxyError):
            proxy.load_config(alias)

    def test_bound_ports_are_rejected_before_config_changes(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            with self.assertRaisesRegex(proxy.ProxyError, "unavailable"):
                proxy.configure(self.d, self.r, socks_port=listener.getsockname()[1])
        self.assertFalse((self.d / proxy.CONFIG).exists())

    def test_configure_and_disable_only_persist(self):
        original_environment = dict(os.environ)
        port = self.available_port()
        with mock.patch.object(proxy, "_stop") as stop, mock.patch.object(proxy, "_launch") as launch:
            for _ in range(2):
                result = proxy.configure(self.d, self.r, socks_port=port)
                self.assertEqual(result["desired"], {"socks_port": port, "http_port": None})
                self.assertTrue(result["restart_required"])
                self.assertEqual(proxy.startup_args(self.d, self.r), ["--socks5-server=127.0.0.1:{}".format(port)])
                self.assertEqual(proxy.disable(self.d, self.r)["desired"], proxy.DEFAULT)
            stop.assert_not_called()
            launch.assert_not_called()
        self.assertEqual(dict(os.environ), original_environment)
        self.assertEqual((self.d / proxy.CONFIG).stat().st_mode & 0o777, 0o600)

    def test_http_and_both_flags_are_loopback_only(self):
        self.assertEqual(proxy._flags({"socks_port": None, "http_port": 1081}),
                         ["--outbound-http-proxy-listen=127.0.0.1:1081"])
        self.assertEqual(proxy._flags({"socks_port": 1080, "http_port": 1081}),
                         ["--socks5-server=127.0.0.1:1080", "--outbound-http-proxy-listen=127.0.0.1:1081"])

    def test_operation_lock_and_symlink_lock_are_refused(self):
        with proxy._operation(self.d):
            with self.assertRaisesRegex(proxy.ProxyError, "another operation"):
                proxy.configure(self.d, self.r)
        (self.d / ".operation.lock").unlink()
        (self.d / ".operation.lock").symlink_to(self.d / "unrelated")
        with self.assertRaises(OSError):
            proxy.configure(self.d, self.r)
        self.assertFalse((self.d / "unrelated").exists())

    def old(self):
        return {"pid": 1234, "ticks": "123", "exe": str(self.d / "tailscaled"),
                "argv": [str(self.d / "tailscaled"), "--tun=userspace-networking",
                         "--statedir=" + str(self.d / "state"), "--socket=" + str(self.r / "tailscaled.sock"), "--port=0"]}

    def test_inspect_actual_arguments_not_markers(self):
        old = self.old()
        self.daemon.return_value = old
        with mock.patch.object(proxy, "_socket_owned", return_value=True):
            result = proxy.inspect_config(self.d, self.r)
            self.assertEqual(result["running"], proxy.DEFAULT)
            self.assertFalse(result["restart_required"])
            self.write(json.dumps({"socks_port": 1080, "http_port": None}))
            self.assertTrue(proxy.inspect_config(self.d, self.r)["restart_required"])
            old["argv"].append("--socks5-server=127.0.0.1:1080")
            with mock.patch.object(proxy, "_listener_owned", return_value=True):
                self.assertFalse(proxy.inspect_config(self.d, self.r)["restart_required"])
            with mock.patch.object(proxy, "_listener_owned", return_value=False):
                self.assertTrue(proxy.inspect_config(self.d, self.r)["restart_required"])

    def test_running_wildcard_listener_and_duplicate_flags_are_refused(self):
        for args in (["--socks5-server=0.0.0.0:1080"], ["--socks5-server=[::1]:1080"],
                     ["--socks5-server=127.0.0.1:1080", "--socks5-server=127.0.0.1:1081"]):
            with self.assertRaises(proxy.ProxyError):
                proxy._argv_config(args)

    @contextlib.contextmanager
    def application(self):
        old = self.old()
        self.daemon.return_value = old
        self.write(json.dumps({"socks_port": 1080, "http_port": None}))
        endpoint = socket.socket(socket.AF_UNIX)
        endpoint.bind(str(self.r / "tailscaled.sock"))
        self.addCleanup(endpoint.close)
        with mock.patch.object(proxy, "_socket_owned", return_value=True), \
                mock.patch.dict(os.environ, {"SSH_CONNECTION": "", "SSH_CLIENT": ""}), \
                mock.patch.object(proxy, "_backend_running", return_value=True), \
                mock.patch.object(proxy, "_environment", return_value={"preserved": "yes"}), \
                mock.patch.object(proxy, "_ports_available"), \
                mock.patch.object(proxy, "_remove_socket") as remove, \
                mock.patch.object(proxy, "_stop") as stop, \
                mock.patch.object(proxy, "_launch") as launch, \
                mock.patch.object(proxy, "_ready") as ready:
            def remove_socket(d, r, process, identity):
                path = r / "tailscaled.sock"
                if path.exists():
                    path.unlink()
            remove.side_effect = remove_socket
            yield old, remove, stop, launch, ready

    def test_apply_success_preserves_base_argv_and_environment(self):
        with self.application() as (old, remove, stop, launch, ready):
            proxy.apply(self.d, self.r)
            stop.assert_called_once_with(old)
            remove.assert_called_once()
            self.assertEqual(remove.call_args[0][:3], (self.d, self.r, old))
            self.assertEqual(len(remove.call_args[0][3]), 2)
            self.assertEqual(launch.call_args[0][0], old["argv"] + ["--socks5-server=127.0.0.1:1080"])
            self.assertEqual(launch.call_args[0][1], {"preserved": "yes"})
            ready.assert_called_once()

    def test_apply_failure_rolls_back_and_leaves_desired_pending(self):
        with self.application() as (old, remove, stop, launch, ready):
            child = mock.Mock(pid=5678)
            child.poll.return_value = 1
            launch.side_effect = [child, mock.Mock()]
            ready.side_effect = [proxy.ProxyError("failed candidate"), None]
            # First identity is the original daemon, then no daemon after its
            # replacement has exited and immediately before rollback.
            self.daemon.side_effect = [old, None, None]
            with self.assertRaisesRegex(proxy.ProxyError, "previous daemon restored"):
                proxy.apply(self.d, self.r)
            self.assertEqual(launch.call_args_list[1][0][0], old["argv"])
            self.assertEqual(proxy.load_config(self.d)["socks_port"], 1080)
            self.assertEqual(ready.call_count, 2)

    def test_stop_failure_never_launches_duplicate(self):
        with self.application() as (old, remove, stop, launch, ready):
            stop.side_effect = proxy.ProxyError("still running")
            with self.assertRaisesRegex(proxy.ProxyError, "still running"):
                proxy.apply(self.d, self.r)
            launch.assert_not_called()
            remove.assert_not_called()

    def test_unknown_live_candidate_prevents_rollback_duplicate(self):
        with self.application() as (old, remove, stop, launch, ready):
            child = mock.Mock(pid=5678)
            child.poll.return_value = None
            launch.return_value = child
            ready.side_effect = proxy.ProxyError("not ready")
            self.daemon.side_effect = [old, None]
            with self.assertRaisesRegex(proxy.ProxyError, "identity uncertain"):
                proxy.apply(self.d, self.r)
            self.assertEqual(launch.call_count, 1)

    def test_rollback_failure_has_explicit_recovery_error(self):
        with self.application() as (old, remove, stop, launch, ready):
            child = mock.Mock(pid=5678)
            child.poll.return_value = 1
            launch.side_effect = [child, mock.Mock()]
            ready.side_effect = [proxy.ProxyError("candidate failed"), proxy.ProxyError("rollback failed")]
            self.daemon.side_effect = [old, None, None]
            with self.assertRaisesRegex(proxy.ProxyError, "platform terminal recovery"):
                proxy.apply(self.d, self.r)
            descriptors = [call.args[2] for call in launch.call_args_list]
            self.assertEqual(len(descriptors), 2)
            self.assertEqual(descriptors[0], descriptors[1])
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])

    def test_other_state_directory_is_rejected_before_stopping(self):
        with self.application() as (old, remove, stop, launch, ready):
            old["argv"][2] = "--statedir=/somewhere/else"
            with self.assertRaisesRegex(proxy.ProxyError, "state directory"):
                proxy.apply(self.d, self.r)
            stop.assert_not_called()
            launch.assert_not_called()

    def test_not_running_baseline_is_rejected_before_stopping(self):
        with self.application() as (old, remove, stop, launch, ready):
            with mock.patch.object(proxy, "_backend_running", return_value=False):
                with self.assertRaisesRegex(proxy.ProxyError, "not Running"):
                    proxy.apply(self.d, self.r)
            stop.assert_not_called()
            launch.assert_not_called()

    def test_duplicate_state_or_tun_arguments_are_refused(self):
        for extra in ("--statedir=/other", "--state=/other/state", "--tun=tailscale0"):
            with self.subTest(extra=extra), self.application() as (old, remove, stop, launch, ready):
                old["argv"].append(extra)
                with self.assertRaises(proxy.ProxyError):
                    proxy.apply(self.d, self.r)
                stop.assert_not_called()
                launch.assert_not_called()
                (self.r / "tailscaled.sock").unlink()

    def test_restart_over_ssh_is_refused_but_noop_is_allowed(self):
        with self.application() as (old, remove, stop, launch, ready):
            with mock.patch.dict(os.environ, {"SSH_CONNECTION": "client server", "SSH_CLIENT": "client"}):
                with self.assertRaisesRegex(proxy.ProxyError, "independent platform terminal"):
                    proxy.apply(self.d, self.r)
                self.write(json.dumps(proxy.DEFAULT))
                self.assertFalse(proxy.apply(self.d, self.r)["restart_required"])
            stop.assert_not_called()
            launch.assert_not_called()

    def test_restart_signals_are_deferred_until_ready_and_handlers_restored(self):
        original = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        for number in original:
            for stage in ("stop", "ready"):
                with self.subTest(signal=number, stage=stage), self.application() as (old, remove, stop, launch, ready):
                    events = []
                    def interrupted_stop(process):
                        events.append("stop")
                        if stage == "stop":
                            os.kill(os.getpid(), number)
                        events.append("stop-finished")
                    def interrupted_ready(*args):
                        if stage == "ready":
                            os.kill(os.getpid(), number)
                        events.append("ready-finished")
                        old["argv"] = proxy._replace_flags(old["argv"], {"socks_port": 1080, "http_port": None})
                    stop.side_effect = interrupted_stop
                    ready.side_effect = interrupted_ready
                    with mock.patch.object(proxy, "_listener_owned", return_value=True):
                        result = proxy.apply(self.d, self.r)
                    self.assertEqual(events, ["stop", "stop-finished", "ready-finished"])
                    self.assertEqual(result["apply_result"], "applied")
                    self.assertEqual(result["interrupted_signal"], number)
                    self.assertFalse(result["restart_required"])
                    self.assertEqual({value: signal.getsignal(value) for value in original}, original)

    def test_signal_during_failed_candidate_finishes_rollback(self):
        original = signal.getsignal(signal.SIGTERM)
        with self.application() as (old, remove, stop, launch, ready):
            child = mock.Mock(pid=5678)
            child.poll.return_value = 1
            launch.side_effect = [child, mock.Mock()]
            self.daemon.side_effect = [old, None, None]
            events = []
            def candidate_then_rollback(*args):
                if not events:
                    os.kill(os.getpid(), signal.SIGTERM)
                    events.append("candidate-failed")
                    raise proxy.ProxyError("candidate failed")
                events.append("rollback-ready")
            ready.side_effect = candidate_then_rollback
            with self.assertRaisesRegex(proxy.ProxyError, "previous daemon restored"):
                proxy.apply(self.d, self.r)
            self.assertEqual(events, ["candidate-failed", "rollback-ready"])
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_library_restart_on_non_main_thread_is_refused_before_stop(self):
        with self.application() as (old, remove, stop, launch, ready):
            errors = []
            def run():
                try:
                    proxy.apply(self.d, self.r)
                except proxy.ProxyError as error:
                    errors.append(str(error))
            worker = threading.Thread(target=run)
            worker.start()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, ["proxy apply restart must run on the main thread"])
            stop.assert_not_called()

    def test_replaced_socket_inode_is_not_removed(self):
        with socket.socket(socket.AF_UNIX) as original, socket.socket(socket.AF_UNIX) as replacement:
            path = self.r / "tailscaled.sock"
            original.bind(str(path))
            identity = proxy._socket_identity(self.r)
            path.unlink()
            replacement.bind(str(path))
            with mock.patch.object(proxy, "_same_process", return_value=False):
                with self.assertRaisesRegex(proxy.ProxyError, "socket changed"):
                    proxy._remove_socket(self.d, self.r, self.old(), identity)
            self.assertTrue(path.exists())

    def test_unknown_stale_socket_after_stop_does_not_launch_rollback(self):
        with self.application() as (old, remove, stop, launch, ready):
            remove.side_effect = proxy.ProxyError("socket changed")
            self.daemon.side_effect = [old, None]
            with self.assertRaisesRegex(proxy.ProxyError, "unknown socket remains"):
                proxy.apply(self.d, self.r)
            launch.assert_not_called()

    def test_apply_log_is_truncated_before_large_append(self):
        path = self.r / "proxy-apply.log"
        path.write_bytes(b"x" * (1024 * 1024))
        path.chmod(0o600)
        with proxy._apply_log(self.r) as log_fd:
            with mock.patch.object(proxy.subprocess, "Popen") as popen:
                proxy._launch(["unused"], {}, log_fd)
                self.assertEqual(popen.call_args.kwargs["stdout"], log_fd)
                self.assertEqual(popen.call_args.kwargs["stderr"], log_fd)
        self.assertEqual(path.stat().st_size, 0)

    def test_unsafe_or_unopenable_log_never_stops_daemon(self):
        path = self.r / "proxy-apply.log"
        target = self.r / "unrelated"
        target.write_bytes(b"unchanged")
        target.chmod(0o600)
        real_open = os.open
        for kind in ("mode", "symlink", "directory", "open-error"):
            with self.subTest(kind=kind), self.application() as (old, remove, stop, launch, ready):
                if kind == "mode":
                    path.write_bytes(b"unchanged")
                    path.chmod(0o644)
                elif kind == "symlink":
                    path.symlink_to(target)
                elif kind == "directory":
                    path.mkdir(mode=0o700)
                def open_log(name, *args, **kwargs):
                    if str(name) == str(path) and kind == "open-error":
                        raise PermissionError("denied")
                    return real_open(name, *args, **kwargs)
                opened = []
                real_fstat = os.fstat
                def stat_fd(fd):
                    info = real_fstat(fd)
                    if kind == "mode" and info.st_mode & 0o777 == 0o644:
                        opened.append(fd)
                    return info
                with mock.patch.object(proxy.os, "open", side_effect=open_log), \
                        mock.patch.object(proxy.os, "fstat", side_effect=stat_fd):
                    with self.assertRaises(proxy.ProxyError):
                        proxy.apply(self.d, self.r)
                stop.assert_not_called()
                remove.assert_not_called()
                launch.assert_not_called()
                ready.assert_not_called()
                self.assertTrue((self.r / "tailscaled.sock").is_socket())
                for fd in opened:
                    with self.assertRaises(OSError):
                        real_fstat(fd)
                self.assertEqual(target.read_bytes(), b"unchanged")
                if kind == "directory":
                    path.rmdir()
                elif path.is_symlink() or path.exists():
                    path.unlink()
                (self.r / "tailscaled.sock").unlink()

    def test_log_descriptor_closed_after_success_and_stop_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), self.application() as (old, remove, stop, launch, ready):
                descriptors = []
                def stopping(process):
                    self.assertEqual((self.r / "proxy-apply.log").stat().st_mode & 0o777, 0o600)
                    if fail:
                        raise proxy.ProxyError("stop failed")
                stop.side_effect = stopping
                real_apply_log = proxy._apply_log
                @contextlib.contextmanager
                def capture_log(r):
                    with real_apply_log(r) as fd:
                        descriptors.append(fd)
                        yield fd
                with mock.patch.object(proxy, "_apply_log", side_effect=capture_log):
                    if fail:
                        with self.assertRaisesRegex(proxy.ProxyError, "stop failed"):
                            proxy.apply(self.d, self.r)
                    else:
                        proxy.apply(self.d, self.r)
                self.assertEqual(len(descriptors), 1)
                with self.assertRaises(OSError):
                    os.fstat(descriptors[0])
                (self.r / "tailscaled.sock").unlink(missing_ok=True)

    def test_replaced_log_path_keeps_same_descriptor_for_launch_and_rollback(self):
        path = self.r / "proxy-apply.log"
        original = self.r / "original-log"
        victim = self.r / "unrelated"
        victim.write_bytes(b"must not change")
        victim.chmod(0o600)
        real_launch = proxy._launch
        for fail_at in ("launch", "ready"):
            with self.subTest(fail_at=fail_at), self.application() as (old, remove, stop, launch, ready):
                stop.side_effect = lambda process: (path.rename(original), path.symlink_to(victim))
                descriptors = []
                def popen(argv, **kwargs):
                    fd = kwargs["stdout"]
                    descriptors.append(fd)
                    self.assertEqual(kwargs["stderr"], fd)
                    self.assertEqual(os.fstat(fd).st_ino, original.stat().st_ino)
                    os.write(fd, b"launch\n")
                    if len(descriptors) == 1 and fail_at == "launch":
                        raise OSError("exec failed")
                    child = mock.Mock(pid=5678)
                    child.poll.return_value = 1
                    return child
                launch.side_effect = real_launch
                if fail_at == "ready":
                    ready.side_effect = [proxy.ProxyError("candidate failed"), None]
                    self.daemon.side_effect = [old, None, None]
                else:
                    self.daemon.side_effect = [old, None]
                with mock.patch.object(proxy.subprocess, "Popen", side_effect=popen):
                    with self.assertRaisesRegex(proxy.ProxyError, "previous daemon restored"):
                        proxy.apply(self.d, self.r)
                self.assertEqual(len(descriptors), 2)
                self.assertEqual(descriptors[0], descriptors[1])
                self.assertEqual(original.read_bytes(), b"launch\nlaunch\n")
                self.assertEqual(victim.read_bytes(), b"must not change")
                with self.assertRaises(OSError):
                    os.fstat(descriptors[0])
                self.daemon.side_effect = None
                path.unlink()
                original.unlink()

    def test_unrelated_unreadable_proc_executable_is_skipped(self):
        entry = mock.MagicMock()
        entry.name = "1234"
        entry.stat.return_value.st_uid = os.getuid()
        entry.__truediv__.return_value.read_bytes.return_value = b"python\0-c\0unrelated\0"
        with mock.patch.object(Path, "is_dir", return_value=True), \
                mock.patch.object(Path, "iterdir", return_value=[entry]), \
                mock.patch.object(proxy.os, "readlink", side_effect=PermissionError("unreadable")) as link:
            self.assertIsNone(self.actual_daemon(self.d, self.r))
            link.assert_not_called()

    def test_environment_preserves_transport_and_never_replaces_with_application_proxy(self):
        environment = {"HTTP_PROXY": "http://127.0.0.1:18089", "HTTPS_PROXY": "http://127.0.0.1:18089",
                       "SSL_CERT_FILE": str(self.d / "shim/bundle.crt"), "OTHER_SETTING": "retained"}
        raw = b"\0".join((key + "=" + value).encode() for key, value in environment.items()) + b"\0"
        with mock.patch.object(Path, "read_bytes", return_value=raw):
            self.assertEqual(proxy._environment(self.old(), self.d), environment)
        with mock.patch.object(Path, "read_bytes", return_value=b"HTTP_PROXY=http://127.0.0.1:1080\0"):
            with self.assertRaisesRegex(proxy.ProxyError, "transport proxy"):
                proxy._environment(self.old(), self.d)

    def test_validate_does_not_probe_or_lock_ports(self):
        self.write(json.dumps({"socks_port": 1080, "http_port": None}))
        with mock.patch.object(proxy, "_ports_available") as ports, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(proxy.main(["validate", str(self.d), str(self.r)]), 0)
        ports.assert_not_called()
        self.assertEqual(output.getvalue(), "")
        self.assertFalse((self.d / ".operation.lock").exists())

    def test_pid_reuse_does_not_signal(self):
        with mock.patch.object(proxy, "_same_process", return_value=False), mock.patch.object(proxy.os, "kill") as kill:
            proxy._stop(self.old())
            kill.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc ownership checks")
    def test_real_proc_socket_listener_ownership_and_start_ticks(self):
        process = {"pid": os.getpid(), "ticks": proxy._ticks(os.getpid()),
                   "exe": os.readlink("/proc/self/exe")}
        self.assertTrue(proxy._same_process(process))
        self.assertFalse(proxy._same_process(dict(process, ticks="different-start-time")))
        with socket.socket(socket.AF_UNIX) as unix, socket.socket() as tcp:
            unix.bind(str(self.r / "tailscaled.sock"))
            unix.listen()
            tcp.bind(("127.0.0.1", 0))
            tcp.listen()
            self.assertTrue(proxy._socket_owned(process, self.r))
            self.assertTrue(proxy._listener_owned(process, tcp.getsockname()[1]))
        self.assertFalse(proxy._socket_owned(process, self.r))

    def test_apply_requires_identified_socket_owner(self):
        with self.assertRaisesRegex(proxy.ProxyError, "identified"):
            proxy.apply(self.d, self.r)
        self.daemon.return_value = self.old()
        with mock.patch.object(proxy, "_socket_owned", return_value=False), mock.patch.object(proxy, "_stop") as stop:
            with self.assertRaisesRegex(proxy.ProxyError, "identified"):
                proxy.apply(self.d, self.r)
            stop.assert_not_called()

    def test_cli_uses_injected_internal_paths_and_no_runtime_argument(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(proxy.main(["show"], self.d, self.r), 0)
        self.assertEqual(json.loads(output.getvalue())["desired"], proxy.DEFAULT)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            proxy.main(["show", "--runtime", str(self.r)], self.d, self.r)


if __name__ == "__main__":
    unittest.main()
