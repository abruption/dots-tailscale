import multiprocessing
import os
import signal
import time
import unittest
from unittest import mock

from bounded_network import bounded_call


class BoundedNetworkTests(unittest.TestCase):
    def children(self):
        return {child.pid for child in multiprocessing.active_children()}

    def test_json_result_and_child_reaped(self):
        before=self.children()
        result=bounded_call(lambda:{'accepted':True,'failure_class':'ok'},timeout=5)
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.value,{'accepted':True,'failure_class':'ok'})
        self.assertEqual(self.children(),before)

    def test_hung_operation_terminated_and_reaped(self):
        before=self.children()
        started=time.monotonic()
        result=bounded_call(lambda:time.sleep(60),timeout=0.05)
        self.assertEqual(result.status,'timeout')
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual(self.children(),before)

    def test_sigterm_ignoring_operation_killed_and_reaped(self):
        before=self.children()
        def operation():
            signal.signal(signal.SIGTERM,signal.SIG_IGN)
            time.sleep(60)
        started=time.monotonic()
        self.assertEqual(bounded_call(operation,timeout=0.1).status,'timeout')
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual(self.children(),before)

    def test_crash_invalid_result_and_exception_are_sanitized(self):
        def fail():raise RuntimeError('SECRET URL TOKEN')
        for name, operation in [('exception',fail), ('invalid_result',lambda:object()),
                                ('oversized_result',lambda:'x'*9000), ('process_exit',lambda:os._exit(2))]:
            with self.subTest(operation=name):
                result=bounded_call(operation,timeout=5)
                self.assertEqual(result.status,'failed')
                self.assertNotIn('SECRET',repr(result))

    def test_deadline_rejects_unbounded_values(self):
        for timeout in (0,-1,True,21,'20',float('inf'),float('nan')):
            with self.assertRaises(ValueError):bounded_call(lambda:None,timeout=timeout)

    def test_exited_worker_does_not_wait_for_an_inherited_writer(self):
        context = multiprocessing.get_context('fork')
        real_pipe = context.Pipe
        holders = []
        before = self.children()
        def inherited_pipe(*args, **kwargs):
            receiver, sender = real_pipe(*args, **kwargs)
            holder = context.Process(target=time.sleep, args=(60,), daemon=True)
            holder.start()  # Keeps the writer open after the actual worker exits.
            holders.append(holder)
            return receiver, sender
        try:
            started = time.monotonic()
            with mock.patch.object(context, 'Pipe', inherited_pipe):
                result = bounded_call(lambda: os._exit(2), timeout=5)
            self.assertEqual(result.status, 'failed')
            self.assertLess(time.monotonic() - started, 3)
        finally:
            for holder in holders:
                holder.terminate(); holder.join(timeout=1)
                if holder.is_alive(): holder.kill(); holder.join(timeout=1)
                holder.close()
        self.assertEqual(self.children(), before)


if __name__=='__main__':unittest.main()
