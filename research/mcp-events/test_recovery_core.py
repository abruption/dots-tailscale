import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from recovery_core import RecoveryCore, COMMAND, PREFLIGHT_REASONS, MAX_PREFLIGHT_REPORTS


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.chmod(self.tmp.name, 0o700)
        self.now = 1800000000.0
        self.path = self.tmp.name+'/recovery.sqlite'
        self.core = self.new_core()

    def tearDown(self):
        self.tmp.cleanup()

    def new_core(self):
        return RecoveryCore(self.path, [], 'operator', clock=lambda:self.now,
                            transport=lambda *args: (_ for _ in ()).throw(AssertionError('network')))

    def observation(self, healthy=False, ssh_error='timeout', ping_error=None):
        ping = {'responded':healthy,'exit_code':0 if healthy else 1}
        if ping_error:
            ping['error_class']=ping_error
        return {'observed_at':datetime.fromtimestamp(self.now,timezone.utc).isoformat(),
                'state':'verified_running' if healthy else 'external_unreachable',
                'ssh':{'command_succeeded':healthy,'probe_valid':healthy,'exit_code':0 if healthy else 255,'error_class':'ok' if healthy else ssh_error},
                'peer_ping':ping}

    def fail(self):
        self.now+=60
        return self.core.ingest(self.observation())

    def incident(self):
        self.fail(); self.fail()
        result=self.fail()
        self.assertTrue(result['claimable'])
        return result['incident']['incident_id']

    def test_three_distinct_samples_and_duplicate(self):
        self.assertIsNone(self.fail()['incident'])
        duplicate=self.core.ingest(self.observation())
        self.assertEqual(duplicate['observation']['failure_streak'],1)
        self.assertIsNone(self.fail()['incident'])
        result=self.fail()
        self.assertTrue(result['new_event_id'])
        self.assertEqual(self.fail()['incident']['incident_id'],result['incident']['incident_id'])
        self.assertIsNone(self.fail()['new_event_id'])

    def test_persistent_single_claim_and_unknown(self):
        iid=self.incident()
        claim=self.core.claim_recovery('operator',iid)
        self.assertEqual(claim['command'],COMMAND)
        self.assertEqual(claim['expires_at'],self.now+120)
        self.core=self.new_core()
        self.assertEqual(self.core.get_current_recovery('operator')['incident']['outcome'],'unknown')
        with self.assertRaises(ValueError): self.core.claim_recovery('operator',iid)
        self.now+=3601
        self.fail(); self.fail(); self.fail()
        with self.assertRaises(ValueError): self.core.claim_recovery('operator',iid)

    def test_success_requires_external_confirmation(self):
        iid=self.incident(); claim=self.core.claim_recovery('operator',iid)
        result=self.core.report_recovery('operator',iid,claim['claim_id'],'success')
        self.assertEqual(result['incident']['state'],'attempted')
        self.now+=60
        self.core.ingest(self.observation(healthy=True))
        result=self.core.get_current_recovery('operator',iid)
        self.assertEqual(result['incident']['state'],'externally_recovered')
        self.assertFalse(result['claimable'])

    def test_excluded_failures_reset_streak(self):
        for error in ['host_key_rejected','authentication_failed','connection_refused','transport_failed','invalid_probe_json','local_execution_failed']:
            self.fail(); self.fail(); self.now+=60
            self.assertIsNone(self.core.ingest(self.observation(ssh_error=error))['incident'])
            self.assertEqual(self.core.get_current_recovery('operator')['observation']['failure_streak'],0)
        self.fail(); self.fail(); self.now+=60
        self.assertIsNone(self.core.ingest(self.observation(ping_error='timeout_or_local_failure'))['incident'])

    def test_gap_and_stale_and_out_of_order(self):
        self.fail(); self.fail(); old=self.observation(); self.now+=151
        with self.assertRaises(ValueError): self.core.ingest(old)
        result=self.core.ingest(self.observation())
        self.assertEqual(result['observation']['failure_streak'],1)
        self.fail(); iid=self.fail()['incident']['incident_id']; self.now+=151
        with self.assertRaises(ValueError): self.core.claim_recovery('operator',iid)
        self.assertEqual(self.core.pending_delivery_ids('operator'),[])

    def test_ownership_and_outcomes(self):
        iid=self.incident()
        for fn in [lambda:self.core.get_current_recovery('other'),lambda:self.core.claim_recovery('other',iid),lambda:self.core.catalog('other')]:
            with self.assertRaises(ValueError): fn()
        claim=self.core.claim_recovery('operator',iid)
        with self.assertRaises(ValueError): self.core.report_recovery('operator',iid,claim['claim_id'],'arbitrary log')
        with self.assertRaises(ValueError): self.core.report_recovery('operator',iid,'wrong','failed')

    def test_daily_cap_and_hour_cooldown(self):
        for n in range(3):
            iid=self.incident(); self.core.claim_recovery('operator',iid)
            self.now+=60; self.core.ingest(self.observation(healthy=True)); self.now+=3600
        self.fail(); self.fail(); result=self.fail()
        self.assertFalse(result['claimable'])
        with self.assertRaises(ValueError): self.core.claim_recovery('operator',result['incident']['incident_id'])

    def test_hour_cooldown_and_later_fresh_claim(self):
        iid=self.incident(); self.core.claim_recovery('operator',iid)
        self.now+=60; self.core.ingest(self.observation(healthy=True))
        self.fail(); self.fail(); result=self.fail()
        self.assertFalse(result['claimable'])
        iid=result['incident']['incident_id']
        self.now+=3600
        self.fail(); self.fail(); result=self.fail()
        self.assertTrue(result['claimable'])
        self.core.claim_recovery('operator',iid)

    def test_late_subscription_backfill_and_closed_exclusion(self):
        iid=self.incident()
        with self.core._db() as db:
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,1)',('s','operator','https://example.com/','{}','unused',self.now+3600))
        ids=self.core.pending_delivery_ids('operator')
        self.assertEqual(len(ids),1)
        self.assertEqual(self.core.pending_delivery_ids('operator'),ids)
        self.now+=60; self.core.ingest(self.observation(healthy=True))
        self.assertEqual(self.core.pending_delivery_ids('operator'),[])

    def test_string_booleans_rejected(self):
        obs=self.observation(); obs['ssh']['command_succeeded']='false'
        with self.assertRaises(ValueError): self.core.ingest(obs)

    def test_preflight_is_durable_and_does_not_consume_claim(self):
        iid = self.incident()
        before = self.core.get_current_recovery('operator', iid)
        result = self.core.report_preflight('operator', iid, 'approval_required')
        self.assertTrue(result['claimable'])
        self.assertEqual(result['incident']['state'], 'pending')
        self.assertIsNone(result['incident']['outcome'])
        self.assertEqual(result['incident']['preflight_report'], {
            'reason': 'approval_required', 'reported_at': self.now, 'report_count': 1})
        self.core = self.new_core()
        self.assertEqual(self.core.get_current_recovery('operator', iid), result)
        self.assertEqual(before['incident']['event_id'], result['incident']['event_id'])
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_incidents WHERE claimed IS NOT NULL').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0], 1)
        claim = self.core.claim_recovery('operator', iid)
        self.assertTrue(claim['claim_id'])

    def test_preflight_latest_only_and_saturating_count(self):
        iid = self.incident()
        for n, reason in enumerate(sorted(PREFLIGHT_REASONS), 1):
            self.now += 1
            result = self.core.report_preflight('operator', iid, reason)
            self.assertEqual(result['incident']['preflight_report'], {
                'reason': reason, 'reported_at': self.now, 'report_count': n})
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM preflight_reports').fetchone()[0], 1)
            db.execute('UPDATE preflight_reports SET report_count=?', (MAX_PREFLIGHT_REPORTS,))
        result = self.core.report_preflight('operator', iid, 'missing_state')
        self.assertEqual(result['incident']['preflight_report']['report_count'], MAX_PREFLIGHT_REPORTS)

    def test_preflight_does_not_refresh_observation_or_bypass_claim_policy(self):
        iid = self.incident()
        self.now += 151
        result = self.core.report_preflight('operator', iid, 'computer_unavailable')
        self.assertFalse(result['observation_fresh'])
        self.assertFalse(result['claimable'])
        with self.assertRaises(ValueError):
            self.core.claim_recovery('operator', iid)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])

    def test_preflight_rejects_invalid_identity_ids_and_reasons(self):
        iid = self.incident()
        with self.assertRaises(ValueError):
            self.core.report_preflight('other', iid, 'permission_denied')
        for invalid in (None, True, [], {}, 0, '', 'private diagnostic text'):
            with self.subTest(reason=invalid), self.assertRaises(ValueError):
                self.core.report_preflight('operator', iid, invalid)
        for invalid in (None, True, [], {}, 0, '', 'x'*129, 'inc_unknown'):
            with self.subTest(incident_id=invalid), self.assertRaises(ValueError):
                self.core.report_preflight('operator', invalid, 'missing_state')
        self.assertIsNone(self.core.get_current_recovery('operator', iid)['incident']['preflight_report'])

    def test_preflight_rejects_closed_and_claimed_without_overwriting(self):
        iid = self.incident()
        self.core.report_preflight('operator', iid, 'approval_required')
        claim = self.core.claim_recovery('operator', iid)
        self.core.report_recovery('operator', iid, claim['claim_id'], 'failed')
        with self.assertRaises(ValueError):
            self.core.report_preflight('operator', iid, 'missing_state')
        state = self.core.get_current_recovery('operator', iid)
        self.assertEqual(state['incident']['outcome'], 'failed')
        self.assertEqual(state['incident']['preflight_report']['reason'], 'approval_required')
        self.now += 60
        self.core.ingest(self.observation(healthy=True))
        self.now += 3600
        iid2 = self.incident()
        self.now += 60
        self.core.ingest(self.observation(healthy=True))
        with self.assertRaises(ValueError):
            self.core.report_preflight('operator', iid2, 'missing_state')
        self.assertIsNone(self.core.get_current_recovery('operator', iid2)['incident']['preflight_report'])


if __name__=='__main__': unittest.main()
