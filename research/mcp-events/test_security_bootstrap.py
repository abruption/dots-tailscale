"""Pinned snapshot and filesystem boundaries; no privileged commands."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import security_bootstrap as bootstrap


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.data={name:b'# reviewed fixture\n' for name in (
            'security_deployment.py','security_ipv6.py','apply_routes.py',
            'services/security/dots-events-ipv6-guard.service')}
        for name,body in self.data.items():
            path=self.root/name
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            path.write_bytes(body)
            path.chmod(0o600)
        self.write_manifest()

    def write_manifest(self,extra=None):
        manifest={name:hashlib.sha256(body).hexdigest() for name,body in self.data.items()}
        manifest.update(extra or {})
        raw=json.dumps(manifest).encode()
        (self.root/'manifest.json').write_bytes(raw)
        (self.root/'manifest.json').chmod(0o600)
        self.digest=hashlib.sha256(raw).hexdigest()

    def snapshot(self):
        # Test fixture sits under a world-writable OS temporary root; bypass
        # ancestor ownership only, retain all relative-dir/file real checks.
        with patch.object(bootstrap,'open_directory',lambda path,owner:os.open(path,os.O_RDONLY|os.O_DIRECTORY)):
            return bootstrap.snapshot_payload(self.root,self.digest,os.getuid())

    def test_valid_snapshot_freezes_bytes_before_source_changes(self):
        frozen=self.snapshot()
        (self.root/'security_ipv6.py').write_text('tampered after verification')
        dest=self.root/'frozen'
        with patch.object(bootstrap,'root_directory'):
            result=bootstrap.freeze_payload(frozen,self.digest,dest)
        self.assertEqual((result/'security_ipv6.py').read_bytes(),self.data['security_ipv6.py'])
        self.assertEqual((result/'security_ipv6.py').stat().st_mode & 0o777,0o600)

    def test_manifest_tamper_and_payload_tamper_fail(self):
        (self.root/'manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'manifest digest mismatch'):
            self.snapshot()
        self.write_manifest()
        (self.root/'security_ipv6.py').write_text('tamper')
        with self.assertRaisesRegex(ValueError,'payload digest mismatch'):
            self.snapshot()

    def test_symlink_file_and_subdirectory_rejected(self):
        target=self.root/'security_ipv6.py'
        target.unlink()
        target.symlink_to(self.root/'apply_routes.py')
        with self.assertRaises(OSError): self.snapshot()
        target.unlink();target.write_bytes(self.data['security_ipv6.py']);target.chmod(0o600)
        directory=self.root/'services/security'
        directory.rename(self.root/'real-security')
        directory.symlink_to(self.root/'real-security',target_is_directory=True)
        with self.assertRaises(OSError): self.snapshot()

    def test_world_writable_source_rejected(self):
        (self.root/'security_ipv6.py').chmod(0o666)
        with self.assertRaisesRegex(ValueError,'ownership/type/mode'):
            self.snapshot()

    def test_path_and_secret_allowlist(self):
        for name in ('../server.py','/server.py','a/../server.py','./server.py',
                     'config.json','oauth.sqlite','key.pem','wheelhouse/../key.pem',
                     'unknown.py','services/security/unknown.service'):
            self.assertFalse(bootstrap.allowed_path(name),name)
        self.assertTrue(bootstrap.allowed_path('test_auth_hardening.py'))
        self.assertTrue(bootstrap.allowed_path('wheelhouse/fixture-1.0-py3-none-any.whl'))
        self.write_manifest({'../server.py':'a'*64})
        with self.assertRaisesRegex(ValueError,'unapproved payload path'):
            self.snapshot()

    def test_size_limit_rejects_before_read(self):
        with patch.object(bootstrap,'MAX_FILE',1):
            with self.assertRaisesRegex(ValueError,'size limit'):
                self.snapshot()

    def test_duplicate_manifest_key_rejected(self):
        raw=b'{"server.py":"'+b'a'*64+b'","server.py":"'+b'b'*64+b'"}'
        (self.root/'manifest.json').write_bytes(raw)
        self.digest=hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ValueError,'duplicate'):
            self.snapshot()

    def test_ancestor_symlink_rejected_by_directory_walk(self):
        # /dev/fd is a symlink on Unix; stop at it before any ownership check.
        with self.assertRaises((OSError,ValueError)):
            bootstrap.open_directory(Path('/dev/fd'),os.getuid())
