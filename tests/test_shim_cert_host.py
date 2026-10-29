"""CONNECT host validation before networking and certificate cache creation.

The CONNECT host reaches certificate cache paths and OpenSSL identity fields.
Invalid hosts must be rejected before upstream networking, and accepted hosts
must never select a path outside the dedicated certificate directory.
"""
import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import shim


VALID_TARGETS = [
    ("controlplane.tailscale.com:443", "controlplane.tailscale.com", "443"),
    ("derp1f.tailscale.com:443", "derp1f.tailscale.com", "443"),
    ("Example.COM:80", "Example.COM", "80"),
    ("192.0.2.10:443", "192.0.2.10", "443"),
    ("localhost:65535", "localhost", "65535"),
    ("a" * 63 + ".example:443", "a" * 63 + ".example", "443"),
    ("[fd7a:115c:a1e0::1]:8080", "[fd7a:115c:a1e0::1]", "8080"),
]

INVALID_TARGETS = [
    "../escaped:443",
    "../../../../tmp/escaped:443",
    "/tmp/escaped:443",
    "certs/../escaped:443",
    "x/O=Injected/CN=evil:443",
    "evil.example\nDNS:second.example:443",
    "evil.example,DNS:second.example:443",
    "evil example:443",
    "ex_ample.com:443",
    "éxample.com:443",
    "１２３.example:443",
    "a..b:443",
    ".:443",
    ".example:443",
    "example.:443",
    "-example.com:443",
    "example-.com:443",
    "a" * 64 + ".example:443",
    ".".join(["a" * 63] * 4) + ":443",
    ":443",
    "",
    "example.com",
    "example.com:",
    "example.com:0",
    "example.com:0443",
    "example.com:65536",
    "example.com:44a",
    "example.com:+443",
    "::1:443",
    "[::1]:443",
    "[::1%eth0]:80",
    "[not-an-ip]:80",
    "[192.0.2.10]:80",
]


class ConnectTargetTests(unittest.TestCase):
    def test_accepts_hostnames_and_passthrough_ip_literals(self):
        for target, host, port in VALID_TARGETS:
            with self.subTest(target=target):
                self.assertEqual(shim.parse_connect_target(target), (host, port))

    def test_rejects_path_identity_and_malformed_authorities(self):
        for target in INVALID_TARGETS:
            with self.subTest(target=target):
                with self.assertRaises(OSError):
                    shim.parse_connect_target(target)

    def test_ipv6_literal_is_not_issued_a_local_certificate(self):
        self.assertEqual(shim.parse_connect_target("[2001:db8::1]:80"), ("[2001:db8::1]", "80"))
        with self.assertRaises(OSError):
            shim.parse_connect_target("[2001:db8::1]:443")


class ClientSocket:
    def __init__(self, data):
        self.data = data
        self.sent = []
        self.closed = False

    def settimeout(self, value):
        pass

    def recv(self, size):
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk

    def sendall(self, data):
        self.sent.append(bytes(data))

    def shutdown(self, how):
        pass

    def close(self):
        self.closed = True


class HandleRejectsBeforeNetworkingTests(unittest.TestCase):
    def test_invalid_target_gets_400_without_upstream_or_certificate(self):
        for target in ["../escaped:443", "/tmp/escaped:443", "example.com:0443", "[::1]:443"]:
            with self.subTest(target=target):
                client = ClientSocket(f"CONNECT {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode("latin1"))
                with mock.patch.object(shim, "upstream_connect",
                                       side_effect=AssertionError("upstream dialed")) as upstream, \
                        mock.patch.object(shim, "leaf", side_effect=AssertionError("leaf called")) as leaf, \
                        contextlib.redirect_stderr(io.StringIO()) as log:
                    shim.handle(client)
                upstream.assert_not_called()
                leaf.assert_not_called()
                self.assertEqual(b"".join(client.sent), b"HTTP/1.1 400 Bad Request\r\n\r\n")
                self.assertTrue(client.closed)
                self.assertNotIn(target.split(":")[0], log.getvalue())


@unittest.skipUnless(shutil.which("openssl"), "openssl is required to issue fixture certificates")
class LeafCertificateCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ca_dir = tempfile.TemporaryDirectory()
        ca = pathlib.Path(cls.ca_dir.name)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(ca / "ca.key"), "-out", str(ca / "ca.crt"), "-days", "1",
                        "-subj", "/CN=fixture-ca"], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.ca_dir.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.shim_dir = self.root / "shim"
        (self.shim_dir / "state").mkdir(parents=True)
        for name in ("ca.crt", "ca.key"):
            shutil.copy(pathlib.Path(self.ca_dir.name) / name, self.shim_dir / name)
        self.certs = self.shim_dir / "certs"
        patches = [mock.patch.object(shim, "D", str(self.shim_dir)),
                   mock.patch.object(shim, "CERTS", str(self.certs))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tree(self):
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))

    def test_valid_host_is_cached_only_inside_certificate_directory(self):
        shim.leaf("controlplane.tailscale.com")
        created = sorted(path.name for path in self.certs.iterdir())
        self.assertTrue(created)
        self.assertTrue(all(name.split(".", 1)[0] != "controlplane" for name in created), created)
        crt, key, _ = shim.cert_paths("controlplane.tailscale.com")
        for path in (crt, key):
            self.assertEqual(pathlib.Path(path).resolve().parent, self.certs.resolve())
        text = subprocess.run(["openssl", "x509", "-in", crt, "-noout", "-subject", "-ext", "subjectAltName"],
                              check=True, capture_output=True, text=True).stdout
        self.assertIn("DNS:controlplane.tailscale.com", text)
        self.assertIn("controlplane.tailscale.com", text.splitlines()[0])

    def test_cached_certificate_is_reused(self):
        shim.leaf("derp1f.tailscale.com")
        with mock.patch.object(shim.subprocess, "run", side_effect=AssertionError("regenerated")):
            shim.leaf("derp1f.tailscale.com")

    def test_distinct_hosts_get_distinct_cache_entries(self):
        self.assertNotEqual(shim.cert_paths("a.example")[0], shim.cert_paths("b.example")[0])

    def test_invalid_hosts_create_no_files_anywhere(self):
        before = self.tree()
        for host in ["../escaped", "../../escaped", str(self.root / "escaped"), "x/O=evil", "", ".", "..",
                     "../ca", "evil.example\nDNS:x"]:
            with self.subTest(host=host):
                with mock.patch.object(shim.subprocess, "run", side_effect=AssertionError("openssl ran")):
                    with self.assertRaises(OSError):
                        shim.leaf(host)
                self.assertEqual(self.tree(), before)

    def test_existing_file_outside_certificate_directory_is_not_overwritten(self):
        victim = self.shim_dir / "state" / "victim.key"
        victim.write_text("ORIGINAL")
        with self.assertRaises(OSError):
            shim.leaf("../state/victim")
        self.assertEqual(victim.read_text(), "ORIGINAL")
        ca_key = (self.shim_dir / "ca.key").read_bytes()
        with self.assertRaises(OSError):
            shim.leaf("../ca")
        self.assertEqual((self.shim_dir / "ca.key").read_bytes(), ca_key)


if __name__ == "__main__":
    unittest.main()
