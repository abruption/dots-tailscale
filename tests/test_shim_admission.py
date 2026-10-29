"""Finite admission and cleanup, with isolated sockets and controlled failures."""
import contextlib
import io
import socket
import threading
import unittest
from unittest import mock

import shim
from test_shim_payload import IdentityTLS, ScriptedSocket, decode_client_frames
from test_shim_transport import exact, head
from ws_test_helpers import valid_response


WAIT = 3


class SocketTLS:
    """Keep real socket concurrency while making TLS itself an explicit fixture."""
    def wrap_socket(self, sock, **kwargs):
        class Wrapped:
            def __getattr__(self, name):
                return getattr(sock, name)
            def do_handshake(self):
                pass
        return Wrapped()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.sockets = []
        self.started = []
        self.attempted = []
        self.peak_threads = 0

    def tearDown(self):
        shim.close(*self.sockets)
        for worker in self.started:
            worker.join(WAIT)
            self.assertFalse(worker.is_alive(), "admitted worker leaked")

    def pair(self):
        pair = socket.socketpair()
        for sock in pair:
            sock.settimeout(WAIT)
            self.sockets.append(sock)
        return pair

    @contextlib.contextmanager
    def track_threads(self, fail_at=None, fail_after_start=False):
        real_start = threading.Thread.start

        def start(worker):
            self.attempted.append(worker)
            attempt = len(self.attempted)
            if attempt == fail_at and not fail_after_start:
                raise RuntimeError("controlled thread startup failure")
            real_start(worker)
            self.started.append(worker)
            self.peak_threads = max(self.peak_threads, sum(t.is_alive() for t in self.started))
            if attempt == fail_at:
                raise RuntimeError("controlled interruption after startup")

        with mock.patch.object(threading.Thread, "start", start), \
                contextlib.redirect_stderr(io.StringIO()):
            yield

    def joined(self):
        # The handler joins all of its own children before giving back its slot.
        for worker in list(self.started):
            worker.join(WAIT)
            self.assertFalse(worker.is_alive(), "connection did not clean up")

    def eof(self, peer):
        self.assertEqual(peer.recv(1), b"")

    def reusable(self, admission):
        peer, owned = self.pair()
        peer.shutdown(socket.SHUT_WR)
        self.assertTrue(admission.dispatch(owned), "connection slot was not returned")
        self.joined()
        self.eof(peer)
        self.assertEqual(owned.fileno(), -1)

    def test_limit_validation_before_listening(self):
        with mock.patch.dict(shim.os.environ, {}, clear=True):
            self.assertEqual(shim.connection_limit(), 64)
        for value in ("1", "64", "256"):
            with self.subTest(value=value), mock.patch.dict(shim.os.environ, {"SHIM_MAX_CONNECTIONS": value}):
                self.assertEqual(shim.connection_limit(), int(value))
        for value in ("", "0", "257", "999999", "-1", "+1", "01", " 2", "2.0", "２", "unlimited"):
            with self.subTest(value=value), mock.patch.dict(shim.os.environ, {"SHIM_MAX_CONNECTIONS": value}), \
                    mock.patch.object(shim.socket, "socket") as listener:
                with self.assertRaises(ValueError):
                    shim.main()
                listener.assert_not_called()
        for value in (0, -1, 257, True, 1.0, "2"):
            with self.subTest(constructor=value), self.assertRaises(ValueError):
                shim.ConnectionAdmission(value)

    def test_capacity_rejects_without_threads_and_recovers_after_eof(self):
        admission = shim.ConnectionAdmission(2)
        opened = threading.Barrier(3, timeout=WAIT)
        release = threading.Event()
        self.addCleanup(release.set)

        def held(conn):
            opened.wait()
            if not release.wait(WAIT):
                raise AssertionError("test did not release handler")

        with self.track_threads(), mock.patch.object(shim, "handle", side_effect=held):
            accepted = [self.pair() for _ in range(2)]
            for _, conn in accepted:
                self.assertTrue(admission.dispatch(conn))
            opened.wait()
            for _ in range(20):
                peer, conn = self.pair()
                self.assertFalse(admission.dispatch(conn))
                self.eof(peer)
                self.assertEqual(conn.fileno(), -1)
            self.assertEqual(len(self.attempted), 2)
            self.assertEqual(self.peak_threads, 2)
            release.set()
            self.joined()
        for peer, conn in accepted:
            self.eof(peer)
            self.assertEqual(conn.fileno(), -1)
        with self.track_threads():
            self.reusable(admission)

    def test_setup_failures_return_capacity_with_concurrent_handlers(self):
        admission = shim.ConnectionAdmission(3)
        # A barrier makes all three failures overlap, instead of testing a
        # sequential request loop that never fills the admission budget.
        entered = threading.Barrier(4, timeout=WAIT)
        real_read = shim.read_head

        def read(*args, **kwargs):
            entered.wait()
            return real_read(*args, **kwargs)

        with self.track_threads(), mock.patch.object(shim, "read_head", side_effect=read):
            peers = [self.pair() for _ in range(3)]
            for peer, conn in peers:
                peer.shutdown(socket.SHUT_WR)
                self.assertTrue(admission.dispatch(conn))
            entered.wait()
            self.joined()
            self.assertEqual(self.peak_threads, 3)
        for peer, conn in peers:
            self.eof(peer)
            self.assertEqual(conn.fileno(), -1)
        with self.track_threads():
            self.reusable(admission)

    def test_handler_constructor_failure_closes_and_returns_slot(self):
        admission = shim.ConnectionAdmission(1)
        peer, conn = self.pair()
        with mock.patch.object(shim.threading, "Thread", side_effect=RuntimeError("no handler")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(admission.dispatch(conn))
        self.eof(peer)
        self.assertEqual(conn.fileno(), -1)
        with self.track_threads():
            self.reusable(admission)

    def test_handler_start_failure_closes_and_returns_slot(self):
        admission = shim.ConnectionAdmission(1)
        peer, conn = self.pair()
        with self.track_threads(fail_at=1):
            self.assertFalse(admission.dispatch(conn))
            self.assertEqual(self.started, [])
            self.eof(peer)
            self.assertEqual(conn.fileno(), -1)
            self.reusable(admission)

    def test_interrupted_handler_start_does_not_double_release(self):
        admission = shim.ConnectionAdmission(1)
        peer, conn = self.pair()
        peer.shutdown(socket.SHUT_WR)
        # Even if startup raises after starting the real thread, dispatch must
        # wait for its cleanup and share one release with the handler's finally.
        with self.track_threads(fail_at=1, fail_after_start=True):
            self.assertFalse(admission.dispatch(conn))
            self.eof(peer)
            self.joined()
            self.reusable(admission)

    def test_raw_stream_outlives_setup_deadline_and_excess_does_not_kill_it(self):
        admission = shim.ConnectionAdmission(1)
        peer, conn = self.pair()
        server, upstream = self.pair()
        budget = 0.05
        with self.track_threads(), mock.patch.object(shim, "SETUP_TIMEOUT", budget), \
                mock.patch.object(shim, "upstream_connect", return_value=(upstream, b"")):
            self.assertTrue(admission.dispatch(conn))
            peer.sendall(b"CONNECT example.test:22 HTTP/1.1\r\n\r\ninitial")
            self.assertIn(b"200", head(peer))
            self.assertEqual(exact(server, 7), b"initial")
            extra_peer, extra_conn = self.pair()
            self.assertFalse(admission.dispatch(extra_conn))
            self.eof(extra_peer)
            # Deliberately cross the short fixture setup deadline; this is a
            # stream-lifetime assertion, not synchronization by arbitrary sleep.
            self.assertFalse(threading.Event().wait(budget * 3))
            peer.sendall(b"still-alive")
            self.assertEqual(exact(server, 11), b"still-alive")
            server.sendall(b"reply")
            self.assertEqual(exact(peer, 5), b"reply")
            self.assertIsNone(conn.gettimeout())
            self.assertIsNone(upstream.gettimeout())
            self.assertEqual(len(self.started), 2)
            self.assertLessEqual(self.peak_threads, 2)
            peer.shutdown(socket.SHUT_WR)
            self.joined()
            self.eof(server)
        with self.track_threads():
            self.reusable(admission)

    def test_upload_worker_start_failure_returns_slot_and_closes_upstream(self):
        admission = shim.ConnectionAdmission(1)
        peer, conn = self.pair()
        server, upstream = self.pair()
        peer.sendall(b"CONNECT example.test:22 HTTP/1.1\r\n\r\n")
        with self.track_threads(fail_at=2), \
                mock.patch.object(shim, "upstream_connect", return_value=(upstream, b"")):
            self.assertTrue(admission.dispatch(conn))
            self.joined()
            self.assertEqual(upstream.fileno(), -1)
        self.assertIn(b"200", head(peer))
        self.eof(peer)
        self.eof(server)
        with self.track_threads():
            self.reusable(admission)

    def test_response_timer_start_failure_joins_already_running_upload_worker(self):
        admission = shim.ConnectionAdmission(1)
        conn = ScriptedSocket([
            b"CONNECT example.test:443 HTTP/1.1\r\n\r\n",
            b"POST / HTTP/1.1\r\nHost: example.test\r\n\r\n",
        ], wait_at_eof=True)
        upstream = ScriptedSocket([], wait_at_eof=True)
        with self.track_threads(fail_at=3), \
                mock.patch.object(shim, "upstream_connect", return_value=(upstream, b"")), \
                mock.patch.object(shim, "leaf", return_value=IdentityTLS()), \
                mock.patch.object(shim, "up_ctx", IdentityTLS()):
            self.assertTrue(admission.dispatch(conn))
            self.joined()
            self.assertEqual(len(self.attempted), 3)
            self.assertEqual(len(self.started), 2)
            self.assertTrue(conn.closed.is_set())
            self.assertTrue(upstream.closed.is_set())
        with self.track_threads():
            self.reusable(admission)

    def test_websocket_worker_start_failure_returns_slot(self):
        admission = shim.ConnectionAdmission(1)
        conn = ScriptedSocket([
            b"CONNECT example.test:443 HTTP/1.1\r\n\r\n",
            b"GET /derp HTTP/1.1\r\nHost: example.test\r\nUpgrade: DERP\r\n\r\n",
        ])
        upstream = ScriptedSocket([valid_response], wait_at_eof=True)
        with self.track_threads(fail_at=2), \
                mock.patch.object(shim, "upstream_connect", return_value=(upstream, b"")), \
                mock.patch.object(shim, "leaf", return_value=IdentityTLS()), \
                mock.patch.object(shim, "up_ctx", IdentityTLS()):
            self.assertTrue(admission.dispatch(conn))
            self.joined()
            self.assertEqual(len(self.attempted), 2)
            self.assertTrue(conn.closed.is_set())
            self.assertTrue(upstream.closed.is_set())
        with self.track_threads():
            self.reusable(admission)

    def test_started_upload_worker_keeps_slot_until_its_join_finishes(self):
        admission = shim.ConnectionAdmission(1)
        peer, conn = self.pair()
        server, upstream = self.pair()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def held_pipe(*args):
            entered.set()
            self.assertTrue(release.wait(WAIT))
        peer.sendall(b"CONNECT example.test:22 HTTP/1.1\r\n\r\n")
        with self.track_threads(fail_at=2, fail_after_start=True), \
                mock.patch.object(shim, "pipe", side_effect=held_pipe), \
                mock.patch.object(shim, "upstream_connect", return_value=(upstream, b"")):
            self.assertTrue(admission.dispatch(conn))
            self.assertTrue(entered.wait(WAIT))
            # Shutdown cannot end this controlled worker; only its release can.
            extra_peer, extra = self.pair()
            self.assertFalse(admission.dispatch(extra))
            self.eof(extra_peer)
            self.assertEqual(len(self.attempted), 2)
            release.set()
            self.joined()
        with self.track_threads():
            self.reusable(admission)

    def test_started_websocket_worker_is_joined_by_its_handler(self):
        admission = shim.ConnectionAdmission(1)
        conn = ScriptedSocket([b"CONNECT example.test:443 HTTP/1.1\r\n\r\n",
                               b"GET /derp HTTP/1.1\r\nHost: example.test\r\nUpgrade: DERP\r\n\r\n"])
        upstream = ScriptedSocket([valid_response], wait_at_eof=True)
        original_join = threading.Thread.join
        joins = []
        def join(worker, *args, **kwargs):
            joins.append((worker, threading.current_thread()))
            return original_join(worker, *args, **kwargs)
        with self.track_threads(fail_at=2, fail_after_start=True), \
                mock.patch.object(shim.threading.Thread, "join", join), \
                mock.patch.object(shim, "upstream_connect", return_value=(upstream, b"")), \
                mock.patch.object(shim, "leaf", return_value=IdentityTLS()), \
                mock.patch.object(shim, "up_ctx", IdentityTLS()):
            self.assertTrue(admission.dispatch(conn))
            self.joined()
            self.assertIn((self.started[1], self.started[0]), joins)
        with self.track_threads():
            self.reusable(admission)

    def test_started_response_timer_interruption_is_cancelled_and_joined(self):
        admission = shim.ConnectionAdmission(1)
        conn = ScriptedSocket([b"CONNECT example.test:443 HTTP/1.1\r\n\r\n",
                               b"POST / HTTP/1.1\r\nHost: example.test\r\n\r\n"], wait_at_eof=True)
        upstream = ScriptedSocket([], wait_at_eof=True)
        with self.track_threads(fail_at=3, fail_after_start=True), \
                mock.patch.object(shim, "upstream_connect", return_value=(upstream, b"")), \
                mock.patch.object(shim, "leaf", return_value=IdentityTLS()), \
                mock.patch.object(shim, "up_ctx", IdentityTLS()):
            self.assertTrue(admission.dispatch(conn))
            self.joined()
            self.assertEqual(len(self.started), 3)
            self.assertFalse(any(worker.is_alive() for worker in self.started))
        with self.track_threads():
            self.reusable(admission)

    def test_two_normal_websocket_bridges_stay_bounded_and_outlive_setup(self):
        admission = shim.ConnectionAdmission(2)
        clients = [self.pair() for _ in range(2)]
        servers = [self.pair() for _ in range(2)]
        budget = 0.2
        with self.track_threads(), mock.patch.object(shim, "SETUP_TIMEOUT", budget), \
                mock.patch.object(shim, "upstream_connect", side_effect=[(u, b"") for _, u in servers]), \
                mock.patch.object(shim, "leaf", return_value=SocketTLS()), \
                mock.patch.object(shim, "up_ctx", SocketTLS()):
            for (peer, conn), (server, upstream) in zip(clients, servers):
                self.assertTrue(admission.dispatch(conn))
                peer.sendall(b"CONNECT example.test:443 HTTP/1.1\r\n\r\n")
                self.assertIn(b"200", head(peer))
                peer.sendall(b"GET /derp HTTP/1.1\r\nHost: example.test\r\nUpgrade: DERP\r\n\r\n")
                request = head(server)
                server.sendall(valid_response(request))
                self.assertIn(b"101", head(peer))
                server.sendall(b"\x82\x01x")
                self.assertEqual(exact(peer, 1), b"x")
                peer.sendall(b"y")
                self.assertEqual(decode_client_frames(exact(server, 7)), [b"y"])
            extra_peer, extra = self.pair()
            self.assertFalse(admission.dispatch(extra))
            self.eof(extra_peer)
            self.assertEqual(len(self.started), 4)
            self.assertLessEqual(self.peak_threads, 4)
            # An intentional lifetime check, after both handshakes completed.
            self.assertFalse(threading.Event().wait(budget * 2))
            for (peer, conn), (server, upstream) in zip(clients, servers):
                server.sendall(b"\x82\x01z")
                self.assertEqual(exact(peer, 1), b"z")
                self.assertIsNone(conn.gettimeout())
                self.assertIsNone(upstream.gettimeout())
                peer.shutdown(socket.SHUT_WR)
            self.joined()
        with self.track_threads():
            self.reusable(admission)


if __name__ == "__main__":
    unittest.main()
