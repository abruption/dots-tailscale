"""Narrow machine-authenticated observation ingestion, separate from MCP OAuth."""
import asyncio
import hashlib
import hmac
import json
import re

from starlette.requests import Request
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.background import BackgroundTask

MAX_BODY = 8192


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


class MonitorIngestMiddleware:
    """Authenticate before MCP OAuth; this credential cannot call MCP tools."""
    def __init__(self, app, recovery, token_sha256=None):
        self.app, self.recovery = app, recovery
        if token_sha256 is not None and not re.fullmatch(r'[a-f0-9]{64}', token_sha256):
            raise ValueError('invalid monitor credential digest')
        self.digest = token_sha256
        self.busy = False
        self.delivering = False

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] != '/monitor/observations':
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        async def reply(status, value, **kwargs):
            await JSONResponse(value, status_code=status, headers={'Cache-Control': 'no-store'}, **kwargs)(scope, receive, send)
        if self.digest is None or self.recovery is None:
            return await reply(404, {'error': 'not_found'})
        auth = request.headers.getlist('authorization')
        token = auth[0][7:] if len(auth) == 1 and auth[0].startswith('Bearer ') else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token) or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self.digest):
            return await reply(401, {'error': 'unauthorized'})
        if request.method != 'POST':
            return await reply(405, {'error': 'method_not_allowed'})
        if self.busy:
            return await reply(429, {'error': 'busy'})
        self.busy = True
        owns_busy = True
        try:
            if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
                return await reply(415, {'error': 'json_required'})
            lengths = request.headers.getlist('content-length')
            if lengths:
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                    return await reply(400, {'error': 'invalid_length'})
                if len(lengths[0]) > 5 or int(lengths[0]) > MAX_BODY:
                    return await reply(413, {'error': 'body_too_large'})
            async def read_body():
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        raise OverflowError
                    body.extend(chunk)
                return body
            try:
                body = await asyncio.wait_for(read_body(), timeout=5)
                observation = json.loads(body, object_pairs_hook=unique_object)
                await asyncio.to_thread(self.recovery.ingest, observation)
            except OverflowError:
                return await reply(413, {'error': 'body_too_large'})
            except asyncio.TimeoutError:
                return await reply(408, {'error': 'body_timeout'})
            except ClientDisconnect:
                return
            except (ValueError, TypeError, KeyError, UnicodeError):
                return await reply(400, {'error': 'invalid_observation'})
            # The acknowledgement contains no incident identifiers or execution authority.
            # Callback work survives in the outbox and is retried by later fresh ingests.
            async def deliver():
                if self.delivering:
                    return
                self.delivering = True
                def work():
                    for eid in self.recovery.pending_delivery_ids(self.recovery.owner):
                        self.recovery.deliver(self.recovery.owner, eid)
                try:
                    await asyncio.to_thread(work)
                finally:
                    self.delivering = False
            # Callback latency cannot prevent fresh observations from being ingested.
            self.busy = False
            owns_busy = False
            with self.recovery._db() as db:
                subscribed = bool(db.execute('SELECT 1 FROM subscriptions WHERE owner=? AND active=1 AND expires>? LIMIT 1',
                                             (self.recovery.owner, self.recovery.clock())).fetchone())
            return await reply(200, {'accepted': True, 'observed_at': observation['observed_at'], 'subscription_active': subscribed},
                               background=BackgroundTask(deliver))
        finally:
            if owns_busy:
                self.busy = False
