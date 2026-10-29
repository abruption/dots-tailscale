"""Automatic source updates: explicit scheduling and durable cancellation fences."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('dots_ts_auto_test', str(ROOT / 'dots-ts'))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


class AutomaticUpdateTests(unittest.TestCase):
    NOW = 1793239200  # 2026-10-29 02:00:00 UTC
    TRANSACTION = 'b' * 32

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='automatic-updates-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / 'installation'
        self.directory.mkdir(mode=0o700)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.identity = {'pid': 123, 'ticks': '456', 'uid': os.getuid(), 'exe': '/usr/bin/python3'}
        self.journals = {}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(tool.time, 'time', return_value=self.NOW))
        self.stack.enter_context(mock.patch.object(tool, 'apply_boot', return_value='boot-fixture'))
        self.stack.enter_context(mock.patch.object(tool, 'apply_identity', return_value=self.identity))
        self.real_preflight = tool.automatic_preflight
        self.stack.enter_context(mock.patch.object(tool, 'package_report', return_value={'integrity': 'match', 'declared_version': '0.8.0'}))
        self.preflight = self.stack.enter_context(mock.patch.object(tool, 'automatic_preflight'))
        self.guard = self.stack.enter_context(mock.patch.object(tool, 'automatic_recovery_guard', side_effect=lambda d: contextlib.nullcontext()))
        self.check = self.stack.enter_context(mock.patch.object(tool, 'update_check', return_value={
            'status': 'update_available', 'release': {'tag': 'v0.8.1', 'version': '0.8.1'}, 'error': None}))
        self.apply = self.stack.enter_context(mock.patch.object(tool, 'update_apply', side_effect=self.submit))
        self.stack.enter_context(mock.patch.object(tool, 'apply_status', side_effect=lambda d, i: self.journals[i]))

    def options(self, **changes):
        values = {'confirm_recovery_terminal': True, 'interval_hours': None, 'timezone': None, 'window': None,
                  'pin': None, 'unpin': False, 'proxy': None, 'direct': False,
                  'provenance_root': self.root / 'trusted-root.json'}
        values.update(changes)
        return types.SimpleNamespace(**values)

    def configure(self, command='enable', **changes):
        return tool.automatic_configure(self.directory, self.runtime, command, self.options(**changes))

    def submit(self, d, r, **kwargs):
        fence = kwargs['automatic']
        self.journals[self.TRANSACTION] = {'state': 'running', 'automatic': fence, 'selection': kwargs['tag']}
        tool.automatic_worker_commit(d, Path(self.TRANSACTION), self.journals[self.TRANSACTION])
        return {'state': 'started', 'transaction_id': self.TRANSACTION}

    def state(self):
        with tool.automatic_store(self.directory) as folder:
            settings = tool.automatic_settings(folder)
            return tool.automatic_state(folder, settings)

    def write_state(self, state):
        with tool.automatic_store(self.directory, create=True) as folder:
            tool.notice_write(folder, 'state.json', state)

    def test_default_disabled_without_storage_or_network_or_process_changes(self):
        self.assertFalse(tool.automatic_show(self.directory)['enabled'])
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime), {'state': 'disabled'})
        self.assertFalse((self.directory / tool.AUTOMATIC_DIRECTORY).exists())
        self.preflight.assert_not_called(); self.check.assert_not_called(); self.apply.assert_not_called()
        stream = io.StringIO()
        with mock.patch.object(tool, 'directories', return_value=(self.directory, self.runtime)), contextlib.redirect_stdout(stream):
            self.assertEqual(tool.main(['update', 'automatic', 'status', '--json']), 0)
        self.assertEqual(json.loads(stream.getvalue())['state'], 'disabled')

    def test_enable_requires_confirmation_and_supported_context(self):
        with self.assertRaisesRegex(tool.AutomaticError, 'confirmation_required'):
            self.configure(confirm_recovery_terminal=False)
        self.assertFalse(tool.automatic_show(self.directory)['enabled'])
        self.preflight.side_effect = tool.AutomaticError('execution_context_unavailable')
        with self.assertRaisesRegex(tool.AutomaticError, 'execution_context_unavailable'):
            self.configure()
        self.assertFalse(tool.automatic_show(self.directory)['enabled'])
        self.apply.assert_not_called()

    def test_enable_pin_interval_and_settings_persist_across_code_replacement(self):
        self.configure(interval_hours=8, pin='v0.8.1', timezone='Asia/Seoul', window='10:00-12:00')
        (self.directory / 'dots-ts').write_text('replacement source')
        report = tool.automatic_show(self.directory)
        self.assertTrue(report['enabled']); self.assertEqual(report['pin'], 'v0.8.1')
        self.assertEqual(report['interval_hours'], 8); self.assertEqual(report['timezone'], 'Asia/Seoul')
        self.assertEqual(report['scheduler'], 'external_caller_required')
        self.assertEqual(report['external_ssh'], 'not_verified')

    def test_validation_of_window_timezone_interval_and_pin(self):
        for changes in ({'interval_hours': 0}, {'interval_hours': 721}, {'window': '24:00-25:00'},
                        {'window': '02:00-02:00'}, {'timezone': '../../private'}, {'timezone': 'Not/AZone'},
                        {'pin': 'v0.8.1-rc.1'}, {'pin': '0.8.1'}, {'provenance_root': Path('relative')}):
            with self.subTest(changes=changes), self.assertRaises(tool.AutomaticError): self.configure(**changes)
        self.assertFalse(tool.automatic_show(self.directory)['enabled'])

    def test_window_dst_and_overnight_without_replaying_installs(self):
        import datetime
        settings = tool.automatic_defaults()
        settings.update(timezone='America/New_York', window_start=60, window_end=120)
        def epoch(text): return int(datetime.datetime.fromisoformat(text).timestamp())
        # The repeated 01:30 belongs to the window both times, but the persistent
        # epoch interval/attempt budget still permits only one installation.
        self.assertTrue(tool.automatic_window(settings, epoch('2026-11-01T05:30:00+00:00')))
        self.assertTrue(tool.automatic_window(settings, epoch('2026-11-01T06:30:00+00:00')))
        self.assertFalse(tool.automatic_window(settings, epoch('2026-11-01T07:30:00+00:00')))
        settings.update(window_start=120, window_end=180)
        self.assertFalse(tool.automatic_window(settings, epoch('2026-03-08T07:30:00+00:00')))
        settings.update(timezone='UTC', window_start=1380, window_end=60)
        self.assertTrue(tool.automatic_window(settings, epoch('2026-10-29T23:30:00+00:00')))
        self.assertTrue(tool.automatic_window(settings, epoch('2026-10-30T00:30:00+00:00')))
        self.assertFalse(tool.automatic_window(settings, epoch('2026-10-30T01:00:00+00:00')))

    def test_hold_disable_and_schedule_do_not_call_update_paths(self):
        self.configure()
        self.preflight.reset_mock()
        self.configure('hold')
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'held')
        self.preflight.assert_not_called()
        self.configure('unhold')
        with mock.patch.object(tool.time, 'time', return_value=self.NOW + 3 * 3600):
            self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'outside_window')
        self.configure('disable')
        self.assertFalse(tool.automatic_show(self.directory)['enabled'])
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'disabled')
        self.check.assert_not_called(); self.apply.assert_not_called()

    def test_tick_reuses_strict_manual_updater_and_pre_records_attempt(self):
        self.configure(pin='v0.8.1', proxy='http://browser-proxy:8889')
        report = tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(report['state'], 'in_progress')
        kwargs = self.apply.call_args.kwargs
        self.assertEqual(kwargs['tag'], 'v0.8.1')
        self.assertTrue(kwargs['confirm']); self.assertIs(kwargs['allow_unsigned_legacy'], False)
        self.assertEqual(kwargs['provenance_root'], str(self.root / 'trusted-root.json'))
        self.assertEqual(kwargs['proxy_url'], 'http://browser-proxy:8889')
        state = self.state()
        self.assertEqual(state['attempts'], [self.NOW])
        self.assertEqual(state['inflight']['phase'], 'execution_committed')
        self.assertFalse(tool.automatic_recovery_fence(self.directory)['recovery_allowed'])
        again = tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(again['state'], 'in_progress')
        self.assertEqual(self.apply.call_count, 1)

    def test_cancel_during_discovery_wins_before_apply_and_is_persistent(self):
        self.configure()
        def discover(*args):
            self.configure('disable')
            return {'status': 'update_available', 'release': {'tag': 'v0.8.1'}, 'error': None}
        self.check.side_effect = discover
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'configuration_changed')
        self.apply.assert_not_called()
        self.assertFalse(tool.automatic_show(self.directory)['enabled'])

    def test_hold_in_detached_gap_rejects_worker_before_mutation(self):
        self.configure()
        def prepare(d, r, **kwargs):
            self.assertEqual(self.state()['inflight']['phase'], 'preparing')
            self.configure('hold')
            journal = {'state': 'running', 'automatic': kwargs['automatic'], 'selection': kwargs['tag']}
            with self.assertRaisesRegex(tool.ApplyError, 'automatic_cancelled_before_commit'):
                tool.automatic_worker_commit(d, Path(self.TRANSACTION), journal)
            self.journals[self.TRANSACTION] = dict(journal, state='rejected')
            return {'state': 'rejected', 'transaction_id': self.TRANSACTION}
        self.apply.side_effect = prepare
        report = tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(report['status'], 'rejected')
        self.assertTrue(tool.automatic_show(self.directory)['held'])
        self.assertIsNone(self.state()['inflight'])

    def test_disable_after_commit_does_not_claim_to_cancel_committed_transaction(self):
        self.configure()
        def committed(d, r, **kwargs):
            result = self.submit(d, r, **kwargs)
            disabled = self.configure('disable')
            self.assertTrue(disabled['committed_transaction_may_finish'])
            self.journals[self.TRANSACTION]['state'] = 'applied'
            return result
        self.apply.side_effect = committed
        result = tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(result['status'], 'applied')
        self.assertFalse(tool.automatic_show(self.directory)['enabled'])
        self.assertIsNone(self.state()['inflight'])
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'disabled')
        self.assertEqual(self.apply.call_count, 1)

    def test_lost_ack_reconciles_same_transaction_without_resubmission(self):
        self.configure()
        def lose_ack(d, r, **kwargs):
            self.submit(d, r, **kwargs)
            raise tool.ApplyError('worker_acknowledgement_timeout_inspect_transaction', self.TRANSACTION)
        self.apply.side_effect = lose_ack
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'in_progress')
        self.journals[self.TRANSACTION]['state'] = 'applied'
        with mock.patch.object(tool.time, 'time', return_value=self.NOW + 5):
            report = tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(report['state'], 'waiting')
        self.assertEqual(self.state()['last_result']['status'], 'applied')
        self.assertIsNone(self.state()['inflight'])
        self.assertEqual(self.apply.call_count, 1)

    def test_unknown_launch_never_retries_even_after_disable_and_reenable(self):
        self.configure()
        self.apply.side_effect = RuntimeError('PRIVATE uncertain subprocess')
        report = tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(report['status'], 'manual_review_required')
        self.assertNotIn('PRIVATE', json.dumps(report))
        self.configure('disable'); self.configure()
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'manual_review_required')
        with self.assertRaisesRegex(tool.AutomaticError, 'manual_review_required'): self.configure('reset-failures')
        self.assertFalse(tool.automatic_recovery_fence(self.directory)['recovery_allowed'])
        self.assertEqual(self.apply.call_count, 1)

    def test_failed_target_held_and_daily_budget_survives_reset(self):
        self.configure()
        def reject(d, r, **kwargs): raise tool.ApplyError('binary_update_unsupported')
        self.apply.side_effect = reject
        report = tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(report['status'], 'rejected')
        self.assertEqual(self.state()['last_failed_tag'], 'v0.8.1')
        self.configure('reset-failures')
        self.assertEqual(len(self.state()['attempts']), 1)
        state = self.state(); state['attempts'] = [self.NOW - 120, self.NOW - 60, self.NOW]
        self.write_state(state)
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'budget_exhausted')
        self.assertEqual(self.apply.call_count, 1)

    def test_bounded_backoff_network_failure_and_no_legacy_or_downgrade(self):
        self.configure()
        self.check.return_value = {'status': 'check_failed', 'release': None, 'error': 'rate_limited'}
        report = tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(report['status'], 'check_failed'); self.assertEqual(report['error'], 'rate_limited')
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'waiting')
        self.assertEqual(self.check.call_count, 1); self.apply.assert_not_called()
        self.configure('reset-failures')
        self.check.return_value = {'status': 'update_available', 'release': {'tag': 'v0.7.1'}, 'error': None}
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['error'], 'unsigned_legacy_not_automatic')
        self.configure('reset-failures')
        self.check.return_value = {'status': 'local_newer', 'release': {'tag': 'v0.8.0'}, 'error': None}
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['status'], 'local_newer')
        self.apply.assert_not_called()

    def test_concurrent_manual_upgrade_cannot_turn_discovery_into_downgrade(self):
        self.configure()
        def upgrade_during_prepare(d, r, **kwargs):
            journal = {'state': 'running', 'automatic': kwargs['automatic'], 'selection': kwargs['tag']}
            with mock.patch.object(tool, 'package_report', return_value={'integrity': 'match', 'declared_version': '0.9.0'}):
                with self.assertRaisesRegex(tool.ApplyError, 'candidate_not_newer'):
                    tool.automatic_worker_commit(d, Path(self.TRANSACTION), journal)
            self.journals[self.TRANSACTION] = dict(journal, state='rejected')
            return {'state': 'rejected', 'transaction_id': self.TRANSACTION}
        self.apply.side_effect = upgrade_during_prepare
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['status'], 'rejected')

    def test_rate_limit_reset_time_is_respected_by_automatic_backoff(self):
        self.configure()
        self.check.return_value = {'status': 'check_failed', 'release': None, 'error': 'rate_limited',
                                   'retry': {'retry_at': self.NOW + 3600}}
        tool.automatic_tick(self.directory, self.runtime)
        self.assertEqual(self.state()['next_check_at'], self.NOW + 3600)

    def test_terminal_uncertainty_is_not_success_or_permission_to_retry(self):
        self.configure(); tool.automatic_tick(self.directory, self.runtime)
        for outcome in ('rollback_failed_manual_required', 'interrupted_manual_required'):
            self.journals[self.TRANSACTION]['state'] = outcome
            self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'manual_review_required')
            self.assertFalse(tool.automatic_recovery_fence(self.directory)['recovery_allowed'])
        self.assertEqual(self.apply.call_count, 1)

    def test_control_and_run_locks_reject_overlap_and_hostile_storage(self):
        self.configure()
        with tool.automatic_store(self.directory, create=True, runner=True):
            with self.assertRaisesRegex(tool.AutomaticError, 'operation_busy'):
                tool.automatic_tick(self.directory, self.runtime)
        path = self.directory / tool.AUTOMATIC_DIRECTORY / 'state.json'
        original = path.read_bytes()
        path.unlink(); path.symlink_to(self.root / 'not-a-state')
        self.assertEqual(tool.automatic_recovery_fence(self.directory)['state'], 'unknown')
        path.unlink(); path.write_bytes(original); path.chmod(0o600)
        os.link(path, path.with_name('extra-link'))
        self.assertEqual(tool.automatic_recovery_fence(self.directory)['state'], 'unknown')

    def test_worker_commit_checks_window_expiry_and_selection(self):
        self.configure()
        def reject(d, r, **kwargs):
            journal = {'state': 'running', 'automatic': kwargs['automatic'], 'selection': 'v0.8.2'}
            with self.assertRaisesRegex(tool.ApplyError, 'automatic_cancelled_before_commit'):
                tool.automatic_worker_commit(d, Path(self.TRANSACTION), journal)
            journal['selection'] = kwargs['tag']
            with mock.patch.object(tool.time, 'time', return_value=self.NOW + 301), self.assertRaisesRegex(tool.ApplyError, 'automatic_cancelled_before_commit'):
                tool.automatic_worker_commit(d, Path(self.TRANSACTION), journal)
            self.journals[self.TRANSACTION] = dict(journal, state='rejected')
            return {'state': 'rejected', 'transaction_id': self.TRANSACTION}
        self.apply.side_effect = reject
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['status'], 'rejected')

    def test_disable_does_not_require_the_configured_timezone_database(self):
        self.configure(timezone='America/New_York')
        with mock.patch.object(tool, 'automatic_zone', side_effect=tool.AutomaticError('timezone_unavailable')):
            self.configure('hold')
            self.assertEqual(self.configure('disable')['state'], 'disabled')
            self.assertFalse(tool.automatic_show(self.directory)['enabled'])

    def test_foreground_watch_is_explicit_bounded_and_never_installs_a_scheduler(self):
        for seconds in (0, 86401):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(tool.automatic_main(['watch', '--seconds', str(seconds), '--json'], self.directory, self.runtime), 1)
            self.assertEqual(json.loads(output.getvalue())['error'], 'invalid_watch_interval')
        output = io.StringIO()
        with mock.patch.object(tool.time, 'sleep', side_effect=AssertionError('disabled watcher must stop')), \
                mock.patch.object(tool.subprocess, 'Popen', side_effect=AssertionError('no service/scheduler')), \
                contextlib.redirect_stdout(output):
            self.assertEqual(tool.automatic_main(['watch', '--seconds', '1', '--poll-seconds', '30', '--json'], self.directory, self.runtime), 0)
        self.assertEqual(json.loads(output.getvalue())['state'], 'disabled')
        self.assertFalse((self.directory / tool.AUTOMATIC_DIRECTORY).exists())

    def test_caller_acknowledgement_can_precede_worker_execution_commit(self):
        self.configure()
        def acknowledged_first(directory, runtime, **kwargs):
            self.journals[self.TRANSACTION] = {'state': 'running', 'automatic': kwargs['automatic'], 'selection': kwargs['tag']}
            with tool.automatic_store(directory, create=True) as folder:
                settings = tool.automatic_settings(folder)
                state = tool.automatic_state(folder, settings)
                state['inflight'].update(phase='submitted', transaction_id=self.TRANSACTION)
                tool.notice_write(folder, 'state.json', state)
            tool.automatic_worker_commit(directory, Path(self.TRANSACTION), self.journals[self.TRANSACTION])
            self.assertEqual(self.state()['inflight']['phase'], 'execution_committed')
            return {'state': 'started', 'transaction_id': self.TRANSACTION}
        self.apply.side_effect = acknowledged_first
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'in_progress')
        self.assertEqual(self.apply.call_count, 1)

    def test_disable_still_wins_after_caller_ack_before_worker_commit(self):
        self.configure()
        def disabled_first(directory, runtime, **kwargs):
            journal = {'state': 'running', 'automatic': kwargs['automatic'], 'selection': kwargs['tag']}
            with tool.automatic_store(directory, create=True) as folder:
                settings = tool.automatic_settings(folder)
                state = tool.automatic_state(folder, settings)
                state['inflight'].update(phase='submitted', transaction_id=self.TRANSACTION)
                tool.notice_write(folder, 'state.json', state)
            self.configure('disable')
            with self.assertRaisesRegex(tool.ApplyError, 'automatic_cancelled_before_commit'):
                tool.automatic_worker_commit(directory, Path(self.TRANSACTION), journal)
            self.journals[self.TRANSACTION] = dict(journal, state='rejected')
            return {'state': 'rejected', 'transaction_id': self.TRANSACTION}
        self.apply.side_effect = disabled_first
        self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['status'], 'rejected')
        self.assertFalse(tool.automatic_show(self.directory)['enabled'])

    def test_result_notification_is_cache_only_and_deduplicated(self):
        class Interactive(io.StringIO):
            def isatty(self): return True
        self.configure()
        state = self.state()
        tool.automatic_result(state, self.NOW, 'applied', tag='v0.8.1', transaction_id=self.TRANSACTION)
        self.write_state(state)
        for expected in (True, False):
            stderr, stdout = Interactive(), io.StringIO()
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                self.assertEqual(tool.automatic_main(['notify', '--json'], self.directory, self.runtime), 0)
            self.assertEqual(json.loads(stdout.getvalue())['notified'], expected)
            self.assertEqual(bool(stderr.getvalue()), expected)
        self.check.assert_not_called(); self.apply.assert_not_called()

    def test_failure_budget_is_finite_and_recovery_unknown_blocks_apply(self):
        self.configure()
        self.guard.side_effect = tool.AutomaticError('recovery_coordination_unavailable')
        for elapsed in (0, 301, 902):
            with mock.patch.object(tool.time, 'time', return_value=self.NOW + elapsed):
                report = tool.automatic_tick(self.directory, self.runtime)
                self.assertEqual(report['status'], 'check_failed')
        with mock.patch.object(tool.time, 'time', return_value=self.NOW + 2000):
            self.assertEqual(tool.automatic_tick(self.directory, self.runtime)['state'], 'budget_exhausted')
        self.assertEqual(len(self.state()['failures']), 3)
        self.apply.assert_not_called()

    def test_preflight_refuses_unavailable_namespace_and_provenance(self):
        import signal
        with mock.patch.object(tool.sys, 'platform', 'linux'), \
                mock.patch.object(tool.os, 'pidfd_open', create=True), \
                mock.patch.object(signal, 'pidfd_send_signal', create=True), \
                mock.patch.object(tool, 'directory_context', return_value={'visibility': 'missing'}):
            with self.assertRaisesRegex(tool.AutomaticError, 'execution_context_unavailable'):
                self.real_preflight(self.directory, self.runtime, tool.automatic_defaults())
        with mock.patch.object(tool, 'provenance_trusted_root', create=True, side_effect=ValueError('PRIVATE')) as root_check, \
                mock.patch.object(tool, 'provenance_gh', create=True):
            with self.assertRaisesRegex(tool.AutomaticError, 'provenance_unavailable'):
                tool.automatic_provenance(self.directory, tool.automatic_defaults())
            root_check.assert_called_once()
        with mock.patch.object(tool.sys, 'platform', 'linux'), \
                mock.patch.object(tool.os, 'pidfd_open', create=True), \
                mock.patch.object(signal, 'pidfd_send_signal', create=True), \
                mock.patch.object(tool, 'directory_context', return_value={'visibility': 'present', 'readable': 'allowed', 'writable': 'allowed', 'filesystem': 'read_write'}), \
                mock.patch.object(tool, 'process_checks', return_value={'shim': 'running', 'tailscaled': 'running', 'sshd': 'running'}), \
                mock.patch.object(tool, 'socket_status', return_value='missing'):
            with self.assertRaisesRegex(tool.AutomaticError, 'local_socket_unavailable'):
                self.real_preflight(self.directory, self.runtime, tool.automatic_defaults())


if __name__ == '__main__': unittest.main()
