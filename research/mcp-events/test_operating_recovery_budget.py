"""Operating cap changes preserve grants and retain a finite rolling limit."""
import unittest
from collections import Counter

from recovery_core import RecoveryIneligible
from server import load_recovery
import test_recovery_core as fixtures


class OperatingRecoveryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RecoveryTests(methodName='test_three_distinct_samples_and_duplicate')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.f = self.fixture

    def configured(self, maximum):
        return self.f.configured_core(max_claims_per_24h=maximum, cooldown_seconds=3600,
                                      new_incident_cooldown_seconds=600)

    def rows(self):
        with self.f.core._db() as db:
            return [tuple(row) for row in db.execute(
                'SELECT * FROM recovery_incidents ORDER BY id')]

    def test_server_loader_exposes_twenty_four_and_preserves_execution_mode(self):
        config = {'enable_recovery': True, 'runtime_dir': self.f.tmp.name,
                  'allowed_callback_origins': [], 'allowed_user': 'operator',
                  'recovery_max_claims_per_24h': 24,
                  'recovery_cooldown_seconds': 3600,
                  'recovery_new_incident_cooldown_seconds': 600}
        for enabled in (False, True):
            state = load_recovery({**config, 'recovery_local_coordination': enabled}).get_current_recovery('operator')
            self.assertEqual(state['policy']['max_claims_per_24h'], 24)
            self.assertEqual(state['policy']['cooldown_seconds'], 3600)
            self.assertEqual(state['policy']['new_incident_cooldown_seconds'], 600)
            self.assertEqual(state['execution_mode'], 'coordinated' if enabled else 'legacy_start')
            self.assertFalse(state['claimable'])
        with self.assertRaises(ValueError):
            load_recovery({**config, 'recovery_max_claims_per_24h': 25})

    def test_raise_from_twelve_preserves_all_outcomes_and_requires_new_claim(self):
        iid = self.f.incident()
        self.f.seed_claims([self.f.now - 12000 + n * 60 for n in range(12)])
        with self.f.core._db() as db:
            for n in range(12):
                # A grant without a report still consumes budget.
                changed = db.execute('UPDATE recovery_incidents SET outcome=? WHERE id=?',
                                     (('success', 'failed', 'unknown', None)[n % 4], 'historic_' + str(n)))
                self.assertEqual(changed.rowcount, 1)
            distribution = Counter(row[0] for row in db.execute(
                'SELECT outcome FROM recovery_incidents WHERE claim_id IS NOT NULL'))
            self.assertEqual(distribution, {'success': 3, 'failed': 3, 'unknown': 3, None: 3})
        self.f.core = self.configured(12)
        denied = self.f.fresh_state()
        self.assertEqual(denied['eligibility_reason'], 'daily_limit')
        with self.assertRaises(RecoveryIneligible):
            self.f.core.claim_recovery('operator', iid)
        before = self.rows()
        self.f.core = self.configured(24)  # Same ledger, as after a reviewed restart.
        state = self.f.fresh_state()
        self.assertEqual(self.rows(), before)
        self.assertEqual(state['policy']['claims_in_window'], 12)
        self.assertEqual(state['policy']['max_claims_per_24h'], 24)
        self.assertEqual(state['policy']['cooldown_seconds'], 3600)
        self.assertEqual(state['policy']['new_incident_cooldown_seconds'], 600)
        self.assertTrue(state['claimable'])
        self.assertIsNone(state['incident']['outcome'])
        grant = self.f.core.claim_recovery('operator', iid)
        self.assertEqual(grant['execution_mode'], 'legacy_start')
        self.assertEqual(self.f.core.get_current_recovery('operator')['policy']['claims_in_window'], 13)
        with self.assertRaises(RecoveryIneligible):
            self.f.core.claim_recovery('operator', iid)
        # Lowering the cap again does not roll back the newly issued grant.
        self.f.core = self.configured(12)
        self.assertEqual(self.f.core.get_current_recovery('operator')['eligibility_reason'], 'already_claimed')
        self.assertEqual(self.f.core.get_current_recovery('operator')['policy']['claims_in_window'], 13)

    def test_twenty_fourth_grant_blocks_next_outage_until_oldest_expires(self):
        iid = self.f.incident()
        start = self.f.now
        self.f.seed_claims([start - 80000] + [start - 6000 + n * 60 for n in range(22)])
        self.f.core = self.configured(24)
        self.assertEqual(self.f.fresh_state()['policy']['claims_in_window'], 23)
        self.f.core.claim_recovery('operator', iid)
        self.f.now += 60
        self.f.core.ingest(self.f.observation(healthy=True))
        self.f.fail(); self.f.fail()
        blocked = self.f.fail()
        self.assertEqual(blocked['policy']['claims_in_window'], 24)
        self.assertEqual(blocked['eligibility_reason'], 'daily_limit')
        self.assertEqual(blocked['retry_at'], start + 6400)
        new_iid = blocked['incident']['incident_id']
        with self.assertRaises(RecoveryIneligible):
            self.f.core.claim_recovery('operator', new_iid)
        self.f.now = start + 6400 - 0.001
        self.assertEqual(self.f.fresh_state()['eligibility_reason'], 'daily_limit')
        self.f.now = start + 6400
        released = self.f.fresh_state()
        self.assertEqual(released['policy']['claims_in_window'], 23)
        self.assertTrue(released['claimable'])
        # Expiry releases eligibility; it never issues a grant on its own.
        self.assertEqual(released['incident']['state'], 'pending')
        self.assertIsNone(released['incident']['outcome'])
