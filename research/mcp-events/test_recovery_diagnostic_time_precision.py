"""Round-trip valid float deadlines that share an ISO microsecond."""
import math
import os
from pathlib import Path
import tempfile
import unittest

from recovery_core import RecoveryCore
from recovery_budget_diagnostics import build_recovery_diagnostics, sanitize_recovery_diagnostics


class DiagnosticTimePrecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.chmod(self.tmp.name, 0o700)
        self.now = 1800000000.0
        self.core = RecoveryCore(str(Path(self.tmp.name) / 'recovery.sqlite'), [], 'fixture',
                                 clock=lambda: self.now, max_claims_per_24h=1,
                                 unclaimed_reminder_interval_seconds=600,
                                 unclaimed_reminder_maximum=1,
                                 transport=lambda *_: self.fail('No callback is allowed'))

    def test_budget_release_float_future_can_round_to_same_iso_as_of(self):
        claimed = self.now - 86400 + math.ulp(self.now)
        self.assertGreater(claimed + 86400, self.now)
        with self.core._db() as db:
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       ('historical', 'event', claimed - 1, claimed + 1, 'grant', claimed, 'unknown'))
        value = build_recovery_diagnostics(self.core)
        self.assertEqual(value['claims_issued'], 1)
        self.assertEqual(value['budget_release_at'], value['as_of'])
        self.assertEqual(sanitize_recovery_diagnostics(value, self.now), value)

    def test_raw_future_response_deadline_can_round_to_same_iso_as_of(self):
        receipt = self.now - 600 + math.ulp(self.now)
        self.assertGreater(receipt + 600, self.now)
        with self.core._db() as db:
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       ('open', 'original', self.now - 1000, None, None, None, None))
            db.execute('INSERT INTO recovery_observation VALUES(1,?,?,?,?)',
                       (self.now - 1, 'timeout', 'failed', 3))
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,1)',
                       ('sub', 'fixture', 'https://callback.example/fixture', '{}', 'DUMMY', self.now + 3600))
            for eid, request in [('original', 'open'), ('reminder', 'open:unclaimed:1')]:
                db.execute('INSERT INTO events VALUES(?,?,?,?)', (eid, 'fixture', request, b'{}'))
            db.execute('INSERT INTO outbox VALUES(?,?,?,?,?)', ('reminder', 'sub', 'received', 1, 200))
            db.execute('INSERT INTO recovery_delivery_receipts VALUES(?,?,?)', ('reminder', 'sub', receipt))
            db.execute('INSERT INTO recovery_followups VALUES(?,?,?,?,?,?,?,?,?)',
                       ('open', 'original', 'reminder', 1, self.now - 1000, self.now + 6200, self.now - 1, 0, None))
        value = build_recovery_diagnostics(self.core)
        self.assertTrue(value['unclaimed_followup']['awaiting_response'])
        self.assertEqual(value['unclaimed_followup']['response_deadline_at'], value['as_of'])
        self.assertEqual(sanitize_recovery_diagnostics(value, self.now), value)
