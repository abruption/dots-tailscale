"""Real ASGI HTTP contract checks, synthetic token provider and callback only."""
import base64
import json
import tempfile
import time
import unittest
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient
from event_core import EventCore, NAME
from server import create_app

ORIGIN = 'https://events.example'
CALLBACK = 'https://callback.example/events'
SECRET = 'whsec_' + base64.b64encode(b'x' * 32).decode()

class FakeProvider:
    resource = ORIGIN + '/mcp'
    def build_auth_routes(self): return []
    async def load_access_token(self, token):
        if token == 'invalid': return None
        return AccessToken(token=token, client_id='test-client', scopes=['events:observe'] if token != 'noscope' else [],
                           expires_at=int(time.time())+3600, resource='https://wrong.example/mcp' if token == 'wrong' else self.resource,
                           subject='bob' if token == 'bob' else 'alice')

class ServerContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.calls=[]
        def transport(url,body,headers):
            self.calls.append((url,json.loads(body)))
            return 200,json.dumps({'challenge':json.loads(body)['challenge']}).encode()
        self.core=EventCore(self.tmp.name+'/events.sqlite',['https://callback.example'],transport)
        self.client=TestClient(create_app(self.core,FakeProvider(),ORIGIN),base_url=ORIGIN)
        self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.tmp.cleanup()
    def rpc(self,method,params=None,token='good'):
        headers={'MCP-Method':method,'Accept':'application/json, text/event-stream','Content-Type':'application/json','MCP-Protocol-Version':'2026-07-28'}
        if token is not None: headers['Authorization']='Bearer '+token
        params=dict(params or {})
        if method == 'tools/call': headers['MCP-Name'] = params['name']
        params['_meta']={'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}
        return self.client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
    def params(self):
        return {'name':NAME,'arguments':{'target_id':'dots'},'delivery':{'mode':'webhook','url':CALLBACK,'secret':SECRET}}
    def test_auth_and_resource_boundaries(self):
        for token in (None,'invalid','wrong','noscope'):
            r=self.rpc('events/list',token=token)
            self.assertIn(r.status_code,(401,403),r.text)
        r=self.rpc('events/list')
        self.assertEqual(r.status_code,200,r.text)
        self.assertIn('result',r.json(),r.text)
    def test_discovery_catalog_and_no_shell_tools(self):
        r=self.rpc('server/discover').json()
        self.assertIn('events',r['result']['capabilities'])
        event=self.rpc('events/list').json()['result']['events'][0]
        self.assertEqual(event['name'],NAME)
        self.assertIn('inputSchema',event)
        self.assertIn('payloadSchema',event)
        self.assertEqual(event['delivery'],['webhook'])
        names={x['name'] for x in self.rpc('tools/list').json()['result']['tools']}
        self.assertEqual(names,{'get_profile','get_probe_capabilities','get_probe_event'})
        self.assertIn('error',self.rpc('tools/call',{'name':'shell','arguments':{}}).json())
    def test_probe_lookup_is_scoped_read_only_and_does_not_emit(self):
        self.core.emit_probe('alice','lookup-test')
        args={'name':'get_probe_event','arguments':{'test_id':'lookup-test'}}
        own=self.rpc('tools/call',args).json()['result']['structuredContent']
        self.assertTrue(own['found'])
        self.assertEqual(own['event']['data']['test_id'],'lookup-test')
        self.assertEqual(self.rpc('tools/call',args,'bob').json()['result']['structuredContent'],{'found':False})
        args['arguments']={'test_id':'absent'}
        self.assertEqual(self.rpc('tools/call',args).json()['result']['structuredContent'],{'found':False})
        self.assertEqual(self.calls,[])
        for invalid in ({},{'test_id':12},{'test_id':'x','owner':'alice'}):
            self.assertIn('error',self.rpc('tools/call',{'name':'get_probe_event','arguments':invalid}).json())
    def test_subscribe_identity_filter_and_owner_isolation(self):
        p=self.params()
        r=self.rpc('events/subscribe',p).json()
        self.assertIn('result',r,r)
        sid=r['result']['id']
        self.assertEqual(self.rpc('events/subscribe',p).json()['result']['id'],sid)
        unsub={'name':NAME,'arguments':p['arguments'],'delivery':{'mode':'webhook','url':CALLBACK}}
        self.assertIn('result',self.rpc('events/unsubscribe',unsub,'bob').json())
        eid=self.core.emit_probe('alice','owner-check')
        self.assertEqual(len(self.core.receipt('alice',eid)['deliveries']),1)
        p['arguments']={'target_id':'other'}
        self.assertIn('error',self.rpc('events/subscribe',p).json())
        self.assertIn('result',self.rpc('events/unsubscribe',unsub).json())
        self.assertEqual(self.core.receipt('alice',self.core.emit_probe('alice','after-unsub'))['deliveries'],[])
    def test_ttl_clamp_and_invalid_type(self):
        p=self.params();p['ttlMs']=999999999
        before=time.time()
        r=self.rpc('events/subscribe',p).json()
        self.assertIn('result',r,r)
        from datetime import datetime
        remaining=datetime.fromisoformat(r['result']['refreshBefore']).timestamp()-before
        self.assertTrue(3599<=remaining<=3602,remaining)
        p['ttlMs']=1000
        self.assertIn('result',self.rpc('events/subscribe',p).json())
        for bad in (0,-1,True,'1000'):
            p['ttlMs']=bad
            self.assertIn('error',self.rpc('events/subscribe',p).json())

if __name__=='__main__': unittest.main()
