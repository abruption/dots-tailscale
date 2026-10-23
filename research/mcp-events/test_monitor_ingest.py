import hashlib
import base64
import concurrent.futures
import json
import tempfile
import time
import threading
import unittest
from datetime import datetime, timezone

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
            self.assertEqual(r.json(), {'accepted': True, 'observed_at': row['observed_at'], 'subscription_active': False})
            self.assertEqual(self.post(row).status_code, 200)
            self.assertEqual(self.recovery.get_current_recovery('alice')['observation']['failure_streak'], number + 1)
            self.now += 60
        state = self.recovery.get_current_recovery('alice')
        self.assertTrue(state['claimable'])
        self.assertEqual(state['incident']['state'], 'pending')
        self.assertIsNone(state['incident']['outcome'])

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
