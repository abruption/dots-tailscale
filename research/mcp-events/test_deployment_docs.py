"""Keep copied deployment examples aligned with the generated OAuth boundary."""
from pathlib import Path
import re
import unittest
from apply_routes import AUTHORIZE_RESOURCE, CONSENT_RESOURCE


class DeploymentExamples(unittest.TestCase):
    def test_documented_browser_routes_match_current_oauth_requirements(self):
        doc = Path(__file__).with_name('deployment.md').read_text()
        caddy = re.search(r'```caddyfile\n(.*?)\n```', doc, re.S).group(1)
        matcher = re.search(r'@consent path (.*)', caddy).group(1).split()
        self.assertEqual(set(matcher), {'/authorize', '/oauth/consent', '/oauth/consent/*'})
        self.assertLess(caddy.index('request_header -Remote-User'), caddy.index('handle @consent'))
        protected = caddy[caddy.index('handle @consent'):caddy.index('        handle {')]
        self.assertIn('forward_auth 127.0.0.1:9091', protected)
        self.assertIn('copy_headers Remote-User', protected)
        self.assertEqual(caddy.count('header_up X-Dots-Client-IP {client_ip}'), 2)
        self.assertNotIn('header_up +X-Dots-Client-IP', caddy)
        yaml = re.search(r'```yaml\n(.*?)\n```', doc, re.S).group(1)
        resource_line = next(line for line in yaml.splitlines() if 'resources:' in line)
        patterns = re.findall(r"'([^']+)'", resource_line)
        self.assertEqual(patterns, [AUTHORIZE_RESOURCE, CONSENT_RESOURCE])
        for path in ('/authorize', '/authorize?client_id=fixture&state=fixture',
                     '/oauth/consent?ticket=fixture', '/oauth/consent'):
            self.assertTrue(any(re.fullmatch(pattern, path) for pattern in patterns), path)
        for path in ('/token', '/revoke', '/.well-known/oauth-authorization-server',
                     '/authorize-evil', '/x/authorize'):
            self.assertFalse(any(re.fullmatch(pattern, path) for pattern in patterns), path)

    def test_legacy_example_links_to_hardened_deployment(self):
        doc = Path(__file__).with_name('deployment.md').read_text()
        self.assertIn('legacy\nloopback listener', doc)
        self.assertIn('[security hardening](security-hardening.md)', doc)
        self.assertIn('../../docs/experimental-recovery.md', doc)
