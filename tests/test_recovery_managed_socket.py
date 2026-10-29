"""Managed socket guards: mocked /proc cases plus one real Linux FD fixture.

These tests never operate a Tailscale node or use real authentication state.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import dots_proxy
import test_recovery_lifecycle as lifecycle

tool = lifecycle.tool
REAL_IDENTITY = tool.recovery_identity
REAL_BOOT = tool.recovery_boot
REAL_PROCESS_CHECKS = tool.process_checks


class ManagedSocketTests(unittest.TestCase):
    setUp = lifecycle.RecoveryLifecycleTests.setUp
    write = lifecycle.RecoveryLifecycleTests.write
    current_identity = lifecycle.RecoveryLifecycleTests.current_identity
    configure = lifecycle.RecoveryLifecycleTests.configure
    state = lifecycle.RecoveryLifecycleTests.state
    save = lifecycle.RecoveryLifecycleTests.save
    options = lifecycle.RecoveryLifecycleTests.options
    assert_rejected = lifecycle.RecoveryLifecycleTests.assert_rejected

    def socket_file(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        # Relative bind avoids Darwin's short sockaddr_un path limit; these
        # unit cases mock /proc. The Linux fixture below binds the real path.
        old = os.open('.', os.O_RDONLY | os.O_DIRECTORY)
        folder = os.open(str(self.runtime), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fchdir(folder)
            server.bind('tailscaled.sock')
            server.listen(1)
        finally:
            os.fchdir(old)
            os.close(folder)
            os.close(old)
        return server

    @contextlib.contextmanager
    def tracked_stale(self):
        self.configure(managed_socket_cleanup=True)
        server = self.socket_file()
        parent, metadata = tool.recovery_socket_metadata(self.runtime)
        server.close()
        namespaces = {name: {'dev': 4, 'ino': index + 1} for index, name in enumerate(('net', 'pid', 'mnt'))}
        registry = {'boot_id': self.boot, 'namespaces': namespaces,
                    'daemon': dict(self.child_identity, exe=str(self.directory / 'tailscaled')),
                    'runtime': parent, 'socket': metadata, 'kernel_inode': '123456', 'captured_at': self.now}
        state = self.state()
        state['managed_socket'] = registry
        self.save(state)
        missing = {'shim': 'running', 'tailscaled': 'not_running', 'sshd': 'running'}
        with mock.patch.object(tool, 'recovery_namespaces', return_value=namespaces) as ns, \
                mock.patch.object(tool, 'recovery_unix_sockets', return_value=[]) as unix, \
                mock.patch.object(tool, 'process_checks', return_value=missing) as processes, \
                mock.patch.object(dots_proxy, '_daemon', return_value=None) as daemon:
            yield registry, ns, unix, processes, daemon

    def test_tracked_stale_socket_is_journaled_and_removed_before_unlocked_start(self):
        with self.tracked_stale():
            state = self.state()
            with mock.patch.object(tool, 'recovery_capture') as capture:
                eligible = lifecycle.ORIGINAL_PROBE(self.directory, self.runtime, state)
            self.assertEqual(eligible['state'], 'components_missing')
            self.assertEqual(eligible['managed_socket_cleanup'], 'eligible')
            capture.assert_not_called()
            self.probe.side_effect = [eligible, {'state': 'healthy'}]
            def start(*args, **kwargs):
                self.assertFalse((self.runtime / 'tailscaled.sock').exists())
                intent = self.state()['attempts'][-1]
                self.assertEqual(intent['socket_cleanup'], 'removed')
                with tool.recovery_lock(self.directory, '.operation.lock'):
                    pass
                return lifecycle.FakeProcess()
            self.popen.side_effect = start
            result = tool.recovery_run(self.directory, self.runtime, self.options())
            self.assertEqual(result['state'], 'local_ready')
            self.assertEqual(result['socket_cleanup'], 'removed')
            self.assertEqual(len(self.state()['attempts']), 1)
            self.assertIsNone(self.state()['managed_socket'])

    def test_active_untracked_other_context_and_unknown_pid_are_never_unlinked(self):
        with self.tracked_stale() as (registry, ns, unix, processes, daemon):
            state = self.state()
            path = self.runtime / 'tailscaled.sock'
            for rows in ([{'inode': '999', 'path': str(path), 'listener': True}],
                         [{'inode': registry['kernel_inode'], 'path': '/other/path', 'listener': False}]):
                unix.return_value = rows
                self.assert_rejected('managed_socket_still_active', lambda: tool.recovery_managed_socket_stale(
                    self.directory, self.runtime, state))
            unix.return_value = []
            self.child_alive = True
            self.assert_rejected('managed_socket_owner_not_proven_exited', lambda: tool.recovery_managed_socket_stale(
                self.directory, self.runtime, state))
            self.child_alive = False
            changed = dict(registry['namespaces'], net={'dev': 4, 'ino': 999})
            ns.return_value = changed
            self.assert_rejected('managed_socket_context_changed', lambda: tool.recovery_managed_socket_stale(
                self.directory, self.runtime, state))
            ns.return_value = registry['namespaces']
            self.boot = 'another-boot'
            self.assert_rejected('managed_socket_context_changed', lambda: tool.recovery_managed_socket_stale(
                self.directory, self.runtime, state))
            self.boot = registry['boot_id']
            with mock.patch.object(tool, 'recovery_identity', side_effect=tool.RecoveryError('process_identity_unknown')):
                self.assert_rejected('process_identity_unknown', lambda: tool.recovery_managed_socket_stale(
                    self.directory, self.runtime, state))
            with mock.patch.object(tool, 'recovery_unix_sockets', side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    tool.recovery_managed_socket_stale(self.directory, self.runtime, state)
            for override in ({'managed_socket_cleanup': False}, {'managed_socket': None}):
                self.assert_rejected('managed_socket_not_tracked', lambda: tool.recovery_managed_socket_stale(
                    self.directory, self.runtime, dict(state, **override)))
            processes.return_value = {'tailscaled': 'running'}
            self.assert_rejected('managed_socket_daemon_not_proven_absent', lambda: tool.recovery_managed_socket_stale(
                self.directory, self.runtime, state))
            processes.return_value = {'tailscaled': 'not_running'}
            daemon.return_value = {'pid': 800001}
            self.assert_rejected('managed_socket_daemon_not_proven_absent', lambda: tool.recovery_managed_socket_stale(
                self.directory, self.runtime, state))
            self.assertTrue(path.exists())
            self.popen.assert_not_called()

    def test_changed_socket_inode_or_ctime_is_not_removed(self):
        with self.tracked_stale():
            state = self.state()
            path = self.runtime / 'tailscaled.sock'
            path.unlink()
            replacement = self.socket_file()
            self.assert_rejected('managed_socket_changed', lambda: tool.recovery_remove_managed_socket(
                self.directory, self.runtime, state))
            self.assertTrue(path.exists())
            replacement.close()
            self.popen.assert_not_called()

    def test_untracked_stale_socket_keeps_unknown_without_query_or_unlink(self):
        self.configure()
        server = self.socket_file()
        server.close()
        with mock.patch.object(tool, 'process_checks', return_value={'shim': 'running', 'tailscaled': 'not_running', 'sshd': 'running'}), \
                mock.patch.object(tool, 'recovery_capture') as capture:
            result = lifecycle.ORIGINAL_PROBE(self.directory, self.runtime, self.state())
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['managed_socket_cleanup'], 'manual_required')
        capture.assert_not_called()
        self.assertTrue((self.runtime / 'tailscaled.sock').exists())
        self.popen.assert_not_called()

    def test_unknown_after_cleanup_preserves_durable_intent_and_blocks_new_ids(self):
        with self.tracked_stale():
            self.probe.return_value = {'state': 'components_missing', 'managed_socket_cleanup': 'eligible'}
            original = tool.recovery_remove_managed_socket
            def interrupted(directory, runtime, state):
                self.assertEqual(self.state()['attempts'][-1]['socket_cleanup'], 'intent')
                original(directory, runtime, state)
                raise OSError('simulated failure after unlink')
            with mock.patch.object(tool, 'recovery_remove_managed_socket', side_effect=interrupted):
                self.assert_rejected('execution_or_journal_uncertain', lambda: tool.recovery_run(
                    self.directory, self.runtime, self.options()))
            self.assertFalse((self.runtime / 'tailscaled.sock').exists())
            self.assertEqual(self.state()['attempts'][-1]['state'], 'running')
            self.assertEqual(self.state()['attempts'][-1]['socket_cleanup'], 'intent')
            self.assert_rejected('unknown_result_requires_reconciliation', lambda: tool.recovery_run(
                self.directory, self.runtime, self.options(identifier='new-after-interruption')))
            self.popen.assert_not_called()

    def test_mode_is_explicit_capture_failure_is_not_tracking_and_disable_keeps_registry(self):
        with mock.patch.object(tool, 'recovery_capture_managed_socket', return_value=None):
            report = self.configure(managed_socket_cleanup=True)
        self.assertTrue(report['managed_socket']['enabled'])
        self.assertFalse(report['managed_socket']['tracking_available'])
        self.assertIsNone(self.state()['managed_socket'])
        with self.tracked_stale() as (registry, *_):
            report = self.configure(managed_socket_cleanup=False)
            self.assertFalse(report['managed_socket']['enabled'])
            self.assertFalse(report['managed_socket']['tracking_available'])
            self.assertEqual(self.state()['managed_socket'], registry)

    def test_legacy_ledger_extension_never_resets_attempts_unknown_budget_or_stop(self):
        self.configure()
        self.probe.return_value = {'state': 'components_missing'}
        self.popen.return_value = lifecycle.FakeProcess(timeout=True)
        tool.recovery_run(self.directory, self.runtime, self.options())
        tool.recovery_stop(self.directory)
        state = self.state()
        state.pop('managed_socket_cleanup')
        state.pop('managed_socket')
        for row in state['attempts']:
            row.pop('socket_cleanup')
        path = tool.recovery_folder(self.directory) / 'state.json'
        path.write_text(json.dumps(state))
        before = path.read_bytes()
        report = tool.recovery_status(self.directory, self.runtime)
        current = self.state()
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(current['enabled'])
        self.assertTrue(report['manual_review_required'])
        self.assertEqual(current['attempts'][0]['state'], 'unknown')
        self.assertEqual(current['attempts'][0]['id'], 'request-1')
        self.assertEqual(report['policy']['attempts_in_window'], 1)
        self.assertFalse(current['managed_socket_cleanup'])

    def test_cli_optin_flag_is_explicit_and_status_has_only_tracking_summary(self):
        output = io.StringIO()
        with mock.patch.object(tool, 'directories', return_value=(self.directory, self.runtime)), \
                contextlib.redirect_stdout(output):
            code = tool.main(['recovery', 'configure', '--enable', '--managed-socket-cleanup', '--json'])
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report['managed_socket']['enabled'])
        self.assertFalse(report['managed_socket']['tracking_available'])
        self.assertEqual(set(report['managed_socket']), {'enabled', 'tracking_available', 'scope'})
        self.popen.assert_not_called()

    def test_invalid_environment_is_rejected_before_start_or_watchdog_intent(self):
        self.configure()
        self.probe.return_value = {'state': 'components_missing'}
        with mock.patch.dict(tool.os.environ, {'UPSTREAM_PROXY': 'https://invalid.example:443'}):
            with self.assertRaises(tool.UpdateError):
                tool.recovery_run(self.directory, self.runtime, self.options())
            with self.assertRaises(tool.UpdateError):
                tool.recovery_watchdog_start(self.directory, self.runtime)
        self.assertEqual(self.state()['attempts'], [])
        self.assertIsNone(self.state()['watchdog'])
        self.popen.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('cc'), 'real Linux /proc and C compiler required')
class LinuxManagedSocketFixture(unittest.TestCase):
    setUp = lifecycle.RecoveryLifecycleTests.setUp
    write = lifecycle.RecoveryLifecycleTests.write
    current_identity = lifecycle.RecoveryLifecycleTests.current_identity
    configure = lifecycle.RecoveryLifecycleTests.configure
    state = lifecycle.RecoveryLifecycleTests.state
    save = lifecycle.RecoveryLifecycleTests.save
    options = lifecycle.RecoveryLifecycleTests.options

    def test_real_configured_process_owns_unix_fd_then_kill_leaves_only_managed_file(self):
        short = tempfile.TemporaryDirectory(prefix='dots-ms-', dir='/tmp')
        self.addCleanup(short.cleanup)
        self.runtime = Path(short.name).resolve() / 'r'
        self.runtime.mkdir(mode=0o700)
        source = self.directory.parent / 'dummy.c'
        source.write_text(r'''
#include <sys/socket.h>
#include <sys/un.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc != 2 || strncmp(argv[1], "--socket=", 9)) return 2;
    const char *path = argv[1] + 9;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    if (strlen(path) >= sizeof(address.sun_path)) return 3;
    memcpy(address.sun_path, path, strlen(path) + 1);
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0 || bind(fd, (struct sockaddr *)&address, sizeof(address)) || listen(fd, 2)) return 4;
    if (write(STDOUT_FILENO, "R", 1) != 1) return 5;
    for (;;) pause();
}
''')
        with mock.patch.object(tool.subprocess, 'Popen', lifecycle.REAL_POPEN):
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(self.directory / 'tailscaled')],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (self.directory / 'tailscaled').chmod(0o700)
            daemon = subprocess.Popen([str(self.directory / 'tailscaled'), '--socket=' + str(self.runtime / 'tailscaled.sock')],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if daemon.poll() is None:
                daemon.kill()
            daemon.wait(timeout=3)
            daemon.stdout.close()
            daemon.stderr.close()
        self.addCleanup(cleanup)
        self.assertTrue(select.select([daemon.stdout], [], [], 3)[0], 'dummy listener did not become ready')
        self.assertEqual(os.read(daemon.stdout.fileno(), 1), b'R')
        with mock.patch.object(tool, 'recovery_identity', REAL_IDENTITY), \
                mock.patch.object(tool, 'recovery_boot', REAL_BOOT), \
                mock.patch.object(tool, 'process_checks', REAL_PROCESS_CHECKS):
            report = self.configure(managed_socket_cleanup=True)
            self.assertTrue(report['managed_socket']['tracking_available'])
            registry = self.state()['managed_socket']
            self.assertEqual(registry['daemon']['pid'], daemon.pid)
            self.assertEqual(registry['daemon']['exe'], str(self.directory / 'tailscaled'))
            self.assertNotIn('argv', registry['daemon'])
            self.assertNotIn('env', registry['daemon'])
            with self.assertRaises(tool.RecoveryError):
                tool.recovery_managed_socket_stale(self.directory, self.runtime, self.state())
            daemon.kill()
            daemon.wait(timeout=3)
            self.assertTrue((self.runtime / 'tailscaled.sock').exists())
            eligible = lifecycle.ORIGINAL_PROBE(self.directory, self.runtime, self.state())
            self.assertEqual(eligible['managed_socket_cleanup'], 'eligible')
            self.probe.side_effect = [eligible, {'state': 'healthy'}]
            def staged_start(*args, **kwargs):
                self.assertFalse((self.runtime / 'tailscaled.sock').exists())
                self.assertEqual(self.state()['attempts'][-1]['socket_cleanup'], 'removed')
                with tool.recovery_lock(self.directory, '.operation.lock'):
                    pass
                return lifecycle.FakeProcess()
            self.popen.side_effect = staged_start
            result = tool.recovery_run(self.directory, self.runtime, self.options())
            self.assertEqual(result['state'], 'local_ready')
            self.assertEqual(result['socket_cleanup'], 'removed')
            self.assertEqual(result['external_confirmation'], 'pending')


if __name__ == '__main__':
    unittest.main()
