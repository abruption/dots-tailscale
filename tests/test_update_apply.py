"""Manual updater: offline fault fixtures; never touches a live installation."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('dots_ts_apply_test', str(ROOT / 'dots-ts'))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


def fixture(folder, installed=False, version='0.4.0-dev', marker='new'):
    files = {'tailscale-setup.sh': b'#!/bin/bash\nTS_VERSION="1.102.4"\n',
             'shim.py': ('# '+marker+'\n').encode(), 'dots-ts': b'# cli\n', 'dots_proxy.py': b'# proxy\n'}
    metadata = {'schema_version': 1, 'package_version': version,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files[tool.MANIFEST] = json.dumps(metadata).encode()
    for name, data in files.items():
        path = folder / (tool.CORE_FILES.get(name, name) if installed else name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-update-apply-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / 'install'; self.directory.mkdir(mode=0o700)
        self.transaction = self.root / 'transaction'; self.transaction.mkdir(mode=0o700)
        (self.transaction / 'stage').mkdir(mode=0o700)
        self.old = fixture(self.directory, True, '0.3.0', 'old')
        self.new = fixture(self.transaction / 'stage')
        (self.directory / 'sshd').mkdir()
        (self.directory / 'sshd/ssh_host_ed25519_key.pub').write_bytes(b'public-key')
        for name in ('state/tailscaled.state', 'sshd/ssh_host_ed25519_key', 'shim/ca.key', 'outbound-proxy.json'):
            path = self.directory / name; path.parent.mkdir(exist_ok=True); path.write_bytes(b'DUMMY fixture only '+name.encode())
        self.protected = {name: ((self.directory / name).stat().st_ino, (self.directory / name).read_bytes())
                          for name in ('state/tailscaled.state', 'sshd/ssh_host_ed25519_key', 'shim/ca.key', 'outbound-proxy.json')}
        self.journal = {'schema': 1, 'state': 'running'}
        self.baseline = {name: {'pid': pid, 'ticks': str(pid), 'exe': '/python', 'uid': os.getuid()}
                         for name, pid in (('shim', 101), ('tailscaled', 102), ('sshd', 103))}
        self.candidate = dict(self.baseline['shim'], pid=201, ticks='201')
        self.restored = dict(self.baseline['shim'], pid=301, ticks='301')
        self.proxy = mock.Mock()
        self.proxy.inspect_config.return_value = {'restart_required': False, 'daemon_running': True}

    def lifecycle(self, ready=None):
        stack = contextlib.ExitStack(); self.addCleanup(stack.close)
        self.stop = stack.enter_context(mock.patch.object(tool, 'apply_stop'))
        self.launch = stack.enter_context(mock.patch.object(tool, 'apply_launch', side_effect=[mock.Mock(), mock.Mock()]))
        self.identify = stack.enter_context(mock.patch.object(tool, 'apply_candidate', side_effect=[self.candidate, self.restored]))
        self.ready = stack.enter_context(mock.patch.object(tool, 'apply_ready', side_effect=ready))
        stack.enter_context(mock.patch.object(tool, 'apply_components', return_value=self.baseline))
        stack.enter_context(mock.patch.object(tool, 'apply_environment', return_value={'UPSTREAM_HOST': 'fixture'}))
        stack.enter_context(mock.patch.object(tool, 'apply_public_identity', return_value='public-hash'))
        return stack

    def run_transaction(self):
        tool.apply_transaction(self.directory, self.root / 'runtime', self.transaction, self.journal, self.proxy)

    def assert_protected(self):
        for name, expected in self.protected.items():
            path = self.directory / name
            self.assertEqual((path.stat().st_ino, path.read_bytes()), expected)
        self.assertFalse((self.directory / 'start.sh').exists())

    def test_success_code_only_and_manifest_last(self):
        self.lifecycle()
        self.run_transaction()
        self.assertEqual(self.journal['state'], 'applied')
        self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.new)
        self.assertEqual(self.journal['completed_file'], tool.MANIFEST)
        self.stop.assert_called_once_with(self.baseline['shim'])
        self.assert_protected()
        self.assertEqual(set(p.name for p in (self.transaction / 'backup').iterdir()), set(self.old))

    def test_readiness_failure_restores_exact_old_code(self):
        self.lifecycle([tool.ApplyError('readiness_timeout'), None]); self.run_transaction()
        self.assertEqual(self.journal['state'], 'failed_rolled_back')
        self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.old)
        self.assertEqual(self.stop.call_args_list, [mock.call(self.baseline['shim']), mock.call(self.candidate)])
        self.assert_protected()

    def test_rollback_failure_is_never_success(self):
        self.lifecycle([tool.ApplyError('readiness_timeout'), tool.ApplyError('readiness_timeout')]); self.run_transaction()
        self.assertEqual(self.journal['state'], 'rollback_failed_manual_required')
        self.assert_protected()

    def test_stop_failure_never_replaces_or_launches(self):
        self.lifecycle(); self.stop.side_effect = tool.ApplyError('process_identity_unknown'); self.run_transaction()
        self.assertEqual(self.journal['state'], 'rollback_failed_manual_required')
        self.launch.assert_not_called(); self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.old)

    def test_fault_after_each_replacement_restores_code(self):
        # Recreate independent fixture for every before/after-manifest boundary.
        for failed_file in list(tool.CORE_FILES) + [tool.MANIFEST]:
            with self.subTest(file=failed_file):
                fixture(self.directory, True, '0.3.0', 'old')
                backup = self.transaction / 'backup'
                if backup.exists():
                    for path in backup.iterdir(): path.unlink()
                    backup.rmdir()
                self.journal = {'schema': 1, 'state': 'running'}
                original = tool.apply_write; fired = []
                def write(path, data, mode=0o600):
                    original(path, data, mode)
                    wanted = self.directory / tool.CORE_FILES.get(failed_file, failed_file)
                    if path == wanted and not fired:
                        fired.append(True); raise OSError('simulated disk failure after rename')
                with self.lifecycle(), mock.patch.object(tool, 'apply_write', side_effect=write):
                    self.run_transaction()
                self.assertEqual(self.journal['state'], 'failed_rolled_back')
                self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.old)
                self.assert_protected()

    def test_missing_public_host_key_stops_before_signal(self):
        self.lifecycle(); (self.directory / 'sshd/ssh_host_ed25519_key.pub').unlink()
        with self.assertRaisesRegex(tool.ApplyError, 'public_ssh_host_key_required'): self.run_transaction()
        self.stop.assert_not_called()

    def test_changed_tailscale_binary_version_refused(self):
        self.lifecycle()
        self.new['tailscale-setup.sh'] = b'TS_VERSION="1.104.0"\n'
        with mock.patch.object(tool, 'apply_bundle', side_effect=[self.old, self.new]):
            with self.assertRaisesRegex(tool.ApplyError, 'binary_update_unsupported'): self.run_transaction()
        self.stop.assert_not_called()

    def test_legacy_mixed_and_symlink_packages_refused(self):
        (self.directory / tool.MANIFEST).unlink()
        with self.assertRaises(tool.ApplyError): tool.apply_bundle(self.directory, 'installed')
        fixture(self.directory, True)
        (self.directory / 'dots-ts').write_bytes(b'changed')
        with self.assertRaises(tool.ApplyError): tool.apply_bundle(self.directory, 'installed')
        fixture(self.directory, True)
        (self.directory / 'dots-ts').unlink(); (self.directory / 'dots-ts').symlink_to(ROOT / 'dots-ts')
        with self.assertRaises(tool.ApplyError): tool.apply_bundle(self.directory, 'installed')

    def test_syntax_checks_do_not_execute_payload(self):
        payload = dict(self.new, **{'shim.py': b'raise RuntimeError("must not execute")\n'})
        tool.apply_syntax(payload)
        payload['shim.py'] = b'if :'
        with self.assertRaisesRegex(tool.ApplyError, 'invalid_python_syntax'): tool.apply_syntax(payload)
        payload['shim.py'] = b''; payload['tailscale-setup.sh'] = b'if then'
        with self.assertRaisesRegex(tool.ApplyError, 'invalid_shell_syntax'): tool.apply_syntax(payload)

    def test_atomic_writer_refuses_preexisting_temporary_symlink(self):
        target = self.root / 'file'; victim = self.root / 'victim'; victim.write_bytes(b'unchanged')
        (self.root / 'file.new').symlink_to(victim)
        with self.assertRaises(FileExistsError): tool.apply_write(target, b'bad')
        self.assertEqual(victim.read_bytes(), b'unchanged')

    def test_unknown_identity_never_signalled(self):
        with mock.patch.object(tool, 'apply_identity', side_effect=tool.ApplyError('process_identity_unknown')):
            with self.assertRaises(tool.ApplyError): tool.apply_stop(self.baseline['shim'])

    def test_status_dead_worker_is_interrupted_not_success(self):
        root = self.directory / tool.APPLY_DIRECTORY; root.mkdir(mode=0o700)
        identifier = 'a' * 32; transaction = root / identifier; transaction.mkdir(mode=0o700)
        journal = {'schema': 1, 'state': 'running', 'boot_id': 'boot', 'worker': self.candidate}
        tool.apply_journal(transaction, journal)
        with mock.patch.object(tool, 'apply_boot', return_value='boot'), mock.patch.object(tool, 'apply_identity', return_value=None):
            report = tool.apply_status(self.directory, identifier)
        self.assertEqual(report['state'], 'interrupted_manual_required')
        self.assertEqual(report['external_ssh'], 'pending')
        self.assertEqual(report['backup_directory'], str(transaction / 'backup'))

    def test_running_status_does_not_claim_external_success(self):
        root = self.directory / tool.APPLY_DIRECTORY; root.mkdir(mode=0o700)
        identifier = 'a' * 32; transaction = root / identifier; transaction.mkdir(mode=0o700)
        journal = {'schema': 1, 'state': 'running', 'boot_id': 'boot', 'worker': self.candidate}
        tool.apply_journal(transaction, journal)
        with mock.patch.object(tool, 'apply_boot', return_value='boot'), mock.patch.object(tool, 'apply_identity', return_value=self.candidate):
            self.assertEqual(tool.apply_status(self.directory, identifier)['state'], 'running')

    def test_real_setup_binary_version_parses(self):
        payload = {'tailscale-setup.sh': (ROOT / 'tailscale-setup.sh').read_bytes()}
        self.assertEqual(tool.apply_ts_version(payload), b'1.102.4')
        payload['tailscale-setup.sh'] = b'TS_VERSION="1.102.4"\n'
        self.assertEqual(tool.apply_ts_version(payload), b'1.102.4')
        payload['tailscale-setup.sh'] = b'TS_VERSION="1.102.4\n'
        with self.assertRaises(tool.ApplyError): tool.apply_ts_version(payload)

    def test_candidate_identity_failure_never_launches_duplicate(self):
        self.lifecycle()
        self.identify.side_effect = tool.ApplyError('process_identity_unknown')
        live_child = mock.Mock(); live_child.poll.return_value = None
        self.launch.side_effect = [live_child]
        self.run_transaction()
        self.assertEqual(self.journal['state'], 'rollback_failed_manual_required')
        self.launch.assert_called_once()
        self.stop.assert_called_once_with(self.baseline['shim'])

    def test_worker_journal_failure_after_destructive_intent_is_manual(self):
        transaction = self.directory / tool.APPLY_DIRECTORY / ('a' * 32)
        transaction.parent.mkdir(mode=0o700); transaction.mkdir(mode=0o700)
        journal = {'schema': 1, 'state': 'prepared', 'boot_id': 'boot', 'phase': 'staging'}
        tool.apply_journal(transaction, journal)
        def transaction_failure(directory, runtime, folder, record, proxy):
            record['destructive'] = True
            raise OSError('simulated journal fsync failure')
        proxy = mock.Mock(); proxy._operation.return_value = contextlib.nullcontext()
        with mock.patch.dict(sys.modules, {'dots_proxy': proxy}), \
             mock.patch.object(tool, 'apply_boot', return_value='boot'), \
             mock.patch.object(tool, 'apply_identity', return_value=self.candidate), \
             mock.patch.object(tool, 'apply_transaction', side_effect=transaction_failure), \
             mock.patch.object(signal, 'signal'):
            tool.apply_worker(self.directory, self.root, transaction)
        self.assertEqual(tool.apply_read_journal(transaction)['state'], 'rollback_failed_manual_required')

    def test_malformed_journal_is_structured_error(self):
        for payload in ({'schema': 1, 'state': []}, {'schema': 1, 'state': 'running', 'worker': []},
                        {'schema': 1, 'state': 'running', 'worker': {'pid': 'unsafe'}}):
            with self.subTest(payload=payload):
                (self.transaction / 'journal.json').write_text(json.dumps(payload))
                with self.assertRaisesRegex(tool.ApplyError, 'invalid_journal'):
                    tool.apply_read_journal(self.transaction)

    def test_pending_transaction_blocks_new_update_before_download(self):
        root = self.directory / tool.APPLY_DIRECTORY; root.mkdir(mode=0o700)
        transaction = root / ('a' * 32); transaction.mkdir(mode=0o700)
        tool.apply_journal(transaction, {'schema': 1, 'state': 'prepared'})
        proxy = mock.Mock(); proxy._operation.return_value = contextlib.nullcontext()
        with mock.patch.dict(sys.modules, {'dots_proxy': proxy}), \
             mock.patch.object(tool.sys, 'platform', 'linux'), \
             mock.patch.object(tool.os, 'pidfd_open', create=True), \
             mock.patch.object(signal, 'pidfd_send_signal', create=True), \
             mock.patch.object(tool, 'apply_bundle', return_value=self.old), \
             mock.patch.object(tool, 'fetch_release') as fetch:
            with self.assertRaisesRegex(tool.ApplyError, 'prior_transaction_requires_manual_review'):
                tool.update_apply(self.directory, self.root, tag='v0.4.0', confirm=True)
            fetch.assert_not_called()

    def test_refuses_without_recovery_acknowledgement(self):
        with self.assertRaisesRegex(tool.ApplyError, 'confirmation_required'):
            tool.update_apply(self.directory, self.root, bundle=self.root)

    def test_readiness_waits_through_transient_backend(self):
        proxy = mock.Mock(); proxy.inspect_config.return_value = {}; proxy._listener_owned.return_value = True
        identities = {item['pid']: item for item in list(self.baseline.values()) + [self.candidate]}
        connection = mock.MagicMock(); connection.__enter__.return_value.recv.return_value = b'SSH-2.0-fixture'
        with mock.patch.object(tool, 'apply_identity', side_effect=lambda pid: identities[pid]), \
             mock.patch.object(tool, 'apply_public_identity', side_effect=[tool.ApplyError('backend_not_running'), 'same']), \
             mock.patch.object(tool.socket, 'create_connection', return_value=connection):
            tool.apply_ready(self.directory, self.root, proxy, self.baseline, self.candidate, 'same', {}, b'public-key', timeout=1)

    def test_identity_change_not_retried_as_transient(self):
        with mock.patch.object(tool, 'apply_identity', return_value=None):
            with self.assertRaisesRegex(tool.ApplyError, 'candidate_identity_changed'):
                tool.apply_ready(self.directory, self.root, self.proxy, self.baseline, self.candidate, 'same', {}, b'public-key', timeout=1)

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open'), 'Linux pidfd integration')
    def test_real_owned_child_sigterm_and_identity(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            identity = tool.apply_identity(child.pid)
            tool.apply_stop(identity, timeout=2)
            self.assertEqual(child.wait(timeout=2), -signal.SIGTERM)
        finally:
            if child.poll() is None: child.terminate(); child.wait(timeout=2)

    def test_detached_child_and_operation_lock_primitives(self):
        # Independent child proves setsid/closed pipes; no daemon or live port.
        child = subprocess.Popen([sys.executable, '-c', 'import os; print(os.getpid()==os.getsid(0))'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        stdout, _ = child.communicate(timeout=3)
        self.assertEqual(stdout.strip(), b'True')
        import fcntl
        path = self.root / 'lock'
        with path.open('w') as first, path.open('w') as second:
            fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == '__main__': unittest.main()
