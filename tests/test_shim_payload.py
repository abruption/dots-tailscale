"""Regression tests for payload already consumed while reading Upgrade headers.

TLS and sockets are controlled in memory so packet boundaries are deterministic.
The upstream reader remains blocked until the bridge shuts down; it cannot race
with the client writer by delivering an artificial early EOF.
"""
import collections
import contextlib
import io
import threading
import unittest
from unittest import mock

import shim
from ws_test_helpers import valid_response


class ScriptedSocket:
    def __init__(self, chunks, wait_at_eof=False):
        self.chunks = collections.deque(chunks)
        self.sent = []
        self.wait_at_eof = wait_at_eof
        self.closed = threading.Event()

    def settimeout(self, value):
        self.timeout = value

    def do_handshake(self):
        pass

    def recv(self, size):
        if self.chunks:
            chunk = self.chunks.popleft()
            if callable(chunk):
                chunk = chunk(b"".join(self.sent))
            if len(chunk) > size:
                self.chunks.appendleft(chunk[size:])
            return chunk[:size]
        if self.wait_at_eof and not self.closed.wait(2):
            raise AssertionError("bridge did not shut down the upstream reader")
        return b""

    def sendall(self, data):
        if self.closed.is_set():
            raise OSError("socket is closed")
        self.sent.append(bytes(data))

    def shutdown(self, how):
        self.closed.set()

    def close(self):
        self.closed.set()


class IdentityTLS:
    def wrap_socket(self, sock, **kwargs):
        return sock


def decode_client_frames(data):
    """Decode independently of shim.ws_frame, including the masking key."""
    frames = []
    while data:
        if len(data) < 2:
            raise AssertionError("truncated frame header")
        first, second = data[:2]
        data = data[2:]
        if first != 0x82 or not second & 0x80:
            raise AssertionError("expected a final, masked binary frame")
        size = second & 0x7f
        if size in (126, 127):
            width = 2 if size == 126 else 8
            if len(data) < width:
                raise AssertionError("truncated extended length")
            size = int.from_bytes(data[:width], "big")
            data = data[width:]
        if len(data) < 4 + size:
            raise AssertionError("truncated masked payload")
        mask, payload = data[:4], data[4:4 + size]
        frames.append(bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))
        data = data[4 + size:]
    return frames


class UpgradePayloadTests(unittest.TestCase):
    def test_raw_connect_response_preserves_tunnel_bytes_through_handle(self):
        banner = b"SSH-2.0-example-server\r\n"
        response = b"HTTP/1.1 200 Connection established\r\n\r\n"
        for prefix in (0, 5, len(banner)):
            with self.subTest(coalesced_bytes=prefix):
                client = ScriptedSocket([
                    b"CONNECT example.test:22 HTTP/1.1\r\n\r\nclient-initial"
                ], wait_at_eof=True)
                upstream = ScriptedSocket([
                    response + banner[:prefix], banner[prefix:] + b"later\x00data"
                ])
                with mock.patch.object(shim.socket, "create_connection", return_value=upstream), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    shim.handle(client)
                self.assertEqual(errors.getvalue(), "")
                self.assertEqual(b"".join(client.sent), response + banner + b"later\x00data")
                self.assertEqual(b"".join(upstream.sent),
                    b"CONNECT example.test:22 HTTP/1.1\r\nHost: example.test:22\r\n\r\nclient-initial")
                self.assertTrue(client.closed.is_set())
                self.assertTrue(upstream.closed.is_set())

    def test_tls_connect_unexpected_prebuffer_fails_before_tls_or_success(self):
        client = ScriptedSocket([b"CONNECT example.test:443 HTTP/1.1\r\n\r\n"])
        upstream = ScriptedSocket([
            b"HTTP/1.1 200 Connection established\r\n\r\nunexpected"
        ])
        with mock.patch.object(shim.socket, "create_connection", return_value=upstream), \
                mock.patch.object(shim, "leaf") as leaf, \
                mock.patch.object(shim, "up_ctx") as upstream_tls, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            shim.handle(client)
        self.assertIn("unexpected tunnel data before upstream TLS handshake", errors.getvalue())
        self.assertEqual(client.sent, [])
        leaf.assert_not_called()
        upstream_tls.wrap_socket.assert_not_called()
        self.assertTrue(client.closed.is_set())
        self.assertTrue(upstream.closed.is_set())

    def run_bridge(self, upgrade, chunks, response=None, fail_initial_send=False):
        client = ScriptedSocket([
            b"CONNECT example.test:443 HTTP/1.1\r\nHost: example.test\r\n\r\n",
            *chunks,
        ])
        upstream = ScriptedSocket([
            response or valid_response
        ], wait_at_eof=True)
        if fail_initial_send:
            sendall = upstream.sendall
            failed = []

            def fail_binary_send(data):
                if data.startswith(b"\x82"):
                    failed.append(True)
                    raise OSError("simulated initial send failure")
                sendall(data)

            upstream.sendall = fail_binary_send
        threads = []
        real_thread = threading.Thread

        def make_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            threads.append(thread)
            return thread

        with mock.patch.object(shim, "upstream_connect", return_value=(upstream, b"")), \
                mock.patch.object(shim, "leaf", return_value=IdentityTLS()), \
                mock.patch.object(shim, "up_ctx", IdentityTLS()), \
                mock.patch.object(shim.threading, "Thread", side_effect=make_thread), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            try:
                shim.handle(client)
                if fail_initial_send:
                    self.assertTrue(failed, "initial binary send was not attempted")
                    self.assertTrue(client.closed.is_set(), "client was not shut down")
                    self.assertTrue(upstream.closed.is_set(), "upstream was not shut down")
            finally:
                client.close()
                upstream.close()
                for thread in threads:
                    thread.join(2)
                    self.assertFalse(thread.is_alive(), "bridge reader leaked")
        request, frames = b"".join(upstream.sent).split(b"\r\n\r\n", 1)
        expected_protocol = b"tailscale-control-protocol" if upgrade == "control" else b"derp"
        self.assertIn(b"Sec-WebSocket-Protocol: " + expected_protocol, request)
        return decode_client_frames(frames), b"".join(client.sent), stderr.getvalue()

    @staticmethod
    def header(upgrade):
        if upgrade == "control":
            return (b"POST /ts2021 HTTP/1.1\r\nHost: example.test\r\n"
                    b"Upgrade: tailscale-control-protocol\r\n"
                    b"X-Tailscale-Handshake: test-handshake\r\n\r\n")
        return (b"GET /derp HTTP/1.1\r\nHost: example.test\r\n"
                b"Upgrade: DERP\r\nDerp-Fast-Start: 1\r\n\r\n")

    def test_header_and_first_payload_in_same_recv(self):
        payload = bytes(range(256))
        for upgrade in ("control", "derp"):
            with self.subTest(upgrade=upgrade):
                frames, client_output, errors = self.run_bridge(
                    upgrade, [self.header(upgrade) + payload])
                self.assertEqual(b"".join(frames), payload)
                self.assertTrue(all(frames))
                self.assertEqual(errors, "")
                self.assertEqual(b"101 Switching Protocols" in client_output, upgrade == "control")

    def test_header_only_then_later_payload(self):
        for upgrade in ("control", "derp"):
            with self.subTest(upgrade=upgrade):
                frames, _, errors = self.run_bridge(
                    upgrade, [self.header(upgrade), b"later-payload"])
                self.assertEqual(b"".join(frames), b"later-payload")
                self.assertTrue(all(frames))
                self.assertEqual(errors, "")

    def test_split_header_delimiter_and_payload(self):
        for upgrade in ("control", "derp"):
            with self.subTest(upgrade=upgrade):
                header = self.header(upgrade)
                frames, _, errors = self.run_bridge(
                    upgrade, [header[:9], header[9:-1], header[-1:] + b"first", b"second"])
                self.assertEqual(b"".join(frames), b"firstsecond")
                self.assertTrue(all(frames))
                self.assertEqual(errors, "")

    def test_initial_then_subsequent_bytes_are_sent_once_in_order(self):
        for upgrade in ("control", "derp"):
            with self.subTest(upgrade=upgrade):
                frames, _, errors = self.run_bridge(
                    upgrade, [self.header(upgrade) + b"initial\x00", b"next\xff", b"last"])
                self.assertEqual(b"".join(frames), b"initial\x00next\xfflast")
                self.assertTrue(all(frames))
                self.assertEqual(errors, "")

    def test_initial_send_failure_shuts_down_both_endpoints(self):
        frames, _, errors = self.run_bridge(
            "control", [self.header("control") + b"initial"], fail_initial_send=True)
        self.assertEqual(frames, [])
        self.assertEqual(errors, "")

    def test_rejected_upgrade_never_sends_initial_payload(self):
        rejection = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 6\r\n\r\ndenied"
        for upgrade in ("control", "derp"):
            with self.subTest(upgrade=upgrade):
                frames, client_output, errors = self.run_bridge(
                    upgrade, [self.header(upgrade) + b"must-not-send", b"later"], rejection)
                self.assertEqual(frames, [])
                self.assertTrue(client_output.endswith(rejection))
                self.assertIn("ws upgrade refused", errors)


if __name__ == "__main__":
    unittest.main()
