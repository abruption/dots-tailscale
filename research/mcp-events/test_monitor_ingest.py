import hashlib
import base64
import concurrent.futures
import json
import tempfile
import time
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from event_core import EventCore
from recovery_core import RecoveryCore
from server import create_app

ORIGIN = 'https://events.example'
MACHINE = 'a' * 43


class Provider:
    resource = ORIGIN + '/mcp'
    def build_auth_routes(self): return []
    async def load_access_token(self, token):
        if token != 'oauth': return None
        return AccessToken(token=token, client_id='fixture', subject='alice', resource=self.resource,
                           scopes=['events:observe', 'recovery:execute'], expires_at=int(time.time()) + 600)


class MonitorHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = float(int(time.time()))
        self.core = EventCore(self.tmp.name + '/probe.sqlite', [])
        self.recovery = RecoveryCore(self.tmp.name + '/recovery.sqlite', [], 'alice', clock=lambda: self.now)
        app = create_app(self.core, Provider(), ORIGIN, self.recovery, hashlib.sha256(MACHINE.encode()).hexdigest())
        self.client = TestClient(app, base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def sample(self):
        return {'observed_at': datetime.fromtimestamp(self.now, timezone.utc).isoformat(), 'state': 'external_unreachable',
                'ssh': {'command_succeeded': False, 'probe_valid': False, 'exit_code': 255, 'error_class': 'timeout'},
                'peer_ping': {'responded': False, 'exit_code': 1}}

    def post(self, row=None, token=MACHINE, **kwargs):
        headers = {'Authorization': 'Bearer ' + token} if token is not None else {}
        return self.client.post('/monitor/observations', headers=headers, json=self.sample() if row is None else row, **kwargs)

    def test_machine_auth_is_separate_from_mcp_oauth(self):
        for token in (None, 'wrong', 'oauth'):
            self.assertEqual(self.post(token=token).status_code, 401)
        self.assertIsNone(self.recovery.get_current_recovery('alice')['observation'])
        self.assertEqual(self.post().status_code, 200)
        r = self.client.post('/mcp', headers={'Authorization': 'Bearer ' + MACHINE})
        self.assertEqual(r.status_code, 401)

    def test_fresh_observation_ack_dedup_and_incident_creation(self):
        for number in range(3):
            row = self.sample()
            r = self.post(row)
            ack = r.json()
            self.assertEqual({key: value for key, value in ack.items()
                              if key != 'recovery_diagnostics'},
                             {'accepted': True, 'observed_at': row['observed_at'],
                              'subscription_active': False})
            diagnostics = ack['recovery_diagnostics']
            self.assertEqual(diagnostics['claims_issued'], 0)
            self.assertEqual(diagnostics['cap'], 3)
            self.assertTrue(diagnostics['unclaimed_followup']['available'])
            self.assertFalse(diagnostics['unclaimed_followup']['operator_attention'])
            self.assertNotIn('incident_id', json.dumps(diagnostics))
            self.assertNotIn('event_id', json.dumps(diagnostics))
            self.assertEqual(self.post(row).status_code, 200)
            self.assertEqual(self.recovery.get_current_recovery('alice')['observation']['failure_streak'], number + 1)
            self.now += 60
        state = self.recovery.get_current_recovery('alice')
        self.assertTrue(state['claimable'])
        self.assertEqual(state['incident']['state'], 'pending')
        self.assertIsNone(state['incident']['outcome'])

    def test_legacy_state_without_followup_field_keeps_budget_snapshot_available(self):
        original = self.recovery._state
        def legacy_state(db, *args, **kwargs):
            state = original(db, *args, **kwargs)
            state.pop('unclaimed_followup', None)
            return state
        with patch.object(self.recovery, '_state', side_effect=legacy_state):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        diagnostic = response.json()['recovery_diagnostics']
        self.assertEqual(diagnostic['claims_issued'], 0)
        self.assertFalse(diagnostic['unclaimed_followup']['available'])
        self.assertIsNone(diagnostic['unclaimed_followup']['operator_attention'])

    def test_optional_diagnostic_failure_preserves_base_subscription_ack(self):
        with patch('monitor_ingest.build_recovery_diagnostics', side_effect=ValueError('DUMMY_PRIVATE_ERROR')):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['subscription_active'], False)
        self.assertNotIn('recovery_diagnostics', response.json())
        self.assertNotIn('DUMMY_PRIVATE_ERROR', response.text)

    def test_unavailable_snapshot_keeps_accepted_ack_without_inventing_subscription_status(self):
        with patch('monitor_ingest.recovery_read_snapshot', side_effect=OSError('DUMMY_PRIVATE_FAILURE')):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['accepted'])
        self.assertNotIn('subscription_active', response.json())
        self.assertNotIn('recovery_diagnostics', response.json())
        self.assertNotIn('DUMMY_PRIVATE_FAILURE', response.text)

    def test_invalid_stale_future_and_type_confused_data_do_not_update_state(self):
        valid = self.sample()
        for offset in (-151, 1):
            row = dict(valid, observed_at=datetime.fromtimestamp(self.now + offset, timezone.utc).isoformat())
            self.assertEqual(self.post(row).status_code, 400)
        for row in ([], {'secret': 'DO_NOT_ECHO'}, dict(valid, observed_at=True)):
            r = self.post(row)
            self.assertEqual(r.status_code, 400)
            self.assertNotIn('DO_NOT_ECHO', r.text)
        self.assertIsNone(self.recovery.get_current_recovery('alice')['observation'])

    def test_ack_contains_bounded_claim_outcomes_but_no_identifiers(self):
        with self.recovery._db() as db:
            for iid, outcome, report in (
                    ('one', 'success', 'success'), ('two', 'unknown', None),
                    ('three', 'blocked', 'blocked')):
                claimed = self.now - 100 - len(iid)
                db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,?,?,?,?)',
                           (iid, 'evt_' + iid, claimed-20, None, 'claim_' + iid,
                            claimed, outcome))
                if report:
                    db.execute('INSERT INTO recovery_reports VALUES(?,?,?)',
                               (iid, report, claimed+1))
        response = self.post()
        self.assertEqual(response.status_code, 200)
        summary = response.json()['recovery_diagnostics']
        self.assertEqual(summary['claims_issued'], 3)
        self.assertEqual(summary['first_reports']['missing'], 1)
        self.assertEqual(summary['first_reports']['success'], 1)
        self.assertEqual(summary['first_reports']['blocked'], 1)
        self.assertEqual(summary['current_outcomes']['unknown'], 1)
        self.assertEqual(summary['externally_closed'], 0)
        self.assertEqual(summary['sustained_confirmed'], 0)
        serialized = json.dumps(summary)
        for private in ('one', 'two', 'three', 'evt_', 'claim_'):
            self.assertNotIn(private, serialized)

    def test_ack_uses_current_read_snapshot_not_stale_ingest_state(self):
        ingest = self.recovery.ingest
        state_read = self.recovery._state

        def current_state(db, incident_id=None, now=None):
            value = state_read(db, incident_id, now=now)
            value['unclaimed_followup'] = {'operator_attention': False, 'reason': None,
                                           'incident_id': 'current_private_id'}
            return value

        def stale_return(row):
            value = ingest(row)
            value['unclaimed_followup'] = {'operator_attention': True, 'reason': 'exhausted',
                                           'incident_id': 'stale_private_id'}
            return value

        with patch.object(self.recovery, '_state', side_effect=current_state):
            with patch.object(self.recovery, 'ingest', side_effect=stale_return):
                response = self.post()
        self.assertEqual(response.status_code, 200)
        summary = response.json()['recovery_diagnostics']
        self.assertEqual(summary['unclaimed_followup'], {
            'available': True, 'operator_attention': False, 'reason_code': None,
            'awaiting_response': False, 'response_deadline_at': None})
        self.assertNotIn('stale_private_id', response.text)
        self.assertNotIn('current_private_id', response.text)

    def test_body_bounds_duplicates_content_type_host_and_method(self):
        headers = {'Authorization': 'Bearer ' + MACHINE, 'Content-Type': 'application/json'}
        for content in ('x' * 8193, iter([b'x' * 4096, b'x' * 4097])):
            self.assertEqual(self.client.post('/monitor/observations', headers=headers, content=content).status_code, 413)
        self.assertEqual(self.client.post('/monitor/observations', headers=headers, content='{"state":"a","state":"b"}').status_code, 400)
        self.assertEqual(self.client.post('/monitor/observations', headers={'Authorization': 'Bearer ' + MACHINE}, content='{}').status_code, 415)
        self.assertEqual(self.client.get('/monitor/observations', headers=headers).status_code, 405)
        self.assertEqual(self.client.post('/monitor/observations', headers={**headers, 'Host': 'evil.example'}, json=self.sample()).status_code, 400)
        self.assertIsNone(self.recovery.get_current_recovery('alice')['observation'])
        self.assertEqual(self.client.post('/monitor/observations', headers={**headers, 'Content-Length': '9' * 5000}, content='{}').status_code, 413)

    def test_slow_callback_does_not_block_fresh_recovery_observations(self):
        entered, release = threading.Event(), threading.Event()
        def callback(url, body, headers):
            data = json.loads(body)
            if 'challenge' in data:
                return 200, json.dumps({'challenge': data['challenge']}).encode()
            entered.set()
            if not release.wait(3): raise RuntimeError('test callback was not released')
            return 200, b'{}'
        self.recovery.transport = callback
        self.recovery.allowed = {'https://callback.example'}
        self.recovery.subscribe('alice', 'https://callback.example/events', 'whsec_' + base64.b64encode(b'x' * 32).decode(), {'target_id': 'dots'}, 3600)
        for _ in range(2):
            self.recovery.ingest(self.sample()); self.now += 60
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.post)
            try:
                self.assertTrue(entered.wait(2))
                self.now += 60
                healthy = self.sample()
                healthy['state'] = 'verified_running'
                healthy['ssh'] = {'command_succeeded': True, 'probe_valid': True, 'exit_code': 0, 'error_class': 'ok'}
                healthy['peer_ping'] = {'responded': True, 'exit_code': 0}
                self.assertEqual(self.post(healthy).status_code, 200)
                self.assertEqual(self.recovery.get_current_recovery('alice')['observation']['ssh'], 'ok')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3).status_code, 200)

    def test_disabled_machine_channel_and_bad_digest_fail_closed(self):
        with TestClient(create_app(self.core, Provider(), ORIGIN, self.recovery), base_url=ORIGIN) as c:
            self.assertEqual(c.post('/monitor/observations', headers={'Authorization': 'Bearer ' + MACHINE}, json=self.sample()).status_code, 404)
        with self.assertRaises(ValueError):
            with TestClient(create_app(self.core, Provider(), ORIGIN, self.recovery, 'bad'), base_url=ORIGIN): pass


if __name__ == '__main__': unittest.main()
