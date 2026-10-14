"""Isolated socket peers exercise setup timeouts, TLS and ownership cleanup."""
import contextlib
import io
import pathlib
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import shim
from test_shim_transport import exact, head

BUDGET = 0.2
WAIT = 3


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        root = pathlib.Path(cls.directory.name)
        cert, key = root / "cert.pem", root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec",
                        "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=example.test"], check=True, capture_output=True)
        cls.server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_ctx.load_cert_chain(cert, key)
        cls.client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cls.client_ctx.check_hostname = False
        cls.client_ctx.verify_mode = ssl.CERT_NONE

    def pair(self):
        left, right = socket.socketpair()
        for sock in (left, right):
            sock.settimeout(WAIT)
            self.addCleanup(sock.close)
        return left, right

    @contextlib.contextmanager
    def worker(self, call):
        errors = []
        def run():
            try:
                call()
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=run, daemon=True)
        with mock.patch.object(shim, "SETUP_TIMEOUT", BUDGET), contextlib.redirect_stderr(io.StringIO()):
            worker.start()
            try:
                yield worker, errors
            finally:
                worker.join(WAIT)
                self.assertFalse(worker.is_alive(), "setup worker leaked")

    def ended(self, worker, *owned):
        worker.join(WAIT)
        self.assertFalse(worker.is_alive(), "setup exceeded bounded deadline")
        for sock in owned:
            self.assertEqual(sock.fileno(), -1, "owned socket was not closed")

    def eof(self, sock):
        # TLS peers may have a pending ClientHello or alert before EOF.
        try:
            while sock.recv(4096):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass

    def test_initial_connect_header_timeout_closes_client(self):
        client, owned = self.pair()
        with self.worker(lambda: shim.handle(owned)) as (worker, errors):
            self.ended(worker, owned)
            self.eof(client)
            self.assertEqual(errors, [])

    def test_upstream_connect_stall_closes_both_sockets(self):
        client, owned = self.pair()
        server, upstream = self.pair()
        with mock.patch.object(shim.socket, "create_connection", return_value=upstream):
            with self.worker(lambda: shim.handle(owned)) as (worker, errors):
                client.sendall(b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")
                self.assertIn(b"CONNECT example.test:443", head(server))
                self.ended(worker, owned, upstream)
                self.eof(client)
                self.eof(server)
                self.assertEqual(errors, [])

    def test_upstream_connect_failure_closes_allocated_socket(self):
        for response in (b"HTTP/1.1 403 Forbidden\r\n\r\n", b"HTTP/1.1 200"):
            with self.subTest(response=response):
                server, owned = self.pair()
                server.sendall(response)
                server.shutdown(socket.SHUT_WR)
                with mock.patch.object(shim.socket, "create_connection", return_value=owned):
                    with self.assertRaises(OSError):
                        shim.upstream_connect("example.test:443")
                self.assertEqual(owned.fileno(), -1)

    def test_client_tls_stall_closes_both_sockets(self):
        client, owned = self.pair()
        server, upstream = self.pair()
        with mock.patch.object(shim, "upstream_connect", return_value=upstream), \
                mock.patch.object(shim, "leaf", return_value=self.server_ctx):
            with self.worker(lambda: shim.handle(owned)) as (worker, errors):
                client.sendall(b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")
                self.assertIn(b"200", head(client))
                self.ended(worker, owned, upstream)
                self.eof(client)
                self.eof(server)
                self.assertEqual(errors, [])

    def test_upstream_tls_stall_closes_both_sockets(self):
        client, owned = self.pair()
        server, upstream = self.pair()
        with mock.patch.object(shim, "upstream_connect", return_value=upstream), \
                mock.patch.object(shim, "leaf", return_value=self.server_ctx), \
                mock.patch.object(shim, "up_ctx", self.client_ctx):
            with self.worker(lambda: shim.handle(owned)) as (worker, errors):
                client.sendall(b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")
                head(client)
                with self.client_ctx.wrap_socket(client, server_hostname="example.test") as tls:
                    self.ended(worker, owned, upstream)
                    self.eof(tls)
                self.eof(server)
                self.assertEqual(errors, [])

    def test_upgrade_stall_and_early_client_eof_close_both_sockets(self):
        for disconnect in (False, True):
            with self.subTest(disconnect=disconnect):
                client, owned = self.pair()
                server, upstream = self.pair()
                request = "GET /derp HTTP/1.1\r\nUpgrade: DERP"
                with self.worker(lambda: shim.ws_bridge(owned, upstream, request, "example.test")) as (worker, errors):
                    head(server)
                    if disconnect:
                        client.shutdown(socket.SHUT_WR)
                    self.ended(worker, owned, upstream)
                    self.eof(client)
                    self.eof(server)
                    self.assertEqual(len(errors), 1)
                    self.assertIsInstance(errors[0], (TimeoutError, socket.timeout))

    def test_slow_trickle_cannot_extend_absolute_header_deadline(self):
        client, owned = self.pair()
        stop = threading.Event()
        def trickle():
            while not stop.wait(0.03):
                try:
                    client.sendall(b"x")
                except OSError:
                    return
        sender = threading.Thread(target=trickle, daemon=True)
        sender.start()
        try:
            started = time.monotonic()
            with mock.patch.object(shim, "SETUP_TIMEOUT", BUDGET):
                with self.assertRaises((TimeoutError, socket.timeout)):
                    shim.read_head(owned)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            stop.set()
            sender.join(WAIT)
            self.assertFalse(sender.is_alive())

    def test_header_size_bound_and_coalesced_payload(self):
        for packet in (b"x" * 33, b"x" * 29 + b"\r\n\r\n"):
            client, owned = self.pair()
            client.sendall(packet)
            with mock.patch.object(shim, "MAX_HEADER_BYTES", 32):
                with self.assertRaisesRegex(OSError, "headers too large"):
                    shim.read_head(owned)
        client, owned = self.pair()
        client.sendall(b"H\r\n\r\n" + b"x" * 100)
        with mock.patch.object(shim, "MAX_HEADER_BYTES", 32):
            parsed, rest = shim.read_head(owned)
        self.assertEqual(parsed, "H")
        self.assertEqual(rest, b"x" * 100)

    def test_tls_http_header_stalls_and_body_forwarding(self):
        # Real socket IO with TLS isolated to the separately tested handshakes.
        class IdentitySocket:
            def __init__(self, sock):
                self.sock = sock
            def do_handshake(self):
                pass
            def __getattr__(self, name):
                return getattr(self.sock, name)
        class IdentityTLS:
            def wrap_socket(self, sock, **kwargs):
                return IdentitySocket(sock)
        for stage in ("client-header", "upstream-header", "interim-stall", "interim-coalesced", "success"):
            with self.subTest(stage=stage):
                client, owned = self.pair()
                server, upstream = self.pair()
                with mock.patch.object(shim, "upstream_connect", return_value=upstream), \
                        mock.patch.object(shim, "leaf", return_value=IdentityTLS()), \
                        mock.patch.object(shim, "up_ctx", IdentityTLS()):
                    with self.worker(lambda: shim.handle(owned)) as (worker, errors):
                        client.sendall(b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")
                        head(client)
                        if stage != "client-header":
                            client.sendall(b"POST / HTTP/1.1\r\nContent-Length: 4\r\n\r\n")
                            self.assertIn(b"POST /", head(server))
                        if stage == "interim-stall":
                            server.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
                            self.assertIn(b"100 Continue", head(client))
                        if stage == "interim-coalesced":
                            server.sendall(b"HTTP/1.1 103 Early Hints\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nreply")
                            self.assertIn(b"103 Early Hints", head(client))
                            self.assertIn(b"200 OK", head(client))
                            self.assertEqual(exact(client, 5), b"reply")
                            server.shutdown(socket.SHUT_WR)
                        if stage == "success":
                            # Interim response must not leave the concurrent
                            # upload reader with a timeout after establishment.
                            server.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
                            self.assertIn(b"100 Continue", head(client))
                            client.sendall(b"body")
                            self.assertEqual(exact(server, 4), b"body")
                            time.sleep(0.03)
                            server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n")
                            self.assertIn(b"200 OK", head(client))
                            time.sleep(BUDGET * 2)
                            self.assertTrue(worker.is_alive())
                            server.sendall(b"reply")
                            self.assertEqual(exact(client, 5), b"reply")
                            server.shutdown(socket.SHUT_WR)
                        self.ended(worker, owned, upstream)
                        self.eof(client)
                        self.eof(server)
                        self.assertEqual(errors, [])

    def test_bridge_thread_start_failure_still_closes_owned_sockets(self):
        client, owned = self.pair()
        server, upstream = self.pair()
        client.sendall(b"CONNECT example.test:22 HTTP/1.1\r\n\r\n")
        with mock.patch.object(shim, "upstream_connect", return_value=upstream), \
                mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("no threads")), \
                contextlib.redirect_stderr(io.StringIO()):
            shim.handle(owned)
        self.assertEqual(owned.fileno(), -1)
        self.assertEqual(upstream.fileno(), -1)
        self.eof(client)
        self.eof(server)

    def test_raw_tunnel_survives_setup_deadline_then_cleans_up(self):
        client, owned = self.pair()
        server, upstream = self.pair()
        with mock.patch.object(shim, "upstream_connect", return_value=upstream):
            with self.worker(lambda: shim.handle(owned)) as (worker, errors):
                client.sendall(b"CONNECT example.test:22 HTTP/1.1\r\n\r\ninitial")
                self.assertIn(b"200", head(client))
                self.assertEqual(exact(server, 7), b"initial")
                time.sleep(BUDGET * 2)
                self.assertTrue(worker.is_alive())
                self.assertIsNone(owned.gettimeout())
                self.assertIsNone(upstream.gettimeout())
                client.sendall(b"alive")
                self.assertEqual(exact(server, 5), b"alive")
                server.sendall(b"reply")
                self.assertEqual(exact(client, 5), b"reply")
                client.shutdown(socket.SHUT_WR)
                self.ended(worker, owned, upstream)
                self.eof(server)
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
