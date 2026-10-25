import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).with_name('collector.py'))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def observation(**changes):
    row = {'uid': 1000, 'boot_sha256': 'a' * 64, 'uptime_s': 123,
           'socket_present': True, 'source_present': {k: True for k in c.PUBLIC},
           'backend': 'Running', 'processes': [{'role': 'shim', 'rss_kib': 20000, 'threads': 7, 'fd_count': 10}],
           'log_bytes': {'shim': 39}}
    row.update(changes)
    return row


class CollectorContract(unittest.TestCase):
    def setUp(self):
        temporary_root = '/Volumes/Kingston NV/tmp' if str(Path.cwd()).startswith('/Volumes/Kingston NV/') else None
        self.temp = tempfile.TemporaryDirectory(prefix='codex-lifecycle20-test-', dir=temporary_root)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = {'data_dir': str(self.directory), 'ssh_host': 'agent@example.invalid', 'ssh_hostname': 'example.invalid'}

    def test_restart_does_not_extend_deadline_or_resample_after_expiry(self):
        state = c.initial_state(None, 100, 60)
        c.save_private(self.directory / 'state.json', state)
        restored = c.initial_state(c.read_private(self.directory / 'state.json'), 159, 86400)
        self.assertEqual(restored['deadline'], 160)
        def forbidden(*args):
            self.fail('expired collector must not execute SSH')
        ended = c.collect_once(self.config, restored, 160, forbidden)
        self.assertEqual(ended['status'], 'expired')
        self.assertFalse((self.directory / 'samples.jsonl').exists())

    def test_successful_samples_request_baseline_only_until_boot_changes(self):
        seen = []
        def sample(config, boot):
            seen.append(boot)
            return observation(boot_sha256=('b' if len(seen) == 2 else 'a') * 64)
        state = c.initial_state(None, 100, 86400)
        for now in [101, 401, 701]:
            state = c.collect_once(self.config, state, now, sample)
        self.assertEqual(seen, ['', 'a' * 64, 'b' * 64])
        self.assertEqual(state['samples'], 3)

    def test_resource_and_log_caps_are_terminal_and_do_not_trigger_actions(self):
        for changes, expected in [
            ({'processes': [{'role': 'tailscaled', 'rss_kib': 262145, 'threads': 10, 'fd_count': 20}]}, 'resource_cap'),
            ({'processes': [{'role': 'shim', 'rss_kib': 100, 'threads': 129, 'fd_count': 20}]}, 'resource_cap'),
            ({'log_bytes': {'tailscaled': 50 * 1024 * 1024 + 1}}, 'runtime_log_cap')]:
            state = c.collect_once(self.config, c.initial_state(None, 100, 500), 101, lambda *_: observation(**changes))
            self.assertEqual(state['status'], expected)
            with patch.object(c, 'ssh_sample', side_effect=AssertionError('terminal collector re-executed')):
                self.assertIs(c.collect_once(self.config, state, 102), state)

    def test_evidence_file_never_exceeds_limit(self):
        path = self.directory / 'samples.jsonl'
        path.write_bytes(b'x' * 100)
        path.chmod(0o600)
        with patch.object(c, 'MAX_BYTES', 101):
            state = c.collect_once(self.config, c.initial_state(None, 100, 500), 101, lambda *_: observation())
        self.assertEqual(state['status'], 'evidence_cap')
        self.assertEqual(path.stat().st_size, 100)
        self.assertEqual(state['samples'], 0)

    def test_untrusted_probe_cannot_persist_private_or_unknown_fields(self):
        raw = observation(hostname='private-host', raw_log='private-token', environment={'SECRET': 'private-token'},
                          baseline={'source_hashes': {'shim.py': 'b' * 64, 'private.key': 'c' * 64},
                                    'package_version': '0.4.0', 'tailscale_version': '1.102.4', 'key': 'private-token'})
        raw['processes'][0]['cmdline'] = 'private-token'
        raw['log_bytes']['secret-path'] = 123
        clean = c.sanitize(raw)
        encoded = json.dumps(clean)
        for private in ['private-token', 'private-host', 'private.key', 'secret-path', 'cmdline', 'environment']:
            self.assertNotIn(private, encoded)
        self.assertEqual(clean['baseline']['source_hashes'], {'shim.py': 'b' * 64})

    def test_raw_remote_error_never_becomes_a_record(self):
        with patch.object(c, 'bounded_output', return_value=('ok', 255, b'private-data')) as run:
            result = c.ssh_sample(self.config, '')
        self.assertEqual(result, {'error': 'ssh_or_remote_failed'})
        argv = run.call_args.args[0]
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertIn('timeout 15s python3', argv[-1])

    def test_real_noisy_child_is_bounded_killed_and_reaped(self):
        created = []
        original = subprocess.Popen
        def track(*args, **kwargs):
            process = original(*args, **kwargs)
            created.append(process)
            return process
        code = "import os,time; os.write(1,b'x'*1000000); time.sleep(30)"
        started = time.monotonic()
        with patch.object(c.subprocess, 'Popen', side_effect=track):
            outcome, code, output = c.bounded_output([sys.executable, '-c', code], 'input', timeout=2)
        self.assertEqual(outcome, 'oversized_response')
        self.assertLessEqual(len(output), 65536)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(created[0].returncode)
        with self.assertRaises(ChildProcessError):
            os.waitpid(created[0].pid, os.WNOHANG)

    def test_real_child_stderr_is_discarded_without_blocking(self):
        code = "import os; os.write(2,b'secret'*200000); os.write(1,b'{}')"
        outcome, status, output = c.bounded_output([sys.executable, '-c', code], 'input', timeout=2)
        self.assertEqual((outcome, status, output), ('ok', 0, b'{}'))

    def test_real_child_timeout_is_killed_and_reaped(self):
        outcome, code, output = c.bounded_output([sys.executable, '-c', 'import time; time.sleep(30)'], 'input', timeout=0.1)
        self.assertEqual(outcome, 'timeout')
        self.assertIsNotNone(code)
        self.assertEqual(output, b'')

    def test_private_state_permissions_and_symlink_refusal(self):
        path = self.directory / 'state.json'
        c.save_private(path, {'ok': True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        link = self.directory / 'link.json'
        link.symlink_to(path)
        with self.assertRaises(OSError):
            c.read_private(link)
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            c.read_private(path)

    def test_main_persists_deadline_before_probe_and_resumes_it(self):
        config_path = self.directory / 'config.json'
        c.save_private(config_path, self.config)
        c_collect = c.collect_once
        def sample(_config, _boot):
            saved = c.read_private(self.directory / 'state.json')
            self.assertEqual(saved['deadline'], 160)
            return observation()
        with patch('sys.argv', ['collector.py', '--config', str(config_path), '--duration', '60', '--once']), patch.object(c.time, 'time', return_value=100), patch.object(c, 'collect_once', side_effect=lambda cfg, st, now: c_collect(cfg, st, now, sample)):
            c.main()
        with patch('sys.argv', ['collector.py', '--config', str(config_path), '--once']), patch.object(c.time, 'time', return_value=161), patch.object(c, 'bounded_output', side_effect=AssertionError('expired resumed main contacted remote')):
            c.main()
        self.assertEqual(c.read_private(self.directory / 'state.json')['status'], 'expired')


if __name__ == '__main__':
    unittest.main()
