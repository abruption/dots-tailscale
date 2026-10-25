"""Bound callback DNS, TLS, headers and body using spawn (never fork threads)."""
import base64
import json
import multiprocessing
import threading
import time

MAX_RESPONSE = 16384
MAX_MESSAGE = 24000
WALL_TIMEOUT = 20.0
_SLOTS = threading.BoundedSemaphore(2)


def _worker(pipe, url, body, headers):
    try:
        from event_core import PublicHTTPS
        status, response = PublicHTTPS()._direct(url, body, headers)
        if type(status) is not int or not 100 <= status <= 599 or len(response) > MAX_RESPONSE:
            raise ValueError('invalid result')
        message = {'status': status, 'body': base64.b64encode(response).decode('ascii')}
    except Exception:
        # URLs, secrets, DNS errors and certificate details never cross the pipe.
        message = {'error': 'callback transport failed'}
    try:
        pipe.send_bytes(json.dumps(message, separators=(',', ':')).encode('ascii'))
    finally:
        pipe.close()


def bounded_request(url, body, headers, *, timeout=WALL_TIMEOUT, worker=_worker):
    """Reject excess work immediately; include process startup in the deadline.

    The private child sends one size-bounded message. Cleanup may consume up to
    one additional second after the network deadline (terminate then kill).
    """
    if not _SLOTS.acquire(blocking=False):
        raise RuntimeError('callback capacity exhausted')
    process = receiver = sender = None
    started = False
    reaped = True
    deadline = time.monotonic() + timeout
    try:
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=worker, args=(sender, url, body, headers), daemon=True)
        try:
            process.start()
        except Exception:
            raise RuntimeError('callback worker unavailable') from None
        started = True
        reaped = False
        sender.close()
        if not receiver.poll(max(0, deadline - time.monotonic())):
            raise TimeoutError('callback deadline exceeded')
        try:
            message = json.loads(receiver.recv_bytes(MAX_MESSAGE))
            if time.monotonic() > deadline:
                raise TimeoutError('callback deadline exceeded')
            if not isinstance(message, dict) or set(message) != {'status', 'body'}:
                raise ValueError()
            status = message['status']
            response = base64.b64decode(message['body'], validate=True)
            if type(status) is not int or not 100 <= status <= 599 or len(response) > MAX_RESPONSE:
                raise ValueError()
            return status, response
        except TimeoutError:
            raise
        except Exception:
            raise RuntimeError('callback transport failed') from None
    finally:
        if sender is not None:
            sender.close()
        if receiver is not None:
            receiver.close()
        try:
            if started:
                process.join(0)
                if process.is_alive():
                    process.terminate()
                    process.join(0.5)
                if process.is_alive():
                    process.kill()
                    process.join(0.5)
                if not process.is_alive():
                    reaped = True
                    process.close()
        finally:
            # Fail closed if the OS cannot reap even a killed child: retain its
            # slot so later requests cannot accumulate additional workers.
            if reaped:
                _SLOTS.release()
