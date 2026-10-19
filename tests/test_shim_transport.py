"""Offline transport tests with real socket pairs and bounded worker cleanup."""
import contextlib
import importlib.util
import pathlib
import socket
import threading
import time
import unittest
from unittest import mock

import shim
from ws_test_helpers import valid_response

TIMEOUT = 3


def exact(sock, count):
    data = b""
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise EOFError("truncated stream")
        data += part
    return data


def head(sock):
    data = b""
    while not data.endswith(b"\r\n\r\n"):
        data += exact(sock, 1)
    return data


def server_frame(op, payload):
    size = len(payload)
    if size < 126:
        length = bytes([size])
    elif size <= 65535:
        length = b"\x7e" + size.to_bytes(2, "big")
    else:
        length = b"\x7f" + size.to_bytes(8, "big")
    return bytes([0x80 | op]) + length + payload


def client_frame(sock):
    first, second = exact(sock, 2)
    assert first & 0x80, "fragmented client frame"
    assert second & 0x80, "client frame must be masked"
    size = second & 0x7f
    if size in (126, 127):
        size = int.from_bytes(exact(sock, 2 if size == 126 else 8), "big")
    mask = exact(sock, 4)
    data = exact(sock, size)
    return first & 0x0f, bytes(value ^ mask[i % 4] for i, value in enumerate(data))


class LimitedRecv:
    """Force partial reads while preserving real socket blocking/EOF semantics."""
    def __init__(self, sock, limit):
        self.sock, self.limit = sock, limit

    def recv(self, size):
        return self.sock.recv(min(size, self.limit))

    def __getattr__(self, name):
        return getattr(self.sock, name)


class FirstPacketRecv:
    """Read one known packet from a real socket, independent of OS coalescing."""
    def __init__(self, sock, size):
        self.sock, self.size = sock, size

    def recv(self, count):
        if self.size is not None:
            size, self.size = self.size, None
            if size > count:
                raise AssertionError("test packet exceeds requested read size")
            return exact(self.sock, size)
        return self.sock.recv(count)

    def __getattr__(self, name):
        return getattr(self.sock, name)


class TransportTests(unittest.TestCase):
    @contextlib.contextmanager
    def sockets(self):
        left, right = socket.socketpair()
        for sock in (left, right):
            sock.settimeout(TIMEOUT)
        try:
            yield left, right
        finally:
            left.close()
            right.close()

    @contextlib.contextmanager
    def bridge(self, response=None, first_frame=b"", split=False, rejected=False,
               initial_data=None, queued_data=b"", upgrade="DERP", fast_start=False):
        with self.sockets() as (client, bridge_client), self.sockets() as (server, bridge_server):
            # Production bridge sockets block: peer-side deadlines must not
            # accidentally unblock a broken bridge through its own recv timeout.
            bridge_client.settimeout(None)
            bridge_server.settimeout(None)
            threads, errors = [], []
            real_thread = threading.Thread

            def track_thread(*args, **kwargs):
                target = kwargs.pop("target")

                def guarded():
                    try:
                        target()
                    except BaseException as error:
                        errors.append(error)

                worker = real_thread(*args, target=guarded, **kwargs)
                threads.append(worker)
                return worker

            request = f"GET /derp HTTP/1.1\r\nHost: example.test\r\nUpgrade: {upgrade}"
            if fast_start:
                request += "\r\nDerp-Fast-Start: 1"
            upstream = LimitedRecv(bridge_server, 1) if split else bridge_server
            if initial_data is not None:
                packet = request.encode() + b"\r\n\r\n" + initial_data
                client.sendall(packet)
                client.sendall(queued_data)

            def run_bridge():
                parsed, prefetched = request, b""
                if initial_data is not None:
                    parsed, prefetched = shim.read_head(FirstPacketRecv(bridge_client, len(packet)))
                shim.ws_bridge(bridge_client, upstream, parsed, "example.test", prefetched)

            with mock.patch.object(shim.threading, "Thread", side_effect=track_thread):
                worker = track_thread(target=run_bridge, daemon=True)
                worker.start()
                try:
                    request_head = head(server)
                    self.assertIn(b"Upgrade: websocket", request_head)
                    reply = response(request_head) if callable(response) else response
                    server.sendall(reply or (valid_response(request_head) + first_frame))
                    if not rejected and not fast_start:
                        self.assertIn(b"101 Switching Protocols", head(client))
                    yield client, server, threads, errors
                finally:
                    for sock in (client, server, bridge_client, bridge_server):
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    for thread in threads:
                        thread.join(TIMEOUT)
                        self.assertFalse(thread.is_alive(), "bridge worker leaked")
            if rejected:
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], OSError)
                self.assertIn("ws upgrade refused", str(errors[0]))
            else:
                self.assertEqual(errors, [])

    def assert_peer_eof(self, peer):
        try:
            data = peer.recv(1)
        except socket.timeout:
            self.fail("bridge did not propagate EOF before the peer deadline")
        self.assertEqual(data, b"")

    def assert_workers_exit(self, threads):
        for thread in threads:
            thread.join(TIMEOUT)
            self.assertFalse(thread.is_alive(), "worker failed to terminate before cleanup")

    def test_read_head_split_delimiter(self):
        with self.sockets() as (reader, writer):
            writer.sendall(b"HTTP/1.1 200 OK\r\nX: y\r\n\r\ntail")
            parsed, rest = shim.read_head(LimitedRecv(reader, 1))
            self.assertEqual(parsed, "HTTP/1.1 200 OK\r\nX: y")
            self.assertEqual(rest, b"")
            self.assertEqual(exact(reader, 4), b"tail")

    def test_read_head_preserves_coalesced_rest(self):
        # Supply a deterministic read boundary without depending on OS coalescing.
        with self.sockets() as (reader, writer):
            packet = b"HTTP/1.1 200 OK\r\n\r\nbinary\x00tail"
            writer.sendall(packet)
            source = mock.Mock()
            source.recv.side_effect = lambda count: exact(reader, len(packet))
            parsed, rest = shim.read_head(source)
            self.assertEqual(parsed, "HTTP/1.1 200 OK")
            self.assertEqual(rest, b"binary\x00tail")

    def test_read_head_eof_before_delimiter(self):
        with self.sockets() as (reader, writer):
            writer.sendall(b"HTTP/1.1 200 OK\r\n")
            writer.shutdown(socket.SHUT_WR)
            with self.assertRaisesRegex(OSError, "closed before headers"):
                shim.read_head(reader)

    def test_recv_exact_preserves_prebuffer_and_extra(self):
        with self.sockets() as (reader, writer):
            buffer = [b"abcdef"]
            self.assertEqual(shim.recv_exact(reader, 2, buffer), b"ab")
            self.assertEqual(buffer, [b"cdef"])
            writer.sendall(b"ghij")
            self.assertEqual(shim.recv_exact(LimitedRecv(reader, 1), 6, buffer), b"cdefgh")
            self.assertEqual(exact(reader, 2), b"ij")

    def test_recv_exact_truncated_stream(self):
        with self.sockets() as (reader, writer):
            writer.sendall(b"ab")
            writer.shutdown(socket.SHUT_WR)
            with self.assertRaisesRegex(OSError, "ws closed"):
                shim.recv_exact(reader, 4, [b"x"])

    def test_ws_frame_masking_and_length_boundaries(self):
        for size in (0, 1, 125, 126, 65535, 65536):
            with self.subTest(size=size):
                payload = bytes(i % 251 for i in range(size))
                mask = b"\x01\x23\x45\x67"
                with mock.patch.object(shim.os, "urandom", return_value=mask):
                    encoded = shim.ws_frame(2, payload)
                width = 0 if size < 126 else 2 if size < 65536 else 8
                self.assertEqual(encoded[0], 0x82)
                self.assertEqual(encoded[1], 0x80 | (size if width == 0 else 126 if width == 2 else 127))
                if width:
                    self.assertEqual(int.from_bytes(encoded[2:2 + width], "big"), size)
                self.assertEqual(encoded[2 + width:6 + width], mask)
                self.assertEqual(len(encoded), 6 + width + size)
                decoded = bytes(byte ^ mask[i % 4] for i, byte in enumerate(encoded[6 + width:]))
                self.assertEqual(decoded, payload)

    def test_bidirectional_data_and_response_coalesced_frame(self):
        with self.bridge(first_frame=server_frame(2, b"welcome\x00")) as (client, server, _, _):
            self.assertEqual(exact(client, 8), b"welcome\x00")
            client.sendall(b"client-data")
            self.assertEqual(client_frame(server), (2, b"client-data"))
            server.sendall(server_frame(2, b"server-data"))
            self.assertEqual(exact(client, 11), b"server-data")

    def test_initial_and_queued_payload_with_server_responses_and_close(self):
        for upgrade, fast_start in (("tailscale-control-protocol", False),
                                    ("DERP", False), ("DERP", True)):
            with self.subTest(upgrade=upgrade, fast_start=fast_start):
                initial = b"initial\x00payload"
                queued = b"later-client-data"
                welcome = b"first-server-response"
                with self.bridge(initial_data=initial, queued_data=queued,
                                 upgrade=upgrade, fast_start=fast_start,
                                 first_frame=server_frame(2, welcome)) as (client, server, threads, _):
                    # Both client writes were queued before the upstream 101.
                    # Do not assume recv boundaries or one write per WS frame.
                    received = b""
                    expected = initial + queued
                    while len(received) < len(expected):
                        try:
                            opcode, payload = client_frame(server)
                        except socket.timeout:
                            self.fail("initial and queued bytes did not arrive before deadline")
                        self.assertEqual(opcode, 2)
                        self.assertTrue(payload, "unexpected empty data frame")
                        received += payload
                    self.assertEqual(received, expected)
                    self.assertEqual(exact(client, len(welcome)), welcome)
                    reply = b"second-server-response"
                    server.sendall(server_frame(2, reply) + server_frame(8, b""))
                    self.assertEqual(exact(client, len(reply)), reply)
                    # Protocol close propagates EOF without relying on a
                    # second SHUT_RDWR after a peer half-close (OS-dependent).
                    # EOF also rejects extra/duplicated data in either direction.
                    self.assert_peer_eof(server)
                    self.assert_peer_eof(client)
                    self.assert_workers_exit(threads)

    def test_split_frames_ping_pong_then_data(self):
        with self.bridge(split=True) as (client, server, _, _):
            server.sendall(server_frame(9, b"alive"))
            self.assertEqual(client_frame(server), (10, b"alive"))
            payload = bytes(range(128))
            server.sendall(server_frame(2, payload))
            self.assertEqual(exact(client, len(payload)), payload)
            client.sendall(b"after-ping")
            self.assertEqual(client_frame(server), (2, b"after-ping"))

    def test_rejected_upgrade_forwards_response_and_body(self):
        response = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 6\r\n\r\ndenied"
        with self.bridge(response=response, rejected=True) as (client, _, threads, _):
            self.assertEqual(exact(client, len(response)), response)
            self.assert_workers_exit(threads)

    def test_client_eof_terminates_both_workers(self):
        with self.bridge() as (client, server, threads, _):
            client.shutdown(socket.SHUT_WR)
            self.assert_peer_eof(server)
            self.assert_workers_exit(threads)

    def test_upstream_eof_terminates_both_workers(self):
        with self.bridge() as (client, server, threads, _):
            server.shutdown(socket.SHUT_WR)
            self.assert_peer_eof(client)
            self.assert_workers_exit(threads)

    def test_close_frame_terminates_both_workers(self):
        with self.bridge() as (client, server, threads, _):
            server.sendall(server_frame(8, b""))
            self.assert_peer_eof(client)
            self.assert_workers_exit(threads)

    def test_invalid_frames_fail_before_payload_or_pong_and_clean_up(self):
        malformed = {
            "masked-server": b"\x82\x83maskABC",
            "rsv1": b"\xc2\x03ABC",
            "rsv2": b"\xa2\x03ABC",
            "rsv3": b"\x92\x03ABC",
            "fragmented-ping": b"\x09\x03ABC",
            "fragmented-close": b"\x08\x00",
            "fragmented-pong": b"\x0a\x00",
            "oversized-control": b"\x89\x7e\x00\x7e" + b"x" * 126,
            "reserved-data-opcode": b"\x83\x03ABC",
            "reserved-control-opcode": b"\x8b\x03ABC",
            "orphan-continuation": b"\x80\x03ABC",
            "nonminimal-16bit": b"\x82\x7e\x00\x03ABC",
            "nonminimal-64bit": b"\x82\x7f" + (3).to_bytes(8, "big") + b"ABC",
            "negative-64bit": b"\x82\x7f" + (1 << 63).to_bytes(8, "big"),
            "one-byte-close": b"\x88\x01x",
        }
        for name, frame in malformed.items():
            for split in (False, True):
                with self.subTest(name=name, split=split):
                    # With no valid frames before the error, both peers must
                    # observe EOF directly: no data, mask bytes, or normal pong.
                    with self.bridge(first_frame=frame, split=split) as (client, server, threads, _):
                        self.assert_peer_eof(client)
                        self.assert_peer_eof(server)
                        self.assert_workers_exit(threads)

    def test_fragmented_data_with_interleaved_control_preserves_stream(self):
        with self.bridge(split=True) as (client, server, threads, _):
            server.sendall(b"\x02\x01A" + server_frame(9, b"alive") +
                           b"\x00\x01B" + server_frame(10, b"ignored") +
                           b"\x80\x01C")
            self.assertEqual(exact(client, 3), b"ABC")
            self.assertEqual(client_frame(server), (10, b"alive"))
            server.sendall(server_frame(2, b"next") + server_frame(8, b""))
            self.assertEqual(exact(client, 4), b"next")
            self.assert_peer_eof(client)
            self.assert_peer_eof(server)
            self.assert_workers_exit(threads)

    def test_new_data_frame_during_fragmented_message_is_rejected(self):
        for opcode in (1, 2):
            with self.subTest(opcode=opcode):
                with self.bridge() as (client, server, threads, _):
                    server.sendall(b"\x02\x01A")
                    self.assertEqual(exact(client, 1), b"A")
                    server.sendall(server_frame(opcode, b"must-not-release"))
                    self.assert_peer_eof(client)
                    self.assert_peer_eof(server)
                    self.assert_workers_exit(threads)

    def test_truncated_frame_terminates_both_workers(self):
        for incomplete in (b"\x82", b"\x82\x7e\x00", b"\x82\x05ab"):
            with self.subTest(incomplete=incomplete):
                with self.bridge() as (client, server, threads, _):
                    server.sendall(incomplete)
                    server.shutdown(socket.SHUT_WR)
                    self.assert_peer_eof(client)
                    self.assert_workers_exit(threads)

    def test_invalid_handshakes_fail_before_payload_or_bridge_worker(self):
        def remove(header):
            def mutate(response):
                return b"\r\n".join(line for line in response.split(b"\r\n")
                                    if not line.lower().startswith(header.lower() + b":"))
            return mutate
        mutations = {
            "missing-accept": remove(b"Sec-WebSocket-Accept"),
            "wrong-accept": lambda r: remove(b"Sec-WebSocket-Accept")(r).replace(
                b"\r\n\r\n", b"\r\nSec-WebSocket-Accept: wrong\r\n\r\n"),
            "duplicate-accept": lambda r: r.replace(b"\r\n\r\n", b"\r\n" +
                next(line for line in r.split(b"\r\n") if line.startswith(b"Sec-WebSocket-Accept:")) + b"\r\n\r\n"),
            "missing-upgrade": remove(b"Upgrade"),
            "wrong-upgrade": lambda r: r.replace(b"Upgrade: websocket", b"Upgrade: h2c"),
            "missing-connection": remove(b"Connection"),
            "non-ascii-whitespace": lambda r: r.replace(b"Connection: Upgrade", b"Connection: \xa0Upgrade"),
            "invalid-connection-token": lambda r: r.replace(b"Connection: Upgrade", b"Connection: Upgrade, bad token"),
            "nul-in-reason": lambda r: r.replace(b"Switching Protocols", b"Switching\x00Protocols"),
            "del-in-reason": lambda r: r.replace(b"Switching Protocols", b"Switching\x7fProtocols"),
            "substring-connection": lambda r: r.replace(b"Connection: Upgrade", b"Connection: notupgrade"),
            "wrong-subprotocol": lambda r: remove(b"Sec-WebSocket-Protocol")(r).replace(
                b"\r\n\r\n", b"\r\nSec-WebSocket-Protocol: unrequested\r\n\r\n"),
            "duplicate-subprotocol": lambda r: r.replace(b"\r\n\r\n",
                b"\r\nSec-WebSocket-Protocol: derp\r\n\r\n"),
            "subprotocol-list": lambda r: r.replace(b"Sec-WebSocket-Protocol: derp",
                b"Sec-WebSocket-Protocol: derp, other"),
            "unrequested-extension": lambda r: r.replace(b"\r\n\r\n",
                b"\r\nSec-WebSocket-Extensions: permessage-deflate\r\n\r\n"),
            "malformed-header": lambda r: r.replace(b"Upgrade: websocket", b"Upgrade websocket"),
            "folded-header": lambda r: r.replace(b"Connection: Upgrade", b"Connection:\r\n Upgrade"),
            "invalid-status": lambda r: r.replace(b"HTTP/1.1 101", b"nonsense 101"),
            "four-digit-status": lambda r: r.replace(b"HTTP/1.1 101", b"HTTP/1.1 1010"),
            "http10-status": lambda r: r.replace(b"HTTP/1.1 101", b"HTTP/1.0 101"),
        }
        for name, mutate in mutations.items():
            for fast_start in (False, True):
                with self.subTest(case=name, fast_start=fast_start):
                    def response(request):
                        return mutate(valid_response(request)) + server_frame(2, b"must-not-release")
                    with self.bridge(response=response, rejected=True, fast_start=fast_start,
                                     initial_data=b"must-not-send") as (client, server, threads, _):
                        self.assert_workers_exit(threads)
                        self.assertEqual(len(threads), 1, "invalid handshake started frame reader")
                        self.assert_peer_eof(client)
                        self.assert_peer_eof(server)

    def test_header_case_token_lists_and_optional_subprotocol(self):
        for selected in (True, False):
            for upgrade in ("DERP", "tailscale-control-protocol"):
                with self.subTest(selected=selected, upgrade=upgrade):
                    def response(request):
                        reply = valid_response(request).replace(b"Upgrade: websocket", b"uPgRaDe: WebSocket")
                        reply = reply.replace(b"Connection: Upgrade", b"connection: keep-alive\r\nCONNECTION: UpGrAdE, custom")
                        reply = reply.replace(b"Sec-WebSocket-Accept: ", b"sEc-WeBsOcKeT-aCcEpT: \t")
                        if not selected:
                            reply = b"\r\n".join(line for line in reply.split(b"\r\n")
                                                   if not line.startswith(b"Sec-WebSocket-Protocol:"))
                        return reply + server_frame(2, b"hello")
                    with self.bridge(response=response, upgrade=upgrade) as (client, server, _, _):
                        self.assertEqual(exact(client, 5), b"hello")
                        client.sendall(b"ready")
                        self.assertEqual(client_frame(server), (2, b"ready"))

    def test_known_rfc6455_accept_vector(self):
        response = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                    "Connection: Upgrade\r\nSec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        shim.validate_ws_response(response, "dGhlIHNhbXBsZSBub25jZQ==", "derp")
        with self.assertRaisesRegex(OSError, "Sec-WebSocket-Accept"):
            shim.validate_ws_response(response, "differentnonce", "derp")

    def test_websocket_accept_python38_and_modern_hashlib(self):
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        request = ("GET /derp HTTP/1.1\r\nSec-WebSocket-Key: " + key +
                   "\r\nSec-WebSocket-Protocol: derp\r\n\r\n").encode("ascii")
        material = (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        digest = shim.websocket_sha1(material)
        for legacy_error in (None,
                "'usedforsecurity' is an invalid keyword argument for openssl_sha1()",
                "openssl_sha1() takes at most 1 argument (2 given)",
                "sha1() takes no keyword arguments"):
            with self.subTest(legacy_error=legacy_error):
                calls = []
                def sha1(data, **kwargs):
                    self.assertEqual(data, material)
                    calls.append(kwargs)
                    if legacy_error and kwargs:
                        raise TypeError(legacy_error)
                    return digest.copy()
                with mock.patch.object(shim.hashlib, "sha1", side_effect=sha1):
                    response = valid_response(request).decode("ascii").split("\r\n\r\n", 1)[0]
                    shim.validate_ws_response(response, key, "derp")
                expected = [{"usedforsecurity": False}, {}] if legacy_error else [{"usedforsecurity": False}]
                self.assertEqual(calls, expected * 2)

    def test_websocket_sha1_does_not_retry_unrelated_typeerror(self):
        with mock.patch.object(shim.hashlib, "sha1", side_effect=TypeError("invalid data")) as sha1:
            with self.assertRaisesRegex(TypeError, "invalid data"):
                shim.websocket_sha1(b"payload")
            sha1.assert_called_once_with(b"payload", usedforsecurity=False)

    def test_status_code_is_not_a_reason_phrase_substring(self):
        response = b"HTTP/1.1 403 refused 101 upgrade\r\nContent-Length: 0\r\n\r\n"
        with self.bridge(response=response, rejected=True) as (client, server, threads, _):
            self.assertEqual(exact(client, len(response)), response)
            self.assert_workers_exit(threads)
            self.assert_peer_eof(server)

    def test_http10_error_response_is_forwarded_before_cleanup(self):
        for status in (b"403 Forbidden", b"407 Proxy Authentication Required", b"502 Bad Gateway"):
            with self.subTest(status=status):
                response = b"HTTP/1.0 " + status + b"\r\nContent-Length: 6\r\n\r\ndenied"
                with self.bridge(response=response, rejected=True, initial_data=b"private") as (client, server, threads, _):
                    self.assertEqual(exact(client, len(response)), response)
                    self.assert_workers_exit(threads)
                    self.assert_peer_eof(client)
                    self.assert_peer_eof(server)

    def test_established_websocket_outlives_setup_deadline(self):
        with mock.patch.object(shim, "SETUP_TIMEOUT", 0.15):
            with self.bridge() as (client, server, _, _):
                time.sleep(0.3)
                client.sendall(b"still-alive")
                self.assertEqual(client_frame(server), (2, b"still-alive"))
                server.sendall(server_frame(2, b"reply"))
                self.assertEqual(exact(client, 5), b"reply")

    def test_import_does_not_bind_or_create_cert_directory(self):
        spec = importlib.util.spec_from_file_location("shim_import_check", pathlib.Path(shim.__file__))
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(socket, "socket", side_effect=AssertionError("import opened socket")), \
                mock.patch.object(shim.os, "makedirs", side_effect=AssertionError("import created directory")):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))


if __name__ == "__main__":
    unittest.main()
