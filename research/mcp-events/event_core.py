"""Research-only synthetic MCP Events core. No scheduler or recovery commands.

All methods require a trusted authenticated principal supplied by the HTTP layer.
Only subscribe (verification) and deliver explicitly perform network requests.
Transport injection is for offline tests; production uses PublicHTTPS.
"""
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import secrets
import socket
import sqlite3
import ssl
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from urllib.parse import urlsplit

NAME = 'dots.recovery_probe'
MAX_BODY = 16384
MAX_ATTEMPTS = 3


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def identity(principal):
    if not isinstance(principal, str) or not 1 <= len(principal) <= 256:
        raise ValueError('authenticated principal required')
    return principal


def secret_bytes(secret):
    try:
        if not isinstance(secret, str) or not secret.startswith('whsec_') or len(secret) > 100:
            raise ValueError()
        value = base64.b64decode(secret[6:], validate=True)
        if not 24 <= len(value) <= 64:
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ValueError('invalid signing secret') from None


def callback(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('invalid callback')
    p = urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or p.port not in (None, 443):
        raise ValueError('callback must use HTTPS port 443 without credentials or fragment')
    host = p.hostname.lower()
    if host.endswith('.') or '%' in host:
        raise ValueError('invalid callback host')
    origin = 'https://' + ('[' + host + ']' if ':' in host else host)
    return origin, origin + (p.path or '/') + ('?' + p.query if p.query else '')


class PublicHTTPS:
    """Resolve once, reject any nonpublic address, pin socket IP and validate TLS SNI.

    No proxy environment variables, redirects, retries, or credential forwarding.
    """
    def __call__(self, url, body, headers):
        _, url = callback(url)
        p = urlsplit(url)
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('callback must resolve exclusively to public addresses')
        family, kind, proto, _, address = addresses[0]
        raw = socket.socket(family, kind, proto)
        raw.settimeout(5)
        conn = http.client.HTTPSConnection(p.hostname, 443, timeout=5)
        try:
            raw.connect(address)
            conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=p.hostname)
            conn.request('POST', (p.path or '/') + ('?' + p.query if p.query else ''), body, headers)
            response = conn.getresponse()
            deadline = time.monotonic() + 5
            result = bytearray()
            while len(result) <= MAX_BODY:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('callback response timeout')
                conn.sock.settimeout(remaining) if conn.sock else None
                chunk = response.read1(min(4096, MAX_BODY + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
            if len(result) > MAX_BODY:
                raise ValueError('callback response too large')
            return response.status, bytes(result)
        finally:
            conn.close()
            raw.close()


class EventCore:
    def __init__(self, db_path, allowed_origins, transport=None, clock=time.time, event_name=NAME):
        self.name = event_name
        self.clock = clock
        self.transport = transport or PublicHTTPS()
        self.allowed = set()
        for origin in allowed_origins:
            normalized, url = callback(origin)
            if url != normalized + '/':
                raise ValueError('allowlist entries must be origins')
            self.allowed.add(normalized)
        # Parent directory must be private; SQLite contains webhook signing secrets.
        parent = os.path.dirname(os.path.abspath(db_path))
        if os.stat(parent).st_mode & 0o077:
            raise ValueError('database directory must have mode 0700')
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if os.stat(db_path).st_mode & 0o077:
            raise ValueError('database must have mode 0600')
        self.db_path = db_path
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY,owner TEXT,url TEXT,filters TEXT,secret TEXT,expires REAL,active INTEGER);
            CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY,subscription TEXT,nonce TEXT,expires REAL,used INTEGER);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,owner TEXT,request TEXT,body BLOB,UNIQUE(owner,request));
            CREATE TABLE IF NOT EXISTS outbox(event TEXT,subscription TEXT,state TEXT,attempts INTEGER,http_status INTEGER,PRIMARY KEY(event,subscription));
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def catalog(self, principal):
        identity(principal)
        return {'name': self.name, 'description': 'Synthetic recovery probe; never executes recovery',
                'delivery': ['webhook'], 'payloadSchema': {'type': 'object', 'properties': {'test_id': {'type': 'string'}, 'target_id': {'const': 'dots'}, 'simulated': {'const': True}}, 'required': ['test_id', 'target_id', 'simulated'], 'additionalProperties': False},
                'inputSchema': {'type': 'object', 'properties': {'target_id': {'const': 'dots'}}, 'required': ['target_id'], 'additionalProperties': False}}

    def subscription_id(self, principal, callback_url, filters=None):
        identity(principal)
        origin, url = callback(callback_url)
        if origin not in self.allowed:
            raise ValueError('callback origin not configured')
        filters = {'target_id': 'dots'} if filters is None else filters
        if filters != {'target_id': 'dots'}:
            raise ValueError('unsupported event filter')
        return 'sub_' + hashlib.sha256(encoded([principal, self.name, url, filters])).hexdigest()

    def _send(self, url, secret, subscription, payload):
        body = encoded(payload)
        if len(body) > MAX_BODY:
            raise ValueError('event body too large')
        msg = payload.get('eventId') or 'msg_' + secrets.token_hex(16)
        stamp = str(int(self.clock()))
        sig = base64.b64encode(hmac.new(secret_bytes(secret), msg.encode()+b'.'+stamp.encode()+b'.'+body, hashlib.sha256).digest()).decode()
        status, result = self.transport(url, body, {'Content-Type': 'application/json', 'webhook-id': msg,
             'webhook-timestamp': stamp, 'webhook-signature': 'v1,'+sig, 'X-MCP-Subscription-Id': subscription})
        if not isinstance(result, bytes) or len(result) > MAX_BODY:
            raise ValueError('invalid callback response')
        return status, result

    def subscribe(self, principal, callback_url, signing_secret, filters=None, ttl=3600):
        sid = self.subscription_id(principal, callback_url, filters)
        secret_bytes(signing_secret)
        if type(ttl) is not int or not 1 <= ttl <= 3600:
            raise ValueError('ttl must be 1..3600 seconds')
        _, url = callback(callback_url)
        nonce, challenge_id = secrets.token_urlsafe(32), secrets.token_hex(16)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM subscriptions WHERE owner=? AND active=1', (principal,)).fetchone()[0] >= 16 and not db.execute('SELECT 1 FROM subscriptions WHERE id=?', (sid,)).fetchone():
                raise ValueError('subscription limit reached')
            db.execute('DELETE FROM challenges WHERE expires<?', (self.clock(),))
            if db.execute('SELECT count(*) FROM challenges WHERE used=0').fetchone()[0] >= 64:
                raise ValueError('verification concurrency limit reached')
            db.execute('INSERT INTO challenges VALUES(?,?,?,?,0)', (challenge_id, sid, nonce, self.clock()+30))
        try:
            status, result = self._send(url, signing_secret, sid, {'type': 'verification', 'challenge': nonce})
            reply = json.loads(result)
            valid = 200 <= status < 300 and isinstance(reply, dict) and isinstance(reply.get('challenge'), str) and hmac.compare_digest(reply['challenge'], nonce)
        except Exception:
            valid = False
        with self._db() as db:
            consumed = db.execute('UPDATE challenges SET used=1 WHERE id=? AND used=0 AND expires>?', (challenge_id, self.clock())).rowcount
        if not valid or consumed != 1:
            raise ValueError('callback verification failed')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM subscriptions WHERE owner=? AND active=1 AND expires>? AND id!=?', (principal, self.clock(), sid)).fetchone()[0] >= 16:
                raise ValueError('subscription limit reached')
            expires = self.clock() + ttl
            db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET secret=excluded.secret,expires=excluded.expires,active=1',
                       (sid, principal, url, encoded({'target_id': 'dots'}).decode(), signing_secret, expires))
        return {'id': sid, 'subscriptionId': sid, 'refreshBefore': datetime.fromtimestamp(expires, timezone.utc).isoformat()}

    def unsubscribe(self, principal, subscription_id):
        identity(principal)
        with self._db() as db:
            db.execute('UPDATE subscriptions SET active=0,secret="" WHERE id=? AND owner=?', (subscription_id, principal))
        return {}

    def emit_probe(self, principal, request_id):
        identity(principal)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or any(not(c.isascii() and (c.isalnum() or c in '-_.')) for c in request_id):
            raise ValueError('invalid request ID')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id FROM events WHERE owner=? AND request=?', (principal, request_id)).fetchone()
            if old:
                return old['id']
            if db.execute('SELECT count(*) FROM events WHERE owner=?', (principal,)).fetchone()[0] >= 1000:
                raise ValueError('research event limit reached')
            eid = 'evt_' + secrets.token_hex(16)
            body = encoded({'eventId': eid, 'name': self.name, 'timestamp': datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                'data': {'test_id': request_id, 'target_id': 'dots', 'simulated': True}, 'cursor': None})
            db.execute('INSERT INTO events VALUES(?,?,?,?)', (eid, principal, request_id, body))
            db.execute("INSERT INTO outbox SELECT ?,id,'pending',0,NULL FROM subscriptions WHERE owner=? AND active=1 AND expires>?", (eid, principal, self.clock()))
        return eid

    def read_probe(self, principal, request_id):
        """Read only this principal's recorded synthetic event, without sending it."""
        identity(principal)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError('invalid request ID')
        with self._db() as db:
            row = db.execute('SELECT body FROM events WHERE owner=? AND request=?', (principal, request_id)).fetchone()
        return {'found': False} if row is None else {'found': True, 'event': json.loads(row['body'])}

    def receipt(self, principal, event_id):
        identity(principal)
        with self._db() as db:
            if not db.execute('SELECT 1 FROM events WHERE id=? AND owner=?', (event_id, principal)).fetchone():
                raise ValueError('unknown event')
            rows = db.execute('SELECT subscription,state,attempts,http_status FROM outbox WHERE event=?', (event_id,)).fetchall()
        return {'eventId': event_id, 'deliveries': [dict(r) for r in rows], 'agentResponse': 'not_observed', 'recovery': 'not_attempted'}

    def deliver(self, principal, event_id):
        self.receipt(principal, event_id)
        with self._db() as db:
            rows = db.execute('SELECT subscription FROM outbox WHERE event=?', (event_id,)).fetchall()
        for row in rows:
            sid = row['subscription']
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                sub = db.execute('SELECT * FROM subscriptions WHERE id=?', (sid,)).fetchone()
                if not sub or not sub['active'] or sub['expires'] <= self.clock():
                    db.execute("UPDATE outbox SET state='expired' WHERE event=? AND subscription=? AND state IN ('pending','retry')", (event_id,sid))
                    continue
                claimed = db.execute("UPDATE outbox SET state='sending',attempts=attempts+1 WHERE event=? AND subscription=? AND state IN ('pending','retry') AND attempts<?", (event_id,sid,MAX_ATTEMPTS)).rowcount
                if not claimed:
                    continue
                payload = json.loads(db.execute('SELECT body FROM events WHERE id=?', (event_id,)).fetchone()[0])
            # A crash after claiming leaves 'sending': outcome unknown; no automatic replay.
            status = None
            try:
                status, _ = self._send(sub['url'],sub['secret'],sid,payload)
            except Exception:
                pass
            with self._db() as db:
                attempts = db.execute('SELECT attempts FROM outbox WHERE event=? AND subscription=?', (event_id,sid)).fetchone()[0]
                state = 'received' if status is not None and 200 <= status < 300 else ('terminal' if status in (410,413) or (status is not None and 300 <= status < 500 and status != 429) or attempts >= MAX_ATTEMPTS else 'retry')
                db.execute('UPDATE outbox SET state=?,http_status=? WHERE event=? AND subscription=?', (state,status,event_id,sid))
        return self.receipt(principal,event_id)
