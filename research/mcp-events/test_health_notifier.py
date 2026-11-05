import datetime as dt
import io
import json
import os
from pathlib import Path
import ssl
import time
import multiprocessing
from bounded_network import bounded_call
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from health_notifier import (assess, run_once, read_file, read_telegram, telegram_send,
                             validate_config, notification_text, recovery_warning_text)
from monitor_relay import save_private

NOW = 1800000000.0


def stamp(when):
    return dt.datetime.fromtimestamp(when,dt.timezone.utc).isoformat()


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.config={key:str(root/name) for key,name in
            [('relay_health_file','health.json'),('observation_file','observation.json'),
             ('state_file','notifier.json'),('telegram_config_file','telegram.json')]}
        save_private(self.config['telegram_config_file'],{'token':'123456789:'+'a'*35,'chat_id':'-1234567'})
        self.healthy(NOW)
        self.sent=[]

    def healthy(self,now):
        save_private(self.config['relay_health_file'],{'checked_at':stamp(now),
            'delivered_at':stamp(now),'failure_class':'ok'})
        save_private(self.config['observation_file'],{'observed_at':stamp(now)})

    def send(self,config,text):
        self.sent.append(text)
        return 'sent'

    def run_at(self,now):
        return run_once(self.config,now,self.send)

    def test_healthy_no_notification_and_episode_reset(self):
        self.assertEqual(self.run_at(NOW),'healthy')
        Path(self.config['observation_file']).unlink()
        self.assertEqual(self.run_at(NOW+1),'grace_period')
        self.healthy(NOW+100)
        self.assertEqual(self.run_at(NOW+100),'healthy')
        state=read_file(self.config['state_file'],private=True)
        self.assertFalse(state['degraded']);self.assertNotIn('first_failure_at',state)
        self.assertEqual(self.sent,[])

    def test_exhaustion_notice_uses_budget_release_as_reference_not_permission(self):
        release = stamp(NOW + 600)
        warnings = {'claims_issued': 24, 'cap': 24, 'warning_threshold': 20,
                    'first_reports': {'success': 0, 'failed': 0, 'blocked': 0,
                                      'unknown': 5, 'missing': 17, 'other': 2},
                    'current_outcomes': {'success': 0, 'failed': 0, 'blocked': 0,
                                         'unknown': 16, 'missing': 5, 'other': 3},
                    'externally_closed': 3, 'sustained_confirmed': 1,
                    'budget_release_at': release, 'as_of': stamp(NOW),
                    'followup_reason': None}
        message = recovery_warning_text(warnings, ['claim_budget_exhausted'])
        self.assertIn('claim_grants_issued_in_rolling_24h: 24/24', message)
        self.assertIn('earliest_budget_release_reference_utc: ' + release, message)
        self.assertIn('time alone is not authorization', message)
        self.assertIn('first_report_outcomes', message)
        self.assertIn('first_report_outcomes_success_failed_blocked_unknown_missing_other: 0/0/0/5/17/2', message)
        self.assertIn('current_outcome_labels_success_failed_blocked_unknown_missing_other: 0/0/0/16/5/3', message)
        self.assertIn('externally_closed_vs_sustained_confirmed: 3/1', message)

    def test_awaiting_response_with_deadline_does_not_trigger_operator_warning(self):
        self.config['operator_recovery_warnings'] = True
        now = NOW + 30
        followup = {'available': True, 'operator_attention': False,
                    'reason_code': None, 'awaiting_response': True,
                    'response_deadline_at': stamp(now + 300)}
        summary = {'schema_version': 1, 'as_of': stamp(now), 'window_seconds': 86400,
                   'cap': 24, 'claims_issued': 1, 'budget_release_at': None,
                   'first_reports': {'success': 0, 'failed': 0, 'blocked': 0,
                                     'unknown': 1, 'missing': 0, 'other': 0},
                   'current_outcomes': {'success': 0, 'failed': 0, 'blocked': 0,
                                        'unknown': 1, 'missing': 0, 'other': 0},
                   'externally_closed': 0, 'sustained_confirmed': 0,
                   'unclaimed_followup': followup}
        health = {'checked_at': stamp(now), 'delivered_at': stamp(now),
                  'failure_class': 'ok', 'recovery_diagnostics_status': 'current',
                  'recovery_diagnostics': summary}
        save_private(self.config['relay_health_file'], health)
        save_private(self.config['observation_file'], {'observed_at': stamp(now)})
        self.assertEqual(run_once(self.config, now, self.send), 'healthy')
        self.assertEqual(self.sent, [])

    def test_ssh_transport_with_opted_in_warnings_is_an_unsupported_monitor_fault(self):
        self.config['operator_recovery_warnings'] = True
        now = NOW + 200
        save_private(self.config['relay_health_file'], {
            'checked_at': stamp(now), 'delivered_at': stamp(now),
            'failure_class': 'ok', 'recovery_diagnostics_status': 'unsupported'})
        save_private(self.config['observation_file'], {'observed_at': stamp(now)})
        report = assess(self.config, now)
        self.assertIn('recovery_diagnostics_unsupported', report['reasons'])
        self.assertEqual(run_once(self.config, now, self.send), 'recovery_diagnostics_unknown')
        self.assertEqual(self.sent, [])
        self.assertEqual(run_once(self.config, now + 180, self.send), 'sent')
        self.assertIn('recovery_diagnostics_unsupported', self.sent[-1])

    def test_corrupt_or_future_warning_state_survives_and_uses_monitor_grace_notice(self):
        self.config['operator_recovery_warnings'] = True
        future_state = {
            'claim_budget_near_limit': {'active': True, 'last_attempt_at': stamp(NOW + 3600),
                                        'last_outcome': 'sent', 'reason_code': None},
            'claim_budget_exhausted': {'active': False, 'last_attempt_at': None,
                                       'last_outcome': None, 'reason_code': None},
            'followup_operator_attention': {'active': False, 'last_attempt_at': None,
                                           'last_outcome': None, 'reason_code': None},
        }
        corrupt = {'operator_warning_state': {'unrecognized': 'preserve this raw value'}}
        for suffix, prior in (('corrupt', corrupt), ('future', {'operator_warning_state': future_state})):
            with self.subTest(state=suffix):
                state_path = Path(self.config['state_file'])
                local_config = dict(self.config, state_file=str(state_path.with_name(suffix + '.json')))
                save_private(local_config['state_file'], prior)
                self.assertEqual(run_once(local_config, NOW, self.send), 'grace_period')
                saved = read_file(local_config['state_file'], private=True)
                self.assertEqual(saved['operator_warning_state'], prior['operator_warning_state'])
                self.assertIn('operator_warning_state_unavailable', saved['reasons'])
                self.assertEqual(run_once(local_config, NOW + 180, self.send), 'sent')
                saved = read_file(local_config['state_file'], private=True)
                self.assertEqual(saved['operator_warning_state'], prior['operator_warning_state'])
                self.assertIn('operator_warning_state_unavailable', self.sent[-1])

    def test_base_notification_failure_is_not_overwritten_by_warning_result(self):
        self.config['operator_recovery_warnings'] = True
        now = NOW + 200
        warning = {'available': True, 'operator_attention': False, 'reason_code': None,
                   'awaiting_response': False, 'response_deadline_at': None}
        summary = {'schema_version': 1, 'as_of': stamp(now), 'window_seconds': 86400,
                   'cap': 24, 'claims_issued': 20, 'budget_release_at': None,
                   'first_reports': {'success': 0, 'failed': 0, 'blocked': 0,
                                     'unknown': 5, 'missing': 15, 'other': 0},
                   'current_outcomes': {'success': 0, 'failed': 0, 'blocked': 0,
                                        'unknown': 20, 'missing': 0, 'other': 0},
                   'externally_closed': 0, 'sustained_confirmed': 0,
                   'unclaimed_followup': warning}
        save_private(self.config['relay_health_file'], {
            'checked_at': stamp(now), 'delivered_at': stamp(now), 'failure_class': 'ok',
            'recovery_diagnostics_status': 'current', 'recovery_diagnostics': summary})
        save_private(self.config['observation_file'], {'observed_at': stamp(NOW - 200)})
        outcomes = iter(('notification_timeout', 'sent'))
        calls = []
        def sender(config, text):
            calls.append(text)
            return next(outcomes)
        self.assertEqual(run_once(self.config, now, sender), 'notification_timeout')
        self.assertEqual(len(calls), 2)
        state = read_file(self.config['state_file'], private=True)
        self.assertEqual(state['last_notification_outcome'], 'notification_timeout')
        self.assertEqual(state['operator_warning_state']['claim_budget_near_limit']['last_outcome'], 'sent')

    def test_base_notification_failure_is_not_overwritten_by_warning_cooldown(self):
        self.config['operator_recovery_warnings'] = True
        warning = {'available': True, 'operator_attention': False, 'reason_code': None,
                   'awaiting_response': False, 'response_deadline_at': None}
        def install_health(now, count):
            summary = {'schema_version': 1, 'as_of': stamp(now), 'window_seconds': 86400,
                       'cap': 24, 'claims_issued': count, 'budget_release_at': None,
                       'first_reports': {'success': 0, 'failed': 0, 'blocked': 0,
                                         'unknown': count, 'missing': 0, 'other': 0},
                       'current_outcomes': {'success': 0, 'failed': 0, 'blocked': 0,
                                            'unknown': count, 'missing': 0, 'other': 0},
                       'externally_closed': 0, 'sustained_confirmed': 0,
                       'unclaimed_followup': warning}
            save_private(self.config['relay_health_file'], {
                'checked_at': stamp(now), 'delivered_at': stamp(now), 'failure_class': 'ok',
                'recovery_diagnostics_status': 'current', 'recovery_diagnostics': summary})
        install_health(NOW, 20)
        self.assertEqual(run_once(self.config, NOW, self.send), 'sent')
        self.assertEqual(len(self.sent), 1)
        install_health(NOW + 180, 20)
        save_private(self.config['observation_file'], {'observed_at': stamp(NOW - 200)})
        calls=[]
        def fail_base(config, text):
            calls.append(text)
            return 'notification_timeout'
        self.assertEqual(run_once(self.config, NOW + 180, fail_base), 'notification_timeout')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.sent[-1].count('claim_grants_issued_in_rolling_24h'), 1)

    def test_dead_relay_source_staleness_and_grace(self):
        self.assertEqual(self.run_at(NOW+151),'grace_period')
        self.assertEqual(self.run_at(NOW+269),'grace_period')
        self.assertEqual(self.run_at(NOW+270),'sent')
        self.assertEqual(len(self.sent),1)
        report=assess(self.config,NOW+270)
        self.assertIn('relay_health_stale',report['reasons'])
        self.assertIn('observer_stale',report['reasons'])
        self.assertIn('delivery_stale',report['reasons'])
        self.assertIn('last_delivery_age_seconds: 270',self.sent[0])
        self.assertNotIn(self.tmp.name,self.sent[0])

    def test_unreadable_sources_require_first_seen_grace(self):
        Path(self.config['relay_health_file']).unlink()
        Path(self.config['observation_file']).write_text('not JSON SECRET')
        self.assertEqual(self.run_at(NOW),'grace_period')
        self.assertEqual(self.run_at(NOW+179),'grace_period')
        self.assertEqual(self.run_at(NOW+180),'sent')
        self.assertNotIn('SECRET',self.sent[0])
        self.assertIn('observation_unavailable',self.sent[0])

    def test_auth_failed_first_failure_and_restart_cooldown(self):
        save_private(self.config['relay_health_file'],{'checked_at':stamp(NOW),
            'delivered_at':stamp(NOW),'failure_class':'auth_required','first_failure_at':stamp(NOW-180)})
        self.assertEqual(self.run_at(NOW),'sent')
        self.healthy(NOW+100)
        save_private(self.config['relay_health_file'],{'checked_at':stamp(NOW+100),
            'delivered_at':stamp(NOW),'failure_class':'auth_required','first_failure_at':stamp(NOW-180)})
        self.assertEqual(self.run_at(NOW+100),'cooldown')
        self.assertEqual(self.run_at(NOW+3599),'cooldown')
        self.assertEqual(self.run_at(NOW+3600),'sent')
        self.assertEqual(len(self.sent),2)

    def test_subscription_inactive_grace_and_old_ack_unknown(self):
        self.assertEqual(self.run_at(NOW),'healthy')
        for offset in (1,180,181):
            self.healthy(NOW+offset)
            health=read_file(self.config['relay_health_file'])
            health['subscription_active']=False
            save_private(self.config['relay_health_file'],health)
            expected='sent' if offset==181 else 'grace_period'
            self.assertEqual(self.run_at(NOW+offset),expected)
        self.assertIn('subscription_inactive',self.sent[0])
        self.healthy(NOW+200)
        self.assertEqual(self.run_at(NOW+200),'healthy')
        self.assertEqual(len(self.sent),1)

    def test_attempt_saved_before_unknown_io_and_failure_cooldown(self):
        Path(self.config['relay_health_file']).unlink()
        def fail(config,text):
            state=read_file(self.config['state_file'],private=True)
            self.assertEqual(state['last_attempt_at'],stamp(NOW+180))
            self.assertEqual(state['last_notification_outcome'],'unknown')
            raise RuntimeError('TOKEN URL SECRET')
        self.assertEqual(self.run_at(NOW),'grace_period')
        self.assertEqual(run_once(self.config,NOW+180,fail),'unknown')
        self.assertEqual(self.run_at(NOW+181),'cooldown')
        self.assertNotIn('SECRET',Path(self.config['state_file']).read_text())

    def test_observer_stale_even_with_fresh_relay_health(self):
        save_private(self.config['observation_file'],{'observed_at':stamp(NOW-400)})
        self.assertEqual(self.run_at(NOW),'sent')
        self.assertIn('observer_stale',self.sent[0])
        self.assertNotIn('delivery_stale',self.sent[0])

    def test_future_and_invalid_sources_are_faults_not_claimed_health(self):
        save_private(self.config['observation_file'],{'observed_at':stamp(NOW+100)})
        report=assess(self.config,NOW)
        self.assertIn('observation_unavailable',report['reasons'])
        save_private(self.config['relay_health_file'],{'checked_at':stamp(NOW+100)})
        self.assertIn('relay_health_unavailable',assess(self.config,NOW)['reasons'])

    def test_credentials_and_config_permissions_and_symlink(self):
        path=Path(self.config['telegram_config_file'])
        path.chmod(0o644)
        with self.assertRaises(ValueError):read_telegram(path)
        path.chmod(0o600)
        with patch('health_notifier.os.getuid',return_value=os.getuid()+1):
            with self.assertRaises(ValueError):read_telegram(path)
        link=Path(self.tmp.name)/'link';link.symlink_to(path)
        with self.assertRaises(OSError):read_telegram(link)
        save_private(path,{'token':'123456789:'+'a'*35+'\r\nSECRET','chat_id':'123'})
        with self.assertRaises(ValueError):read_telegram(path)
        with self.assertRaises(ValueError):validate_config(dict(self.config,state_file='relative'))

    def test_telegram_tls_no_redirects_and_response_bounds(self):
        for status,body,expected in [(200,b'{"ok":true}','sent'),(302,b'','notification_rejected'),
                (401,b'','notification_rejected'),(200,b'{"ok":false}','notification_rejected'),
                (200,b'x'*4097,'notification_response_invalid')]:
            test=self
            class Response:
                def __init__(self):self.status=status;self.stream=io.BytesIO(body)
                def read1(self,n):return self.stream.read(n)
                def close(self):self.stream.close()
            class Connection:
                sock=SimpleNamespace(settimeout=lambda *a:None)
                def __init__(self,host,port,**kw):
                    test.assertEqual((host,port),('api.telegram.org',443))
                    test.assertEqual(kw['context'].verify_mode,ssl.CERT_REQUIRED)
                    test.assertTrue(kw['context'].check_hostname)
                def connect(self):pass
                def request(self,method,path,**kw):
                    test.assertEqual(method,'POST')
                    test.assertEqual(json.loads(kw['body'])['text'],'safe summary')
                def getresponse(self):return Response()
                def close(self):pass
            self.assertEqual(telegram_send(self.config,'safe summary',Connection),expected)

    def test_real_telegram_hung_dns_wall_deadline_and_child_cleanup(self):
        before={p.pid for p in multiprocessing.active_children()}
        with patch('socket.getaddrinfo',side_effect=lambda *a,**kw:time.sleep(60)), \
                patch('health_notifier.bounded_call',side_effect=lambda operation:bounded_call(operation,timeout=0.1)):
            started=time.monotonic()
            result=telegram_send(self.config,'safe summary')
        self.assertEqual(result,'notification_timeout')
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual({p.pid for p in multiprocessing.active_children()},before)

    def test_telegram_transport_exception_is_sanitized(self):
        def fail(*a,**kw):raise ssl.SSLCertVerificationError('TOKEN SECRET URL')
        result=telegram_send(self.config,'safe summary',fail)
        self.assertEqual(result,'notification_tls_failed')
        self.assertNotIn('SECRET',result)


if __name__=='__main__':unittest.main()
