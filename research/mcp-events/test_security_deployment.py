import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing

from apply_routes import candidates as initial_candidates
from security_deployment import candidates, copy_database, open_incidents, trusted_tree, validate_offline_lock, lock_pins, authelia_binary, create_application_directory


class SecurityDeploymentTests(unittest.TestCase):
    def test_application_directory_traversable_under_private_umask(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'app'
            previous = os.umask(0o077)
            try:
                create_application_directory(path)
            finally:
                os.umask(previous)
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_authelia_discovery_uses_system_local_bin_not_inherited_path(self):
        with patch('security_deployment.shutil.which', return_value='/usr/local/bin/authelia') as which:
            self.assertEqual(authelia_binary(), '/usr/local/bin/authelia')
            which.assert_called_once_with('authelia', path='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
        with patch('security_deployment.shutil.which', return_value=None):
            with self.assertRaises(ValueError):
                authelia_binary()

    def test_scoped_routes_keep_other_host_and_single_user(self):
        c,a=initial_candidates('(tls_origin_dev) {\n}\nother.example {\n respond hi\n}\n',
            'access_control:\n  rules:\n', 'dots-events.example.com','operator')
        nc,na=candidates(c,a,'dots-events.example.com','operator')
        self.assertIn('other.example {\n respond hi\n}',nc)
        self.assertEqual(nc.count('reverse_proxy unix//run/dots-events-recovery.sock'),2)
        self.assertIn('/oauth/consent/* /authorize',nc)
        self.assertIn("policy: 'two_factor'",na)
        self.assertIn("subject: ['user:operator']",na)
        self.assertIn("'^/authorize(?:\\?.*)?$'",na)
        self.assertIn("'^/oauth/consent(?:/[^?]*)?(?:\\?.*)?$'",na)
        with self.assertRaises(ValueError): candidates(nc,na,'dots-events.example.com','operator')
        with self.assertRaises(ValueError): candidates(c,a.replace('operator','someone'),'dots-events.example.com','operator')

    def test_legacy_and_identity_proxy_blocks_migrate_without_trusting_input(self):
        c,a=initial_candidates('(tls_origin_dev) {\n}\n',
            'access_control:\n  rules:\n', 'dots-events.example.com','operator')
        current,auth=candidates(c,a,'dots-events.example.com','operator')
        self.assertEqual(current.count('header_up X-Dots-Client-IP {client_ip}'),2)
        self.assertEqual(current.count('reverse_proxy unix//run/dots-events-recovery.sock {'),2)
        legacy=c.replace('reverse_proxy 127.0.0.1:18743 {\n                header_up X-Dots-Client-IP {client_ip}\n            }',
                         'reverse_proxy 127.0.0.1:18743')
        self.assertEqual(candidates(legacy,a,'dots-events.example.com','operator'),(current,auth))
        for header in ('{http.request.header.X-Dots-Client-IP}', '{http.request.header.X-Forwarded-For}',
                       '127.0.0.1', '{remote_host}'):
            with self.subTest(header=header),self.assertRaises(ValueError):
                candidates(c.replace('header_up X-Dots-Client-IP {client_ip}',
                                     'header_up X-Dots-Client-IP '+header),a,'dots-events.example.com','operator')
        with self.assertRaises(ValueError):
            candidates(c.replace('header_up X-Dots-Client-IP', 'header_up +X-Dots-Client-IP'),a,'dots-events.example.com','operator')
        with self.assertRaises(ValueError):
            candidates(c.replace('header_up X-Dots-Client-IP {client_ip}',
                                 'header_up X-Dots-Client-IP {client_ip}\n                header_up Remote-User forged'),a,'dots-events.example.com','operator')

    def test_sqlite_backup_includes_wal_and_preserves_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); src=root/'recovery.sqlite'; target=root/'snapshot.sqlite'
            with closing(sqlite3.connect(src)) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE recovery_incidents(id TEXT,closed REAL,claim_id TEXT)')
                db.execute("INSERT INTO recovery_incidents VALUES ('incident',NULL,'claimed')")
                db.commit()
                self.assertEqual(open_incidents(root),1)
                copy_database(src,target)
                with closing(sqlite3.connect(target)) as saved:
                    self.assertEqual(saved.execute('SELECT claim_id FROM recovery_incidents').fetchone()[0],'claimed')
                self.assertEqual(target.stat().st_mode & 0o777,0o600)
                with self.assertRaises(ValueError): copy_database(src,target)

    def test_reject_linked_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); actual=root/'db'; actual.touch(); linked=root/'linked'; linked.symlink_to(actual)
            with self.assertRaises(ValueError): copy_database(linked,root/'copy')

    def test_stage_requires_absolute_root_trusted_path(self):
        with self.assertRaises(ValueError): trusted_tree(Path('relative'))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError): trusted_tree(Path(temp))

    def test_offline_pins_match_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); original=root/'original'; wheel=root/'wheel'
            original.write_text('demo_pkg==1.0 \\\n    --hash=sha256:'+('a'*64)+'\n')
            wheel.write_text('demo-pkg==1.0 --hash=sha256:'+('b'*64)+'\n')
            validate_offline_lock(original,wheel)
            wheel.write_text('demo-pkg==1.1 --hash=sha256:'+('b'*64)+'\n')
            with self.assertRaises(ValueError): validate_offline_lock(original,wheel)
        self.assertGreater(len(lock_pins(Path(__file__).parent/'requirements.lock')), 0)

    def test_unit_has_separate_identity_and_only_runtime_writable(self):
        root=Path(__file__).parent/'services/security'
        unit=(root/'dots-events-recovery.service').read_text()
        for line in ('User=dots-events','ProtectHome=yes','ProtectSystem=strict','NoNewPrivileges=yes',
                     'MemoryMax=256M','CPUQuota=50%','TasksMax=64','ReadWritePaths=/var/lib/dots-events'):
            self.assertIn(line,unit)
        socket=(root/'dots-events-recovery.socket').read_text()
        self.assertIn('SocketMode=0660',socket)
        self.assertIn('SocketGroup=caddy',socket)

if __name__=='__main__': unittest.main()
