"""Isolated local recovery policy; no real startup, daemon or network changes."""
import argparse
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('dots_ts_recovery_test', str(ROOT / 'dots-ts'))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)
ORIGINAL_PROBE = tool.recovery_probe
ORIGINAL_CAPTURE = tool.recovery_capture
REAL_POPEN = subprocess.Popen


class FakeProcess:
    pid = 900001

    def __init__(self, code=0, timeout=False, entered=None, release=None):
        self.code = code
        self.timeout = timeout
        self.killed = False
        self.entered = entered
        self.release = release

    def wait(self, timeout=None):
        if self.entered:
            self.entered.set()
            if not self.release.wait(3):
                raise AssertionError('fixture process not released')
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired('fixture start', timeout)
        return -9 if self.killed else self.code

    def kill(self):
        self.killed = True


class RecoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='recovery-lifecycle-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / 'install'
        self.directory.mkdir(mode=0o700)
        self.runtime = self.directory.parent / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.now = 200000
        self.boot = 'boot-a'
        self.identity = {'pid': os.getpid(), 'ticks': '1', 'uid': os.getuid(), 'exe': sys.executable}
        self.child_identity = {'pid': FakeProcess.pid, 'ticks': '2', 'uid': os.getuid(), 'exe': '/bin/bash'}
        self.child_alive = False
        for name in ('shim', 'state', 'sshd'):
            (self.directory / name).mkdir(mode=0o700)
        manifest = {'schema_version': 1, 'package_version': '0.8.0-dev', 'files': {}}
        for source, installed in tool.CORE_FILES.items():
            data = (ROOT / source).read_bytes()
            self.write(installed, data, 0o700)
            manifest['files'][source] = hashlib.sha256(data).hexdigest()
        self.write(tool.MANIFEST, json.dumps(manifest).encode())
        self.write('start.sh', tool.RECOVERY_START, 0o700)
        for name in ('installed', 'state/tailscaled.state', 'shim/ca.crt', 'shim/ca.key',
                     'shim/bundle.crt', 'sshd/sshd_config', 'sshd/ssh_host_ed25519_key', 'sshd/authorized_keys'):
            self.write(name, ('fixture ' + name).encode())
        self.write('authenticated', b'')
        for name in ('tailscale', 'tailscaled'):
            self.write(name, b'fixture executable; never run', 0o700)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(tool.time, 'time', lambda: self.now))
        self.stack.enter_context(mock.patch.object(tool, 'recovery_boot', lambda: self.boot))
        self.stack.enter_context(mock.patch.object(tool, 'recovery_identity', self.current_identity))
        self.probe = self.stack.enter_context(mock.patch.object(tool, 'recovery_probe', return_value={
            'state': 'healthy', 'backend': 'Running', 'ssh': 'banner_ready'}))
        self.popen = self.stack.enter_context(mock.patch.object(tool.subprocess, 'Popen', return_value=FakeProcess()))

    def write(self, name, data, mode=0o600):
        path = self.directory / name
        path.write_bytes(data)
        path.chmod(mode)

    def current_identity(self, pid):
        if pid == os.getpid():
            return self.identity
        return self.child_identity if pid == FakeProcess.pid and self.child_alive else None

    def configure(self, **changes):
        options = argparse.Namespace(enabled=True, max_attempts=None, cooldown_seconds=None,
                                     timeout_seconds=None, interval_seconds=None, restart_after_external=None)
        for key, value in changes.items():
            setattr(options, key, value)
        return tool.recovery_configure(self.directory, self.runtime, options)

    def state(self):
        return tool.recovery_read(tool.recovery_folder(self.directory))

    def save(self, state):
        tool.recovery_write(tool.recovery_folder(self.directory), state)

    def options(self, actor='external', identifier='request-1', **changes):
        result = argparse.Namespace(actor=actor, request_id=identifier,
                                    claim_expires_at=self.now + 120 if actor == 'external' else None,
                                    observation_time=self.now if actor == 'external' else None,
                                    ssh_failed=actor == 'external', ping_failed=actor == 'external')
        for key, value in changes.items():
            setattr(result, key, value)
        return result

    def missing_then_healthy(self):
        self.probe.side_effect = [{'state': 'components_missing', 'backend': 'unavailable', 'ssh': 'unavailable'},
                                  {'state': 'healthy', 'backend': 'Running', 'ssh': 'banner_ready'}]

    def assert_rejected(self, code, call):
        with self.assertRaisesRegex(tool.RecoveryError, '^' + code + '$'):
            call()

    def test_default_status_is_read_only_and_recovery_is_not_configured(self):
        before = set(self.directory.iterdir())
        report = tool.recovery_status(self.directory, self.runtime)
        self.assertEqual(report['state'], 'not_configured')
        self.assertFalse(report['enabled'])
        self.assertEqual(before, set(self.directory.iterdir()))
        self.popen.assert_not_called()

    def test_supported_start_is_the_installer_generated_wrapper(self):
        installer = (ROOT / 'tailscale-setup.sh').read_bytes()
        match = re.search(rb"cat > \"\$tmp/start.sh\" <<'START'\n(.*?)\nSTART\n", installer, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(tool.RECOVERY_START, match[1] + b'\n')

    def test_configure_is_explicit_and_failed_optin_leaves_disabled_state(self):
        (self.directory / 'state/tailscaled.state').unlink()
        with self.assertRaises(FileNotFoundError):
            self.configure()
        self.assertFalse(self.state()['enabled'])
        self.write('state/tailscaled.state', b'fixture state')
        report = self.configure()
        self.assertTrue(report['enabled'])
        self.assertFalse(report['watchdog']['wanted'])
        self.popen.assert_not_called()
        for key, value in (('max_attempts', 13), ('cooldown_seconds', 59),
                           ('timeout_seconds', 121), ('interval_seconds', 301)):
            with self.subTest(key=key):
                self.assert_rejected('invalid_recovery_state', lambda: self.configure(**{key: value}))
        self.assertEqual(self.state()['max_attempts_24h'], 3)

    def test_existing_start_only_and_result_is_not_external_recovery(self):
        self.configure()
        self.missing_then_healthy()
        report = tool.recovery_run(self.directory, self.runtime, self.options())
        self.assertEqual(report['state'], 'local_ready')
        self.assertEqual(report['external_confirmation'], 'pending')
        self.assertTrue(report['local_result_only'])
        self.assertEqual(report['external_claim_validation'], 'caller_asserted_structure_and_freshness_only')
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], ['/bin/bash', str(self.directory / 'start.sh')])
        self.assertTrue(kwargs['close_fds'])
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertNotIn('PYTHONPATH', kwargs['env'])
        self.assertEqual(len(self.state()['attempts']), 1)

    def test_delayed_external_event_skips_once_and_healthy_watchdog_has_no_ledger_growth(self):
        self.configure()
        state = self.state()
        state['watchdog_wanted'] = True
        self.save(state)
        report = tool.recovery_run(self.directory, self.runtime, self.options())
        self.assertEqual(report['state'], 'skipped_healthy')
        for _ in range(3):
            result = tool.recovery_run(self.directory, self.runtime, self.options('watchdog', 'healthy-tick'))
            self.assertEqual(result['state'], 'healthy')
        self.assertEqual(len(self.state()['attempts']), 1)
        self.assertFalse(self.state()['attempts'][0]['counted'])
        self.popen.assert_not_called()

    def test_duplicate_claim_and_actor_conflict_never_execute_again(self):
        self.configure()
        self.missing_then_healthy()
        first = tool.recovery_run(self.directory, self.runtime, self.options())
        self.now += 3601
        replay = tool.recovery_run(self.directory, self.runtime, self.options(claim_expires_at=1))
        self.assertTrue(replay['replayed'])
        self.assertEqual(first['created_at'], replay['created_at'])
        self.assertEqual(self.popen.call_count, 1)
        self.assert_rejected('request_id_conflict', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options('watchdog')))

    def test_expired_future_malformed_and_partial_external_claims_are_rejected(self):
        self.configure()
        for changes in ({'claim_expires_at': self.now}, {'claim_expires_at': self.now + 121},
                        {'observation_time': self.now - 151}, {'observation_time': self.now + 1},
                        {'claim_expires_at': None}, {'ping_failed': False}, {'ssh_failed': False},
                        {'request_id': '../outside'}):
            with self.subTest(changes=changes):
                code = 'invalid_request_id' if 'request_id' in changes else 'invalid_or_stale_external_claim'
                self.assert_rejected(code, lambda: tool.recovery_run(self.directory, self.runtime, self.options(**changes)))
        self.assertEqual(self.state()['attempts'], [])
        self.popen.assert_not_called()

    def test_external_freshness_is_rechecked_after_local_probe(self):
        self.configure()
        def delayed_probe(*args):
            self.now += 121
            return {'state': 'components_missing'}
        self.probe.side_effect = delayed_probe
        self.assert_rejected('invalid_or_stale_external_claim', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options()))
        self.popen.assert_not_called()
        self.assertEqual(self.state()['attempts'], [])

    def test_shared_cooldown_and_rolling_budget_persist_across_calls_and_actors(self):
        self.configure(max_attempts=2, cooldown_seconds=60)
        state = self.state()
        state['watchdog_wanted'] = True
        self.save(state)
        self.missing_then_healthy()
        tool.recovery_run(self.directory, self.runtime, self.options('watchdog', 'internal-1'))
        self.probe.side_effect = None
        self.probe.return_value = {'state': 'components_missing'}
        self.now += 59
        self.assert_rejected('shared_cooldown', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options(identifier='external-2')))
        self.now += 1
        self.missing_then_healthy()
        tool.recovery_run(self.directory, self.runtime, self.options(identifier='external-2'))
        self.now += 60
        self.probe.side_effect = None
        self.probe.return_value = {'state': 'components_missing'}
        self.assert_rejected('shared_daily_budget_exhausted', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options('watchdog', 'internal-3')))
        self.now += 86400
        self.missing_then_healthy()
        tool.recovery_run(self.directory, self.runtime, self.options('watchdog', 'internal-3'))
        self.assertEqual(len(self.state()['attempts']), 3)

    def test_stop_during_execution_is_preserved_and_prevents_later_external_start(self):
        self.configure(restart_after_external=True)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.popen.return_value = FakeProcess(entered=entered, release=release)
        self.missing_then_healthy()
        results = []
        errors = []
        def execute():
            try:
                results.append(tool.recovery_run(self.directory, self.runtime, self.options()))
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=execute)
        worker.start()
        self.addCleanup(lambda: worker.join(3))
        self.assertTrue(entered.wait(3))
        self.assert_rejected('recovery_busy', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options('watchdog', 'simultaneous')))
        tool.recovery_stop(self.directory)
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]['watchdog_restart'], 'not_requested_or_stopped')
        self.assertFalse(self.state()['enabled'])
        self.assertFalse(self.state()['watchdog_wanted'])
        self.assert_rejected('recovery_disabled', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options(identifier='later')))
        self.assertEqual(self.popen.call_count, 1)

    def test_root_operation_lock_is_checked_but_not_held_across_start(self):
        self.configure()
        self.missing_then_healthy()
        with tool.recovery_lock(self.directory, '.operation.lock'):
            self.assert_rejected('recovery_busy', lambda: tool.recovery_run(
                self.directory, self.runtime, self.options()))
        self.assertEqual(self.state()['attempts'], [])
        def launch(*args, **kwargs):
            with tool.recovery_lock(self.directory, '.operation.lock'):
                pass
            return FakeProcess()
        self.popen.side_effect = launch
        self.missing_then_healthy()
        self.assertEqual(tool.recovery_run(self.directory, self.runtime, self.options())['state'], 'local_ready')

    def test_timeout_fences_all_new_ids_until_explicit_reconciliation(self):
        self.configure()
        self.probe.return_value = {'state': 'components_missing'}
        self.popen.return_value = FakeProcess(timeout=True)
        report = tool.recovery_run(self.directory, self.runtime, self.options())
        self.assertEqual(report['state'], 'unknown')
        self.assertTrue(self.popen.return_value.killed)
        self.assert_rejected('unknown_result_requires_reconciliation', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options(identifier='new-id')))
        reconcile = argparse.Namespace(confirm_reviewed=False, request_id='request-1', outcome='failed')
        self.assert_rejected('explicit_review_required', lambda: tool.recovery_reconcile(self.directory, self.runtime, reconcile))
        reconcile.confirm_reviewed = True
        self.assertTrue(tool.recovery_reconcile(self.directory, self.runtime, reconcile)['reconciled'])
        self.now += 3600
        self.popen.return_value = FakeProcess()
        self.missing_then_healthy()
        self.assertEqual(tool.recovery_run(self.directory, self.runtime, self.options(identifier='new-id'))['state'], 'local_ready')

    def test_crashed_executor_and_new_boot_do_not_clear_uncertain_attempt(self):
        self.configure()
        self.missing_then_healthy()
        tool.recovery_run(self.directory, self.runtime, self.options())
        state = self.state()
        state['attempts'][0].update(state='running', finished_at=None)
        self.save(state)
        self.boot = 'boot-b'
        self.assertTrue(tool.recovery_status(self.directory, self.runtime)['manual_review_required'])
        self.assert_rejected('unknown_result_requires_reconciliation', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options(identifier='another-claim')))
        options = argparse.Namespace(confirm_reviewed=True, request_id='request-1', outcome='not_executed')
        report = tool.recovery_reconcile(self.directory, self.runtime, options)
        self.assertEqual(report['state'], 'not_executed')
        self.assertEqual(report['external_confirmation'], 'pending')

    def test_running_child_blocks_manual_reconciliation(self):
        self.configure()
        self.child_alive = True
        self.probe.return_value = {'state': 'components_missing'}
        self.popen.return_value = FakeProcess(timeout=True)
        tool.recovery_run(self.directory, self.runtime, self.options())
        options = argparse.Namespace(confirm_reviewed=True, request_id='request-1', outcome='failed')
        self.assert_rejected('attempt_process_not_proven_exited', lambda: tool.recovery_reconcile(
            self.directory, self.runtime, options))

    def test_clock_rollback_ledger_full_and_corruption_fail_closed(self):
        self.configure()
        self.now -= 1
        self.assert_rejected('clock_rollback_manual_review', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options()))
        self.now += 1
        self.missing_then_healthy()
        tool.recovery_run(self.directory, self.runtime, self.options())
        state = self.state()
        row = state['attempts'][0]
        state['attempts'] = [dict(row, id='historical-' + str(i), created_at=1,
                                  finished_at=2, state='failed') for i in range(tool.RECOVERY_MAX_RECORDS)]
        self.save(state)
        self.probe.side_effect = None
        self.probe.return_value = {'state': 'components_missing'}
        self.assert_rejected('recovery_ledger_full', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options(identifier='overflow')))
        root = tool.recovery_folder(self.directory)
        (root / 'state.json').write_text('{"schema":1,"schema":1}')
        self.assertEqual(tool.recovery_update_fence(self.directory)['state'], 'unknown')
        self.assert_rejected('invalid_recovery_state', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options(identifier='corrupt')))

    def test_missing_authentication_integrity_changes_and_symlinks_never_launch(self):
        self.configure()
        self.probe.return_value = {'state': 'components_missing'}
        for relative in ('state/tailscaled.state', 'sshd/ssh_host_ed25519_key', 'shim/ca.key', 'authenticated'):
            with self.subTest(missing=relative):
                path = self.directory / relative
                data = path.read_bytes()
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    tool.recovery_run(self.directory, self.runtime, self.options())
                self.write(relative, data)
        self.write('start.sh', tool.RECOVERY_START + b'echo changed\n', 0o700)
        self.assert_rejected('unsupported_start_script', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options()))
        self.write('start.sh', tool.RECOVERY_START, 0o700)
        self.write('dots_proxy.py', b'modified source', 0o700)
        self.assert_rejected('untrusted_or_mixed_package', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options()))
        self.write('dots_proxy.py', (ROOT / 'dots_proxy.py').read_bytes(), 0o700)
        path = self.directory / 'sshd/ssh_host_ed25519_key'
        path.rename(path.with_name('actual-key'))
        path.symlink_to(path.with_name('actual-key'))
        self.assert_rejected('unsafe_recovery_file', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options()))
        self.popen.assert_not_called()

    def test_hardlinks_and_writable_permissions_are_refused_without_mutating_identity(self):
        self.configure()
        path = self.directory / 'state/tailscaled.state'
        content = path.read_bytes()
        os.link(path, path.with_name('state-alias'))
        self.assert_rejected('unsafe_recovery_file', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options()))
        path.with_name('state-alias').unlink()
        path.chmod(0o644)
        self.assert_rejected('unsafe_recovery_file', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options()))
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.popen.assert_not_called()

    def test_automatic_update_gap_and_recovery_guard_are_fail_closed(self):
        with tool.recovery_update_guard(self.directory):
            pass
        self.assertFalse((self.directory / tool.RECOVERY_DIRECTORY).exists())
        self.configure()
        with tool.recovery_update_guard(self.directory):
            self.assert_rejected('recovery_busy', lambda: tool.recovery_run(
                self.directory, self.runtime, self.options()))
        self.probe.return_value = {'state': 'components_missing'}
        (self.directory / '.automatic-updates').mkdir(mode=0o700)
        self.assert_rejected('automatic_update_state_unknown', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options()))
        with mock.patch.object(tool, 'automatic_recovery_fence', return_value={'state': 'in_progress', 'recovery_allowed': False}, create=True):
            self.assert_rejected('automatic_update_blocks_recovery', lambda: tool.recovery_run(
                self.directory, self.runtime, self.options()))
        for invalid in ({'recovery_allowed': False}, {'state': 'idle', 'recovery_allowed': 1},
                        {'state': 'in_progress', 'recovery_allowed': True}):
            with mock.patch.object(tool, 'automatic_recovery_fence', return_value=invalid):
                self.assert_rejected('automatic_update_state_unknown', lambda: tool.recovery_run(
                    self.directory, self.runtime, self.options()))
        tool.recovery_stop(self.directory)
        self.assertFalse(tool.recovery_update_fence(self.directory)['update_allowed'])
        self.assert_rejected('recovery_blocks_update', lambda: tool.recovery_update_guard(self.directory).__enter__())

    def test_watchdog_start_deduplicates_same_boot_and_checks_new_boot(self):
        self.configure(restart_after_external=True)
        self.child_alive = True
        first = tool.recovery_watchdog_start(self.directory, self.runtime)
        self.assertEqual(first['state'], 'started')
        self.assertEqual(tool.recovery_watchdog_start(self.directory, self.runtime)['state'], 'already_running')
        self.assertEqual(self.popen.call_count, 1)
        self.boot = 'boot-b'
        self.assertEqual(tool.recovery_status(self.directory, self.runtime)['watchdog']['status'], 'previous_boot')
        (self.directory / 'authenticated').unlink()
        with self.assertRaises(FileNotFoundError):
            tool.recovery_watchdog_start(self.directory, self.runtime, explicit=False)
        self.assertEqual(self.popen.call_count, 1)
        self.write('authenticated', b'')
        self.assertEqual(tool.recovery_watchdog_start(self.directory, self.runtime, explicit=False)['state'], 'started')
        self.assertEqual(self.popen.call_count, 2)

    def test_explicit_watchdog_stop_is_not_revoked_by_external_recovery(self):
        self.configure(restart_after_external=True)
        state = self.state()
        state['watchdog_wanted'] = True
        self.save(state)
        tool.recovery_watchdog_stop(self.directory)
        self.missing_then_healthy()
        report = tool.recovery_run(self.directory, self.runtime, self.options())
        self.assertEqual(report['watchdog_restart'], 'not_requested_or_stopped')
        self.assertFalse(self.state()['watchdog_wanted'])
        self.assertTrue(self.state()['enabled'])
        self.assertEqual(self.popen.call_count, 1)

    def test_watchdog_worker_preserves_stop_and_exits_when_package_code_changes(self):
        self.configure()
        state = self.state()
        state['watchdog_wanted'] = True
        state['watchdog'] = {'id': 'watchdog-fixture', 'boot_id': self.boot, 'identity': self.identity,
                             'state': 'running', 'code_hash': hashlib.sha256((self.directory / 'dots-ts').read_bytes()).hexdigest()}
        self.save(state)
        def stop_after_tick(_):
            tool.recovery_watchdog_stop(self.directory)
        with mock.patch.object(tool.time, 'sleep', side_effect=stop_after_tick):
            self.assertEqual(tool.recovery_watchdog_worker(self.directory, self.runtime, 'watchdog-fixture'), 0)
        self.assertEqual(self.state()['watchdog']['state'], 'stopped')
        self.assertFalse(self.state()['watchdog_wanted'])
        state = self.state()
        state['watchdog_wanted'] = True
        state['watchdog']['state'] = 'running'
        self.save(state)
        self.write('dots-ts', (self.directory / 'dots-ts').read_bytes() + b'\n# changed\n', 0o700)
        self.assertEqual(tool.recovery_watchdog_worker(self.directory, self.runtime, 'watchdog-fixture'), 0)
        self.assertEqual(self.state()['watchdog']['state'], 'code_changed')
        self.assertTrue(self.state()['watchdog_wanted'])
        self.popen.assert_not_called()

    def test_recovery_dispatch_uses_json_errors_without_raw_data(self):
        stdout = io.StringIO()
        with mock.patch.object(tool, 'directories', return_value=(self.directory, self.runtime)), contextlib.redirect_stdout(stdout):
            code = tool.main(['recovery', 'run', '--actor', 'external', '--request-id', 'PRIVATE\nVALUE', '--json'])
        self.assertEqual(code, 1)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report['error'], 'invalid_request_id')
        self.assertNotIn('PRIVATE', stdout.getvalue())
        self.popen.assert_not_called()

    def test_journal_failure_after_process_start_stays_uncertain_and_is_not_retried(self):
        self.configure()
        original_write = tool.recovery_write
        for fail_at in (2, 3):
            with self.subTest(fail_at=fail_at):
                state = self.state()
                state['attempts'] = []
                self.save(state)
                self.missing_then_healthy()
                self.popen.reset_mock()
                calls = []
                def write(folder, value):
                    calls.append(True)
                    if len(calls) == fail_at:
                        raise OSError('PRIVATE failure detail')
                    original_write(folder, value)
                with mock.patch.object(tool, 'recovery_write', side_effect=write):
                    self.assert_rejected('execution_or_journal_uncertain', lambda: tool.recovery_run(
                        self.directory, self.runtime, self.options()))
                self.assertEqual(self.state()['attempts'][0]['state'], 'running')
                self.assert_rejected('unknown_result_requires_reconciliation', lambda: tool.recovery_run(
                    self.directory, self.runtime, self.options(identifier='new-after-error')))
                self.assertEqual(self.popen.call_count, 1)

    def test_watchdog_restart_failure_does_not_erase_successful_start_result(self):
        self.configure(restart_after_external=True)
        state = self.state()
        state['watchdog_wanted'] = True
        self.save(state)
        self.missing_then_healthy()
        with mock.patch.object(tool, 'recovery_watchdog_start', side_effect=tool.RecoveryError('watchdog_start_uncertain')):
            report = tool.recovery_run(self.directory, self.runtime, self.options())
        self.assertEqual(report['state'], 'local_ready')
        self.assertEqual(report['watchdog_restart'], 'unavailable_manual_review')
        self.assertEqual(self.state()['attempts'][0]['state'], 'local_ready')
        self.assertEqual(self.popen.call_count, 1)

    def test_explicit_watchdog_restart_resumes_existing_same_boot_process(self):
        self.configure()
        self.child_alive = True
        tool.recovery_watchdog_start(self.directory, self.runtime)
        with tool.recovery_lock(tool.recovery_folder(self.directory), 'watchdog.lock'):
            tool.recovery_watchdog_stop(self.directory)
            self.assertFalse(self.state()['watchdog_wanted'])
            self.assertEqual(tool.recovery_watchdog_start(self.directory, self.runtime)['state'], 'already_running')
        self.assertTrue(self.state()['watchdog_wanted'])
        self.assertEqual(self.popen.call_count, 1)

    def test_cancelled_uncertain_watchdog_launch_can_be_explicitly_started(self):
        self.configure()
        with self.assertRaisesRegex(tool.RecoveryError, 'watchdog_start_uncertain'):
            tool.recovery_watchdog_start(self.directory, self.runtime)
        self.assertEqual(self.state()['watchdog']['state'], 'starting')
        tool.recovery_watchdog_stop(self.directory)
        self.assertEqual(self.state()['watchdog']['state'], 'stopped')
        self.child_alive = True
        self.assertEqual(tool.recovery_watchdog_start(self.directory, self.runtime)['state'], 'started')

    def test_real_probe_classifies_auth_unknown_and_ssh_only_without_external_claims(self):
        self.configure()
        healthy = {'shim': 'running', 'tailscaled': 'running', 'sshd': 'running'}
        with mock.patch.object(tool, 'recovery_probe', ORIGINAL_PROBE), \
                mock.patch.object(tool, 'process_checks', return_value=healthy) as processes, \
                mock.patch.object(tool, 'socket_status', return_value='present'), \
                mock.patch.object(tool, 'recovery_capture', return_value={'BackendState': 'NeedsLogin'}) as capture:
            self.assertEqual(tool.recovery_probe(self.directory, self.runtime)['state'], 'authentication_required')
            capture.return_value = {'BackendState': 'Running'}
            processes.return_value = dict(healthy, sshd='not_running')
            self.assertEqual(tool.recovery_probe(self.directory, self.runtime)['state'], 'ssh_only')
            processes.return_value = dict(healthy, sshd='unknown')
            self.assertEqual(tool.recovery_probe(self.directory, self.runtime)['state'], 'unknown')
            processes.return_value = healthy
            with mock.patch.object(tool.socket, 'create_connection') as connect:
                connect.return_value.__enter__.return_value.recv.return_value = b'SSH-2.0-fixture\r\n'
                report = tool.recovery_probe(self.directory, self.runtime)
                self.assertEqual(report['state'], 'healthy')
                self.assertEqual(report['ssh'], 'banner_ready')
                connect.assert_called_once_with(('127.0.0.1', 22), timeout=2)

    def test_authentication_required_and_uncertain_probe_do_not_execute(self):
        self.configure()
        for value, error in (('authentication_required', 'authentication_requires_manual_review'),
                             ('unknown', 'local_probe_unknown')):
            self.probe.return_value = {'state': value}
            self.assert_rejected(error, lambda: tool.recovery_run(self.directory, self.runtime, self.options()))
        self.popen.assert_not_called()

    def test_capture_has_output_and_execution_bounds(self):
        with mock.patch.object(tool.subprocess, 'Popen', REAL_POPEN), \
                mock.patch.object(tool, 'MANAGEMENT_TIMEOUT', .1):
            self.assertEqual(ORIGINAL_CAPTURE([sys.executable, '-c', 'print("{}")']), {})
            for program in ('print("x" * 65537)', 'import time; time.sleep(5)', 'print("invalid json")'):
                with self.subTest(program=program):
                    self.assert_rejected('local_probe_unknown', lambda: ORIGINAL_CAPTURE([sys.executable, '-c', program]))

    def test_nonzero_start_result_is_failed_and_still_consumes_shared_budget(self):
        self.configure()
        self.probe.return_value = {'state': 'components_missing'}
        self.popen.return_value = FakeProcess(code=17)
        report = tool.recovery_run(self.directory, self.runtime, self.options())
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['exit_code'], 17)
        self.assertEqual(tool.recovery_status(self.directory, self.runtime)['policy']['attempts_in_window'], 1)
        self.assert_rejected('shared_cooldown', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options(identifier='another-id')))
        self.assertEqual(self.popen.call_count, 1)

    def test_known_healthy_probe_does_not_silently_reconcile_an_unknown_result(self):
        self.configure()
        self.probe.return_value = {'state': 'components_missing'}
        self.popen.return_value = FakeProcess(timeout=True)
        tool.recovery_run(self.directory, self.runtime, self.options())
        self.probe.return_value = {'state': 'healthy'}
        self.assert_rejected('unknown_result_requires_reconciliation', lambda: tool.recovery_run(
            self.directory, self.runtime, self.options(identifier='healthy-new-id')))
        self.assertTrue(tool.recovery_status(self.directory, self.runtime)['manual_review_required'])
        self.assertEqual(self.popen.call_count, 1)


if __name__ == '__main__':
    unittest.main()
