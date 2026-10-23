import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from event_core import EventCore, PublicHTTPS, secret_bytes

SECRET = 'whsec_' + base64.b64encode(b'x' * 32).decode()
URL = 'https://callback.example/events'


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/state.sqlite'
        self.now = 1000000
        self.sent = []
        self.status = 202
        self.echo = True
        self.core = self.new_core()

    def tearDown(self):
        self.tmp.cleanup()

    def transport(self, url, body, headers):
        self.sent.append((url, body, headers))
        expected = hmac.new(b'x' * 32, (headers['webhook-id'] + '.' + headers['webhook-timestamp'] + '.').encode() + body, hashlib.sha256).digest()
        self.assertEqual(headers['webhook-signature'], 'v1,' + base64.b64encode(expected).decode())
        payload = json.loads(body)
        if payload.get('type') == 'verification':
            return 200, json.dumps({'challenge': payload['challenge'] if self.echo else 'wrong'}).encode()
        return self.status, b'{}'

    def new_core(self):
        return EventCore(self.path, ['https://callback.example'], self.transport, lambda: self.now)

    def sub(self, owner='alice'):
        return self.core.subscribe(owner, URL, SECRET)['subscriptionId']

    def test_signature_echo_and_renewal_identity(self):
        sid = self.sub()
        self.now += 10
        self.assertEqual(sid, self.sub())
        self.assertNotEqual(self.sent[0][2]['webhook-id'], self.sent[1][2]['webhook-id'])
        self.assertNotEqual(json.loads(self.sent[0][1])['challenge'], json.loads(self.sent[1][1])['challenge'])
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT sum(used) FROM challenges').fetchone()[0], 2)
            self.assertEqual(db.execute('UPDATE challenges SET used=1 WHERE used=0').rowcount, 0)

    def test_wrong_echo_and_expired_challenge(self):
        self.echo = False
        with self.assertRaises(ValueError):
            self.sub()
        self.echo = True
        def slow(url, body, headers):
            self.now += 31
            return self.transport(url, body, headers)
        self.core.transport = slow
        with self.assertRaises(ValueError):
            self.sub()
        with self.core._db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM subscriptions').fetchone()[0], 0)

    def test_restart_dedupe_owner_and_receipt_not_response(self):
        sid = self.sub()
        eid = self.core.emit_probe('alice', 'request-1')
        self.core = self.new_core()
        self.assertEqual(eid, self.core.emit_probe('alice', 'request-1'))
        with self.assertRaises(ValueError):
            self.core.deliver('bob', eid)
        self.core.unsubscribe('bob', sid)
        result = self.core.deliver('alice', eid)
        self.assertEqual(result['deliveries'][0]['state'], 'received')
        self.assertEqual(result['agentResponse'], 'not_observed')
        self.assertEqual(result['recovery'], 'not_attempted')
        self.core.deliver('alice', eid)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.core.emit_probe('bob','request-1') == eid, False)

    def test_expiry_unsubscribe_and_filter(self):
        sid = self.sub()
        eid = self.core.emit_probe('alice', 'r')
        self.now += 3601
        self.assertEqual(self.core.deliver('alice', eid)['deliveries'][0]['state'], 'expired')
        self.sub()
        self.core.unsubscribe('alice', sid)
        self.assertEqual(self.core.receipt('alice', self.core.emit_probe('alice', 'r2'))['deliveries'], [])
        for args in ({}, {'target_id': 'other'}, {'target_id': 'dots', 'extra': 1}):
            with self.assertRaises(ValueError):
                self.core.subscribe('alice', URL, SECRET, args)

    def test_terminal_statuses_and_bounded_retries(self):
        self.sub()
        for code in (410, 413, 302, 503):
            self.status = code
            eid = self.core.emit_probe('alice', str(code))
            for _ in range(5):
                receipt = self.core.deliver('alice',eid)
            row = receipt['deliveries'][0]
            self.assertEqual(row['state'], 'terminal')
            self.assertEqual(row['attempts'], 3 if code == 503 else 1)
            deliveries = [headers for _, body, headers in self.sent if json.loads(body).get('eventId') == eid]
            self.assertTrue(all(headers['webhook-id'] == eid for headers in deliveries))

    def test_invalid_inputs_do_not_send(self):
        for value in ('bad', 'whsec_%%%','whsec_'+base64.b64encode(b'x'*23).decode(), 'whsec_'+base64.b64encode(b'x'*65).decode()):
            with self.assertRaises(ValueError): secret_bytes(value)
        for url in ('http://callback.example/events','https://other.example/', 'https://callback.example:8443/', 'https://user@callback.example/', 'https://callback.example/#x'):
            with self.assertRaises(ValueError): self.core.subscribe('alice',url,SECRET)
        with self.assertRaises(ValueError): self.core.catalog('')
        self.assertEqual(self.sent, [])

    def test_private_resolution_refused_before_socket(self):
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','100.64.0.1'):
            with patch('event_core.socket.getaddrinfo', return_value=[(2,1,6,'',(address,443))]), patch('event_core.socket.socket') as sock:
                with self.assertRaises(ValueError): PublicHTTPS()._direct(URL,b'{}',{})
                sock.assert_not_called()

    def test_unknown_sending_survives_restart(self):
        sid=self.sub()
        eid=self.core.emit_probe('alice','crash')
        with self.core._db() as db:
            db.execute("UPDATE outbox SET state='sending', attempts=1 WHERE event=?",(eid,))
        self.core=self.new_core()
        self.assertEqual(self.core.deliver('alice',eid)['deliveries'][0]['state'],'sending')
        self.assertEqual(len(self.sent),1)
        self.assertEqual(os.stat(self.path).st_mode & 0o777,0o600)

if __name__ == '__main__':
    unittest.main()
