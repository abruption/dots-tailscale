import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from recovery_core import RecoveryCore
from recovery_budget_diagnostics import (build_recovery_diagnostics,
                                         recovery_read_snapshot,
                                         sanitize_recovery_diagnostics)


class RecoveryBudgetDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.chmod(self.tmp.name, 0o700)
        self.addCleanup(self.tmp.cleanup)
        self.now = 1_800_000_000.0
        self.core = RecoveryCore(self.tmp.name + '/recovery.sqlite', [], 'operator',
                                 clock=lambda: self.now, max_claims_per_24h=24,
                                 cooldown_seconds=3600, new_incident_cooldown_seconds=600)

    def add_claim(self, iid, offset, current, first=None, closed=None, confirmed=None):
        claimed = self.now + offset
        with self.core._db() as db:
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       (iid, 'evt_' + iid, claimed - 10, closed, 'claim_' + iid,
                        claimed, current))
            if first is not None:
                db.execute('INSERT INTO recovery_reports VALUES(?,?,?)',
                           (iid, first, claimed + 1))
            if confirmed is not None:
                db.execute('INSERT INTO recovery_confirmations VALUES(?,?,?,?,?)',
                           (iid, confirmed - 60, confirmed, 3, confirmed))

    def test_aggregate_counts_all_issued_claims_and_distinguishes_report_and_confirmation_states(self):
        self.add_claim('success', -100, 'success', first='success',
                       closed=self.now - 20, confirmed=self.now - 10)
        self.add_claim('corrected', -200, 'success', first='unknown')
        self.add_claim('unknown', -300, 'unknown')
        self.add_claim('blocked', -400, 'blocked', first='blocked',
                       closed=self.now - 30)
        self.add_claim('boundary', -86400, 'failed', first='failed')
        self.add_claim('old', -86401, 'success', first='success')
        state = {'unclaimed_followup': {
            'operator_attention': True, 'reason': 'expired', 'incident_id': 'inc_private_fixture'}}

        with patch.object(self.core, '_state', return_value=state):
            diagnostics = build_recovery_diagnostics(self.core)

        self.assertEqual(diagnostics['claims_issued'], 4)
        self.assertEqual(diagnostics['cap'], 24)
        self.assertEqual(diagnostics['first_reports'], {
            'success': 1, 'failed': 0, 'blocked': 1, 'unknown': 1, 'missing': 1, 'other': 0})
        self.assertEqual(diagnostics['current_outcomes'], {
            'success': 2, 'failed': 0, 'blocked': 1, 'unknown': 1, 'missing': 0, 'other': 0})
        self.assertEqual(diagnostics['externally_closed'], 2)
        self.assertEqual(diagnostics['sustained_confirmed'], 1)
        self.assertEqual(diagnostics['unclaimed_followup'], {
            'available': True, 'operator_attention': True, 'reason_code': 'expired',
            'awaiting_response': False, 'response_deadline_at': None})
        self.assertNotIn('inc_private_fixture', json.dumps(diagnostics))
        self.assertNotIn('evt_', json.dumps(diagnostics))
        self.assertNotIn('claim_', json.dumps(diagnostics))

    def test_read_snapshot_is_query_only_and_carries_current_followup_attention(self):
        self.add_claim('one', -1, 'unknown')
        current_state = {'unclaimed_followup': {
            'operator_attention': False, 'reason': None}}
        with recovery_read_snapshot(self.core) as db:
            with patch.object(self.core, '_state', return_value=current_state):
                diagnostics = build_recovery_diagnostics(self.core, db=db)
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
        self.assertEqual(diagnostics['claims_issued'], 1)
        self.assertEqual(diagnostics['unclaimed_followup'], {
            'available': True, 'operator_attention': False, 'reason_code': None,
            'awaiting_response': False, 'response_deadline_at': None})

    def test_relative_database_path_opens_the_actual_cwd_database_without_creating_missing_files(self):
        previous_cwd = os.getcwd()
        original_path = self.core.db_path
        try:
            os.chdir(self.tmp.name)
            self.core.db_path = 'recovery.sqlite'
            with recovery_read_snapshot(self.core) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM recovery_incidents').fetchone()[0], 0)
            missing = SimpleNamespace(db_path='missing.sqlite')
            with self.assertRaises(sqlite3.OperationalError):
                with recovery_read_snapshot(missing):
                    self.fail('A missing read-only database must not open')
            self.assertFalse(Path('missing.sqlite').exists())
        finally:
            self.core.db_path = original_path
            os.chdir(previous_cwd)

    def test_query_only_diagnostics_work_in_delete_and_wal_journal_modes(self):
        for mode in ('DELETE', 'WAL'):
            with self.subTest(mode=mode):
                with self.core._db() as db:
                    actual = db.execute('PRAGMA journal_mode=' + mode).fetchone()[0]
                self.assertEqual(actual.lower(), mode.lower())
                with recovery_read_snapshot(self.core) as db:
                    self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
                    self.assertEqual(build_recovery_diagnostics(self.core, db=db)['claims_issued'], 0)
                    self.assertEqual(db.total_changes, 0)

    def test_awaiting_response_deadline_is_projected_without_incident_identity(self):
        deadline = datetime.fromtimestamp(self.now + 300, timezone.utc).timestamp()
        state = {'unclaimed_followup': {'operator_attention': False,
                                        'reason': 'awaiting_response',
                                        'response_deadline_at': deadline,
                                        'incident_id': 'private'}}
        with patch.object(self.core, '_state', return_value=state):
            value = build_recovery_diagnostics(self.core)
        self.assertTrue(value['unclaimed_followup']['awaiting_response'])
        self.assertEqual(value['unclaimed_followup']['response_deadline_at'],
                         datetime.fromtimestamp(deadline, timezone.utc).isoformat())
        self.assertNotIn('private', json.dumps(value))
        self.assertEqual(sanitize_recovery_diagnostics(value, self.now), value)

    def test_expired_followup_with_response_deadline_is_unavailable_not_cleared(self):
        state = {'unclaimed_followup': {'operator_attention': True,
                                        'reason': 'expired',
                                        'response_deadline_at': self.now + 300}}
        with patch.object(self.core, '_state', return_value=state):
            value = build_recovery_diagnostics(self.core)
        self.assertEqual(value['unclaimed_followup'], {
            'available': False, 'operator_attention': None, 'reason_code': None,
            'awaiting_response': None, 'response_deadline_at': None})

    def test_attention_reason_cannot_be_silently_downgraded_to_clear(self):
        state = {'unclaimed_followup': {'operator_attention': False, 'reason': 'storage_full'}}
        with patch.object(self.core, '_state', return_value=state):
            value = build_recovery_diagnostics(self.core)
        self.assertFalse(value['unclaimed_followup']['available'])
        self.assertIsNone(value['unclaimed_followup']['operator_attention'])

    def test_snapshot_validator_rejects_unbounded_or_inconsistent_diagnostics(self):
        with recovery_read_snapshot(self.core) as db:
            value = build_recovery_diagnostics(self.core, db=db)
        self.assertEqual(sanitize_recovery_diagnostics(value, self.now), value)
        with self.assertRaises(ValueError):
            sanitize_recovery_diagnostics({**value, 'incident_id': 'private'}, self.now)
        with self.assertRaises(ValueError):
            sanitize_recovery_diagnostics({**value, 'claims_issued': 1}, self.now)
        with self.assertRaises(ValueError):
            sanitize_recovery_diagnostics(value, self.now + 151)
        with self.assertRaises(ValueError):
            sanitize_recovery_diagnostics(value, self.now - 6)

    def test_exhausted_budget_release_reference_uses_cap_offset_and_strict_window(self):
        core = RecoveryCore(self.tmp.name + '/lowered.sqlite', [], 'operator',
                            clock=lambda: self.now, max_claims_per_24h=12)
        timestamps = [self.now - 86400, *[self.now - (100 * n) for n in range(1, 15)]]
        with core._db() as db:
            for n, claimed in enumerate(timestamps):
                db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                           (f'i{n}', f'e{n}', claimed-1, None, f'c{n}', claimed, 'unknown'))
        value = build_recovery_diagnostics(core)
        self.assertEqual(value['claims_issued'], 14)
        # With 14 issued claims and a lowered cap of 12, the third-oldest
        # in-window claim is the first expiry that can reduce use below 12.
        expected = datetime.fromtimestamp(
            self.now - 1200 + 86400, timezone.utc).isoformat()
        self.assertEqual(value['budget_release_at'], expected)
        self.assertEqual(sanitize_recovery_diagnostics(value, self.now)['budget_release_at'], expected)

    def test_one_post_snapshot_clock_covers_window_and_followup_deadline(self):
        base = self.now
        calls = []
        def advancing_clock():
            calls.append(None)
            return base if len(calls) == 1 else base + 200
        core = RecoveryCore(
            self.tmp.name + '/clock-edge.sqlite', [], 'operator', clock=advancing_clock,
            max_claims_per_24h=24, cooldown_seconds=3600,
            new_incident_cooldown_seconds=600,
            unclaimed_reminder_interval_seconds=600,
            unclaimed_reminder_maximum=3,
            unclaimed_reminder_lifetime_seconds=7200)
        calls.clear()
        receipt = base - 500
        with core._db() as db:
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       ('prior', 'evt_prior', base-90000, base-10, 'claim_prior',
                        base-86400+0.5, 'success'))
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       ('open', 'evt_open', base-1000, None, None, None, None))
            db.execute('INSERT INTO recovery_observation VALUES(1,?,?,?,?)',
                       (base-1, 'timeout', 'failed', 3))
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,1)',
                       ('sub_fixture', 'operator', 'https://callback.example/event',
                        '{}', 'unused', base+7200))
            db.execute('INSERT INTO events VALUES(?,?,?,?)',
                       ('evt_open', 'operator', 'open', b'{}'))
            db.execute('INSERT INTO outbox VALUES(?,?,?,?,?)',
                       ('evt_open', 'sub_fixture', 'received', 1, 200))
            db.execute('INSERT INTO recovery_delivery_receipts VALUES(?,?,?)',
                       ('evt_open', 'sub_fixture', receipt))
            db.execute('INSERT INTO recovery_followups VALUES(?,?,?,?,?,?,?,?,?)',
                       ('open', 'evt_open', 'evt_open', 3, receipt, base+300,
                        base-10, 0, None))

        # The single sample is before both boundaries: the recent claim still
        # counts, and the follow-up remains awaiting its response deadline.
        value = build_recovery_diagnostics(core)
        self.assertEqual(calls, [None])
        self.assertEqual(value['claims_issued'], 1)
        self.assertEqual(value['unclaimed_followup']['reason_code'], None)
        self.assertTrue(value['unclaimed_followup']['awaiting_response'])
        self.assertEqual(value['unclaimed_followup']['response_deadline_at'],
                         datetime.fromtimestamp(base+100, timezone.utc).isoformat())


if __name__ == '__main__':
    unittest.main()
