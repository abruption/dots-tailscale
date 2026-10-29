"""Manual updater: offline fault fixtures; never touches a live installation."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
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
        stack.enter_context(mock.patch.object(tool, 'apply_interpreter'))
        self.stop = stack.enter_context(mock.patch.object(tool, 'apply_stop'))
        self.launch = stack.enter_context(mock.patch.object(tool, 'apply_launch', side_effect=[mock.Mock(poll=mock.Mock(return_value=None)), mock.Mock(poll=mock.Mock(return_value=None))]))
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

    def migration_fixture(self):
        stage = self.transaction / 'stage'
        self.new['tailscale-setup.sh'] = b'#!/bin/bash\nTS_VERSION=1.104.4\n'
        metadata = json.loads(self.new[tool.MANIFEST])
        metadata['files']['tailscale-setup.sh'] = hashlib.sha256(self.new['tailscale-setup.sh']).hexdigest()
        self.new[tool.MANIFEST] = json.dumps(metadata).encode()
        for name, body in self.new.items(): (stage / name).write_bytes(body)
        for name in ('tailscale', 'tailscaled'):
            path = self.directory / name
            path.write_text('#!/bin/sh\nprintf "1.104.4\\n"\n'); path.chmod(0o700)
            self.protected[name] = (path.stat().st_ino, path.read_bytes())
        supervisor = self.transaction / 'supervisor'; supervisor.mkdir(mode=0o700)
        for name in ('dots-ts', 'dots_proxy.py'): (supervisor / name).write_bytes(self.new[name])
        self.journal.update(operation='migrate_baseline',
            baseline_package_hashes=tool.apply_payload_hashes(self.old),
            target_package_hashes=tool.apply_payload_hashes(self.new))
        self.baseline['_socket'] = [1, 2]
        self.proxy.inspect_config.return_value = {
            'restart_required': False, 'daemon_running': True,
            'desired': {'socks_port': None, 'http_port': None}}
        self.proxy._flags.return_value = []
        self.proxy._socket_owned.return_value = True
        self.proxy._socket_identity.return_value = [1, 2]
        runtime = self.root / 'runtime'
        self.proxy._daemon.return_value = {
            'pid': self.baseline['tailscaled']['pid'],
            'argv': [str(self.directory / 'tailscaled'), '--tun=userspace-networking',
                     '--statedir=' + str(self.directory / 'state'),
                     '--socket=' + str(runtime / 'tailscaled.sock'), '--port=0']}
        stack = self.lifecycle()
        stack.enter_context(mock.patch.object(tool, 'apply_identity', return_value=self.baseline['tailscaled']))
        reply = mock.Mock(returncode=0, stdout=json.dumps({'Version': '1.104.4-tabc1234567-gdef1234567', 'BackendState': 'Running'}))
        self.runtime_reply = reply
        stack.enter_context(mock.patch.object(tool, 'run_cli', return_value=reply))
        return stack

    def migration_guard(self):
        return tool.apply_baseline_guard(self.old, self.new, self.directory,
                                         self.root / 'runtime', self.proxy, True)

    def test_baseline_migration_changes_sources_only(self):
        self.migration_fixture()
        self.run_transaction()
        self.assertEqual(self.journal['state'], 'applied')
        self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.new)
        self.assert_protected()
        self.stop.assert_called_once_with(self.baseline['shim'])

    def test_baseline_migration_readiness_failure_restores_code_without_binary_downgrade(self):
        self.migration_fixture()
        self.ready.side_effect = [tool.ApplyError('readiness_timeout'), None]
        self.run_transaction()
        self.assertEqual(self.journal['state'], 'failed_rolled_back')
        self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.old)
        self.assert_protected()

    def test_baseline_migration_partial_replacement_restores_manifest_and_sources(self):
        self.migration_fixture()
        write = tool.apply_write; fired = []
        def fail(path, data, mode=0o600):
            write(path, data, mode)
            if path == self.directory / 'dots-ts' and not fired:
                fired.append(True); raise OSError('fixture after replacement')
        with mock.patch.object(tool, 'apply_write', side_effect=fail): self.run_transaction()
        self.assertEqual(self.journal['state'], 'failed_rolled_back')
        self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.old)
        self.assert_protected()

    def test_baseline_migration_does_not_relax_ordinary_apply_guard(self):
        self.migration_fixture()
        self.journal.pop('operation')
        with self.assertRaisesRegex(tool.ApplyError, 'binary_update_unsupported'): self.run_transaction()
        self.stop.assert_not_called(); self.assert_protected()

    def test_baseline_migration_exact_transition_only(self):
        self.migration_fixture()
        old = dict(self.old); old['tailscale-setup.sh'] = b'TS_VERSION=1.104.2\n'
        with self.assertRaisesRegex(tool.ApplyError, 'unsupported_baseline_migration'):
            tool.apply_baseline_guard(old, self.new, self.directory, self.root / 'runtime', self.proxy, True)
        self.stop.assert_not_called()

    def test_baseline_migration_wrong_or_partial_pair_refused(self):
        self.migration_fixture()
        cli = self.directory / 'tailscale'; daemon = self.directory / 'tailscaled'
        cli.write_text('#!/bin/sh\nprintf "1.102.4\\n"\n')
        with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_runtime_required'): self.migration_guard()
        cli.write_text('#!/bin/sh\nprintf "1.104.4\\n"\n'); daemon.unlink()
        with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_runtime_required'): self.migration_guard()
        self.stop.assert_not_called()

    def test_baseline_migration_daemon_actual_version_and_backend_required(self):
        self.migration_fixture()
        for data in ({'Version':'1.102.4','BackendState':'Running'},
                     {'Version':'1.104.4','BackendState':'NeedsLogin'},
                     {'BackendState':'Running'}, {'Version':True,'BackendState':'Running'}):
            self.runtime_reply.stdout = json.dumps(data)
            with self.subTest(data=data), self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_daemon_version_mismatch'):
                self.migration_guard()
        self.stop.assert_not_called()

    def test_baseline_migration_userspace_socket_proxy_contract_required(self):
        self.migration_fixture()
        self.proxy._daemon.return_value['argv'].append('--tun=tailscale0')
        with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_daemon_required'): self.migration_guard()
        self.stop.assert_not_called()

    def test_baseline_migration_rejects_pending_runtime_and_cleanup_warning(self):
        self.migration_fixture()
        root = self.directory / tool.RUNTIME_DIRECTORY; root.mkdir(mode=0o700)
        (root / ('b' * 32)).mkdir(mode=0o700)
        for status in ({'state':'running'}, {'state':'applied','cleanup_warning':'worker_cleanup_failed'}):
            with mock.patch.object(tool, 'runtime_status', return_value=status):
                with self.assertRaisesRegex(tool.ApplyError, 'prior_transaction_requires_manual_review'): self.migration_guard()
        self.stop.assert_not_called()

    def test_baseline_migration_stage_or_frozen_worker_change_rejected(self):
        self.migration_fixture()
        (self.transaction / 'supervisor/dots-ts').write_bytes(b'# stale worker')
        with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_source_changed'): self.run_transaction()
        self.stop.assert_not_called()
        (self.transaction / 'supervisor/dots-ts').write_bytes(self.new['dots-ts'])
        self.journal['target_package_hashes']['shim.py'] = '0' * 64
        with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_source_changed'): self.run_transaction()
        self.stop.assert_not_called()

    def test_baseline_migration_rejects_valid_but_changed_installed_package(self):
        self.migration_fixture()
        fixture(self.directory, True, '0.6.0', 'concurrent reviewed change')
        with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_source_changed'): self.run_transaction()
        self.stop.assert_not_called(); self.assert_protected()

    def test_baseline_migration_rejects_symlink_pair_before_probing(self):
        self.migration_fixture()
        daemon = self.directory / 'tailscaled'; daemon.unlink()
        daemon.symlink_to(self.directory / 'tailscale')
        with mock.patch.object(tool, 'runtime_verify_pair') as pair:
            with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_runtime_required'): self.migration_guard()
        pair.assert_not_called(); self.stop.assert_not_called()

    def test_baseline_migration_journal_mode_type_and_hashes_validated(self):
        self.migration_fixture()
        for operation in (True, 1, None, 'unknown'):
            record = dict(self.journal, operation=operation)
            tool.apply_journal(self.transaction, record)
            with self.assertRaisesRegex(tool.ApplyError, 'invalid_journal'): tool.apply_read_journal(self.transaction)
        record = dict(self.journal, target_package_hashes={'dots-ts':'0'*64})
        tool.apply_journal(self.transaction, record)
        with self.assertRaisesRegex(tool.ApplyError, 'invalid_journal'): tool.apply_read_journal(self.transaction)

    def test_baseline_migration_rechecks_before_signals(self):
        self.migration_fixture()
        actual = tool.apply_baseline_guard; calls = []
        def guard(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2: raise tool.ApplyError('baseline_migration_daemon_changed')
            return actual(*args, **kwargs)
        with mock.patch.object(tool, 'apply_baseline_guard', side_effect=guard):
            with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_daemon_changed'): self.run_transaction()
        self.stop.assert_not_called(); self.assert_protected()

    def test_baseline_migration_caller_requires_matching_reviewed_checkout(self):
        self.migration_fixture()
        self.proxy._operation.return_value = contextlib.nullcontext()
        with mock.patch.dict(sys.modules, {'dots_proxy': self.proxy}), \
             mock.patch.object(tool.sys, 'platform', 'linux'), \
             mock.patch.object(tool.os, 'pidfd_open', create=True), \
             mock.patch.object(signal, 'pidfd_send_signal', create=True), \
             mock.patch.object(tool, '__file__', str(self.directory / 'dots-ts')):
            with self.assertRaisesRegex(tool.ApplyError, 'baseline_migration_candidate_mismatch'):
                tool.update_apply(self.directory, self.root / 'runtime', bundle=self.transaction / 'stage',
                                  confirm=True, allow_unsigned_legacy=True, migrate_baseline=True)
        self.stop.assert_not_called(); self.assert_protected()

    def test_baseline_migration_caller_freezes_intent_and_source_hashes(self):
        self.migration_fixture()
        self.proxy._operation.return_value = contextlib.nullcontext()
        seen = []
        def spawn(argv, **kwargs):
            transaction = Path(argv[-1]); record = tool.apply_read_journal(transaction)
            seen.append(record.copy())
            tool.apply_journal(transaction, record, state='rejected')
            return mock.Mock()
        with mock.patch.dict(sys.modules, {'dots_proxy': self.proxy}), \
             mock.patch.object(tool.sys, 'platform', 'linux'), \
             mock.patch.object(tool.os, 'pidfd_open', create=True), \
             mock.patch.object(signal, 'pidfd_send_signal', create=True), \
             mock.patch.object(tool, '__file__', str(self.transaction / 'stage/dots-ts')), \
             mock.patch.object(subprocess, 'Popen', side_effect=spawn), \
             mock.patch.object(tool, 'apply_syntax'), \
             mock.patch.object(tool, 'apply_boot', return_value='boot'), \
             mock.patch.object(tool, 'runtime_verify_pair'):
            result = tool.update_apply(self.directory, self.root / 'runtime', bundle=self.transaction / 'stage',
                                       confirm=True, allow_unsigned_legacy=True, migrate_baseline=True)
        self.assertEqual(result['state'], 'rejected')
        self.assertEqual(seen[0]['operation'], 'migrate_baseline')
        self.assertEqual(seen[0]['baseline_package_hashes'], tool.apply_payload_hashes(self.old))
        self.assertEqual(seen[0]['target_package_hashes'], tool.apply_payload_hashes(self.new))
        self.stop.assert_not_called(); self.assert_protected()

    def test_baseline_migration_fence_blocks_before_binary_probes(self):
        import dots_proxy as proxy
        self.migration_fixture()
        (self.directory / tool.RUNTIME_FENCE).write_text('fixture fence')
        with mock.patch.object(tool.sys, 'platform', 'linux'), \
             mock.patch.object(tool.os, 'pidfd_open', create=True), \
             mock.patch.object(signal, 'pidfd_send_signal', create=True), \
             mock.patch.object(tool, '__file__', str(self.transaction / 'stage/dots-ts')), \
             mock.patch.object(tool, 'runtime_verify_pair') as pair:
            with self.assertRaisesRegex(tool.ApplyError, 'operation_busy_or_unsafe'):
                tool.update_apply(self.directory, self.root / 'runtime', bundle=self.transaction / 'stage',
                                  confirm=True, allow_unsigned_legacy=True, migrate_baseline=True)
        pair.assert_not_called(); self.stop.assert_not_called()

    def test_baseline_migration_cli_exposes_only_explicit_bundle_workflow(self):
        output = io.StringIO()
        with mock.patch.object(tool, 'directories', return_value=(self.directory, self.root)), \
             mock.patch.object(tool, 'update_apply', return_value={'state':'prepared'}) as migrate, \
             contextlib.redirect_stdout(output):
            code = tool.main(['update','migrate-baseline','--bundle',str(self.transaction / 'stage'),
                              '--confirm-recovery-terminal','--json'])
        self.assertEqual(code, 0)
        self.assertEqual(migrate.call_args.kwargs, {'bundle':self.transaction / 'stage',
                                                  'confirm':True, 'migrate_baseline':True})
        self.assertEqual(json.loads(output.getvalue()), {'state':'prepared'})

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
        self.stop.assert_called_once_with(self.baseline['shim'])
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

    def test_identity_stat_to_zombie_transition_is_positive_exit(self):
        running = '1 (fixture) ' + ' '.join(['S'] + ['0'] * 18 + ['42'])
        zombie = '1 (fixture) ' + ' '.join(['Z'] + ['0'] * 18 + ['42'])
        with mock.patch.object(Path, 'read_text', side_effect=[running, zombie]), \
             mock.patch.object(Path, 'stat', return_value=mock.Mock(st_uid=os.getuid())), \
             mock.patch.object(os, 'readlink', side_effect=FileNotFoundError()):
            self.assertIsNone(tool.apply_identity(1))

    def test_identity_missing_exe_but_running_stays_unknown(self):
        running = '1 (fixture) ' + ' '.join(['S'] + ['0'] * 18 + ['42'])
        with mock.patch.object(Path, 'read_text', return_value=running), \
             mock.patch.object(Path, 'stat', return_value=mock.Mock(st_uid=os.getuid())), \
             mock.patch.object(os, 'readlink', side_effect=FileNotFoundError()):
            with self.assertRaisesRegex(tool.ApplyError, 'process_identity_unknown'):
                tool.apply_identity(1)

    def test_stop_waits_on_pidfd_without_post_signal_proc_reads(self):
        selector = mock.MagicMock()
        selector.__enter__.return_value.select.return_value = [(mock.Mock(), 1)]
        with mock.patch.object(tool, 'apply_identity', side_effect=[self.baseline['shim'], self.baseline['shim']]) as identity, \
             mock.patch.object(os, 'pidfd_open', return_value=77, create=True), \
             mock.patch.object(signal, 'pidfd_send_signal', create=True) as send, \
             mock.patch.object(tool.selectors, 'DefaultSelector', return_value=selector), \
             mock.patch.object(os, 'close'):
            tool.apply_stop(self.baseline['shim'])
        self.assertEqual(identity.call_count, 2)
        send.assert_called_once_with(77, signal.SIGTERM)
        selector.__enter__.return_value.register.assert_called_once_with(77, tool.selectors.EVENT_READ)

    def test_exited_owned_candidate_skips_uncertain_proc_stop(self):
        self.lifecycle()
        self.identify.side_effect = [tool.ApplyError('process_identity_unknown'), self.restored]
        dead = mock.Mock(poll=mock.Mock(return_value=1))
        self.launch.side_effect = [dead, mock.Mock()]
        self.run_transaction()
        self.assertEqual(self.journal['state'], 'failed_rolled_back')
        self.stop.assert_called_once_with(self.baseline['shim'])
        dead.wait.assert_called_once_with(timeout=8)

    def test_real_setup_binary_version_parses(self):
        payload = {'tailscale-setup.sh': (ROOT / 'tailscale-setup.sh').read_bytes()}
        self.assertEqual(tool.apply_ts_version(payload), b'1.104.4')
        payload['tailscale-setup.sh'] = b'TS_VERSION="1.102.4"\n'
        self.assertEqual(tool.apply_ts_version(payload), b'1.102.4')
        payload['tailscale-setup.sh'] = b'TS_VERSION="1.102.4\n'
        with self.assertRaises(tool.ApplyError): tool.apply_ts_version(payload)

    def test_owned_exiting_candidate_unknown_identity_wait_success_rolls_back(self):
        self.lifecycle()
        self.identify.side_effect = [tool.ApplyError('process_identity_unknown'), self.restored]
        live_child = mock.Mock(); live_child.poll.return_value = None
        self.launch.side_effect = [live_child, mock.Mock()]
        self.run_transaction()
        self.assertEqual(self.journal['state'], 'failed_rolled_back')
        self.assertEqual(self.launch.call_count, 2)
        live_child.terminate.assert_called_once()
        live_child.wait.assert_called_once_with(timeout=8)
        self.stop.assert_called_once_with(self.baseline['shim'])

    def test_owned_child_timeout_never_launches_duplicate(self):
        self.lifecycle()
        self.identify.side_effect = tool.ApplyError('process_identity_unknown')
        live_child = mock.Mock(); live_child.poll.return_value = None
        live_child.wait.side_effect = subprocess.TimeoutExpired('fixture', 8)
        self.launch.side_effect = [live_child]
        self.run_transaction()
        self.assertEqual(self.journal['state'], 'rollback_failed_manual_required')
        self.launch.assert_called_once()


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

    def test_all_rollback_journal_failures_do_not_prevent_restoration(self):
        self.lifecycle([tool.ApplyError('readiness_timeout'), None])
        original = tool.apply_journal
        broken = []
        def journal(folder, record, **changes):
            if changes.get('phase') == 'rollback_intent': broken.append(True)
            if broken: raise OSError('PRIVATE disk failure')
            return original(folder, record, **changes)
        with mock.patch.object(tool, 'apply_journal', side_effect=journal): self.run_transaction()
        self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.old)
        self.assertEqual(self.launch.call_count, 2)
        self.assertEqual(self.journal['state'], 'failed_rolled_back')
        self.assertTrue(self.journal['journal_persistence_failed'])
        self.assertNotEqual(tool.apply_read_journal(self.transaction)['state'], 'failed_rolled_back')
        self.assertEqual((self.transaction / 'worker.log').read_text(),
                         'update_outcome=failed_rolled_back journal_persisted=false\n')
        self.assert_protected()

    def test_journal_failure_before_mutation_still_relaunches_old_shim(self):
        self.lifecycle()
        original = tool.apply_journal
        def journal(folder, record, **changes):
            if changes.get('phase') in ('replace_intent', 'rollback_intent'):
                raise OSError('PRIVATE disk failure')
            return original(folder, record, **changes)
        with mock.patch.object(tool, 'apply_journal', side_effect=journal): self.run_transaction()
        self.assertEqual(self.journal['state'], 'failed_rolled_back')
        self.launch.assert_called_once()
        self.assertEqual(tool.apply_bundle(self.directory, 'installed'), self.old)

    def test_nonchild_unknown_second_identity_only_accepts_readable_pidfd(self):
        for readable in (True, False):
            with self.subTest(readable=readable):
                selector = mock.MagicMock()
                selector.__enter__.return_value.select.return_value = [(mock.Mock(), 1)] if readable else []
                with mock.patch.object(tool, 'apply_identity', side_effect=[self.baseline['shim'], tool.ApplyError('process_identity_unknown')]), \
                     mock.patch.object(os, 'pidfd_open', return_value=77, create=True), \
                     mock.patch.object(signal, 'pidfd_send_signal', create=True) as send, \
                     mock.patch.object(tool.selectors, 'DefaultSelector', return_value=selector), \
                     mock.patch.object(os, 'close'):
                    if readable: tool.apply_stop(self.baseline['shim'])
                    else:
                        with self.assertRaises(tool.ApplyError): tool.apply_stop(self.baseline['shim'])
                    send.assert_not_called()

    def test_interpreter_deleted_or_nonexecutable_refused(self):
        for executable in ('/python (deleted)', str(self.directory / 'dots-ts')):
            with self.assertRaisesRegex(tool.ApplyError, 'shim_interpreter_unavailable'):
                tool.apply_interpreter({'exe': executable})
        tool.apply_interpreter({'exe': sys.executable})

    def test_ssl_environment_paths_preserved_only_in_memory(self):
        env = b'UPSTREAM_HOST=fixture\0SSL_CERT_FILE=/certs/bundle.pem\0SSL_CERT_DIR=/certs/ca\0HOME=/private\0TOKEN=secret\0'
        with mock.patch.object(tool, 'apply_identity', return_value=self.baseline['shim']), \
             mock.patch.object(Path, 'read_bytes', return_value=env):
            result = tool.apply_environment(self.baseline['shim'])
        self.assertEqual(result['SSL_CERT_FILE'], '/certs/bundle.pem')
        self.assertEqual(result['SSL_CERT_DIR'], '/certs/ca')
        self.assertNotIn('HOME', result); self.assertNotIn('TOKEN', result)

    def test_shim_connection_budget_survives_update_and_bad_values_refuse(self):
        for value in ('1', '32', '64', '256', None):
            env = b'UPSTREAM_HOST=fixture\0'
            if value is not None:
                env += b'SHIM_MAX_CONNECTIONS=' + value.encode() + b'\0'
            with mock.patch.object(tool, 'apply_identity', return_value=self.baseline['shim']), \
                    mock.patch.object(Path, 'read_bytes', return_value=env):
                self.assertEqual(tool.apply_environment(self.baseline['shim'])['SHIM_MAX_CONNECTIONS'], value or '64')
        for value in ('0', '257', '01', '-1', 'unlimited'):
            env = b'SHIM_MAX_CONNECTIONS=' + value.encode() + b'\0'
            with mock.patch.object(tool, 'apply_identity', return_value=self.baseline['shim']), \
                    mock.patch.object(Path, 'read_bytes', return_value=env), \
                    self.assertRaisesRegex(tool.ApplyError, 'unsupported_shim_environment'):
                tool.apply_environment(self.baseline['shim'])

    def test_apply_public_errors_static_and_transaction_id_retained(self):
        class ProxyError(RuntimeError): pass
        proxy = mock.Mock(ProxyError=ProxyError)
        cases = [(ProxyError('PRIVATE lock'), 'operation_busy_or_unsafe'),
                 (OSError('PRIVATE filesystem'), 'filesystem_or_process_error'),
                 (KeyError('PRIVATE manifest'), 'invalid_apply_data')]
        for error, expected in cases:
            def failed(*args):
                args[-1]['transaction_id'] = 'a' * 32
                raise error
            with mock.patch.dict(sys.modules, {'dots_proxy': proxy}), \
                 mock.patch.object(tool, '_update_apply', side_effect=failed):
                with self.assertRaises(tool.ApplyError) as caught:
                    tool.update_apply(self.directory, self.root, confirm=True, allow_unsigned_legacy=True)
                self.assertEqual(tool.apply_error_report(caught.exception),
                                 {'error': expected, 'transaction_id': 'a' * 32})

    def test_unpersisted_outcome_marker_prevents_terminal_success_claim(self):
        root = self.directory / tool.APPLY_DIRECTORY; root.mkdir(mode=0o700)
        identifier = 'a' * 32; transaction = root / identifier; transaction.mkdir(mode=0o700)
        tool.apply_journal(transaction, {'schema': 1, 'state': 'applied'})
        (transaction / 'worker.log').write_text('update_outcome=failed_rolled_back journal_persisted=false\n')
        result = tool.apply_status(self.directory, identifier)
        self.assertEqual(result['state'], 'interrupted_manual_required')
        self.assertEqual(result['observed_outcome'], 'failed_rolled_back')
        self.assertTrue(result['journal_persistence_failed'])

    def test_public_status_oserror_is_json_without_private_detail(self):
        output = io.StringIO()
        with mock.patch.object(tool, 'directories', return_value=(self.directory, self.root)), \
             mock.patch.object(tool, 'apply_status', side_effect=OSError('PRIVATE path')), \
             contextlib.redirect_stdout(output):
            code = tool.main(['update', 'status', '--id', 'a' * 32, '--json'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue()), {'error': 'filesystem_or_process_error'})

    def test_terminal_worker_ack_overrides_worker_identity(self):
        proxy = mock.Mock(); proxy._operation.return_value = contextlib.nullcontext()
        def spawn(argv, **kwargs):
            transaction = Path(argv[-1]); record = tool.apply_read_journal(transaction)
            tool.apply_journal(transaction, record, state='rejected', worker=self.candidate)
            return mock.Mock()
        with mock.patch.dict(sys.modules, {'dots_proxy': proxy}), \
             mock.patch.object(tool.sys, 'platform', 'linux'), \
             mock.patch.object(tool.os, 'pidfd_open', create=True), \
             mock.patch.object(signal, 'pidfd_send_signal', create=True), \
             mock.patch.object(tool, 'apply_bundle', return_value=self.old), \
             mock.patch.object(tool, 'apply_syntax'), \
             mock.patch.object(tool, 'apply_boot', return_value='boot'), \
             mock.patch.object(subprocess, 'Popen', side_effect=spawn):
            result = tool.update_apply(self.directory, self.root, bundle=self.transaction / 'stage', confirm=True, allow_unsigned_legacy=True)
        self.assertEqual(result['state'], 'rejected')
        self.assertEqual(result['external_ssh'], 'pending')

    def test_real_owned_child_cleanup_during_immediate_exit(self):
        child = subprocess.Popen([sys.executable, '-c', 'raise SystemExit(1)'])
        try:
            tool.apply_stop_child(child, timeout=2)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None: child.terminate(); child.wait(timeout=2)

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
                tool.update_apply(self.directory, self.root, tag='v0.4.0', confirm=True, allow_unsigned_legacy=True)
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

    def test_expired_readiness_cannot_report_success(self):
        identities = {item['pid']: item for item in list(self.baseline.values()) + [self.candidate]}
        def slow_backend(*args):
            time.sleep(.02)
            return 'same'
        self.proxy.inspect_config.return_value = {}
        self.proxy._listener_owned.return_value = True
        with mock.patch.object(tool, 'apply_identity', side_effect=lambda pid: identities[pid]), \
             mock.patch.object(tool, 'apply_public_identity', side_effect=slow_backend), \
             mock.patch.object(tool.socket, 'create_connection') as connect:
            with self.assertRaisesRegex(tool.ApplyError, 'readiness_timeout'):
                tool.apply_ready(self.directory, self.root, self.proxy, self.baseline, self.candidate, 'same', {}, b'public-key', timeout=.01)
            connect.assert_not_called()

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
