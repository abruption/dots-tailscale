#!/usr/bin/env python3
"""Local CONNECT proxy for tailscaled.

License: MIT; see LICENSE. Local transport regression tests cover Upgrade-payload buffering;
platform-specific TLS/proxy compatibility requires live verification.

The upstream MITM proxy picks ALPN h2 whenever it is offered, which breaks
Tailscale's HTTP/1.1 Upgrade (control /ts2021, DERP). This shim terminates the
client TLS (offering only http/1.1, cert signed by a local CA trusted via
SSL_CERT_FILE) and re-dials upstream through the proxy offering only http/1.1.
The proxy also only relays data after *WebSocket* upgrades, so Tailscale's raw
upgrades (control "tailscale-control-protocol", DERP "DERP") are re-issued
upstream as WebSockets and bridged to the client as a raw byte stream.
Non-443 CONNECTs are passed through untouched.
"""
import base64, hashlib, re, os, socket, ssl, struct, subprocess, threading, sys, urllib.parse, time

UP = (os.environ.get("UPSTREAM_HOST", "browser-proxy"), int(os.environ.get("UPSTREAM_PORT", "8889")))
LISTEN = ("127.0.0.1", int(os.environ.get("SHIM_PORT", "18089")))
D = os.path.dirname(os.path.abspath(__file__))
CERTS = os.path.join(D, "certs")
# Per-stage absolute deadlines apply only while establishing a connection.
# Established raw/DERP/control streams remain long-lived and blocking.
SETUP_TIMEOUT = 15
MAX_HEADER_BYTES = 65536
lock = threading.Lock()
up_ctx = ssl.create_default_context(); up_ctx.set_alpn_protocols(["http/1.1"])

def leaf(host):
    crt, key = os.path.join(CERTS, host + ".crt"), os.path.join(CERTS, host + ".key")
    with lock:
        os.makedirs(CERTS, mode=0o700, exist_ok=True)
        if not os.path.exists(crt):
            ext = os.path.join(CERTS, host + ".ext")
            open(ext, "w").write(f"subjectAltName=DNS:{host}\nextendedKeyUsage=serverAuth\n")
            subprocess.run(["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                            "-nodes", "-keyout", key, "-subj", f"/CN={host}", "-out", crt + ".csr"], check=True, capture_output=True)
            subprocess.run(["openssl", "x509", "-req", "-in", crt + ".csr", "-CA", f"{D}/ca.crt", "-CAkey", f"{D}/ca.key",
                            "-CAcreateserial", "-days", "825", "-extfile", ext, "-out", crt], check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(crt, key); ctx.set_alpn_protocols(["http/1.1"])
    return ctx

def set_deadline(sock, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("connection setup deadline exceeded")
    sock.settimeout(remaining)

def shutdown(*sockets):
    for sock in sockets:
        if sock is not None:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def close(*sockets):
    shutdown(*sockets)
    for sock in sockets:
        if sock is not None:
            try: sock.close()
            except OSError: pass

def upstream_connect(target):
    deadline = time.monotonic() + SETUP_TIMEOUT
    s = socket.create_connection(UP, timeout=SETUP_TIMEOUT)
    try:
        set_deadline(s, deadline)
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        head, rest = read_head(s, deadline)
        if " 200 " not in head.split("\r\n")[0]:
            raise OSError(head.split("\r\n")[0])
        return s, rest
    except BaseException:
        close(s)
        raise

def pipe(a, b, tag=""):
    first = True
    try:
        while (d := a.recv(65536)):
            if first and os.environ.get("SHIM_DEBUG"): print(tag, repr(d[:600]), file=sys.stderr, flush=True)
            first = False
            b.sendall(d)
    except OSError: pass
    finally:
        for x in (a, b):
            try: x.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def read_head(sock, deadline=None, initial=b"", configure_timeout=True):
    if deadline is None:
        deadline = time.monotonic() + SETUP_TIMEOUT
    buf = initial
    while b"\r\n\r\n" not in buf:
        if configure_timeout:
            set_deadline(sock, deadline)
        elif time.monotonic() >= deadline:
            raise TimeoutError("connection setup deadline exceeded")
        d = sock.recv(4096)
        if not d: raise OSError("closed before headers")
        buf += d
        end = buf.find(b"\r\n\r\n")
        if (end + 4 if end >= 0 else len(buf)) > MAX_HEADER_BYTES:
            raise OSError("HTTP headers too large")
    head, rest = buf.split(b"\r\n\r\n", 1)
    if len(head) + 4 > MAX_HEADER_BYTES:
        raise OSError("HTTP headers too large")
    return head.decode("latin1"), rest

def recv_exact(sock, n, pre):
    while len(pre[0]) < n:
        d = sock.recv(65536)
        if not d: raise OSError("ws closed")
        pre[0] += d
    out, pre[0] = pre[0][:n], pre[0][n:]
    return out

def ws_frame(op, payload):
    n = len(payload); mask = os.urandom(4)
    hdr = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else
          bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
    return hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

def response_status(head):
    # Parse the status code, never search for " 101 " in arbitrary text.
    line = head.split("\r\n", 1)[0]
    match = re.fullmatch(r"HTTP/1\.[01] ([0-9]{3})(?: [\t\x20-\x7e\x80-\xff]*)?", line)
    if match is None:
        raise OSError("ws upgrade refused: malformed HTTP status")
    return int(match.group(1))

def websocket_sha1(data):
    # SHA-1 is mandated by the WebSocket protocol, not used for security here.
    # Python 3.8 predates the usedforsecurity keyword.
    try:
        return hashlib.sha1(data, usedforsecurity=False)
    except TypeError as error:
        message = str(error)
        if ("usedforsecurity" not in message and "takes no keyword arguments" not in message
                and "takes at most 1 argument (2 given)" not in message):
            raise
        return hashlib.sha1(data)

def validate_ws_response(head, key, protocol):
    """RFC 6455 section 4.1: validate before releasing any protocol bytes."""
    if response_status(head) != 101 or not head.startswith("HTTP/1.1 "):
        raise OSError("ws upgrade refused: expected HTTP/1.1 status 101")
    headers = {}
    for line in head.split("\r\n")[1:]:
        name, separator, value = line.partition(":")
        if not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise OSError("ws upgrade refused: malformed header")
        if any(ord(char) < 32 and char != "\t" or ord(char) == 127 for char in value):
            raise OSError("ws upgrade refused: malformed header value")
        headers.setdefault(name.lower(), []).append(value.strip(" \t"))
    if [value.lower() for value in headers.get("upgrade", [])] != ["websocket"]:
        raise OSError("ws upgrade refused: invalid Upgrade")
    connection = [token.strip(" \t").lower() for value in headers.get("connection", []) for token in value.split(",")]
    if "upgrade" not in connection or any(
            token and not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", token) for token in connection):
        raise OSError("ws upgrade refused: invalid Connection")
    accept = base64.b64encode(websocket_sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
    if headers.get("sec-websocket-accept") != [accept]:
        raise OSError("ws upgrade refused: invalid Sec-WebSocket-Accept")
    if "sec-websocket-protocol" in headers and headers["sec-websocket-protocol"] != [protocol]:
        raise OSError("ws upgrade refused: unrequested or ambiguous subprotocol")
    # No extensions were offered; even a valid Accept cannot authorize one.
    if "sec-websocket-extensions" in headers:
        raise OSError("ws upgrade refused: unrequested extension")

def ws_bridge(c, u, req_head, host, initial_data=b""):
    try:
        return _ws_bridge(c, u, req_head, host, initial_data)
    finally:
        close(c, u)

def _ws_bridge(c, u, req_head, host, initial_data=b""):
    lines = req_head.split("\r\n"); method, path, _ = lines[0].split(" ", 2)
    hdrs = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
    upg = hdrs.get("upgrade", "")
    if upg.lower() == "tailscale-control-protocol":
        q = urllib.parse.urlencode({"X-Tailscale-Handshake": hdrs.get("x-tailscale-handshake", "")})
        path = path + ("&" if "?" in path else "?") + q; proto = "tailscale-control-protocol"
    else:
        proto = "derp"
    extra = "".join(f"{k}: {v}\r\n" for k, v in hdrs.items() if k.startswith(("derp-", "x-tailscale")) and k != "derp-fast-start")
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    deadline = time.monotonic() + SETUP_TIMEOUT
    set_deadline(u, deadline)
    u.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
               f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Protocol: {proto}\r\n{extra}\r\n").encode())
    resp, rest = read_head(u, deadline)
    set_deadline(c, deadline)
    if response_status(resp) != 101:
        c.sendall(resp.encode("latin1") + b"\r\n\r\n" + rest); raise OSError("ws upgrade refused: " + resp.split("\r\n")[0])
    validate_ws_response(resp, key, proto)
    if hdrs.get("derp-fast-start") != "1":
        c.sendall(f"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: {upg}\r\n\r\n".encode())
    c.settimeout(None)
    u.settimeout(None)
    wlock = threading.Lock()
    def up_to_client():
        pre = [rest]
        fragmented = False
        try:
            while True:
                b0, b1 = recv_exact(u, 2, pre)
                final, op, n = bool(b0 & 0x80), b0 & 0x0F, b1 & 0x7F
                # No extensions are negotiated. Servers must not mask frames;
                # checking before reading payload also avoids leaking mask bytes.
                if b0 & 0x70 or b1 & 0x80 or op not in (0, 1, 2, 8, 9, 10):
                    raise OSError("invalid WebSocket frame flags or opcode")
                if op >= 8 and (not final or n > 125):
                    raise OSError("invalid WebSocket control frame")
                if (op == 0 and not fragmented) or (op in (1, 2) and fragmented):
                    raise OSError("invalid WebSocket continuation")
                if n == 126:
                    n = struct.unpack(">H", recv_exact(u, 2, pre))[0]
                    if n < 126:
                        raise OSError("nonminimal WebSocket frame length")
                elif n == 127:
                    n = struct.unpack(">Q", recv_exact(u, 8, pre))[0]
                    if n < 65536 or n & (1 << 63):
                        raise OSError("invalid WebSocket frame length")
                if op == 8 and n == 1:
                    raise OSError("invalid WebSocket close payload length")
                payload = recv_exact(u, n, pre)
                if op in (0, 1, 2):
                    fragmented = not final
                    c.sendall(payload)
                elif op == 9:
                    with wlock: u.sendall(ws_frame(0xA, payload))
                elif op == 8: break
        except OSError: pass
        for x in (c, u):
            try: x.shutdown(socket.SHUT_RDWR)
            except OSError: pass
    # read_head() may have consumed protocol bytes after the HTTP headers.
    # Flush them after the upstream Upgrade, before reading any later bytes.
    worker = None
    try:
        if initial_data:
            u.sendall(ws_frame(2, initial_data))
        candidate = threading.Thread(target=up_to_client, daemon=True)
        candidate.start()
        worker = candidate
        while (d := c.recv(65536)):
            with wlock: u.sendall(ws_frame(2, d))
    except OSError: pass
    finally:
        shutdown(c, u)
        if worker is not None:
            worker.join()

def handle(c):
    u = None
    worker = None
    try:
        head, initial = read_head(c)
        method, target, _ = head.split("\r\n")[0].split(" ", 2)
        if method != "CONNECT":
            c.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        host, port = target.rsplit(":", 1)
        u, upstream_initial = upstream_connect(target)
        if port == "443" and upstream_initial:
            # An upstream TLS server cannot send handshake records until this
            # client sends ClientHello. Reject unexpected pre-handshake bytes:
            # wrap_socket cannot consume a CONNECT parser's detached prebuffer.
            raise OSError("unexpected tunnel data before upstream TLS handshake")
        c.settimeout(SETUP_TIMEOUT)
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        if port == "443":
            # Retain ownership before handshaking: wrap_socket's automatic
            # handshake can otherwise lose the socket on failure.
            c = leaf(host).wrap_socket(c, server_side=True, do_handshake_on_connect=False)
            c.settimeout(SETUP_TIMEOUT)
            c.do_handshake()
            u = up_ctx.wrap_socket(u, server_hostname=host, do_handshake_on_connect=False)
            u.settimeout(SETUP_TIMEOUT)
            u.do_handshake()
            head, rest = read_head(c)
            if os.environ.get("SHIM_DEBUG"): print(">", host, head.split("\r\n")[0], file=sys.stderr, flush=True)
            if "\r\nupgrade:" in head.lower() and "upgrade: websocket" not in head.lower():
                return ws_bridge(c, u, head, host, rest)
            u.settimeout(SETUP_TIMEOUT)
            u.sendall(head.encode("latin1") + b"\r\n\r\n" + rest)
        elif initial:
            u.settimeout(SETUP_TIMEOUT)
            u.sendall(initial)
        if upstream_initial:
            # This runs before the receive pump, preserving order and avoiding
            # duplication regardless of CONNECT response/read boundaries.
            c.sendall(upstream_initial)
        # Never change shared socket timeouts underneath the upload worker.
        # An in-flight recv/sendall retains its original timeout even if the
        # timeout is later cleared. The HTTP response stage uses shutdown on a
        # scoped timer instead; after it is cancelled both streams stay blocking.
        c.settimeout(None)
        u.settimeout(None)
        candidate = threading.Thread(target=pipe, args=(c, u, ">" + host), daemon=True)
        candidate.start()
        worker = candidate
        if port == "443":
            # Forward request-body bytes concurrently while bounding the first
            # HTTP response. Do not deadlock uploads waiting for their response.
            deadline = time.monotonic() + SETUP_TIMEOUT
            timer = threading.Timer(SETUP_TIMEOUT, shutdown, args=(c, u))
            timer.daemon = True
            timer.start()
            try:
                rest = b""
                while True:
                    response, rest = read_head(u, deadline, rest, configure_timeout=False)
                    status = response.split("\r\n")[0].split()
                    interim = len(status) >= 2 and status[1].isdigit() and 100 <= int(status[1]) < 200 and status[1] != "101"
                    c.sendall(response.encode("latin1") + b"\r\n\r\n" + (b"" if interim else rest))
                    if not interim:
                        break
            finally:
                timer.cancel()
                timer.join()
        pipe(u, c, "<" + host)
    except Exception as e:
        print(f"shim: {e}", file=sys.stderr, flush=True)
    finally:
        shutdown(c, u)
        try:
            if worker is not None:
                worker.join()
        finally:
            close(c, u)

def main():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(LISTEN)
        listener.listen(64)
        print(f"shim listening on {LISTEN}", flush=True)
        while True:
            conn, _ = listener.accept()
            threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
