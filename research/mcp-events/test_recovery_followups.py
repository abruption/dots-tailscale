"""Offline received-but-unclaimed reminders; no production callbacks or claims."""
import base64
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from recovery_core import RecoveryCore, RecoveryIneligible

SECRET = 'whsec_' + base64.b64encode(b'x' * 32).decode()


class RecoveryFollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.chmod(self.tmp.name, 0o700)
        self.path = self.tmp.name + '/recovery.sqlite'
        self.now = 1800000000.0
        self.sent = []
        self.callback_status = 200
        self.core = self.make_core()
        self.sid = self.subscribe()

    def transport(self, url, body, headers):
        value = json.loads(body)
        if value.get('type') == 'verification':
            return 200, json.dumps({'challenge': value['challenge']}).encode()
        self.sent.append(value)
        return self.callback_status, b'{}'

    def make_core(self, **options):
        return RecoveryCore(self.path, ['https://callback.example'], 'operator',
                            transport=self.transport, clock=lambda: self.now,
                            max_claims_per_24h=options.pop('maximum', 24),
                            unclaimed_reminder_interval_seconds=options.pop('interval', 600),
                            **options)

    def subscribe(self):
        return self.core.subscribe('operator', 'https://callback.example/events', SECRET)['id']

    def observation(self, healthy=False):
        from datetime import datetime, timezone
        return {'observed_at': datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
                'state': 'verified_running' if healthy else 'external_unreachable',
                'ssh': {'command_succeeded': healthy, 'probe_valid': healthy,
                        'exit_code': 0 if healthy else 255,
                        'error_class': 'ok' if healthy else 'timeout'},
                'peer_ping': {'responded': healthy, 'exit_code': 0 if healthy else 1}}

    def incident(self):
        for _ in range(3):
            self.now += 60
            state = self.core.ingest(self.observation())
        self.iid = state['incident']['incident_id']
        self.original = state['incident']['event_id']
        self.assertTrue(state['claimable'])
        return self.iid

    def initial_receipt(self):
        self.incident()
        self.core.deliver('operator', self.original)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.anchor = self.now

    def tick(self, seconds=600):
        end = self.now + seconds
        for offset in (-120, -60, 0):
            self.now = end + offset
            self.core.ingest(self.observation())
        return self.core.pending_delivery_ids('operator')

    def status(self):
        return self.core.get_current_recovery('operator', self.iid)['unclaimed_followup']

    def test_received_event_gets_distinct_bounded_reminders_and_one_attention_notice(self):
        self.initial_receipt()
        old_receipt = self.core.receipt('operator', self.original)
        for generation in range(1, 4):
            pending = self.tick()
            self.assertEqual(len(pending), 1)
            eid = pending[0]
            self.assertNotEqual(eid, self.original)
            self.assertEqual(self.status()['reminders_created'], generation)
            self.core.deliver('operator', eid)
            self.assertEqual(self.sent[-1]['data'], {
                'incident_id': self.iid, 'target_id': 'dots', 'simulated': False})
        with self.assertLogs(level='WARNING') as logs:
            self.assertEqual(self.tick(), [])
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(self.status()['reason'], 'exhausted')
        self.assertTrue(self.status()['operator_attention'])
        with patch('recovery_followups.logging.warning') as warning:
            self.core.pending_delivery_ids('operator')
            warning.assert_not_called()
        self.assertEqual(self.core.receipt('operator', self.original), old_receipt)
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(self.core.get_current_recovery('operator')['policy']['claims_in_window'], 0)

    def test_status_is_read_only_and_restart_keeps_count_deadline(self):
        self.initial_receipt()
        first = self.tick()[0]
        before = self.status()
        self.assertEqual(self.status(), before)
        self.core = self.make_core()
        after = self.status()
        self.assertEqual(after['expires_at'], self.anchor + 7200)
        self.assertEqual(after['reminders_created'], 1)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [first])
        self.core.deliver('operator', first)
        self.assertEqual(self.status()['next_reminder_at'], self.now + 600)

    def test_concurrent_schedulers_create_one_semantic_event(self):
        self.initial_receipt()
        end = self.now + 600
        for offset in (-120, -60, 0):
            self.now = end + offset
            self.core.ingest(self.observation())
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)
        self.assertEqual(self.status()['reminders_created'], 0)
        barrier = threading.Barrier(4)
        def first_creation(_):
            core = self.make_core()
            barrier.wait(timeout=5)
            return core.pending_delivery_ids('operator')
        with ThreadPoolExecutor(max_workers=4) as pool:
            returned = list(pool.map(first_creation, range(4)))
        self.assertTrue(all(len(ids) == 1 for ids in returned))
        self.assertTrue(all(ids == returned[0] for ids in returned))
        self.assertEqual(self.status()['reminders_created'], 1)
        with self.core._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE request LIKE '%:unclaimed:%'").fetchone()[0], 1)

    def test_delayed_reminder_after_claim_is_cancelled_and_cannot_duplicate_startup(self):
        self.initial_receipt()
        eid = self.tick()[0]
        self.core.claim_recovery('operator', self.iid)
        sent = len(self.sent)
        self.core.deliver('operator', eid)
        self.assertEqual(len(self.sent), sent)
        self.assertEqual(self.core.receipt('operator', eid)['deliveries'][0]['state'], 'cancelled')
        with self.assertRaises(RecoveryIneligible):
            self.core.claim_recovery('operator', self.iid)
        self.assertEqual(self.status()['reason'], 'claimed')

    def test_preflight_stop_suppresses_creation_and_queued_dispatch(self):
        self.initial_receipt()
        eid = self.tick()[0]
        self.core.report_preflight('operator', self.iid, 'approval_required')
        self.core.deliver('operator', eid)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(self.status()['reason'], 'preflight_reported')
        self.assertEqual(self.core.get_current_recovery('operator', self.iid)['incident']['preflight_report']['reason'], 'approval_required')

    def test_external_recovery_cancels_pending_reminder(self):
        self.initial_receipt()
        eid = self.tick()[0]
        original_receipt = self.core.receipt('operator', self.original)
        self.now += 1
        self.core.ingest(self.observation(healthy=True))
        self.assertEqual(self.core.receipt('operator', eid)['deliveries'][0]['state'], 'cancelled')
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(self.core.receipt('operator', self.original), original_receipt)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.status()['reason'], 'incident_closed')

    def test_disable_cancels_queue_and_reenable_never_resets_or_replays_it(self):
        self.initial_receipt()
        eid = self.tick()[0]
        before = self.status()
        self.core = self.make_core(interval=None)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.core.deliver('operator', eid)
        self.core = self.make_core()
        self.assertEqual(self.status()['reminders_created'], before['reminders_created'])
        self.assertEqual(self.status()['expires_at'], before['expires_at'])
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(len(self.sent), 1)

    def test_expiry_and_clock_rollback_do_not_rearm_or_refund(self):
        self.initial_receipt()
        eid = self.tick()[0]
        before = self.status()
        self.now -= 1
        self.core.deliver('operator', eid)
        self.assertEqual(self.status()['reason'], 'clock_regressed')
        self.assertEqual(len(self.sent), 1)
        self.now = self.anchor + 7200
        with self.assertLogs(level='WARNING'):
            self.core.pending_delivery_ids('operator')
        self.core.deliver('operator', eid)
        self.assertEqual(self.status()['reminders_created'], before['reminders_created'])
        self.assertEqual(self.status()['reason'], 'expired')
        self.now = self.anchor + 1000
        self.assertEqual(self.status()['reason'], 'expired')
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(len(self.sent), 1)

    def test_stale_observation_defers_queued_callback_until_fresh(self):
        self.initial_receipt()
        eid = self.tick()[0]
        self.now += 151
        self.core.deliver('operator', eid)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.core.receipt('operator', eid)['deliveries'][0]['state'], 'pending')
        self.tick(180)
        self.core.deliver('operator', eid)
        self.assertEqual(len(self.sent), 2)

    def test_unsubscribe_expiry_and_renewal_do_not_bypass_dispatch_gate(self):
        self.initial_receipt()
        eid = self.tick()[0]
        self.core.unsubscribe('operator', self.sid)
        self.core.deliver('operator', eid)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.status()['reason'], 'no_active_subscription')
        self.sid = self.subscribe()
        self.assertEqual(self.status()['reminders_created'], 1)
        self.core.deliver('operator', eid)
        self.assertEqual(len(self.sent), 1)  # expired transport rows are never reset

    def test_legacy_received_rows_cannot_gain_fabricated_receipt_timing(self):
        self.initial_receipt()
        with self.core._db() as db:
            db.execute('DELETE FROM recovery_delivery_receipts')
            db.execute('DELETE FROM recovery_followups')
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.status()['reason'], 'legacy_receipt_unavailable')
        self.assertTrue(self.status()['operator_attention'])
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0], 0)

    def test_unknown_transport_outcome_and_terminal_failure_do_not_create_semantic_retry(self):
        self.initial_receipt()
        eid = self.tick()[0]
        with self.core._db() as db:
            db.execute("UPDATE outbox SET state='sending',attempts=1 WHERE event=?", (eid,))
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.status()['reminders_created'], 1)
        self.assertEqual(self.status()['reason'], 'awaiting_delivery')
        with self.core._db() as db:
            db.execute("UPDATE outbox SET state='terminal',http_status=410 WHERE event=?", (eid,))
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.status()['reason'], 'delivery_unconfirmed')

    def test_policy_unblocked_event_precedes_followup_without_resetting_its_budget(self):
        self.initial_receipt()
        queued = self.tick()[0]
        with self.core._db() as db:
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       ('old', 'old-event', self.now-7201, self.now-7199,
                        'old-claim', self.now-7200, 'unknown'))
        self.core = self.make_core(maximum=1)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.core.deliver('operator', queued)
        self.assertEqual(len(self.sent), 1)  # policy block defers, not dispatches
        self.core = self.make_core(maximum=24)
        transition = self.core.pending_delivery_ids('operator')
        self.assertEqual(len(transition), 1)
        self.assertNotEqual(transition[0], queued)
        self.assertEqual(self.core.receipt('operator', queued)['deliveries'][0]['state'], 'cancelled')
        self.core.deliver('operator', transition[0])
        self.core.pending_delivery_ids('operator')
        self.assertEqual(self.status()['reminders_created'], 1)
        self.assertEqual(self.status()['expires_at'], self.anchor + 7200)
        second = self.tick()[0]
        self.assertEqual(self.status()['reminders_created'], 2)
        original_before = self.core.receipt('operator', self.original)
        policy_before = self.core.receipt('operator', transition[0])
        self.now += 1
        self.core.ingest(self.observation(healthy=True))
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(self.core.receipt('operator', second)['deliveries'][0]['state'], 'cancelled')
        self.assertEqual(self.core.receipt('operator', self.original), original_before)
        self.assertEqual(self.core.receipt('operator', transition[0]), policy_before)

    def test_subscription_renewal_keeps_real_receipt_and_reminder_deadline(self):
        self.initial_receipt()
        original = self.status()
        self.now += 1
        self.assertEqual(self.subscribe(), self.sid)
        self.assertEqual(self.status()['last_received_at'], original['last_received_at'])
        self.assertEqual(self.status()['next_reminder_at'], original['next_reminder_at'])

    def test_storage_caps_fail_closed_without_erasing_proof(self):
        self.initial_receipt()
        with self.core._db() as db:
            db.execute('DELETE FROM recovery_followups')
        with patch('recovery_followups.MAX_FOLLOWUP_RECORDS', 0):
            self.assertEqual(self.tick(), [])
            self.assertEqual(self.status()['reason'], 'storage_full')
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0], 1)

    def test_late_first_scheduling_persists_an_expired_deadline(self):
        self.incident()
        self.core.deliver('operator', self.original)
        received = self.now
        self.now += 7200
        self.subscribe()
        with self.core._db() as db:
            db.execute('UPDATE recovery_observation SET stamp=?,streak=3', (self.now,))
        with self.assertLogs(level='WARNING'):
            self.core.pending_delivery_ids('operator')
        self.assertEqual(self.status()['reason'], 'expired')
        self.core = self.make_core(unclaimed_reminder_lifetime_seconds=86400)
        self.assertEqual(self.status()['expires_at'], received + 7200)
        self.assertEqual(self.status()['reason'], 'expired')
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])

    def test_server_configuration_selects_followups_without_granting_execution(self):
        from server import load_recovery
        cfg = {'enable_recovery': True, 'runtime_dir': self.tmp.name,
               'allowed_callback_origins': [], 'allowed_user': 'operator',
               'recovery_unclaimed_reminder_interval_seconds': 600,
               'recovery_unclaimed_reminder_maximum': 2,
               'recovery_unclaimed_reminder_lifetime_seconds': 7200}
        state = load_recovery(cfg).get_current_recovery('operator')
        self.assertEqual(state['unclaimed_followup']['max_reminders'], 2)
        self.assertTrue(state['unclaimed_followup']['enabled'])
        self.assertFalse(state['claimable'])
        with self.assertRaises(ValueError):
            load_recovery({**cfg, 'recovery_unclaimed_reminder_interval_seconds': '600'})

    def test_authenticated_ingestion_background_path_delivers_semantic_reminder(self):
        import hashlib
        from starlette.applications import Starlette
        from starlette.testclient import TestClient
        from monitor_ingest import MonitorIngestMiddleware
        token = 'a' * 43
        app = MonitorIngestMiddleware(Starlette(), self.core,
                                     hashlib.sha256(token.encode()).hexdigest())
        with TestClient(app, base_url='https://events.example') as client:
            def post():
                response = client.post('/monitor/observations', json=self.observation(),
                                       headers={'Authorization': 'Bearer ' + token})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('incident_id', response.text)
            for _ in range(3):
                self.now += 60
                post()
            state = self.core.get_current_recovery('operator')
            self.iid = state['incident']['incident_id']
            original_event = self.sent[0]['eventId']
            end = self.now + 600
            for offset in (-120, -60, 0):
                self.now = end + offset
                post()
        self.assertEqual(len(self.sent), 2)
        self.assertNotEqual(self.sent[1]['eventId'], original_event)
        self.assertEqual(self.sent[1]['data']['incident_id'], self.iid)
        self.assertEqual(self.status()['reminders_created'], 1)
        self.assertEqual(self.core.get_current_recovery('operator')['policy']['claims_in_window'], 0)

    def test_disable_never_cancels_late_subscription_to_policy_base(self):
        self.initial_receipt()
        self.tick()
        with self.core._db() as db:
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       ('old', 'old-event', self.now-7201, self.now-7199,
                        'old-claim', self.now-7200, 'unknown'))
        self.core = self.make_core(maximum=1)
        self.core.pending_delivery_ids('operator')
        self.core = self.make_core(maximum=24)
        policy = self.core.pending_delivery_ids('operator')[0]
        self.core.deliver('operator', policy)
        self.core.pending_delivery_ids('operator')
        late = self.core.subscribe('operator', 'https://callback.example/late', SECRET)['id']
        self.assertEqual(self.core.pending_delivery_ids('operator'), [policy])
        self.core = self.make_core(interval=None)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [policy])
        with self.core._db() as db:
            record = db.execute('SELECT state FROM outbox WHERE event=? AND subscription=?',
                                (policy, late)).fetchone()
            self.assertEqual(record['state'], 'pending')
        self.assertEqual(self.status()['reminders_created'], 1)

    def test_receipt_storage_full_does_not_fabricate_timestamp(self):
        self.incident()
        with patch('recovery_followups.MAX_RECEIPT_RECORDS', 0):
            self.core.deliver('operator', self.original)
            self.assertEqual(self.core.pending_delivery_ids('operator'), [])
            self.assertEqual(self.status()['reason'], 'receipt_tracking_full')
            self.assertTrue(self.status()['operator_attention'])
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0], 0)

    def test_duplicate_deliver_call_never_refreshes_real_receipt_time(self):
        self.initial_receipt()
        original = self.status()['last_received_at']
        self.now += 1
        self.core.deliver('operator', self.original)
        self.assertEqual(self.status()['last_received_at'], original)
        self.assertEqual(len(self.sent), 1)

    def test_stuck_initial_delivery_gets_bounded_attention_without_receipt_or_replay(self):
        self.incident()
        created = self.core.get_current_recovery('operator')['incident']['created_at']
        with self.core._db() as db:
            db.execute("UPDATE outbox SET state='sending',attempts=1 WHERE event=?", (self.original,))
        self.assertEqual(self.status()['reason'], 'awaiting_delivery')
        self.assertFalse(self.status()['operator_attention'])
        self.now = created + 7200
        self.subscribe()  # Keep subscription validity distinct from callback state.
        with self.core._db() as db:
            db.execute('UPDATE recovery_observation SET stamp=?,streak=3', (self.now,))
        for restarted in (False, True):
            if restarted:
                self.core = self.make_core()
            state = self.status()
            self.assertEqual(state['reason'], 'delivery_unconfirmed')
            self.assertTrue(state['operator_attention'])
            self.assertIsNone(state['last_received_at'])
            self.assertIsNone(state['expires_at'])
            self.assertEqual(state['reminders_created'], 0)
            self.assertEqual(self.core.pending_delivery_ids('operator'), [])
            self.core.deliver('operator', self.original)
            self.assertEqual(len(self.sent), 0)
            with self.core._db() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT count(*) FROM recovery_followups').fetchone()[0], 0)

    def test_received_plus_stuck_subscription_also_gets_bounded_attention(self):
        self.incident()
        late = self.core.subscribe('operator', 'https://callback.example/second', SECRET)['id']
        self.core.pending_delivery_ids('operator')
        with self.core._db() as db:
            db.execute("UPDATE outbox SET state='sending',attempts=1 WHERE event=? AND subscription=?", (self.original, late))
        self.core.deliver('operator', self.original)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.status()['reason'], 'awaiting_delivery')
        self.now += 7200
        self.subscribe()
        self.core.subscribe('operator', 'https://callback.example/second', SECRET)
        with self.core._db() as db:
            db.execute('UPDATE recovery_observation SET stamp=?,streak=3', (self.now,))
        self.assertEqual(self.status()['reason'], 'delivery_unconfirmed')
        self.assertTrue(self.status()['operator_attention'])
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(len(self.sent), 1)

    def test_final_reminder_grace_is_response_wait_not_another_scheduled_reminder(self):
        self.initial_receipt()
        self.core = self.make_core(unclaimed_reminder_maximum=1)
        eid = self.tick()[0]
        self.core.deliver('operator', eid)
        before = self.status()
        self.assertEqual(before['reason'], 'awaiting_response')
        self.assertFalse(before['operator_attention'])
        self.assertIsNone(before['next_reminder_at'])
        self.assertEqual(before['response_deadline_at'], self.now + 600)
        self.tick(180)
        with self.core._db() as db:
            row = db.execute('SELECT last_clock FROM recovery_followups WHERE incident_id=?', (self.iid,)).fetchone()
            self.assertEqual(row['last_clock'], self.now)
        self.assertEqual(self.status()['reason'], 'awaiting_response')
        self.assertEqual(self.status()['response_deadline_at'], before['response_deadline_at'])
        self.assertIsNone(self.status()['next_reminder_at'])
        with self.assertLogs(level='WARNING'):
            self.tick(420)
        self.assertEqual(self.status()['reason'], 'exhausted')
        self.assertIsNone(self.status()['response_deadline_at'])
        self.assertEqual(len(self.sent), 2)

    def test_lifetime_must_be_strictly_longer_than_enabled_interval(self):
        with self.assertRaisesRegex(ValueError, 'greater'):
            self.make_core(interval=600, unclaimed_reminder_lifetime_seconds=600)
        self.make_core(interval=600, unclaimed_reminder_lifetime_seconds=601)

    def test_disabled_followups_keep_original_late_subscription_contract(self):
        self.initial_receipt()
        queued = self.tick()[0]
        original_receipt = self.core.receipt('operator', self.original)
        self.core = self.make_core(interval=None)
        late = self.core.subscribe('operator', 'https://callback.example/new-task', SECRET)['id']
        self.assertEqual(self.core.pending_delivery_ids('operator'), [self.original])
        self.assertEqual(self.core.receipt('operator', queued)['deliveries'][0]['state'], 'cancelled')
        self.core.deliver('operator', self.original)
        self.assertEqual(self.sent[-1]['eventId'], self.original)
        self.assertEqual(len(self.sent), 2)
        with self.core._db() as db:
            previous = db.execute('SELECT state,attempts,http_status FROM outbox WHERE event=? AND subscription=?',
                                  (self.original, self.sid)).fetchone()
            self.assertEqual(tuple(previous), ('received', 1, 200))
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_delivery_receipts WHERE subscription_id=?', (late,)).fetchone()[0], 0)
        self.assertEqual(original_receipt['deliveries'][0]['state'], 'received')
        self.assertEqual(self.status()['reminders_created'], 1)

    def test_lower_maximum_fences_a_previously_queued_higher_generation(self):
        self.initial_receipt()
        first = self.tick()[0]
        self.core.deliver('operator', first)
        second = self.tick()[0]
        self.core = self.make_core(unclaimed_reminder_maximum=1)
        self.core.deliver('operator', second)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.core.receipt('operator', second)['deliveries'][0]['state'], 'cancelled')
        with self.assertLogs(level='WARNING'):
            self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(self.status()['reason'], 'exhausted')
        self.assertEqual(self.status()['reminders_created'], 2)
        self.core = self.make_core()
        self.assertEqual(self.status()['reason'], 'exhausted')
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(self.status()['reminders_created'], 2)

    def test_receipts_are_not_collected_while_disabled_or_adopted_after_enable(self):
        self.core = self.make_core(interval=None)
        self.initial_receipt()
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_followups').fetchone()[0], 0)
        self.core = self.make_core()
        self.assertEqual(self.status()['reason'], 'legacy_receipt_unavailable')
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(self.status()['reminders_created'], 0)
        self.assertIsNone(self.status()['last_received_at'])
        self.assertEqual(len(self.sent), 1)

    def test_next_reminder_is_not_advertised_beyond_the_recorded_expiry(self):
        self.core = self.make_core(unclaimed_reminder_lifetime_seconds=601)
        self.initial_receipt()
        eid = self.tick()[0]
        self.core.deliver('operator', eid)
        self.assertEqual(self.status()['reason'], 'awaiting_response')
        self.assertIsNone(self.status()['next_reminder_at'])
        self.assertEqual(self.status()['response_deadline_at'], self.anchor + 601)
        self.now += 1
        with self.assertLogs(level='WARNING'):
            self.core.pending_delivery_ids('operator')
        self.assertEqual(self.status()['reason'], 'expired')
        self.assertEqual(self.status()['reminders_created'], 1)

    def test_read_only_exhaustion_view_does_not_adopt_terminal_or_clock_state(self):
        self.initial_receipt()
        self.core = self.make_core(unclaimed_reminder_maximum=1)
        eid = self.tick()[0]
        self.core.deliver('operator', eid)
        end = self.now + 600
        for offset in (-120, -60, 0):
            self.now = end + offset
            self.core.ingest(self.observation())
        with self.core._db() as db:
            before = tuple(db.execute('SELECT * FROM recovery_followups WHERE incident_id=?', (self.iid,)).fetchone())
            event_count = db.execute('SELECT count(*) FROM events').fetchone()[0]
        for _ in range(2):
            self.assertEqual(self.status()['reason'], 'exhausted')
        with self.core._db() as db:
            after = db.execute('SELECT * FROM recovery_followups WHERE incident_id=?', (self.iid,)).fetchone()
            self.assertEqual(tuple(after), before)
            self.assertIsNone(after['stopped_reason'])
            self.assertEqual(after['notified'], 0)
            self.assertLess(after['last_clock'], self.now)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], event_count)

    def test_stopped_semantic_queue_preserves_base_delivery_for_late_subscriptions(self):
        for stop in ('exhausted', 'expired', 'preflight'):
            with self.subTest(stop=stop):
                self.path = self.tmp.name + '/' + stop + '.sqlite'
                self.sent = []
                self.core = self.make_core(unclaimed_reminder_maximum=1)
                self.sid = self.subscribe()
                self.initial_receipt()
                queued = self.tick()[0]
                if stop == 'exhausted':
                    self.core.deliver('operator', queued)
                    with self.assertLogs(level='WARNING'):
                        self.tick()
                elif stop == 'expired':
                    end = self.anchor + 7200
                    for offset in (-120, -60, 0):
                        self.now = end + offset
                        self.core.ingest(self.observation())
                    self.subscribe()
                    with self.assertLogs(level='WARNING'):
                        self.core.pending_delivery_ids('operator')
                else:
                    self.core.report_preflight('operator', self.iid, 'approval_required')
                    self.core.pending_delivery_ids('operator')
                before = self.status()
                late = self.core.subscribe('operator', 'https://callback.example/late-' + stop, SECRET)['id']
                self.assertEqual(self.core.pending_delivery_ids('operator'), [self.original])
                self.core.deliver('operator', self.original)
                self.assertEqual(self.sent[-1]['eventId'], self.original)
                after = self.status()
                self.assertEqual(after['reminders_created'], before['reminders_created'])
                self.assertEqual(after['expires_at'], before['expires_at'])
                with self.core._db() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 2)
                    self.assertEqual(db.execute('SELECT state FROM outbox WHERE event=? AND subscription=?',
                                                (self.original, late)).fetchone()[0], 'received')
                    self.assertEqual(db.execute('SELECT state FROM outbox WHERE event=? AND subscription=?',
                                                (self.original, self.sid)).fetchone()[0], 'received')
                if stop == 'preflight':
                    self.assertEqual(self.core.get_current_recovery('operator', self.iid)['incident']['preflight_report']['reason'], 'approval_required')

    def test_deferred_semantic_queue_does_not_fall_back_to_base(self):
        self.initial_receipt()
        self.tick()
        with self.core._db() as db:
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       ('old', 'old-event', self.now-7201, self.now-7199,
                        'old-claim', self.now-7200, 'unknown'))
        self.core = self.make_core(maximum=1)
        late = self.core.subscribe('operator', 'https://callback.example/deferred-late', SECRET)['id']
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM outbox WHERE event=? AND subscription=?',
                                        (self.original, late)).fetchone()[0], 0)
        self.assertEqual(self.status()['reminders_created'], 1)

    def test_late_initial_receipt_uses_its_recorded_window_for_unknown_reminder_attention(self):
        self.incident()
        end = self.now + 8000
        for offset in (-120, -60, 0):
            self.now = end + offset
            self.core.ingest(self.observation())
        self.subscribe()
        self.core.deliver('operator', self.original)
        self.core.pending_delivery_ids('operator')
        received = self.now
        queued = self.tick()[0]
        state = self.status()
        self.assertEqual(state['reason'], 'awaiting_delivery')
        self.assertFalse(state['operator_attention'])
        self.assertEqual(state['expires_at'], received + 7200)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [queued])
        self.assertEqual(state['reminders_created'], 1)

    def test_tracked_sending_delivery_expires_without_replacing_its_receipt(self):
        self.initial_receipt()
        queued = self.tick()[0]
        with self.core._db() as db:
            db.execute("UPDATE outbox SET state='sending',attempts=1 WHERE event=?", (queued,))
        self.now = self.anchor + 7200
        with self.assertLogs(level='WARNING') as logs:
            self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(self.status()['reason'], 'expired')
        self.assertTrue(self.status()['operator_attention'])
        with patch('recovery_followups.logging.warning') as warning:
            self.core.pending_delivery_ids('operator')
            warning.assert_not_called()
        self.assertEqual(self.core.receipt('operator', queued)['deliveries'][0]['state'], 'sending')
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0], 1)
        self.assertEqual(len(self.sent), 1)

    def test_concurrent_callback_results_cannot_overfill_receipt_capacity(self):
        events = [self.core.emit_probe('operator', key) for key in ('cap-first', 'cap-second')]
        barrier = threading.Barrier(2)
        def concurrent_transport(url, body, headers):
            self.sent.append(json.loads(body))
            barrier.wait(timeout=5)
            return 200, b'{}'
        self.core.transport = concurrent_transport
        with patch('recovery_followups.MAX_RECEIPT_RECORDS', 1):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda eid: self.core.deliver('operator', eid), events))
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0], 1)
            rows = db.execute('SELECT state,attempts FROM outbox ORDER BY event').fetchall()
            self.assertEqual([tuple(row) for row in rows], [('received', 1), ('received', 1)])
        self.assertEqual(len(self.sent), 2)

    def test_reenabled_new_subscription_can_receive_same_generation_without_reset(self):
        self.initial_receipt()
        queued = self.tick()[0]
        before = self.status()
        self.core = self.make_core(interval=None)
        self.core.pending_delivery_ids('operator')
        late = self.core.subscribe('operator', 'https://callback.example/reenabled-new', SECRET)['id']
        self.assertEqual(self.core.pending_delivery_ids('operator'), [self.original])
        self.core.deliver('operator', self.original)
        self.core = self.make_core()
        self.assertEqual(self.core.pending_delivery_ids('operator'), [queued])
        self.core.deliver('operator', queued)
        self.assertEqual(self.sent[-1]['eventId'], queued)
        self.assertEqual(self.status()['reminders_created'], before['reminders_created'])
        self.assertEqual(self.status()['expires_at'], before['expires_at'])
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT state FROM outbox WHERE event=? AND subscription=?',
                                        (queued, self.sid)).fetchone()[0], 'cancelled')
            self.assertEqual(db.execute('SELECT state FROM outbox WHERE event=? AND subscription=?',
                                        (queued, late)).fetchone()[0], 'received')
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 2)

    def test_configuration_is_strict_and_disabled_by_default(self):
        disabled = RecoveryCore(self.path, [], 'operator', clock=lambda: self.now)
        self.assertFalse(disabled.get_current_recovery('operator')['unclaimed_followup']['enabled'])
        for key, value in [('unclaimed_reminder_interval_seconds', True),
                           ('unclaimed_reminder_interval_seconds', 299),
                           ('unclaimed_reminder_interval_seconds', 3601),
                           ('unclaimed_reminder_maximum', 0),
                           ('unclaimed_reminder_maximum', 4),
                           ('unclaimed_reminder_maximum', '3'),
                           ('unclaimed_reminder_lifetime_seconds', 299),
                           ('unclaimed_reminder_lifetime_seconds', 86401)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                RecoveryCore(self.path, [], 'operator', **{key: value})
