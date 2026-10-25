"""Pure candidate-generation tests; never execute privileged deployment."""
import unittest
import re
from apply_routes import candidates, authelia_validation_command, CONSENT_RESOURCE, AUTHORIZE_RESOURCE

CADDY = '''(tls_origin_dev) {
    tls /test/cert /test/key
}
https://*.example.test {
    redir https://example.test{uri} permanent
}
'''
AUTHELIA = '''server:
  address: 'tcp://127.0.0.1:9091/'
access_control:
  default_policy: deny
  rules:
    - domain: 'existing.example.test'
      policy: two_factor
      subject: ['group:existing']
session:
  name: example
'''
HOST = 'dots-events.example.test'


class CandidateTests(unittest.TestCase):
    def test_validation_uses_service_environment_without_secret_argv(self):
        command = authelia_validation_command('/usr/local/bin/authelia', '/private/candidate path.yml')
        self.assertEqual(command[0], 'systemd-run')
        self.assertIn('--wait', command)
        self.assertIn('--collect', command)
        self.assertIn('--property=RuntimeMaxSec=45s', command)
        self.assertIn('--property=EnvironmentFile=/etc/authelia/authelia.env', command)
        self.assertEqual(command[-4:], ['/usr/local/bin/authelia', 'validate-config', '--config', '/private/candidate path.yml'])

    def test_existing_content_preserved_and_new_rule_scoped(self):
        c, a = candidates(CADDY, AUTHELIA, HOST, 'operator')
        self.assertTrue(c.startswith(CADDY))
        prefix, suffix = AUTHELIA.split('  rules:\n', 1)
        self.assertTrue(a.startswith(prefix + '  rules:\n'))
        self.assertTrue(a.endswith(suffix))
        self.assertEqual(a.count('default_policy: deny'), 1)
        self.assertIn(f"resources: ['{AUTHORIZE_RESOURCE}', '{CONSENT_RESOURCE}']", a)
        self.assertIn('@consent path /oauth/consent /oauth/consent/* /authorize', c)
        self.assertIn("subject: ['user:operator']", a)
        self.assertIn("policy: 'two_factor'", a)
        self.assertIn('request_header -Remote-User', c)
        self.assertLess(c.index('request_header -Remote-User'), c.index('forward_auth'))

    def test_rerun_refuses_duplicate_in_either_config(self):
        c, a = candidates(CADDY, AUTHELIA, HOST, 'operator')
        for pair in [(c, AUTHELIA), (CADDY, a), (c, a)]:
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                candidates(*pair, HOST, 'operator')

    def test_invalid_input_and_unexpected_configuration_fail_closed(self):
        for host, user in [('x\n}', 'operator'), (HOST, "x'\npolicy: bypass"), ('https://example.com', 'operator')]:
            with self.subTest(host=host, user=user), self.assertRaises(ValueError):
                candidates(CADDY, AUTHELIA, host, user)
        for c, a in [('', AUTHELIA), (CADDY, 'access_control: {}\n'), (CADDY, 'access_control:\n  rules: []\n')]:
            with self.subTest(c=c, a=a), self.assertRaises(ValueError):
                candidates(c, a, HOST, 'operator')

    def test_rules_in_other_section_do_not_match(self):
        value = 'access_control:\n  default_policy: deny\nother:\n  rules:\n    - domain: wrong\n'
        with self.assertRaises(ValueError):
            candidates(CADDY, value, HOST, 'operator')

    def test_consent_query_matches_but_neighbor_paths_do_not(self):
        for path in ['/oauth/consent', '/oauth/consent?ticket=abc', '/oauth/consent/?ticket=abc']:
            self.assertIsNotNone(re.fullmatch(CONSENT_RESOURCE, path))
        for path in ['/oauth/consent-evil?ticket=abc', '/authorize', '/token', '/x/oauth/consent']:
            self.assertIsNone(re.fullmatch(CONSENT_RESOURCE, path))

    def test_exact_repair_preserves_all_other_configuration(self):
        c, fixed = candidates(CADDY, AUTHELIA, HOST, 'operator')
        # This command repairs the legacy consent-only policy, not the new
        # authorization-admission policy used by a fresh hardened deployment.
        fixed = fixed.replace(f"'{AUTHORIZE_RESOURCE}', ", '')
        fixed = fixed.replace("browser authorization and consent paths", "only the browser consent path")
        broken = fixed.replace(CONSENT_RESOURCE, '^/oauth/consent(?:/.*)?$')
        after_c, after_a = candidates(c, broken, HOST, 'operator', True)
        self.assertEqual(after_c, c)
        self.assertEqual(after_a, fixed)
        with self.assertRaises(ValueError):
            candidates(c, fixed, HOST, 'operator', True)
        with self.assertRaises(ValueError):
            candidates(c, broken, HOST, 'other-user', True)


if __name__ == '__main__':
    unittest.main()
