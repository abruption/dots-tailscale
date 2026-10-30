import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from recovery_core import RecoveryCore, COMMAND, PREFLIGHT_REASONS, MAX_PREFLIGHT_REPORTS, RecoveryIneligible, MAX_ELIGIBILITY_NOTIFICATIONS


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

    def test_ping_error_class_matches_replay_failure_classification(self):
        from offline_replay import failed_sample
        for marker in ('absent', None, 'ok', 'timeout_or_local_failure',
                       'local_execution_failed', 'permission_denied', '', False):
            with self.subTest(error_class=marker):
                self.now+=60
                self.core.ingest(self.observation(healthy=True))
                expected=marker in ('absent', None, 'ok')
                for _ in range(3):
                    self.now+=60
                    observation=self.observation()
                    if marker != 'absent':
                        observation['peer_ping']['error_class']=marker
                    self.assertEqual(failed_sample(observation),expected)
                    state=self.core.ingest(observation)
                    self.assertEqual(state['observation']['ping'],
                                     'failed' if expected else 'local_error')
                self.assertEqual(state['claimable'],expected)
                self.assertEqual(state['incident'] is not None,expected)

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


    def configured_core(self, **policy):
        return RecoveryCore(self.path, [], 'operator', clock=lambda:self.now,
                            transport=lambda *args: (_ for _ in ()).throw(AssertionError('network')), **policy)

    def seed_claims(self, stamps):
        with self.core._db() as db:
            for n, stamp in enumerate(stamps):
                db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                           ('historic_'+str(n),'event_'+str(n),stamp-1,stamp+1,'claim_'+str(n),stamp,'unknown'))

    def fresh_state(self):
        with self.core._db() as db:
            db.execute('UPDATE recovery_observation SET stamp=?,ssh=?,ping=?,streak=3', (self.now,'timeout','failed'))
        return self.core.get_current_recovery('operator')

    def test_policy_bounds_and_strict_types(self):
        for value in (True, False, 0, 25, 3.0, '3', None):
            with self.subTest(maximum=value), self.assertRaises(ValueError):
                self.configured_core(max_claims_per_24h=value)
        for value in (True, False, 0, 299, 86401, 3600.0, '3600', None):
            with self.subTest(cooldown=value), self.assertRaises(ValueError):
                self.configured_core(cooldown_seconds=value)
        core=self.configured_core(max_claims_per_24h=24,cooldown_seconds=86400)
        self.assertEqual(core.get_current_recovery('operator')['policy'],
                         {'max_claims_per_24h':24,'cooldown_seconds':86400,'window_seconds':86400,'claims_in_window':0,
                          'new_incident_cooldown_seconds':None,'effective_cooldown_seconds':86400,
                          'cooldown_basis':'global'})
        self.configured_core(max_claims_per_24h=1,cooldown_seconds=300)

    def test_policy_reason_boundaries_and_combined_retry(self):
        iid=self.incident(); now=self.now
        self.seed_claims([now-86300,now-4000,now-100])
        state=self.fresh_state()
        self.assertEqual(state['eligibility_reason'],'daily_limit')
        self.assertEqual(state['retry_at'],now+3500)
        with self.assertRaises(RecoveryIneligible) as error:
            self.core.claim_recovery('operator',iid)
        self.assertEqual(error.exception.reason,'daily_limit')
        self.assertEqual(error.exception.retry_at,now+3500)
        self.now=now+100
        state=self.fresh_state()
        self.assertEqual(state['policy']['claims_in_window'],2)
        self.assertEqual(state['eligibility_reason'],'cooldown')
        self.now=now+3499.999
        self.assertFalse(self.fresh_state()['claimable'])
        self.now=now+3500
        state=self.fresh_state()
        self.assertTrue(state['claimable']);self.assertIsNone(state['retry_at'])

    def test_lowered_cap_uses_correct_expiring_claim(self):
        self.incident(); now=self.now
        self.seed_claims([now-80000,now-70000,now-60000,now-50000])
        self.core=self.configured_core(max_claims_per_24h=2)
        self.assertEqual(self.fresh_state()['retry_at'],now+26400)
        self.now+=26400
        state=self.fresh_state()
        self.assertTrue(state['claimable'])
        self.assertEqual(state['policy']['claims_in_window'],1)

    def test_eligibility_precedence_and_read_only_status(self):
        state=self.core.get_current_recovery('operator')
        self.assertEqual(state['eligibility_reason'],'no_incident')
        iid=self.incident()
        with self.core._db() as db:
            db.execute('DELETE FROM recovery_observation')
        self.assertEqual(self.core.get_current_recovery('operator')['eligibility_reason'],'observation_missing')
        self.fail();self.fail();self.fail()
        self.now+=151
        self.assertEqual(self.core.get_current_recovery('operator')['eligibility_reason'],'observation_stale')
        self.now+=1;self.core.ingest(self.observation(ssh_error='authentication_failed'))
        self.assertEqual(self.core.get_current_recovery('operator')['eligibility_reason'],'failure_not_confirmed')
        self.assertEqual(self.fail()['eligibility_reason'],'insufficient_failure_streak')
        self.fail();self.fail()
        self.core.claim_recovery('operator',iid)
        self.now+=86401
        state=self.core.get_current_recovery('operator')
        self.assertEqual(state['eligibility_reason'],'already_claimed');self.assertIsNone(state['retry_at'])
        self.core.ingest(self.observation(healthy=True))
        self.assertEqual(self.core.get_current_recovery('operator',iid)['eligibility_reason'],'externally_recovered')

    def test_unblocked_event_durable_distinct_and_preserves_original_delivery(self):
        self.seed_claims([self.now-1000])
        self.fail();self.fail();state=self.fail()
        iid=state['incident']['incident_id']; original=state['incident']['event_id']
        self.assertEqual(state['eligibility_reason'],'cooldown')
        with self.core._db() as db:
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,1)',('s','operator','https://example.com/','{}','unused',self.now+86400))
            db.execute("INSERT INTO outbox VALUES(?,?,'delivered',2,200)",(original,'s'))
        self.now=state['retry_at']
        self.assertEqual(self.core.pending_delivery_ids('operator'),[]) # stale must not emit
        self.fail();self.fail();state=self.fail()
        reminder=state['new_event_id']
        self.assertIsNotNone(reminder);self.assertNotEqual(reminder,original)
        self.assertEqual(state['incident']['event_id'],original)
        self.assertEqual(self.core.pending_delivery_ids('operator'),[reminder])
        self.core=self.new_core()
        self.assertEqual(self.core.pending_delivery_ids('operator'),[reminder])
        with self.core._db() as db:
            self.assertEqual(tuple(db.execute('SELECT state,attempts,http_status FROM outbox WHERE event=?',(original,)).fetchone()),('delivered',2,200))
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],2)
            body=json.loads(db.execute('SELECT body FROM events WHERE id=?',(reminder,)).fetchone()[0])
            self.assertEqual(body['data']['incident_id'],iid)
            self.assertEqual(body['eventId'],reminder)
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,1)',('late','operator','https://example.com/','{}','unused',self.now+3600))
        self.assertEqual(self.core.pending_delivery_ids('operator'),[reminder])
        self.core.claim_recovery('operator',iid)
        self.assertEqual(self.core.pending_delivery_ids('operator'),[])

    def test_config_raise_retains_ledger_and_bounds_notifications(self):
        self.seed_claims([self.now-7200])
        self.core=self.configured_core(max_claims_per_24h=1)
        self.fail();self.fail();state=self.fail();iid=state['incident']['incident_id']
        original=state['incident']['event_id']
        for generation in range(1,6):
            self.core=self.configured_core(max_claims_per_24h=1)
            self.core.pending_delivery_ids('operator')
            self.core=self.configured_core(max_claims_per_24h=12)
            # Read-only status must not emit an event.
            with self.core._db() as db: before=db.execute('SELECT count(*) FROM events').fetchone()[0]
            state=self.core.get_current_recovery('operator')
            with self.core._db() as db: self.assertEqual(before,db.execute('SELECT count(*) FROM events').fetchone()[0])
            self.assertTrue(state['claimable'])
            self.core.pending_delivery_ids('operator')
            with self.core._db() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1+min(generation,MAX_ELIGIBILITY_NOTIFICATIONS))
                self.assertEqual(db.execute('SELECT count(*) FROM recovery_incidents WHERE claim_id IS NOT NULL').fetchone()[0],1)
            self.assertEqual(state['incident']['event_id'],original)
        claim=self.core.claim_recovery('operator',iid)
        self.core=self.configured_core(max_claims_per_24h=24,cooldown_seconds=300)
        with self.assertRaises(RecoveryIneligible):self.core.claim_recovery('operator',iid)
        self.assertEqual(self.core.get_current_recovery('operator')['policy']['claims_in_window'],2)

    def test_concurrent_unblocked_notification_and_claim_are_single(self):
        self.seed_claims([self.now-7200])
        self.core=self.configured_core(max_claims_per_24h=1)
        self.fail();self.fail();state=self.fail();iid=state['incident']['incident_id']
        self.core=self.configured_core(max_claims_per_24h=12)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:self.core.pending_delivery_ids('operator'),range(8)))
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT notification_count FROM recovery_eligibility WHERE incident_id=?',(iid,)).fetchone()[0],1)
        def claim(_):
            try:
                self.core.claim_recovery('operator',iid)
                return True
            except RecoveryIneligible:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim,range(8))),1)

    def test_no_unblocked_notification_after_external_recovery(self):
        self.seed_claims([self.now-1000])
        self.fail();self.fail();state=self.fail()
        self.now+=1;self.core.ingest(self.observation(healthy=True))
        self.now+=4000
        self.assertEqual(self.core.pending_delivery_ids('operator'),[])
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)



class NewIncidentCooldownTests(unittest.TestCase):
    setUp = RecoveryTests.setUp
    tearDown = RecoveryTests.tearDown
    observation = RecoveryTests.observation

    def new_core(self):
        return RecoveryTests.configured_core(self, new_incident_cooldown_seconds=600)

    def failed_sample(self):
        self.now += 60
        return self.core.ingest(self.observation())

    def incident(self):
        self.failed_sample(); self.failed_sample()
        state = self.failed_sample()
        self.assertTrue(state['claimable'])
        return state['incident']['incident_id']

    def healthy(self, seconds=30):
        self.now += seconds
        return self.core.ingest(self.observation(healthy=True))

    def prior(self, outcome='success', offsets=(10, 40, 70)):
        iid = self.incident(); claim = self.core.claim_recovery('operator', iid)
        claimed = self.now
        self.core.report_recovery('operator', iid, claim['claim_id'], outcome)
        for offset in offsets:
            self.now = claimed + offset
            self.core.ingest(self.observation(healthy=True))
        return iid, claim, claimed

    def outage(self):
        self.failed_sample(); self.failed_sample()
        return self.failed_sample()

    def fresh_failure_at(self, stamp):
        while self.now + 120 < stamp:
            self.now += 120
            self.core.ingest(self.observation())
        self.now = stamp
        return self.core.ingest(self.observation())

    def proof(self, incident):
        with self.core._db() as db:
            row = db.execute('SELECT * FROM recovery_confirmations WHERE incident_id=?', (incident,)).fetchone()
            return dict(row) if row is not None else None

    def test_optional_policy_strict_bounds_and_default_compatibility(self):
        for value in (True, False, 0, 299, 3601, 600.0, '600', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RecoveryTests.configured_core(self, new_incident_cooldown_seconds=value)
        for value in (None, 300, 3600):
            core = RecoveryTests.configured_core(self, new_incident_cooldown_seconds=value)
            self.assertEqual(core.get_current_recovery('operator')['policy']['new_incident_cooldown_seconds'], value)
        self.core = RecoveryTests.configured_core(self)
        self.prior()
        state = self.outage()
        self.assertEqual(state['policy']['cooldown_basis'], 'global')
        self.assertEqual(state['policy']['effective_cooldown_seconds'], 3600)

    def test_qualified_new_outage_obeys_short_boundary_and_single_claim(self):
        prior, _, claimed = self.prior()
        state = self.outage(); iid = state['incident']['incident_id']
        self.assertNotEqual(iid, prior)
        self.assertEqual(state['policy']['cooldown_seconds'], 3600)
        self.assertEqual(state['policy']['effective_cooldown_seconds'], 600)
        self.assertEqual(state['policy']['cooldown_basis'], 'confirmed_recovery')
        self.assertEqual(state['eligibility_reason'], 'cooldown')
        self.assertEqual(state['retry_at'], claimed + 600)
        self.assertFalse(self.fresh_failure_at(claimed + 599.999)['claimable'])
        self.assertTrue(self.fresh_failure_at(claimed + 600)['claimable'])
        claim = self.core.claim_recovery('operator', iid)
        self.assertEqual(claim['command'], COMMAND)
        self.assertEqual(claim['expires_at'], self.now + 120)
        with self.assertRaises(RecoveryIneligible): self.core.claim_recovery('operator', iid)

    def test_success_report_with_one_healthy_sample_is_not_confirmation(self):
        prior, _, claimed = self.prior(offsets=(60,))
        self.assertIsNone(self.proof(prior)['confirmed_at'])
        state = self.outage()
        self.assertEqual(state['policy']['cooldown_basis'], 'global')
        self.assertEqual(state['retry_at'], claimed + 3600)

    def test_three_samples_need_elapsed_span_and_status_cannot_complete_it(self):
        prior, _, claimed = self.prior(offsets=(1, 2, 3))
        self.assertEqual(self.proof(prior)['streak'], 3)
        self.assertIsNone(self.proof(prior)['confirmed_at'])
        self.now = claimed + 61
        self.core.get_current_recovery('operator')
        self.assertIsNone(self.proof(prior)['confirmed_at'])
        self.assertEqual(self.outage()['policy']['cooldown_basis'], 'global')

    def test_duplicate_rejected_and_status_reads_do_not_advance_proof(self):
        prior, _, _ = self.prior(offsets=(20,))
        before = self.proof(prior); same = self.observation(healthy=True)
        for _ in range(3):
            self.core.ingest(same); self.core.get_current_recovery('operator')
        self.assertEqual(self.proof(prior), before)
        self.now += 1
        older = self.observation(healthy=True)
        older['observed_at'] = datetime.fromtimestamp(self.now-2, timezone.utc).isoformat()
        with self.assertRaises(ValueError): self.core.ingest(older)
        malformed = self.observation(healthy=True); malformed['ssh']['command_succeeded'] = 'true'
        with self.assertRaises(ValueError): self.core.ingest(malformed)
        self.assertEqual(self.proof(prior), before)
        self.now += 151
        with self.assertRaises(ValueError): self.core.ingest(same)
        self.assertEqual(self.proof(prior), before)
        self.core.ingest(self.observation(healthy=True))
        self.assertEqual(self.proof(prior)['streak'], 1)
        self.assertIsNone(self.proof(prior)['confirmed_at'])

    def test_fresh_healthy_gap_resets_streak(self):
        prior, _, claimed = self.prior(offsets=(1, 152, 212))
        self.assertEqual(self.proof(prior)['streak'], 2)
        self.assertIsNone(self.proof(prior)['confirmed_at'])
        self.healthy(60)
        self.assertEqual(self.proof(prior)['confirmed_at'], claimed+272)
        self.assertEqual(self.outage()['policy']['cooldown_basis'], 'confirmed_recovery')

    def test_accepted_error_partial_state_and_contradiction_reset_streak(self):
        prior, _, _ = self.prior(offsets=(30, 60))
        for bad in ('local_error', 'partial', 'probe_failed', 'contradictory_ping'):
            with self.subTest(bad=bad):
                self.now += 30
                observation = self.observation(healthy=True)
                if bad == 'local_error':
                    observation['ssh'].update(command_succeeded=False, probe_valid=False, exit_code=255,
                                              error_class='authentication_failed')
                elif bad == 'partial': observation['peer_ping'].update(responded=False, exit_code=1)
                elif bad == 'probe_failed': observation['state'] = 'probe_failed'
                else: observation['peer_ping']['error_class'] = 'local_execution_failed'
                self.core.ingest(observation)
                self.assertEqual(self.proof(prior)['streak'], 0)
                self.assertIsNone(self.proof(prior)['confirmed_at'])
                self.healthy(30); self.healthy(30)
                self.assertEqual(self.proof(prior)['streak'], 2)
                self.assertIsNone(self.proof(prior)['confirmed_at'])
        self.healthy(30)
        self.assertIsNotNone(self.proof(prior)['confirmed_at'])

    def test_preclaim_timestamp_cannot_count_as_postclaim_evidence(self):
        iid = self.incident(); claim = self.core.claim_recovery('operator', iid)
        self.core.report_recovery('operator', iid, claim['claim_id'], 'success')
        with self.assertRaises(ValueError): self.core.ingest(self.observation(healthy=True))
        self.assertEqual(self.proof(iid)['streak'], 0)
        self.assertIsNone(self.proof(iid)['confirmed_at'])
        self.healthy(1)
        self.assertEqual(self.proof(iid)['streak'], 1)

    def test_confirmation_survives_restart_and_subsequent_failed_samples(self):
        prior, _, claimed = self.prior(); proof = self.proof(prior)
        state = self.outage()
        self.assertEqual(self.proof(prior), proof)
        self.core = self.new_core()
        after = self.core.get_current_recovery('operator')
        self.assertEqual(after, {k:v for k,v in state.items() if k!='new_event_id'})
        self.assertEqual(after['retry_at'], claimed + 600)
        self.assertEqual(self.proof(prior), proof)

    def test_legacy_closed_row_does_not_infer_confirmation(self):
        prior, _, claimed = self.prior()
        with self.core._db() as db:
            db.execute('DELETE FROM recovery_confirmations WHERE incident_id=?', (prior,))
        self.core = self.new_core()
        state = self.outage()
        self.assertEqual(state['policy']['cooldown_basis'], 'global')
        self.assertEqual(state['retry_at'], claimed + 3600)

    def test_unknown_failed_and_blocked_reports_keep_global_policy(self):
        # Fresh databases isolate each outcome without altering the claim ledger.
        for outcome in ('unknown', 'failed', 'blocked'):
            with self.subTest(outcome=outcome):
                path = self.path
                self.path = path + '-' + outcome
                self.core = self.new_core()
                _, _, claimed = self.prior(outcome=outcome)
                state = self.outage()
                self.assertEqual(state['policy']['cooldown_basis'], 'global')
                self.assertEqual(state['retry_at'], claimed + 3600)
                self.path = path

    def test_legacy_and_rollback_code_claims_never_gain_proof_slot(self):
        for stored_outcome in ('unknown', 'success'):
            with self.subTest(stored_outcome=stored_outcome):
                old_path = self.path; self.path = old_path + '-legacy-' + stored_outcome
                self.core = self.new_core()
                iid = self.incident(); claimed = self.now
                # The old claim implementation updates its original row only,
                # even when additive tables already exist after a code rollback.
                with self.core._db() as db:
                    db.execute('UPDATE recovery_incidents SET claim_id=?,claimed=?,outcome=? WHERE id=?',
                               ('legacy-claim', claimed, stored_outcome, iid))
                self.assertIsNone(self.proof(iid))
                self.healthy(10); self.healthy(30); self.healthy(30)
                self.assertIsNone(self.proof(iid))
                # A new-code report cannot retroactively seed a slot either.
                self.core.report_recovery('operator',iid,'legacy-claim','success')
                self.healthy(30); self.healthy(30); self.healthy(30)
                self.assertIsNone(self.proof(iid))
                state = self.outage()
                self.assertEqual(state['policy']['cooldown_basis'], 'global')
                self.assertEqual(state['retry_at'], claimed + 3600)
                self.path = old_path

    def test_claim_and_prospective_slot_are_one_atomic_write(self):
        import sqlite3
        iid = self.incident()
        with self.core._db() as db:
            db.execute('INSERT INTO recovery_confirmations VALUES(?,NULL,NULL,0,NULL)',(iid,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.core.claim_recovery('operator',iid)
        state = self.core.get_current_recovery('operator',iid)
        self.assertEqual(state['incident']['state'],'pending')
        self.assertEqual(state['policy']['claims_in_window'],0)
        with self.core._db() as db:
            self.assertIsNone(db.execute('SELECT claim_id FROM recovery_incidents WHERE id=?',(iid,)).fetchone()[0])

    def test_explicit_unknown_first_report_remains_global_after_success(self):
        iid = self.incident(); claim = self.core.claim_recovery('operator',iid)
        claimed = self.now
        first = self.core.report_recovery('operator',iid,claim['claim_id'],'unknown')
        self.assertEqual(first['incident']['first_report'],
                         {'outcome':'unknown','reported_at':claimed})
        self.now += 1
        self.core.report_recovery('operator',iid,claim['claim_id'],'success')
        self.healthy(10); self.healthy(30); self.healthy(30)
        self.assertIsNotNone(self.proof(iid)['confirmed_at'])
        state = self.outage()
        self.assertEqual(state['policy']['cooldown_basis'], 'global')
        self.assertEqual(state['retry_at'], claimed + 3600)
        historical = self.core.get_current_recovery('operator',iid)
        self.assertEqual(historical['incident']['outcome'],'success')
        self.assertEqual(historical['incident']['first_report'],
                         {'outcome':'unknown','reported_at':claimed})

    def test_unreported_initial_unknown_then_first_success_can_qualify(self):
        iid = self.incident(); claim = self.core.claim_recovery('operator',iid)
        state = self.core.get_current_recovery('operator',iid)
        self.assertEqual(state['incident']['outcome'],'unknown')
        self.assertIsNone(state['incident']['first_report'])
        self.healthy(10); self.healthy(30); self.healthy(30)
        self.core.report_recovery('operator',iid,claim['claim_id'],'success')
        state = self.outage()
        self.assertEqual(state['policy']['cooldown_basis'],'confirmed_recovery')

    def test_first_report_is_idempotent_and_rejected_reports_do_not_change_it(self):
        iid = self.incident(); claim = self.core.claim_recovery('operator',iid)
        self.core.report_recovery('operator',iid,claim['claim_id'],'success')
        expected = {'outcome':'success','reported_at':self.now}
        self.now += 10
        self.core.report_recovery('operator',iid,claim['claim_id'],'success')
        for args in [('operator',iid,claim['claim_id'],'failed'),
                     ('operator',iid,'wrong','unknown'),
                     ('other',iid,claim['claim_id'],'unknown'),
                     ('operator',iid,claim['claim_id'],'private text')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.core.report_recovery(*args)
        self.assertEqual(self.core.get_current_recovery('operator',iid)['incident']['first_report'],expected)
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_reports').fetchone()[0],1)

    def test_delayed_preclaim_healthy_timestamp_is_accepted_but_cannot_advance_slot(self):
        iid = self.incident(); before_claim = self.now
        self.now += 30
        claim = self.core.claim_recovery('operator',iid)
        self.core.report_recovery('operator',iid,claim['claim_id'],'success')
        self.now += 15
        delayed = self.observation(healthy=True)
        delayed['observed_at'] = datetime.fromtimestamp(before_claim+10,timezone.utc).isoformat()
        # Chronological relative to the previous accepted observation, yet older
        # than the claim itself: exercise _track_confirmation's own time guard.
        result = self.core.ingest(delayed)
        self.assertEqual(result['observation']['observed_at'],before_claim+10)
        self.assertEqual(self.core.get_current_recovery('operator',iid)['incident']['state'],'externally_recovered')
        self.assertEqual(self.proof(iid)['streak'],0)
        self.assertIsNone(self.proof(iid)['since'])
        self.assertIsNone(self.proof(iid)['confirmed_at'])
        self.healthy(30)
        self.assertEqual(self.proof(iid)['streak'],1)

    def test_latest_claim_cannot_reuse_an_older_confirmation(self):
        prior, _, claimed = self.prior(); self.outage()
        state = self.fresh_failure_at(claimed + 600); iid = state['incident']['incident_id']
        self.core.claim_recovery('operator', iid)
        self.healthy(60)
        state = self.outage()
        self.assertEqual(state['policy']['cooldown_basis'], 'global')
        self.assertEqual(state['retry_at'], claimed + 4200)
        old = self.core.get_current_recovery('operator', prior)
        self.assertEqual(old['eligibility_reason'], 'externally_recovered')
        self.assertEqual(old['policy']['cooldown_basis'], 'global')

    def test_ambiguous_latest_claim_or_open_incident_keeps_global_policy(self):
        prior, _, claimed = self.prior()
        state = self.outage(); current = state['incident']['incident_id']
        with self.core._db() as db:
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                       ('tied-history','tied-event',claimed-1,claimed+1,'tied-claim',claimed,'success'))
        self.assertEqual(self.core.get_current_recovery('operator',current)['policy']['cooldown_basis'], 'global')
        with self.core._db() as db:
            db.execute('DELETE FROM recovery_incidents WHERE id=?', ('tied-history',))
            db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,NULL,NULL,NULL,NULL)',
                       ('ambiguous-open','ambiguous-event',self.now))
        for iid in (current, 'ambiguous-open'):
            self.assertEqual(self.core.get_current_recovery('operator',iid)['policy']['cooldown_basis'], 'global')
        self.assertIsNotNone(self.proof(prior)['confirmed_at'])

    def test_daily_budget_dominates_shorter_cooldown(self):
        self.core = RecoveryTests.configured_core(self, max_claims_per_24h=1,
                                                 new_incident_cooldown_seconds=600)
        _, _, claimed = self.prior(); state = self.outage()
        self.assertEqual(state['policy']['effective_cooldown_seconds'], 600)
        self.assertEqual(state['eligibility_reason'], 'daily_limit')
        self.assertEqual(state['retry_at'], claimed + 86400)
        with self.assertRaises(RecoveryIneligible):
            self.core.claim_recovery('operator', state['incident']['incident_id'])

    def test_short_reminder_durable_and_concurrent_claim_stays_single(self):
        _, _, claimed = self.prior(); state = self.outage(); iid = state['incident']['incident_id']
        original = state['incident']['event_id']
        with self.core._db() as db:
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,1)',
                       ('short','operator','https://example.com/','{}','unused',self.now+86400))
            db.execute("INSERT INTO outbox VALUES(?,?,'delivered',2,200)",(original,'short'))
        self.now = claimed + 600
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])
        self.failed_sample(); self.failed_sample(); state = self.failed_sample()
        reminder = state['new_event_id']
        self.assertIsNotNone(reminder); self.assertNotEqual(reminder, original)
        self.core = self.new_core()
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda _:self.core.pending_delivery_ids('operator'), range(8)))
        self.assertTrue(all(ids == [reminder] for ids in rows))
        with self.core._db() as db:
            self.assertEqual(tuple(db.execute('SELECT state,attempts,http_status FROM outbox WHERE event=?',
                                             (original,)).fetchone()), ('delivered',2,200))
            self.assertEqual(db.execute('SELECT notification_count FROM recovery_eligibility WHERE incident_id=?',
                                        (iid,)).fetchone()[0], 1)
        def claim(_):
            try: self.core.claim_recovery('operator', iid); return True
            except RecoveryIneligible: return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim,range(8))), 1)
        self.assertEqual(self.core.pending_delivery_ids('operator'), [])


if __name__=='__main__': unittest.main()
