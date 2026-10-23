import multiprocessing
import os
import signal
import time
import unittest
from unittest.mock import patch
import callback_transport as transport


def success(pipe, *args):
    pipe.send_bytes(b'{"status":200,"body":"b2s="}')
    pipe.close()


def hang(pipe, *args):
    time.sleep(60)


def ignores_terminate(pipe, *args):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)


def dns_hang(pipe, url, body, headers):
    def stalled(*args, **kwargs):
        time.sleep(60)
    with patch('event_core.socket.getaddrinfo', side_effect=stalled):
        transport._worker(pipe, url, body, headers)


def early_exit(pipe, *args):
    os._exit(1)


def malformed(pipe, *args):
    pipe.send_bytes(b'private callback URL and token')
    pipe.close()


def oversize(pipe, *args):
    pipe.send_bytes(b'x' * 25000)
    pipe.close()


def direct_exception(pipe, url, body, headers):
    with patch('event_core.PublicHTTPS._direct', side_effect=ValueError('secret-token')):
        transport._worker(pipe, url, body, headers)


class CallbackTransportTest(unittest.TestCase):
    def run_worker(self, worker, timeout=3):
        return transport.bounded_request('https://callback.example/path', b'{}', {}, timeout=timeout, worker=worker)

    def test_success_and_reaped(self):
        before = {p.pid for p in multiprocessing.active_children()}
        self.assertEqual(self.run_worker(success), (200, b'ok'))
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    def test_hang_deadline_and_reaped(self):
        before = {p.pid for p in multiprocessing.active_children()}
        start = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, '^callback deadline exceeded$'):
            self.run_worker(hang, timeout=.3)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)
        self.assertEqual(self.run_worker(success), (200, b'ok'))

    def test_dns_deadline_and_kill_fallback(self):
        for worker in (dns_hang, ignores_terminate):
            with self.subTest(worker=worker.__name__):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    self.run_worker(worker, timeout=.5)
                self.assertLess(time.monotonic() - started, 2)
                self.assertFalse(multiprocessing.active_children())

    def test_exit_malformed_oversize_and_safe_exception(self):
        for worker in (early_exit, malformed, oversize, direct_exception):
            with self.subTest(worker=worker.__name__):
                with self.assertRaisesRegex(RuntimeError, '^callback transport failed$'):
                    self.run_worker(worker)
                self.assertFalse(multiprocessing.active_children())

    def test_capacity_is_global_and_never_starts_worker(self):
        self.assertTrue(transport._SLOTS.acquire(False))
        self.assertTrue(transport._SLOTS.acquire(False))
        try:
            with patch('callback_transport.multiprocessing.get_context') as context:
                with self.assertRaisesRegex(RuntimeError, '^callback capacity exhausted$'):
                    self.run_worker(success)
                context.assert_not_called()
        finally:
            transport._SLOTS.release()
            transport._SLOTS.release()

    def test_public_transport_uses_bound_and_validates_url(self):
        from event_core import PublicHTTPS
        with patch('callback_transport.bounded_request', return_value=(202, b'')) as bounded:
            self.assertEqual(PublicHTTPS()('https://callback.example/x', b'{}', {}), (202, b''))
            bounded.assert_called_once()
            with self.assertRaises(ValueError):
                PublicHTTPS()('http://callback.example/x', b'{}', {})
            bounded.assert_called_once()


if __name__ == '__main__':
    unittest.main()
