import multiprocessing
import os
import signal
import time
import unittest

from bounded_network import bounded_call


class BoundedNetworkTests(unittest.TestCase):
    def children(self):
        return {child.pid for child in multiprocessing.active_children()}

    def test_json_result_and_child_reaped(self):
        before=self.children()
        result=bounded_call(lambda:{'accepted':True,'failure_class':'ok'},timeout=1)
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
        for operation in (fail,lambda:object(),lambda:'x'*9000,lambda:os._exit(2)):
            result=bounded_call(operation,timeout=1)
            self.assertEqual(result.status,'failed')
            self.assertNotIn('SECRET',repr(result))

    def test_deadline_rejects_unbounded_values(self):
        for timeout in (0,-1,True,21,'20',float('inf'),float('nan')):
            with self.assertRaises(ValueError):bounded_call(lambda:None,timeout=timeout)


if __name__=='__main__':unittest.main()
