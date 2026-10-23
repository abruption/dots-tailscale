"""Bound complete network operations, including blocking OS DNS, in a child.

Fork is intentional: this private single-threaded Mac/Linux CLI must not put
credentials into process arguments, environment variables, or temporary files.
The child returns bounded JSON bytes, never pickled network objects or errors.
"""
from dataclasses import dataclass
import json
import multiprocessing
from multiprocessing.connection import wait
import time

MAX_RESULT_BYTES = 8192


@dataclass(frozen=True)
class BoundedResult:
    status: str
    value: object = None


def _worker(operation, sender, receiver):
    receiver.close()
    try:
        raw = json.dumps(operation(), allow_nan=False, separators=(',', ':')).encode()
        if len(raw) > MAX_RESULT_BYTES:
            return
        sender.send_bytes(raw)
    except BaseException:
        # No exception objects, raw response bodies, URLs, or credentials cross.
        pass
    finally:
        sender.close()


def bounded_call(operation, timeout=20):
    """Return completed/timeout/failed and JSON primitives; clean up the child."""
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 20:
        raise ValueError('invalid_network_deadline')
    started = time.monotonic()
    context = multiprocessing.get_context('fork')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(operation, sender, receiver),
                              name='bounded-network-operation', daemon=True)
    try:
        try:
            process.start()
        except (OSError, ValueError):
            return BoundedResult('failed')
        sender.close()
        remaining = max(0, timeout-(time.monotonic()-started))
        ready = wait([receiver, process.sentinel], timeout=remaining)
        if not ready:
            return BoundedResult('timeout')
        if receiver not in ready and not receiver.poll(0):
            return BoundedResult('failed')
        try:
            raw = receiver.recv_bytes(MAX_RESULT_BYTES)
            value = json.loads(raw)
        except (EOFError, OSError, ValueError, UnicodeError):
            return BoundedResult('failed')
        return BoundedResult('completed', value)
    finally:
        receiver.close()
        sender.close()
        if process.pid is not None:
            process.join(timeout=0.1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.25)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.25)
            if not process.is_alive():
                process.close()
