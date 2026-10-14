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
import base64, os, socket, ssl, struct, subprocess, threading, sys, urllib.parse, time

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
        head, _ = read_head(s, deadline)
        if " 200 " not in head.split("\r\n")[0]:
            raise OSError(head.split("\r\n")[0])
        return s
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

def read_head(sock, deadline=None):
    if deadline is None:
        deadline = time.monotonic() + SETUP_TIMEOUT
    buf = b""
    while b"\r\n\r\n" not in buf:
        set_deadline(sock, deadline)
        d = sock.recv(4096)
        if not d: raise OSError("closed before headers")
        buf += d
        end = buf.find(b"\r\n\r\n")
        if (end + 4 if end >= 0 else len(buf)) > MAX_HEADER_BYTES:
            raise OSError("HTTP headers too large")
    head, rest = buf.split(b"\r\n\r\n", 1)
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
    deadline = time.monotonic() + SETUP_TIMEOUT
    set_deadline(u, deadline)
    u.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
               f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
               f"Sec-WebSocket-Protocol: {proto}\r\n{extra}\r\n").encode())
    resp, rest = read_head(u, deadline)
    set_deadline(c, deadline)
    if " 101 " not in resp.split("\r\n")[0]:
        c.sendall(resp.encode("latin1") + b"\r\n\r\n" + rest); raise OSError("ws upgrade refused: " + resp.split("\r\n")[0])
    if hdrs.get("derp-fast-start") != "1":
        c.sendall(f"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: {upg}\r\n\r\n".encode())
    c.settimeout(None)
    u.settimeout(None)
    wlock = threading.Lock()
    def up_to_client():
        pre = [rest]
        try:
            while True:
                b0, b1 = recv_exact(u, 2, pre)
                op, n = b0 & 0x0F, b1 & 0x7F
                if n == 126: n = struct.unpack(">H", recv_exact(u, 2, pre))[0]
                elif n == 127: n = struct.unpack(">Q", recv_exact(u, 8, pre))[0]
                payload = recv_exact(u, n, pre)
                if op in (0, 1, 2): c.sendall(payload)
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
        worker = threading.Thread(target=up_to_client, daemon=True)
        worker.start()
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
        u = upstream_connect(target)
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
        # The request-body reader blocks until shutdown. Changing a socket's
        # timeout cannot cancel a recv deadline that is already in progress.
        c.settimeout(None)
        if port != "443":
            u.settimeout(None)
        worker = threading.Thread(target=pipe, args=(c, u, ">" + host), daemon=True)
        worker.start()
        if port == "443":
            # Forward request-body bytes concurrently while bounding the first
            # HTTP response. Do not deadlock uploads waiting for their response.
            response, rest = read_head(u)
            c.settimeout(SETUP_TIMEOUT)
            c.sendall(response.encode("latin1") + b"\r\n\r\n" + rest)
            c.settimeout(None)
            u.settimeout(None)
        pipe(u, c, "<" + host)
    except Exception as e:
        print(f"shim: {e}", file=sys.stderr, flush=True)
    finally:
        shutdown(c, u)
        if worker is not None:
            worker.join()
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
