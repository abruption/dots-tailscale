"""Isolated deployment fixtures; never access production files or services."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import new_incident_policy_deployment as deploy


SERVER = b'''# Existing mail connector customization: \xe3\x83\xa1\xe3\x83\xbc\xe3\x83\xab
from mail_core import MailCore
def load_mail(cfg):
    return MailCore(cfg['mail_database'])

def load_recovery(cfg):
    if not cfg.get('enable_recovery'):
        return None
    from recovery_core import RecoveryCore
    return RecoveryCore(Path(cfg['runtime_dir'])/'recovery.sqlite',
                        cfg['allowed_callback_origins'], cfg['allowed_user'],
                        max_claims_per_24h=cfg.get('recovery_max_claims_per_24h', 3),
                        cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600))

def list_mail(principal):
    return {'tools': ['get_mail', 'list_mail']}
'''


class PatchTests(unittest.TestCase):
    def test_adds_only_one_keyword_preserving_mail_bytes(self):
        result = deploy.patch_server(SERVER)
        insertion = b",\n                        new_incident_cooldown_seconds=cfg.get('recovery_new_incident_cooldown_seconds')"
        self.assertEqual(result.replace(insertion, b'', 1), SERVER)
        self.assertEqual(result.count(b'new_incident_cooldown_seconds='), 1)
        compile(result, '<fixture>', 'exec')

    def test_verified_already_patched_source_is_idempotent(self):
        result = deploy.patch_server(SERVER)
        self.assertEqual(deploy.patch_server(result), result)

    def test_refuses_ambiguous_or_unsupported_call_shapes(self):
        variants = [
            SERVER + b'\ndef load_recovery(cfg):\n    return None\n',
            SERVER.replace(b"return RecoveryCore(", b"other = RecoveryCore()\n    return RecoveryCore("),
            SERVER.replace(b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600)",
                           b"cooldown_seconds=600"),
            SERVER.replace(b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600)",
                           b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600), extra=True"),
            SERVER.replace(b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600)",
                           b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600), **options"),
            SERVER.replace(b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600)",
                           b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600), cooldown_seconds=600"),
        ]
        for body in variants:
            with self.subTest(body=body), self.assertRaises(ValueError):
                deploy.patch_server(body)

    def test_accepts_existing_trailing_comma_without_duplicate_comma(self):
        body = SERVER.replace(b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600))",
                              b"cooldown_seconds=cfg.get('recovery_cooldown_seconds', 3600),)")
        result = deploy.patch_server(body)
        compile(result, '<fixture>', 'exec')
        self.assertNotIn(b',,', result)


class ConfigTests(unittest.TestCase):
    def config(self):
        return {'enable_recovery': True, 'recovery_max_claims_per_24h': 12,
                'recovery_cooldown_seconds': 3600, 'mail': {'token': 'DUMMY_TEST_ONLY'},
                'origin': 'https://recovery.example.com'}

    def test_changes_only_new_key_and_preserves_budget_and_secrets(self):
        before = self.config()
        candidate, policy = deploy.config_candidate(json.dumps(before).encode(), 600)
        self.assertEqual(candidate.pop('recovery_new_incident_cooldown_seconds'), 600)
        self.assertEqual(candidate, before)
        self.assertEqual(policy, {'max_claims_per_24h': 12, 'cooldown_seconds': 3600,
                                  'new_incident_cooldown_seconds': 600})
        self.assertNotIn('DUMMY_TEST_ONLY', json.dumps(policy))

    def test_defaults_are_not_written_as_unrequested_changes(self):
        candidate, policy = deploy.config_candidate(b'{"enable_recovery":true}', 600)
        self.assertEqual(candidate, {'enable_recovery': True, 'recovery_new_incident_cooldown_seconds': 600})
        self.assertEqual(policy['max_claims_per_24h'], 3)
        self.assertEqual(policy['cooldown_seconds'], 3600)

    def test_rejects_invalid_new_or_existing_policy_values(self):
        for seconds in (True, '600', None, 299, 3601, -1):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                deploy.config_candidate(json.dumps(self.config()).encode(), seconds)
        for key, value in [('recovery_max_claims_per_24h', True),
                           ('recovery_max_claims_per_24h', '12'),
                           ('recovery_max_claims_per_24h', 25),
                           ('recovery_cooldown_seconds', '3600'),
                           ('recovery_cooldown_seconds', False),
                           ('enable_recovery', False)]:
            cfg = self.config(); cfg[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                deploy.config_candidate(json.dumps(cfg).encode(), 600)

    def test_duplicate_configuration_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            deploy.config_candidate(b'{"enable_recovery":true,"enable_recovery":false}', 600)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'source.py'
        self.path.write_bytes(b'old public code\n'); self.path.chmod(0o600)

    def test_atomic_replace_and_restore_preserve_metadata(self):
        old, info = deploy.read_regular(self.path, os.getuid())
        deploy.atomic_replace(self.path, b'candidate public code\n', info)
        self.assertEqual(self.path.read_bytes(), b'candidate public code\n')
        actual = self.path.stat()
        self.assertEqual((actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)),
                         (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)))
        deploy.atomic_replace(self.path, old, info)
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(list(self.root.glob('.new-incident-policy-*')), [])

    def test_pin_rejects_mismatch_before_use(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(deploy.pinned_body(self.path, digest, os.getuid())[0], self.path.read_bytes())
        for invalid in ('0' * 64, 'bad', True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                deploy.pinned_body(self.path, invalid, os.getuid())

    def test_rejects_symlink_hardlink_unsafe_mode_and_oversized_file(self):
        link = self.root / 'link'; link.symlink_to(self.path)
        with self.assertRaises(OSError):
            deploy.read_regular(link, os.getuid())
        hard = self.root / 'hard'; os.link(self.path, hard)
        with self.assertRaises(ValueError):
            deploy.read_regular(self.path, os.getuid())
        hard.unlink(); self.path.chmod(0o666)
        with self.assertRaises(ValueError):
            deploy.read_regular(self.path, os.getuid())
        self.path.chmod(0o600)
        with self.assertRaises(ValueError):
            deploy.read_regular(self.path, os.getuid(), limit=2)

    def test_quiescent_preflight_refuses_each_unsafe_condition(self):
        healthy = {'observation_fresh': True, 'external_healthy': True,
                   'open_incidents': 0, 'sending_deliveries': 0}
        deploy.safe_summary(healthy)
        for key, value in [('observation_fresh', False), ('external_healthy', False),
                           ('open_incidents', 1), ('sending_deliveries', 1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                deploy.safe_summary({**healthy, key: value})


class OrchestrationTests(unittest.TestCase):
    def test_schema_failure_restores_only_code_and_config_not_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); app = root / 'app'; app.mkdir()
            data = root / 'data'; data.mkdir(); records = root / 'records'; records.mkdir()
            stage = root / 'stage'; stage.mkdir()
            core = b'OLD_CORE = True\n'; candidate = b'NEW_CORE = True\n'
            cfg = json.dumps({'enable_recovery': True, 'origin': 'https://recovery.example.com',
                              'recovery_max_claims_per_24h': 12,
                              'recovery_cooldown_seconds': 3600}).encode()
            config = data / 'config.json'
            for path, body in [(app / 'recovery_core.py', core), (app / 'server.py', SERVER),
                               (stage / 'recovery_core.py', candidate), (config, cfg)]:
                path.write_bytes(body); path.chmod(0o600)
            ledger = data / 'recovery.sqlite'
            ledger.write_bytes(b'Never rewind authorization ledger')
            summary = {'observation_fresh': True, 'external_healthy': True,
                       'open_incidents': 0, 'sending_deliveries': 0, 'claims': 5,
                       'subscriptions': 1, 'active_subscriptions': 1}
            commands = []

            def run(argv, **kwargs):
                commands.append(argv)
                if '-c' in argv:
                    script = argv[argv.index('-c') + 1]
                    if script == deploy.SUMMARY:
                        return subprocess.CompletedProcess(argv, 0, json.dumps(summary), '')
                    if script == deploy.ADOPT:
                        # This happens after writes and must trigger code-only rollback.
                        self.assertEqual((app / 'recovery_core.py').read_bytes(), candidate)
                        raise RuntimeError('simulated schema adoption failure')
                return subprocess.CompletedProcess(argv, 0, '', '')

            actual_read = deploy.read_regular
            actual_pin = deploy.pinned_body
            argv = ['helper', '--source-dir', str(stage), '--expected-core-sha256', deploy.sha256(core),
                    '--expected-server-sha256', deploy.sha256(SERVER),
                    '--candidate-core-sha256', deploy.sha256(candidate),
                    '--new-incident-cooldown-seconds', '600']
            output = io.StringIO(); previous_umask = os.umask(0o077)
            try:
                with contextlib.ExitStack() as stack:
                    for key, value in [('APP', app), ('DATA', data), ('CONFIG', config), ('RECORDS', records)]:
                        stack.enter_context(patch.object(deploy, key, value))
                    stack.enter_context(patch.object(deploy.os, 'geteuid', return_value=0))
                    stack.enter_context(patch.object(deploy.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())))
                    stack.enter_context(patch.object(deploy, 'trusted_directory'))
                    stack.enter_context(patch.object(deploy, 'read_regular', side_effect=lambda p, owner=0, limit=deploy.LIMIT: actual_read(p, os.getuid(), limit)))
                    stack.enter_context(patch.object(deploy, 'pinned_body', side_effect=lambda p, digest, owner=0: actual_pin(p, digest, os.getuid())))
                    stack.enter_context(patch.object(deploy, 'acquire_lock', side_effect=lambda p: os.open(p, os.O_CREAT | os.O_RDWR, 0o600)))
                    stack.enter_context(patch.object(deploy.subprocess, 'run', side_effect=run))
                    stack.enter_context(patch('sys.argv', argv))
                    stack.enter_context(contextlib.redirect_stdout(output))
                    with self.assertRaisesRegex(RuntimeError, 'root-private record'):
                        deploy.main()
            finally:
                os.umask(previous_umask)
            self.assertEqual((app / 'recovery_core.py').read_bytes(), core)
            self.assertEqual((app / 'server.py').read_bytes(), SERVER)
            self.assertEqual(config.read_bytes(), cfg)
            self.assertEqual(ledger.read_bytes(), b'Never rewind authorization ledger')
            result = json.loads(output.getvalue())
            self.assertEqual(result['rollback'], 'code_and_config_restored')
            self.assertFalse(result['ledger_restored'])
            self.assertEqual(result['failed_phase'], 'schema_adoption')
            self.assertEqual(sum(cmd[:2] == ['systemctl', 'start'] for cmd in commands), 1)


if __name__ == '__main__':
    unittest.main()
