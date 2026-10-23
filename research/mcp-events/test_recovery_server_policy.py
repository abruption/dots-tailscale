"""Deployment configuration must reach the same server-enforced recovery policy."""
import tempfile
import unittest
from server import load_recovery


class RecoveryServerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = {'enable_recovery': True, 'runtime_dir': self.tmp.name,
                       'allowed_callback_origins': [], 'allowed_user': 'alice'}

    def test_default_and_explicit_trial_configuration_reach_status(self):
        default = load_recovery(self.config).get_current_recovery('alice')
        self.assertEqual(default['policy']['max_claims_per_24h'], 3)
        self.assertEqual(default['policy']['cooldown_seconds'], 3600)
        trial = load_recovery({**self.config, 'recovery_max_claims_per_24h': 12,
                               'recovery_cooldown_seconds': 3600}).get_current_recovery('alice')
        self.assertEqual(trial['policy']['max_claims_per_24h'], 12)
        self.assertEqual(trial['policy']['cooldown_seconds'], 3600)
        self.assertFalse(trial['claimable'])  # A higher budget alone is not permission.

    def test_bad_deployment_values_are_not_coerced_to_valid_policy(self):
        for key, value in [('recovery_max_claims_per_24h', True),
                           ('recovery_max_claims_per_24h', '12'),
                           ('recovery_max_claims_per_24h', 0),
                           ('recovery_cooldown_seconds', True),
                           ('recovery_cooldown_seconds', '3600'),
                           ('recovery_cooldown_seconds', -1)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                load_recovery({**self.config, key: value})

    def test_disabled_recovery_does_not_open_a_ledger(self):
        from pathlib import Path
        self.assertIsNone(load_recovery({'enable_recovery': False}))
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])


class PolicyErrorHTTPTests(unittest.TestCase):
    def test_structured_policy_denial_survives_mcp_transport(self):
        from unittest.mock import patch
        from test_recovery_http import RecoveryHTTP
        from recovery_core import RecoveryIneligible
        fixture = RecoveryHTTP(methodName='test_read_scope_cannot_claim_and_grant_is_single_use')
        fixture.setUp()
        try:
            incident = fixture.incident()
            rejected = RecoveryIneligible({'eligibility_reason': 'cooldown', 'retry_at': 1234567890.0})
            with patch.object(fixture.recovery, 'claim_recovery', side_effect=rejected):
                result = fixture.call('claim_recovery', {'incident_id': incident}, 'write')
            self.assertEqual(result['error']['data'],
                             {'eligibility_reason': 'cooldown', 'retry_at': 1234567890.0})
            self.assertNotIn('command', result['error']['data'])
        finally:
            fixture.tearDown()
