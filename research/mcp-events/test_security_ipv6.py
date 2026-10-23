import copy
import subprocess
import unittest
import stat
from types import SimpleNamespace
from unittest.mock import patch, Mock
from security_ipv6 import CHAIN, JUMP, RULES, Firewall, mutation_lock

class Fake(Firewall):
    def __init__(self):
        self.rows = [['-P', 'INPUT', 'ACCEPT'], ['-N', 'ts-input'], ['-A', 'INPUT', '-j', 'ts-input'], ['-A', 'INPUT', '-s', '2001:db8::/32', '-j', 'DROP']]
        self.calls = []
        self.fail_insert = False
        self.fail_append_number = None
        self.appends = 0
    def snapshot(self):
        return copy.deepcopy(self.rows)
    def command(self, *args):
        args = list(args)
        self.calls.append(args)
        if args[0] == '-N':
            self.rows.append(args)
        elif args[0] == '-A':
            self.appends += 1
            if self.appends == self.fail_append_number:
                raise subprocess.CalledProcessError(1, args)
            self.rows.append(args)
        elif args[0] == '-I':
            if self.fail_insert:
                raise subprocess.CalledProcessError(1, args)
            index = next(i for i, line in enumerate(self.rows) if line[:2] == ['-A', 'INPUT'])
            self.rows.insert(index, ['-A', 'INPUT'] + args[3:])
        elif args[0] == '-D':
            self.rows.remove(['-A'] + args[1:])
        elif args[0] == '-X':
            self.rows.remove(['-N', args[1]])
        else:
            raise AssertionError(args)
        return ''

class Tests(unittest.TestCase):
    def test_command_has_bounded_timeout(self):
        runner = Mock(return_value=SimpleNamespace(stdout=''))
        Firewall(runner).command('-S')
        self.assertEqual(runner.call_args.kwargs['timeout'], 10)
        self.assertIn('-w', runner.call_args.args[0])

    def test_lock_rejects_unsafe_metadata_and_closes_fd(self):
        for mode, uid, links in ((stat.S_IFREG | 0o644, 0, 1),
                                 (stat.S_IFREG | 0o600, 1000, 1),
                                 (stat.S_IFIFO | 0o600, 0, 1),
                                 (stat.S_IFREG | 0o600, 0, 2)):
            with patch('security_ipv6.os.open', return_value=9), \
                 patch('security_ipv6.os.fstat', return_value=SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=links)), \
                 patch('security_ipv6.os.close') as close, \
                 patch('security_ipv6.fcntl.flock') as flock:
                with self.assertRaises(RuntimeError):
                    with mutation_lock():
                        self.fail('Unsafe lock admitted')
                close.assert_called_once_with(9)
                flock.assert_not_called()

    def test_idempotence_preserves_other_rules(self):
        fw = Fake(); before = fw.snapshot()
        self.assertTrue(fw.apply()); self.assertFalse(fw.apply())
        self.assertEqual(fw.rows.count(JUMP), 1)
        self.assertTrue(fw.remove()); self.assertFalse(fw.remove())
        self.assertEqual(fw.rows, before)
        self.assertFalse(any(c[0] == '-F' for c in fw.calls))
    def test_populate_before_jump(self):
        fw = Fake(); fw.apply()
        i = next(i for i,c in enumerate(fw.calls) if c[0] == '-I')
        self.assertEqual(fw.calls[i-1], ['-A', CHAIN, '-j', 'DROP'])
    def test_drift_refused(self):
        for op in ('apply','remove'):
            fw = Fake(); fw.rows += [['-N',CHAIN],['-A',CHAIN,'-j','ACCEPT']]
            before = fw.snapshot()
            with self.assertRaises(RuntimeError): getattr(fw,op)()
            self.assertEqual(fw.rows,before); self.assertEqual(fw.calls,[])
    def test_extra_reference_or_wrong_order_refused(self):
        for extra in (['-A','OUTPUT','-j',CHAIN], JUMP):
            fw = Fake(); fw.apply(); fw.rows.append(extra)
            before = fw.snapshot()
            with self.assertRaises(RuntimeError): fw.remove()
            self.assertEqual(fw.rows,before)
        fw = Fake(); fw.apply(); fw.rows.remove(JUMP); fw.rows.append(JUMP)
        with self.assertRaises(RuntimeError): fw.apply()
    def test_failed_insert_cleanup(self):
        fw = Fake(); before = fw.snapshot(); fw.fail_insert = True
        with self.assertRaises(subprocess.CalledProcessError): fw.apply()
        self.assertEqual(fw.rows,before)
    def test_failed_population_cleanup(self):
        fw = Fake(); before = fw.snapshot(); fw.fail_append_number = 3
        with self.assertRaises(subprocess.CalledProcessError): fw.apply()
        self.assertEqual(fw.rows,before)
    def test_preexisting_chain_survives_failed_insert(self):
        fw = Fake(); fw.rows += [['-N',CHAIN]] + [['-A',CHAIN]+r for r in RULES]
        before = fw.snapshot(); fw.fail_insert = True
        with self.assertRaises(subprocess.CalledProcessError): fw.apply()
        self.assertEqual(fw.rows,before)
    def test_return_preserves_later_drop(self):
        self.assertTrue(all(r[-1]=='RETURN' for r in RULES[:-1]))
        self.assertEqual(RULES[-1],['-j','DROP'])
        fw = Fake(); fw.apply()
        caller = [r for r in fw.rows if r[:2]==['-A','INPUT']]
        self.assertEqual(caller[0],JUMP)
        self.assertEqual(caller[-1][-1],'DROP')

if __name__ == '__main__': unittest.main()
