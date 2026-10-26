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
MAX_ENVELOPE_BYTES = MAX_RESULT_BYTES + 1024
ERROR_KINDS = frozenset(('worker_start_failed', 'worker_exited', 'result_too_large',
                        'invalid_worker_result', 'deadline_exceeded', 'worker_exception',
                        'RuntimeError', 'OSError', 'ValueError', 'TypeError',
                        'ImportError', 'ModuleNotFoundError', 'FileNotFoundError',
                        'PermissionError', 'TimeoutError', 'MemoryError'))
_EXCEPTION_KINDS = {kind: kind.__name__ for kind in (
    RuntimeError, OSError, ValueError, TypeError, ImportError, ModuleNotFoundError,
    FileNotFoundError, PermissionError, TimeoutError, MemoryError)}


@dataclass(frozen=True)
class BoundedResult:
    status: str
    value: object = None
    error_kind: str | None = None
    exit_code: int | None = None


def _worker(operation, sender, receiver):
    receiver.close()
    try:
        try:
            value = operation()
        except BaseException as exc:
            envelope = {'protocol': 1, 'kind': 'error',
                        'error_kind': _EXCEPTION_KINDS.get(type(exc), 'worker_exception')}
        else:
            try:
                raw = json.dumps(value, allow_nan=False, separators=(',', ':')).encode()
                envelope = ({'protocol': 1, 'kind': 'error', 'error_kind': 'result_too_large'}
                            if len(raw) > MAX_RESULT_BYTES else
                            {'protocol': 1, 'kind': 'value', 'value': value})
            except (TypeError, ValueError, OverflowError, RecursionError):
                envelope = {'protocol': 1, 'kind': 'error', 'error_kind': 'invalid_worker_result'}
        sender.send_bytes(json.dumps(envelope, allow_nan=False, separators=(',', ':')).encode())
    except BaseException:
        # Raw exception text, URLs and credentials never cross the IPC boundary.
        pass
    finally:
        sender.close()


def bounded_call(operation, timeout=20):
    """Return completed/timeout/failed with additive sanitized worker diagnostics."""
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 20:
        raise ValueError('invalid_network_deadline')
    started = time.monotonic()
    context = multiprocessing.get_context('fork')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(operation, sender, receiver),
                              name='bounded-network-operation', daemon=True)
    exit_code = None

    def receive_result():
        try:
            process.start()
        except (OSError, ValueError, RuntimeError):
            return BoundedResult('failed', error_kind='worker_start_failed')
        sender.close()
        remaining = max(0, timeout-(time.monotonic()-started))
        ready = wait([receiver, process.sentinel], timeout=remaining)
        if not ready:
            return BoundedResult('timeout', error_kind='deadline_exceeded')
        if receiver not in ready and not receiver.poll(0):
            return BoundedResult('failed', error_kind='worker_exited')
        try:
            raw = receiver.recv_bytes(MAX_ENVELOPE_BYTES)
        except EOFError:
            return BoundedResult('failed', error_kind='worker_exited')
        except OSError:
            return BoundedResult('failed', error_kind='invalid_worker_result')
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or type(envelope.get('protocol')) is not int or envelope['protocol'] != 1:
                raise ValueError()
            if envelope.get('kind') == 'value' and set(envelope) == {'protocol', 'kind', 'value'}:
                value = envelope['value']
                if len(json.dumps(value, allow_nan=False, separators=(',', ':')).encode()) > MAX_RESULT_BYTES:
                    return BoundedResult('failed', error_kind='result_too_large')
                return BoundedResult('completed', value)
            if (envelope.get('kind') == 'error' and set(envelope) == {'protocol', 'kind', 'error_kind'}
                    and isinstance(envelope.get('error_kind'), str) and envelope['error_kind'] in ERROR_KINDS):
                return BoundedResult('failed', error_kind=envelope['error_kind'])
        except (ValueError, TypeError, UnicodeError, RecursionError):
            pass
        return BoundedResult('failed', error_kind='invalid_worker_result')

    try:
        result = receive_result()
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
            # Capture before close(), which makes process properties unavailable.
            exit_code = process.exitcode
            if not process.is_alive():
                process.close()
    return BoundedResult(result.status, result.value, result.error_kind, exit_code)
