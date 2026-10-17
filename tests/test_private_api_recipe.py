"""Exercise the documented client through local HTTPS and CONNECT fixtures.

Only disposable certificates and in-memory dummy credentials are used. No
tailnet, installed daemon, external service, or privileged port is involved.
"""
import http.server
import json
import os
import pathlib
import re
import secrets
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUIDES = [ROOT / "docs/private-http-api.md", ROOT / "docs/private-http-api.ko.md"]


def recipe(path):
    match = re.search(r"```bash\npython3 - <<'PY'\n(.*?)\nPY\n```", path.read_text(), re.S)
    if match is None:
        raise AssertionError("missing executable API recipe in " + str(path))
    return match.group(1)


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        authorized = self.headers.get("Authorization") == "Bearer " + self.server.expected_token
        status = 200 if authorized else self.server.rejection_status
        body = self.server.body if authorized else b'{"error":"private-rejection-detail"}'
        self.server.requests.append(authorized)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        header = b""
        try:
            while b"\r\n\r\n" not in header and len(header) < 16384:
                block = self.request.recv(1024)
                if not block:
                    return
                header += block
            first = header.split(b"\r\n", 1)[0]
            expected = "CONNECT 127.0.0.1:{} HTTP/1.1".format(self.server.target_port).encode()
            if first != expected:
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            self.server.connections.append(first)
            with socket.create_connection(("127.0.0.1", self.server.target_port), timeout=5) as peer:
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([self.request, peer], [], [], 0.2)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (peer if source is self.request else self.request).sendall(data)
        except (OSError, ValueError):
            # TLS rejection and client termination deliberately close tunnels.
            return


class ConnectServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@unittest.skipUnless(shutil.which("curl") and shutil.which("openssl"), "curl and openssl required")
class PrivateApiRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="codex-private-api-")
        cls.addClassCleanup(cls.temp.cleanup)
        directory = pathlib.Path(cls.temp.name)
        directory.chmod(0o700)
        cls.cert = directory / "fixture.crt"
        key = directory / "fixture.key"
        config = directory / "openssl.cnf"
        config.write_text(
            "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
            "[dn]\nCN=local-test-only\n[ext]\nsubjectAltName=IP:127.0.0.1\n"
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,digitalSignature,keyEncipherment\n"
        )
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-config", str(config), "-keyout", str(key), "-out", str(cls.cert)],
            capture_output=True, check=True, timeout=15,
        )
        key.chmod(0o600)
        cls.api = ThreadedHTTPServer(("127.0.0.1", 0), HealthHandler)
        cls.addClassCleanup(cls.api.server_close)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cls.cert), str(key))
        cls.api.socket = context.wrap_socket(cls.api.socket, server_side=True)
        cls.api_thread = threading.Thread(target=cls.api.serve_forever, daemon=True)
        cls.api_thread.start()
        cls.addClassCleanup(cls.api.shutdown)
        cls.proxy = ConnectServer(("127.0.0.1", 0), ConnectHandler)
        cls.addClassCleanup(cls.proxy.server_close)
        cls.proxy.target_port = cls.api.server_port
        cls.proxy.connections = []
        cls.proxy_thread = threading.Thread(target=cls.proxy.serve_forever, daemon=True)
        cls.proxy_thread.start()
        cls.addClassCleanup(cls.proxy.shutdown)

    def setUp(self):
        self.api.expected_token = secrets.token_urlsafe(24)
        self.api.rejection_status = 401
        self.api.body = b'{"status":"ok"}'
        self.api.requests = []
        self.proxy.connections.clear()

    def run_recipe(self, token=None, trust=True, proxy_port=None):
        token = self.api.expected_token if token is None else token
        source = recipe(GUIDES[0])
        source = source.replace(
            'url = "https://api.example.internal:8443/health"',
            'url = "https://127.0.0.1:{}/health"'.format(self.api.server_port),
        ).replace(
            'proxy = "http://127.0.0.1:8080"',
            'proxy = "http://127.0.0.1:{}"'.format(proxy_port or self.proxy.server_address[1]),
        )
        if trust:
            source = source.replace('ca_file = ""', 'ca_file = ' + repr(str(self.cert)))
        # The generated credential reaches the child on stdin only. Audit the
        # real curl invocation in memory before forwarding it unchanged.
        prefix = """
import getpass, subprocess
_dummy_token = {token!r}
getpass.getpass = lambda prompt: _dummy_token
_real_run = subprocess.run
def audited_run(command, **kwargs):
    assert _dummy_token not in repr(command)
    assert all(_dummy_token not in str(v) for v in kwargs['env'].values())
    assert 'NO_PROXY' not in kwargs['env'] and 'no_proxy' not in kwargs['env']
    assert command[command.index('--noproxy') + 1] == ''
    assert command[command.index('--header') + 1] == '@-'
    assert kwargs['input'] == 'Authorization: Bearer ' + _dummy_token + '\\n'
    return _real_run(command, **kwargs)
subprocess.run = audited_run
""".format(token=token)
        env = dict(os.environ, NO_PROXY="*", no_proxy="*")
        # Do not inherit an unrelated private trust override into TLS tests.
        for name in ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
            env.pop(name, None)
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-"], input=prefix + source, env=env,
            capture_output=True, text=True, timeout=28,
        )
        self.assertLess(time.monotonic() - started, 27)
        self.assertNotIn(token, result.stdout + result.stderr)
        return result

    def test_bilingual_recipe_matches_and_success_really_uses_connect_proxy(self):
        self.assertEqual(recipe(GUIDES[0]), recipe(GUIDES[1]))
        result = self.run_recipe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "HTTP 200\nExpected read-only health response received\n")
        self.assertEqual(self.api.requests, [True])
        self.assertEqual(len(self.proxy.connections), 1, "NO_PROXY must not bypass the proxy")

    def test_invalid_token_rejected_and_missing_token_server_rejects(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.api.rejection_status = status
                result = self.run_recipe(token=secrets.token_urlsafe(24))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("HTTP {}".format(status), result.stdout)
                self.assertNotIn("private-rejection-detail", result.stdout + result.stderr)
        # The recipe refuses an empty credential before curl. Independently
        # verify the fixture's missing-header case without injecting any token.
        context = ssl.create_default_context(cafile=str(self.cert))
        import http.client
        connection = http.client.HTTPSConnection("127.0.0.1", self.api.server_port, context=context, timeout=5)
        self.addCleanup(connection.close)
        self.api.rejection_status = 401
        connection.request("GET", "/health")
        response = connection.getresponse()
        self.assertEqual(response.status, 401)
        response.read()

    def test_untrusted_tls_is_rejected_without_authentication(self):
        result = self.run_recipe(trust=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("curl failed (exit 60)", result.stderr)
        self.assertEqual(self.api.requests, [])
        self.assertEqual(len(self.proxy.connections), 1)

    def test_unexpected_private_response_never_printed(self):
        self.api.body = json.dumps({"status": "ok", "private": "never-print-this-record"}).encode()
        result = self.run_recipe()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected health response; body omitted", result.stderr)
        self.assertNotIn("never-print-this-record", result.stdout + result.stderr)

    def test_closed_proxy_fails_bounded_without_direct_fallback(self):
        # Keep a bound socket without a listener so another process cannot take
        # the selected port between discovery and the curl request.
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            result = self.run_recipe(proxy_port=closed.getsockname()[1])
        self.assertNotEqual(result.returncode, 0)
        # A bound-but-unlistening loopback port is refused on some kernels and
        # reaches the explicit connect timeout on others (including macOS).
        self.assertRegex(result.stderr, r"curl failed \(exit (7|28)\)")
        self.assertEqual(self.api.requests, [], "a proxy failure must not fall back to direct HTTPS")


if __name__ == "__main__":
    unittest.main()
