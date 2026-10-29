"""Explicit connection evidence over relay -> authenticated HTTP -> real core.

All credentials, observations and SQLite ledgers are synthetic and isolated.
No real SSH, peer ping, recovery command or callback is performed.
"""
from datetime import datetime, timezone
import hashlib
import os
import tempfile
import unittest

from starlette.applications import Starlette
from starlette.testclient import TestClient

from monitor_ingest import MonitorIngestMiddleware
from monitor_relay import sanitize
from recovery_core import RecoveryCore


class ExternalHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.chmod(self.tmp.name, 0o700)
        self.now = 1800000000.0
        self.path = self.tmp.name + '/recovery.sqlite'
        self.core = self.new_core()
        self.token = 'synthetic-monitor-credential-0000000000'
        app = MonitorIngestMiddleware(Starlette(), self.core,
            hashlib.sha256(self.token.encode()).hexdigest())
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def new_core(self):
        def no_network(*args):
            raise AssertionError('unexpected callback')
        return RecoveryCore(self.path, [], 'operator', clock=lambda: self.now,
                            new_incident_cooldown_seconds=600, transport=no_network,
                            max_claims_per_24h=getattr(self, 'max_claims', 3))

    def raw(self, healthy=True, state='prior_boot'):
        return {'observed_at': datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
                'state': state if healthy else 'external_unreachable',
                'ssh': {'command_succeeded': healthy, 'probe_valid': healthy,
                        'exit_code': 0 if healthy else 255,
                        'error_class': 'ok' if healthy else 'timeout'},
                'peer_ping': {'responded': healthy, 'exit_code': 0 if healthy else 1}}

    def post(self, wire, expected=200, token=None):
        response = self.client.post('/monitor/observations', json=wire,
            headers={'Authorization': 'Bearer ' + (self.token if token is None else token)})
        self.assertEqual(response.status_code, expected, response.text)
        return response

    def sample(self, healthy, step=30, state='prior_boot', legacy=False):
        self.now += step
        row = sanitize(self.raw(healthy, state), self.now)
        if legacy:
            row.pop('external_health')
        self.post(row)
        return row

    def outage(self):
        for _ in range(3):
            self.sample(False)
        return self.core.get_current_recovery('operator')

    def claim(self, outcome='success'):
        iid = self.outage()['incident']['incident_id']
        grant = self.core.claim_recovery('operator', iid)
        self.core.report_recovery('operator', iid, grant['claim_id'], outcome)
        return iid, self.now

    def proof(self, iid):
        with self.core._db() as db:
            return dict(db.execute('SELECT * FROM recovery_confirmations WHERE incident_id=?',
                                   (iid,)).fetchone())

    def test_prior_boot_wire_confirms_and_shortens_next_outage_after_restart(self):
        iid, claimed = self.claim()
        for _ in range(3):
            wire = self.sample(True)
            self.assertEqual(wire['state'], 'prior_boot')
            self.assertEqual(wire['external_health'], 'healthy')
        self.assertEqual(self.proof(iid)['confirmed_at'], claimed + 90)
        self.assertEqual(self.proof(iid)['streak'], 3)
        # A real new core over the same isolated ledger must retain the proof.
        self.core = self.new_core()
        self.client.app.recovery = self.core
        state = self.outage()
        self.assertEqual(state['policy']['cooldown_basis'], 'confirmed_recovery')
        self.assertEqual(state['policy']['effective_cooldown_seconds'], 600)
        self.assertEqual(state['retry_at'], claimed + 600)
        self.assertEqual(state['eligibility_reason'], 'cooldown')
        while self.now < claimed + 600:
            self.sample(False, step=min(120, claimed + 600 - self.now))
        self.assertTrue(self.core.get_current_recovery('operator')['claimable'])

    def test_old_relay_prior_boot_is_compatible_but_cannot_earn_short_proof(self):
        iid, _ = self.claim()
        for _ in range(3):
            self.sample(True, legacy=True)
        self.assertEqual(self.proof(iid)['streak'], 0)
        self.assertIsNone(self.proof(iid)['confirmed_at'])
        self.assertEqual(self.outage()['policy']['cooldown_basis'], 'global')

    def test_old_relay_verified_running_keeps_its_existing_contract(self):
        iid, _ = self.claim()
        for _ in range(3):
            self.sample(True, state='verified_running', legacy=True)
        self.assertIsNotNone(self.proof(iid)['confirmed_at'])
        self.assertEqual(self.outage()['policy']['cooldown_basis'], 'confirmed_recovery')

    def test_claim_evidence_cannot_be_created_by_unauthenticated_post(self):
        iid, _ = self.claim()
        before = self.proof(iid)
        self.now += 30
        self.post(sanitize(self.raw(), self.now), expected=401, token='wrong-credential')
        self.assertEqual(self.proof(iid), before)

    def test_new_field_enum_and_positive_contradictions_are_rejected_without_mutation(self):
        iid, _ = self.claim()
        before = self.proof(iid)
        bad = []
        for value in (None, True, False, 1, [], {}, '', 'running'):
            row = self.raw(); row['external_health'] = value; bad.append(row)
        for state in ('external_unreachable', 'peer_reachable_ssh_failed', 'probe_failed',
                      'service_degraded', 'not_running', 'unknown', 'ssh_ready_ping_unverified'):
            row = self.raw(state=state); row['external_health'] = 'healthy'; bad.append(row)
        for group, key, value in (
                ('ssh', 'command_succeeded', False), ('ssh', 'probe_valid', False),
                ('peer_ping', 'responded', False), ('ssh', 'exit_code', True),
                ('ssh', 'exit_code', 0.0), ('ssh', 'exit_code', '0'),
                ('ssh', 'exit_code', None), ('ssh', 'exit_code', 255),
                ('peer_ping', 'exit_code', False), ('peer_ping', 'exit_code', 1),
                ('ssh', 'error_class', 'authentication_failed'),
                ('peer_ping', 'error_class', 'timeout_or_local_failure')):
            row = self.raw(); row['external_health'] = 'healthy'; row[group][key] = value; bad.append(row)
        for row in bad:
            with self.subTest(row=row):
                self.now += 1
                row['observed_at'] = self.raw()['observed_at']
                self.post(row, expected=400)
                self.assertEqual(self.proof(iid), before)

    def test_relay_derives_field_and_does_not_copy_a_supplied_health_claim(self):
        for state in ('prior_boot', 'verified_running', 'not_running', 'service_degraded',
                      'external_unreachable', 'probe_failed'):
            with self.subTest(state=state):
                row = self.raw(state=state)
                row['external_health'] = 'attacker-chosen'
                row['ssh']['remote'] = {'private': 'must be dropped'}
                clean = sanitize(row, self.now)
                self.assertEqual(clean['state'], state)
                self.assertEqual(clean['external_health'],
                    'healthy' if state in ('prior_boot', 'verified_running') else 'unconfirmed')
                self.assertNotIn('remote', clean['ssh'])
        for group, key, value in (('ssh', 'exit_code', None), ('peer_ping', 'exit_code', 1),
                                  ('ssh', 'error_class', 'invalid_probe_json')):
            row = self.raw(); row[group][key] = value
            self.assertEqual(sanitize(row, self.now)['external_health'], 'unconfirmed')

    def test_explicit_unconfirmed_resets_streak_even_with_positive_flags(self):
        iid, _ = self.claim()
        self.sample(True)
        self.assertEqual(self.proof(iid)['streak'], 1)
        self.now += 30
        row = sanitize(self.raw(), self.now); row['external_health'] = 'unconfirmed'
        self.post(row)
        self.assertEqual(self.proof(iid)['streak'], 0)

    def test_replay_cannot_upgrade_confirmation_with_the_new_field(self):
        iid, _ = self.claim()
        row = self.sample(True, legacy=True)
        before = self.proof(iid)
        replay = dict(row, external_health='healthy')
        self.post(replay)
        self.assertEqual(self.proof(iid), before)
        self.now -= 1
        stale = sanitize(self.raw(), self.now)
        self.post(stale, expected=400)
        self.assertEqual(self.proof(iid), before)

    def test_explicit_health_does_not_override_an_unknown_first_report(self):
        iid, _ = self.claim(outcome='unknown')
        for _ in range(3):
            self.sample(True)
        self.assertIsNotNone(self.proof(iid)['confirmed_at'])
        self.assertEqual(self.outage()['policy']['cooldown_basis'], 'global')

    def test_daily_limit_dominates_explicit_healthy_new_incident(self):
        self.max_claims = 1
        self.core = self.new_core()
        self.client.app.recovery = self.core
        self.claim()
        for _ in range(3):
            self.sample(True)
        state = self.outage()
        self.assertEqual(state['policy']['cooldown_basis'], 'confirmed_recovery')
        self.assertEqual(state['eligibility_reason'], 'daily_limit')
        self.assertFalse(state['claimable'])

    def test_new_field_cannot_invent_an_older_claim_evidence_slot(self):
        iid, _ = self.claim()
        # Model a legacy claim that predates slot creation, never a live ledger.
        with self.core._db() as db:
            db.execute('DELETE FROM recovery_confirmations WHERE incident_id=?', (iid,))
        for _ in range(3):
            self.sample(True)
        with self.core._db() as db:
            self.assertIsNone(db.execute('SELECT * FROM recovery_confirmations WHERE incident_id=?',
                                        (iid,)).fetchone())
        self.assertEqual(self.outage()['policy']['cooldown_basis'], 'global')

    def test_long_gap_restarts_explicit_health_evidence(self):
        iid, _ = self.claim()
        self.sample(True); self.sample(True)
        self.sample(True, step=151)
        self.assertEqual(self.proof(iid)['streak'], 1)
        self.assertIsNone(self.proof(iid)['confirmed_at'])


if __name__ == '__main__':
    unittest.main()
