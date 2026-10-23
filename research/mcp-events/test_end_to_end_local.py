"""Real OAuth + SDK HTTP + signed synthetic callback, entirely offline."""
import base64
import hashlib
import json
import re
import tempfile
import unittest
from urllib.parse import urlencode, urlsplit, parse_qs

from starlette.testclient import TestClient
from standardwebhooks.webhooks import Webhook
from event_core import EventCore, NAME
from oauth_adapter import OAuthAdapter
from server import create_app


class LocalEndToEnd(unittest.TestCase):
    def test_link_subscribe_deliver_unsubscribe(self):
        origin = 'https://events.example'
        redirect = 'https://chatgpt.com/connector_platform_oauth_redirect'
        secret = 'whsec_' + base64.b64encode(b'x' * 32).decode()
        delivered = []
        def transport(url, body, headers):
            event = Webhook(secret).verify(body, headers)
            if event.get('type') == 'verification':
                return 200, json.dumps({'challenge': event['challenge']}).encode()
            delivered.append(event)
            return 202, b'{}'
        with tempfile.TemporaryDirectory() as directory:
            auth = OAuthAdapter(directory+'/oauth.sqlite', origin, origin+'/mcp', 'alice', 'test-client', redirect)
            core = EventCore(directory+'/events.sqlite', ['https://callback.example'], transport)
            app = create_app(core, auth, origin)
            with TestClient(app, base_url=origin, client=('127.0.0.1', 12345), follow_redirects=False) as client:
                discovery = client.get('/.well-known/oauth-authorization-server')
                self.assertEqual(discovery.status_code, 200)
                self.assertTrue(discovery.json()['authorization_response_iss_parameter_supported'])
                verifier = 'v' * 64
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                authorization = client.get('/authorize?'+urlencode(dict(client_id='test-client', response_type='code',
                    redirect_uri=redirect, resource=origin+'/mcp', scope='events:observe',
                    state='test-state', code_challenge=challenge, code_challenge_method='S256')))
                self.assertIn(authorization.status_code, (302, 303), authorization.text)
                consent_url = authorization.headers['location']
                denied = client.get(consent_url)
                self.assertEqual(denied.status_code, 403)
                page = client.get(consent_url, headers={'Remote-User': 'alice'})
                self.assertEqual(page.status_code, 200, page.text)
                self.assertIn("form-action 'self' "+redirect+";", page.headers['Content-Security-Policy'])
                self.assertNotIn("unsafe-inline", page.headers['Content-Security-Policy'])
                ticket = parse_qs(urlsplit(consent_url).query)['ticket'][0]
                csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
                grant = client.post('/oauth/consent', headers={'Remote-User': 'alice', 'Origin': origin},
                    data={'ticket': ticket, 'csrf': csrf, 'decision': 'allow'})
                self.assertEqual(grant.status_code, 303, grant.text)
                params = parse_qs(urlsplit(grant.headers['location']).query)
                self.assertEqual(params['iss'], [origin])
                token_result = client.post('/token', data=dict(grant_type='authorization_code', client_id='test-client',
                    code=params['code'][0], redirect_uri=redirect, code_verifier=verifier, resource=origin+'/mcp'))
                self.assertEqual(token_result.status_code, 200, token_result.text)
                token = token_result.json()['access_token']
                def rpc(method, arguments=None):
                    data = dict(arguments or {})
                    data['_meta'] = {'io.modelcontextprotocol/protocolVersion': '2026-07-28', 'io.modelcontextprotocol/clientCapabilities': {}}
                    headers = {'Authorization': 'Bearer '+token, 'MCP-Method': method,
                        'MCP-Protocol-Version': '2026-07-28', 'Accept': 'application/json, text/event-stream'}
                    if method == 'tools/call':
                        headers['MCP-Name'] = arguments['name']
                    response = client.post('/mcp', headers=headers,
                        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': data})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn('result', response.json(), response.text)
                    return response.json()['result']
                self.assertIn('events', rpc('server/discover')['capabilities'])
                self.assertEqual(rpc('tools/call', {'name': 'get_profile', 'arguments': {}})['structuredContent']['id'], 'alice')
                rpc('events/subscribe', {'name': NAME, 'arguments': {'target_id': 'dots'},
                    'delivery': {'mode': 'webhook', 'url': 'https://callback.example/events', 'secret': secret}})
                eid = core.emit_probe('alice', 'offline-smoke')
                result = core.deliver('alice', eid)
                self.assertEqual(result['deliveries'][0]['state'], 'received')
                self.assertEqual(delivered[0]['data']['test_id'], 'offline-smoke')
                rpc('events/unsubscribe', {'name': NAME, 'arguments': {'target_id': 'dots'},
                    'delivery': {'mode': 'webhook', 'url': 'https://callback.example/events'}})
                after = core.emit_probe('alice', 'after-unsubscribe')
                self.assertEqual(core.receipt('alice', after)['deliveries'], [])


if __name__ == '__main__':
    unittest.main()
