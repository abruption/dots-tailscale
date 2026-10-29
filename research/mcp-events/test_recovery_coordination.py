"""Opt-in broker/local contract and independent-account fixtures, no live Dots."""
from pathlib import Path
import shlex
import tempfile
import unittest

from recovery_core import COMMAND, RecoveryCore
import test_recovery_core as fixture


class CoordinatedRecoveryTests(unittest.TestCase):
    setUp = fixture.RecoveryTests.setUp
    tearDown = fixture.RecoveryTests.tearDown
    new_core = fixture.RecoveryTests.new_core
    observation = fixture.RecoveryTests.observation
    fail = fixture.RecoveryTests.fail
    incident = fixture.RecoveryTests.incident

    def test_opt_in_command_is_fixed_and_claim_bound_across_restart(self):
        self.core = RecoveryCore(self.path, [], 'operator', clock=lambda: self.now, local_coordination=True)
        iid = self.incident()
        result = self.core.claim_recovery('operator', iid)
        argv = result['command_argv']
        self.assertEqual(argv[:5], ['/workspace/.dots-tailscale/dots-ts', 'recovery', 'run', '--actor', 'external'])
        self.assertEqual(shlex.split(result['command']), argv)
        self.assertEqual(argv[argv.index('--request-id') + 1], result['claim_id'])
        self.assertEqual(int(argv[argv.index('--claim-expires-at') + 1]), int(self.now + 120))
        self.assertEqual(int(argv[argv.index('--observation-time') + 1]), int(self.now))
        self.assertIn('--ssh-failed', argv)
        self.assertIn('--ping-failed', argv)
        # A config change cannot silently rewrite the already issued contract.
        restarted = RecoveryCore(self.path, [], 'operator', clock=lambda: self.now)
        state = restarted.get_current_recovery('operator', iid)
        self.assertEqual(state['incident']['execution_contract']['mode'], 'coordinated')
        with self.assertRaises(ValueError):
            restarted.claim_recovery('operator', iid)

    def test_legacy_stays_explicit_and_invalid_configuration_fails(self):
        iid = self.incident()
        result = self.core.claim_recovery('operator', iid)
        self.assertEqual(result['command'], COMMAND)
        self.assertEqual(result['execution_mode'], 'legacy_start')
        for value in ('true', 1, None, []):
            with self.assertRaises(ValueError):
                RecoveryCore(self.path, [], 'operator', local_coordination=value)

    def test_two_clean_accounts_cannot_read_claim_or_report_each_others_incidents(self):
        with tempfile.TemporaryDirectory() as directory:
            other = RecoveryCore(Path(directory) / 'separate.sqlite', [], 'second-owner', clock=lambda: self.now,
                                 local_coordination=True)
            iid = self.incident()
            result = self.core.claim_recovery('operator', iid)
            with self.assertRaises(ValueError):
                self.core.get_current_recovery('second-owner', iid)
            with self.assertRaises(ValueError):
                self.core.report_recovery('second-owner', iid, result['claim_id'], 'success')
            self.assertIsNone(other.get_current_recovery('second-owner', iid)['incident'])
            with self.assertRaises(ValueError):
                other.claim_recovery('second-owner', iid)
            self.assertEqual(other.get_current_recovery('second-owner')['policy']['claims_in_window'], 0)


if __name__ == '__main__':
    unittest.main()
