"""Security boundaries exercised through ASGI without real credentials/network."""
import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from starlette.applications import Starlette
import oauth_adapter as auth
from server import listener_options

ORIGIN = 'https://research.example.test'
CALLBACK = 'https://chatgpt.com/connector/oauth/fixture'
QUERY = urlencode(dict(client_id='fixture', response_type='code', redirect_uri=CALLBACK,
                       scope='events:observe', code_challenge='a'*43, code_challenge_method='S256',
                       resource=ORIGIN+'/mcp', state='fixture')).encode()


class HardeningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.adapter = auth.OAuthAdapter(Path(self.tmp.name)/'auth.sqlite', ORIGIN, ORIGIN+'/mcp',
                                         'alice', 'fixture', CALLBACK)
        self.app = Starlette(routes=self.adapter.build_auth_routes())

    async def request(self, path, receive, headers=(), client=('127.0.0.1', 1234), method='POST'):
        messages = []
        async def send(message):
            messages.append(message)
        scope = dict(type='http', asgi={'version':'3.0'}, http_version='1.1', method=method,
                     scheme='https', path=path, raw_path=path.encode(), query_string=QUERY,
                     root_path='', server=('research.example.test',443), client=client,
                     headers=[(b'host',b'research.example.test'), *headers])
        await self.app(scope, receive, send)
        return next(m['status'] for m in messages if m['type']=='http.response.start')

    async def empty(self):
        return {'type':'http.request', 'body':b'', 'more_body':False}

    async def test_anonymous_authorization_does_not_consume_any_tickets(self):
        for _ in range(65):
            self.assertEqual(await self.request('/authorize', self.empty, method='GET'),403)
        with self.adapter.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM oauth_items').fetchone()[0],0)
        self.assertEqual(await self.request('/authorize',self.empty,[(b'remote-user',b'alice')],method='GET'),302)

    async def test_identity_requires_exact_transport_host_and_single_user(self):
        for headers, client in [([(b'remote-user',b'alice')], ('192.0.2.1',1)),
                                ([(b'remote-user',b'alice')], None),
                                ([(b'remote-user',b'alice'),(b'remote-user',b'alice')], ('127.0.0.1',1)),
                                ([(b'remote-user',b'other')], ('127.0.0.1',1))]:
            self.assertEqual(await self.request('/authorize', self.empty, headers, client, 'GET'),403)
        self.adapter.trusted_proxy_transport='unix'
        self.assertEqual(await self.request('/authorize',self.empty,[(b'remote-user',b'alice')],None,'GET'),302)
        self.assertEqual(await self.request('/authorize',self.empty,[(b'remote-user',b'alice')],method='GET'),403)

    async def test_oversized_body_is_rejected_before_next_receive(self):
        for path in ('/token','/revoke'):
            calls=0
            async def receive():
                nonlocal calls
                calls+=1
                if calls>1:
                    raise AssertionError('oversized request should not read further')
                return {'type':'http.request','body':b'a'*16385,'more_body':True}
            self.assertEqual(await self.request(path,receive),413)
            self.assertEqual(calls,1)

    async def test_trickled_body_has_total_deadline_and_releases_admission(self):
        async def receive():
            await asyncio.sleep(.01)
            return {'type':'http.request','body':b'x','more_body':True}
        with patch.object(auth,'BODY_TIMEOUT_SECONDS',.04):
            self.assertEqual(await self.request('/token',receive),408)
        self.assertEqual(self.adapter._auth_inflight,0)

    async def test_auth_admission_is_bounded_and_released_on_cancellation(self):
        ready=asyncio.Event()
        entered=0
        async def receive():
            nonlocal entered
            entered+=1
            if entered==auth.AUTH_INFLIGHT_LIMIT:
                ready.set()
            await asyncio.Event().wait()
        self.adapter.trusted_proxy_transport='unix'
        tasks=[asyncio.create_task(self.request('/token',receive,[(b'x-dots-client-ip',f'192.0.2.{i+1}'.encode())],None)) for i in range(auth.AUTH_INFLIGHT_LIMIT)]
        await asyncio.wait_for(ready.wait(),1)
        try:
            self.assertEqual(await self.request('/revoke',self.empty,[(b'x-dots-client-ip',b'192.0.2.99')],None),429)
            self.assertEqual(await self.request('/authorize',self.empty,[(b'remote-user',b'alice')],None,'GET'),302)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        self.assertEqual(self.adapter._auth_inflight,0)

    async def test_one_slow_client_cannot_block_consent_or_other_client_refresh(self):
        import time
        from urllib.parse import urlencode
        self.adapter.trusted_proxy_transport='unix'
        with self.adapter.connect() as db:
            tokens=self.adapter.issue(db,'alice',['events:observe'],'fixture-family',int(time.time())+3600)
        entered=asyncio.Event()
        async def slow():
            entered.set()
            await asyncio.Event().wait()
        attacker=[(b'x-dots-client-ip',b'192.0.2.1')]
        task=asyncio.create_task(self.request('/token',slow,attacker,None))
        await entered.wait()
        try:
            for _ in range(12):
                self.assertEqual(await self.request('/revoke',self.empty,attacker,None),429)
            self.assertEqual(await self.request('/authorize',self.empty,[(b'remote-user',b'alice')],None,'GET'),302)
            body=urlencode({'grant_type':'refresh_token','client_id':'fixture','refresh_token':tokens.refresh_token}).encode()
            async def valid_body(): return {'type':'http.request','body':body,'more_body':False}
            self.assertEqual(await self.request('/token',valid_body,
                             [(b'x-dots-client-ip',b'192.0.2.2'),(b'content-type',b'application/x-www-form-urlencoded')],None),200)
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(self.adapter._public_inflight,0)

    async def test_identity_header_cannot_override_direct_peer_or_duplicate_unix_identity(self):
        from starlette.requests import Request
        def request(client, values):
            return Request({'type':'http','client':client,'headers':[(b'x-dots-client-ip',v) for v in values]})
        self.assertEqual(self.adapter.public_client(request(('127.0.0.1',1),[b'192.0.2.1'])),'127.0.0.1')
        self.assertIsNone(self.adapter.public_client(request(('192.0.2.1',1),[b'127.0.0.1'])))
        self.adapter.trusted_proxy_transport='unix'
        for values in ([],[b'192.0.2.1',b'192.0.2.2'],[b'192.0.2.1:80'],[b'fe80::1%lo']):
            self.assertIsNone(self.adapter.public_client(request(None,values)))
        self.assertIsNone(self.adapter.public_client(request(('127.0.0.1',1),[b'192.0.2.1'])))
        self.assertEqual(self.adapter.public_client(request(None,[b'2001:0db8::0001'])),'2001:db8::1')
        self.assertEqual(self.adapter.public_client(request(None,[b'::ffff:192.0.2.1'])),'192.0.2.1')
        self.assertEqual(await self.request('/token',self.empty,[],None),403)

    async def test_public_rate_limit_refills_without_consuming_interactive_capacity(self):
        import time
        now=time.monotonic()
        self.adapter.trusted_proxy_transport='unix'
        identity=[(b'x-dots-client-ip',b'192.0.2.1')]
        with patch.object(auth.time,'monotonic',return_value=now):
            for _ in range(auth.PUBLIC_BURST):
                self.assertNotEqual(await self.request('/token',self.empty,identity,None),429)
            self.assertEqual(await self.request('/token',self.empty,identity,None),429)
            self.assertEqual(await self.request('/authorize',self.empty,[(b'remote-user',b'alice')],None,'GET'),302)
        with patch.object(auth.time,'monotonic',return_value=now+1.01):
            self.assertNotEqual(await self.request('/token',self.empty,identity,None),429)

    async def test_client_map_bounded_and_active_entries_survive_churn(self):
        import time
        now=time.monotonic()
        with patch.object(auth.time,'monotonic',return_value=now):
            self.assertTrue(self.adapter.admit_public('active'))
        for i in range(auth.PUBLIC_CLIENT_LIMIT+10):
            with patch.object(auth.time,'monotonic',return_value=now+i+1):
                key=f'192.0.2.{i}'
                self.assertTrue(self.adapter.admit_public(key))
                self.adapter._public_clients[key]['active']=False
                self.adapter._public_inflight-=1
        self.assertEqual(len(self.adapter._public_clients),auth.PUBLIC_CLIENT_LIMIT)
        self.assertTrue(self.adapter._public_clients['active']['active'])
        self.assertEqual(self.adapter._public_inflight,1)

    async def test_public_global_bucket_bounds_rotating_clients(self):
        import time
        now=time.monotonic()
        with patch.object(auth.time,'monotonic',return_value=now):
            for i in range(auth.PUBLIC_GLOBAL_BURST):
                key=str(i)
                self.assertTrue(self.adapter.admit_public(key))
                self.adapter._public_clients[key]['active']=False
                self.adapter._public_inflight-=1
            self.assertFalse(self.adapter.admit_public('new-client'))



class ListenerTests(unittest.TestCase):
    def test_unix_requires_explicit_fd_and_no_tcp_fallback(self):
        for cfg in ({'trusted_proxy_transport':'unix'}, {'trusted_proxy_transport':'unix','listen_fd':True},
                    {'trusted_proxy_transport':'unix','listen_fd':4}, {'listen_fd':3},
                    {'trusted_proxy_transport':'invalid'}):
            with self.assertRaises(ValueError):
                listener_options(cfg)

    @unittest.skipUnless(sys.platform == "linux", "systemd listener validation uses Linux SO_ACCEPTCONN")
    def test_real_inherited_socket_family_and_listening_validation(self):
        # fd3 is manipulated only in a child, never in the test runner.
        script = '''import os,socket,sys
from server import listener_options
family=socket.AF_UNIX if sys.argv[1]=='unix' else socket.AF_INET
s=socket.socket(family,socket.SOCK_STREAM)
if family==socket.AF_UNIX: s.bind(sys.argv[2])
else: s.bind(('127.0.0.1',0))
if sys.argv[3]=='listen': s.listen(1)
if s.fileno()!=3: os.dup2(s.fileno(),3)
try:
 print(listener_options({'trusted_proxy_transport':'unix','listen_fd':3}))
except ValueError: sys.exit(7)
'''
        with tempfile.TemporaryDirectory() as tmp:
            for family, mode, expected in [('unix','listen',0),('tcp','listen',7),('unix','idle',7)]:
                result=subprocess.run([sys.executable,'-c',script,family,tmp+'/'+family+mode,mode],
                                      cwd=Path(__file__).parent,capture_output=True,text=True)
                self.assertEqual(result.returncode,expected,result.stderr)


class MCPUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_upload_deadline_unauth_rejected_before_read(self):
        import time
        import server
        from event_core import EventCore
        from mcp.server.auth.provider import AccessToken
        class Provider:
            resource=ORIGIN+'/mcp'
            def build_auth_routes(self): return []
            async def load_access_token(self, token):
                if token!='fixture': return None
                return AccessToken(token=token,client_id='fixture',subject='alice',resource=self.resource,
                                   scopes=['events:observe'],expires_at=int(time.time())+600)
        with tempfile.TemporaryDirectory() as tmp:
            app=server.create_app(EventCore(tmp+'/events.sqlite',[]),Provider(),ORIGIN)
            async def request(token,receive):
                messages=[]
                async def send(message): messages.append(message)
                scope=dict(type='http',asgi={'version':'3.0'},http_version='1.1',method='POST',scheme='https',
                           path='/mcp',raw_path=b'/mcp',query_string=b'',root_path='',
                           server=('research.example.test',443),client=('127.0.0.1',1),
                           headers=[(b'host',b'research.example.test'),(b'authorization',('Bearer '+token).encode())])
                await app(scope,receive,send)
                return next(m['status'] for m in messages if m['type']=='http.response.start')
            async def forbidden_read():
                raise AssertionError('unauthenticated body was consumed')
            self.assertEqual(await request('invalid',forbidden_read),401)
            async def trickle():
                await asyncio.sleep(.01)
                return {'type':'http.request','body':b'x','more_body':True}
            with patch.object(server,'MCP_BODY_TIMEOUT_SECONDS',.04):
                self.assertEqual(await request('fixture',trickle),408)
            async def oversize():
                return {'type':'http.request','body':b'x'*65537,'more_body':True}
            self.assertEqual(await request('fixture',oversize),413)

    async def test_guard_does_not_timeout_streaming_response_or_get(self):
        import server
        entered=False
        async def inner(scope,receive,send):
            nonlocal entered
            entered=True
            if scope['method']=='POST':
                self.assertEqual((await receive())['body'],b'{}')
            await asyncio.sleep(.03)
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'data: fixture\n\n','more_body':False})
        async def receive(): return {'type':'http.request','body':b'{}','more_body':False}
        async def send(message): pass
        with patch.object(server,'MCP_BODY_TIMEOUT_SECONDS',.01):
            for method in ('GET','POST'):
                entered=False
                await server.MCPBodyGuard(inner)({'type':'http','method':method},receive,send)
                self.assertTrue(entered)
