import tempfile
import time
import unittest
from datetime import datetime, timezone
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient
from event_core import EventCore
from recovery_core import RecoveryCore
from server import create_app

ORIGIN = 'https://events.example'


class Provider:
    resource = ORIGIN+'/mcp'
    def build_auth_routes(self): return []
    async def load_access_token(self, token):
        return AccessToken(token=token, client_id='fixture', subject='alice', resource=self.resource,
            scopes=['events:observe']+(['recovery:execute'] if token == 'write' else []), expires_at=int(time.time())+600)


class RecoveryHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.now=float(int(time.time()))
        self.core=EventCore(self.tmp.name+'/probe.sqlite',[])
        self.recovery=RecoveryCore(self.tmp.name+'/recovery.sqlite',[], 'alice',clock=lambda:self.now)
        self.client=TestClient(create_app(self.core,Provider(),ORIGIN,self.recovery),base_url=ORIGIN)
        self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None);self.tmp.cleanup()
    def rpc(self,method,args,token='read'):
        params=dict(args)
        params['_meta']={'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}
        headers={'Authorization':'Bearer '+token,'MCP-Method':method,'MCP-Protocol-Version':'2026-07-28','Accept':'application/json, text/event-stream'}
        if method=='tools/call':headers['MCP-Name']=args['name']
        r=self.client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
        self.assertIn(r.status_code,(200,400),r.text)
        if r.status_code == 400:
            self.assertIn('error', r.json())
        return r.json()
    def observation(self,healthy=False):
        return {'observed_at':datetime.fromtimestamp(self.now,timezone.utc).isoformat(),'state':'verified_running' if healthy else 'external_unreachable',
          'ssh':{'command_succeeded':healthy,'probe_valid':healthy,'exit_code':0 if healthy else 255,'error_class':'ok' if healthy else 'timeout'},
          'peer_ping':{'responded':healthy,'exit_code':0 if healthy else 1}}
    def incident(self):
        for _ in range(3):
            self.now+=60;result=self.recovery.ingest(self.observation())
        return result['incident']['incident_id']
    def call(self,name,args,token='read'):return self.rpc('tools/call',{'name':name,'arguments':args},token)
    def test_read_scope_cannot_claim_and_grant_is_single_use(self):
        iid=self.incident()
        self.assertIn('error',self.call('claim_recovery',{'incident_id':iid}))
        result=self.call('claim_recovery',{'incident_id':iid},'write')['result']['structuredContent']
        self.assertEqual(result['command'],'bash /workspace/.dots-tailscale/start.sh')
        self.assertIn('error',self.call('claim_recovery',{'incident_id':iid},'write'))
        self.assertIn('error',self.call('report_recovery_attempt',{'incident_id':iid,'claim_id':result['claim_id'],'outcome':'success'}))
        state=self.call('report_recovery_attempt',{'incident_id':iid,'claim_id':result['claim_id'],'outcome':'success'},'write')['result']['structuredContent']
        self.assertEqual(state['incident']['state'],'attempted')
    def test_stale_or_recovered_incidents_do_not_authorize_commands(self):
        iid=self.incident();self.now+=151
        self.assertIn('error',self.call('claim_recovery',{'incident_id':iid},'write'))
        self.recovery.ingest(self.observation(True))
        self.assertIn('error',self.call('claim_recovery',{'incident_id':iid},'write'))
    def test_catalog_and_explicit_write_scopes(self):
        names={e['name'] for e in self.rpc('events/list',{})['result']['events']}
        self.assertEqual(names,{'dots.recovery_probe','dots.recovery_requested'})
        tools={t['name']:t for t in self.rpc('tools/list',{})['result']['tools']}
        self.assertIn('recovery:execute',tools['claim_recovery']['securitySchemes'][0]['scopes'])
        self.assertFalse(tools['claim_recovery']['annotations']['readOnlyHint'])
        self.assertNotIn('shell',tools)
    def test_unknown_and_extra_arguments_are_rejected(self):
        self.assertIn('error',self.call('claim_recovery',{},'write'))
        self.assertIn('error',self.call('claim_recovery',{'incident_id':'bad','command':'rm'},'write'))
        self.assertIn('error',self.call('get_current_recovery',{'incident_id':42}))

    def test_preflight_reports_need_write_scope_but_do_not_claim(self):
        iid = self.incident()
        args = {'incident_id': iid, 'reason': 'computer_unavailable'}
        self.assertIn('error', self.call('report_recovery_preflight', args))
        result = self.call('report_recovery_preflight', args, 'write')['result']['structuredContent']
        self.assertEqual(result['incident']['preflight_report']['reason'], 'computer_unavailable')
        self.assertEqual(result['incident']['state'], 'pending')
        self.assertTrue(result['claimable'])
        self.assertIn('error', self.call('report_recovery_preflight', dict(args, reason='SECRET'), 'write'))
        tool = next(t for t in self.rpc('tools/list', {})['result']['tools'] if t['name'] == 'report_recovery_preflight')
        self.assertIn('recovery:execute', tool['securitySchemes'][0]['scopes'])


if __name__=='__main__':unittest.main()
