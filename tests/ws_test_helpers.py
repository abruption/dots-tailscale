"""Independent server fixture deriving Accept from the actual request nonce."""
import base64
import hashlib


def valid_response(request):
    headers = {}
    for line in request.decode("latin1").split("\r\n")[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.lower()] = value.strip()
    nonce = headers["sec-websocket-key"]
    assert len(base64.b64decode(nonce, validate=True)) == 16
    material = (nonce + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
    try:
        digest = hashlib.sha1(material, usedforsecurity=False)
    except TypeError as error:
        if "usedforsecurity" not in str(error) and "takes no keyword arguments" not in str(error):
            raise
        digest = hashlib.sha1(material)
    accept = base64.b64encode(digest.digest())
    return (b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept +
            b"\r\nSec-WebSocket-Protocol: " + headers["sec-websocket-protocol"].encode() + b"\r\n\r\n")
