"""Offline relay-health to operator notices; the sender is always a local stub."""
import os
from pathlib import Path
import tempfile
import unittest

import health_notifier as notifier
import monitor_relay as relay

NOW = 1800000000.0


def stamp(now):
    return notifier.iso(now)


def summary(now, used=20, followup=None):
    return {'schema_version': 1, 'as_of': stamp(now), 'window_seconds': 86400,
            'cap': 24, 'claims_issued': used,
            'budget_release_at': stamp(now + 86400) if used >= 24 else None,
            'first_reports': {'success': 0, 'failed': 0, 'blocked': 0,
                              'unknown': 0, 'missing': used, 'other': 0},
            'current_outcomes': {'success': 0, 'failed': 0, 'blocked': 0,
                                 'unknown': used, 'missing': 0, 'other': 0},
            'externally_closed': 0, 'sustained_confirmed': 0,
            'unclaimed_followup': followup or {
                'available': True, 'operator_attention': False, 'reason_code': None,
                'awaiting_response': False, 'response_deadline_at': None}}


class RecoveryOperatorWarningPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.config = {key: str(self.root / filename) for key, filename in (
            ('relay_health_file', 'health.json'), ('observation_file', 'observation.json'),
            ('state_file', 'notifier.json'), ('telegram_config_file', 'unused-telegram.json'))}
        self.config['operator_recovery_warnings'] = True
        self.relay_state = {}
        self.health = {}
        self.sent = []
        self.at_send = []

    def send(self, config, text):
        # No credential file is created or read. No production transport runs.
        self.sent.append(text)
        self.at_send.append(notifier.read_file(config['state_file'], private=True))
        return 'sent'

    def received(self, now, value=None, status='current'):
        observation = {'observed_at': stamp(now), 'state': 'prior_boot',
                       'ssh': {'command_succeeded': True, 'probe_valid': True,
                               'exit_code': 0, 'error_class': 'ok'},
                       'peer_ping': {'responded': True, 'exit_code': 0}}
        relay.save_private(self.config['observation_file'], observation)
        result = relay.TransportResult(True, subscription_active=True,
                                       recovery_diagnostics=value,
                                       recovery_diagnostics_status=status)
        diagnostics = {}
        self.relay_state, outcome = relay.step(
            self.config, self.relay_state, now, lambda *_: result, diagnostics)
        self.assertEqual(outcome, 'delivered')
        self.health = relay.update_health(self.health, self.relay_state, outcome, diagnostics, now)
        relay.save_private(self.config['relay_health_file'], self.health)

    def run_at(self, now, sender=None):
        return notifier.run_once(self.config, now, self.send if sender is None else sender)

    def state(self):
        return notifier.read_file(self.config['state_file'], private=True)

    def test_opt_in_is_required_even_for_exhaustion(self):
        self.config.pop('operator_recovery_warnings')
        self.received(NOW, summary(NOW, 24))
        self.assertEqual(self.run_at(NOW), 'healthy')
        self.assertEqual(self.sent, [])
        self.assertFalse(Path(self.config['telegram_config_file']).exists())

    def test_twenty_warns_and_twenty_four_escalates_without_changing_cap(self):
        self.received(NOW, summary(NOW, 19))
        self.assertEqual(self.run_at(NOW), 'healthy')
        self.received(NOW + 1, summary(NOW + 1, 20))
        self.assertEqual(self.run_at(NOW + 1), 'sent')
        self.assertIn('20/24', self.sent[-1])
        self.assertIn('claim', self.sent[-1])
        attempt = self.at_send[-1]['operator_warning_state']['claim_budget_near_limit']
        self.assertEqual(attempt['last_attempt_at'], stamp(NOW + 1))
        self.assertEqual(attempt['last_outcome'], 'unknown')
        self.received(NOW + 2, summary(NOW + 2, 20))
        self.assertNotEqual(self.run_at(NOW + 2), 'healthy')
        self.assertEqual(len(self.sent), 1)
        self.received(NOW + 3, summary(NOW + 3, 24))
        self.assertEqual(self.run_at(NOW + 3), 'sent')
        self.assertEqual(len(self.sent), 2)
        self.assertIn('24/24', self.sent[-1])
        self.assertEqual(self.health['recovery_diagnostics']['cap'], 24)

    def test_unknown_new_ack_retains_warning_until_a_real_clear(self):
        self.received(NOW, summary(NOW, 20)); self.run_at(NOW)
        before = self.state()['operator_warning_state']['claim_budget_near_limit']
        self.received(NOW + 1, None, 'missing')
        self.assertEqual(self.run_at(NOW + 1), 'recovery_diagnostics_unknown')
        self.assertEqual(self.state()['operator_warning_state']['claim_budget_near_limit'], before)
        self.assertEqual(self.health['recovery_diagnostics']['as_of'], stamp(NOW))
        self.received(NOW + 2, summary(NOW + 2, 19))
        self.assertEqual(self.run_at(NOW + 2), 'healthy')
        self.assertFalse(self.state()['operator_warning_state']['claim_budget_near_limit']['active'])
        self.assertEqual(len(self.sent), 1)

    def test_unknown_followup_component_does_not_clear_prior_attention(self):
        attention = {'available': True, 'operator_attention': True, 'reason_code': 'expired',
                     'awaiting_response': False, 'response_deadline_at': None}
        self.received(NOW, summary(NOW, 1, attention)); self.run_at(NOW)
        before = self.state()['operator_warning_state']['followup_operator_attention']
        missing = {'available': False, 'operator_attention': None, 'reason_code': None,
                   'awaiting_response': None, 'response_deadline_at': None}
        self.received(NOW + 1, summary(NOW + 1, 1, missing))
        report = notifier.assess(self.config, NOW + 1)['recovery_warnings']
        self.assertEqual(report['status'], 'current')
        self.assertEqual(report['followup_status'], 'unknown')
        self.assertNotEqual(self.run_at(NOW + 1), 'healthy')
        self.assertEqual(self.state()['operator_warning_state']['followup_operator_attention'], before)
        self.assertEqual(len(self.sent), 1)

    def test_cached_as_of_survives_noop_then_stale_observation_is_unknown(self):
        self.received(NOW, summary(NOW, 20)); self.run_at(NOW)
        for now in (NOW + 15, NOW + 151):
            diagnostics = relay.cached_recovery_diagnostics(self.health)
            def forbidden_send(*_):
                self.fail('No transport for an unchanged or stale observation')
            self.relay_state, outcome = relay.step(
                self.config, self.relay_state, now, forbidden_send, diagnostics)
            self.assertEqual(outcome, 'already_delivered' if now == NOW + 15 else 'observation_unavailable')
            self.health = relay.update_health(self.health, self.relay_state, outcome, diagnostics, now)
            relay.save_private(self.config['relay_health_file'], self.health)
            self.assertEqual(self.health['recovery_diagnostics']['as_of'], stamp(NOW))
            if now == NOW + 15:
                self.assertEqual(self.health['recovery_diagnostics_status'], 'current')
            expected_status = 'current' if now == NOW + 15 else 'unknown'
            self.assertEqual(notifier.assess_recovery_warnings(self.config, self.health, now)['status'], expected_status)
            self.assertNotEqual(self.run_at(now), 'healthy')
        self.assertTrue(self.state()['operator_warning_state']['claim_budget_near_limit']['active'])
        self.assertEqual(len(self.sent), 1)

    def test_transport_failure_retains_a_fresh_unsent_exhaustion_warning(self):
        self.received(NOW, summary(NOW, 24))
        observation = relay.read_private(self.config['observation_file'])
        observation['observed_at'] = stamp(NOW + 15)
        relay.save_private(self.config['observation_file'], observation)
        diagnostics = relay.cached_recovery_diagnostics(self.health)
        failed = relay.TransportResult(False, 'timeout')
        self.relay_state, outcome = relay.step(
            self.config, self.relay_state, NOW + 15, lambda *_: failed, diagnostics)
        self.assertEqual(outcome, 'transport_failed')
        self.health = relay.update_health(self.health, self.relay_state, outcome, diagnostics, NOW + 15)
        relay.save_private(self.config['relay_health_file'], self.health)
        self.assertEqual(self.health['recovery_diagnostics_status'], 'current')
        self.assertEqual(self.health['recovery_diagnostics']['as_of'], stamp(NOW))
        self.assertEqual(self.run_at(NOW + 15), 'operator_warning_sent')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('24/24', self.sent[0])

    def test_known_operator_send_rejection_remains_distinct_from_unknown(self):
        self.received(NOW, summary(NOW, 20))
        self.health.update(failure_class='timeout', first_failure_at=stamp(NOW - notifier.GRACE_SECONDS))
        relay.save_private(self.config['relay_health_file'], self.health)
        results = iter(['sent', 'notification_rejected'])
        def mixed_results(config, text):
            return next(results)
        self.assertEqual(self.run_at(NOW, mixed_results), 'operator_warning_notification_rejected')
        state = self.state()
        self.assertEqual(state['last_notification_outcome'], 'sent')
        self.assertEqual(state['operator_warning_state']['claim_budget_near_limit']['last_outcome'], 'notification_rejected')

    def test_known_budget_warning_is_reported_with_an_unknown_followup_component(self):
        unavailable = {'available': False, 'operator_attention': None,
                       'reason_code': None, 'awaiting_response': None,
                       'response_deadline_at': None}
        self.received(NOW, summary(NOW, 24, unavailable))
        self.assertEqual(self.run_at(NOW), 'operator_warning_sent')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('24/24', self.sent[0])
        self.assertEqual(self.state()['operator_warning_state']['claim_budget_exhausted']['last_outcome'], 'sent')

    def test_missing_diagnostics_gets_a_bounded_monitor_fault_notice_after_grace(self):
        for offset in (0, notifier.GRACE_SECONDS, notifier.GRACE_SECONDS + 1):
            self.received(NOW + offset, None, 'missing')
            self.assertNotEqual(self.run_at(NOW + offset), 'healthy')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('recovery_diagnostics_unavailable', self.sent[0])
        self.assertNotIn('claim_issuance_budget', self.sent[0])
        self.assertFalse(Path(self.config['telegram_config_file']).exists())

    def test_ambiguous_operator_send_survives_restart_and_is_not_retried_immediately(self):
        self.received(NOW, summary(NOW, 20))
        calls = []
        def ambiguous(config, text):
            calls.append(text)
            raise TimeoutError('DUMMY_UNCERTAIN_SEND')
        self.assertEqual(self.run_at(NOW, ambiguous), 'unknown')
        first = self.state()['operator_warning_state']['claim_budget_near_limit']
        self.assertEqual(first['last_outcome'], 'unknown')
        self.received(NOW + 60, summary(NOW + 60, 20))
        self.assertNotEqual(self.run_at(NOW + 60, ambiguous), 'healthy')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.state()['operator_warning_state']['claim_budget_near_limit']['last_attempt_at'], stamp(NOW))

    def test_followup_and_budget_notices_have_separate_durable_categories(self):
        attention = {'available': True, 'operator_attention': True,
                     'reason_code': 'legacy_receipt_unavailable',
                     'awaiting_response': False, 'response_deadline_at': None}
        self.received(NOW, summary(NOW, 1, attention)); self.run_at(NOW)
        self.assertIn('legacy_receipt_unavailable', self.sent[-1])
        self.received(NOW + 1, summary(NOW + 1, 24, attention)); self.run_at(NOW + 1)
        self.assertEqual(len(self.sent), 2)
        self.assertIn('24/24', self.sent[-1])
        self.assertNotIn('unclaimed_followup_operator_attention', self.sent[-1])
        self.assertEqual(set(self.state()['operator_warning_state']), set(notifier.WARNING_KEYS))
        self.assertNotIn(self.tmp.name, '\n'.join(self.sent))
