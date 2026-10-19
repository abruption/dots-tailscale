"""Exercise the real startup readiness loop with an isolated status CLI."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / 'tailscale-setup.sh').read_text()


def function(name):
    return re.search(r'^' + name + r'\(\) \{\n.*?^}\n', SOURCE, re.M | re.S).group()


class StartupReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-readiness-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.cli = self.directory / 'tailscale'
        self.program = self.directory / 'fake_cli.py'
        self.program.write_text('''import json, pathlib, sys, time
d = pathlib.Path(__file__).parent
assert sys.argv[1:] == ['--socket=fixture.sock', 'status', '--json']
counter = d / 'calls'
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
sequence = json.loads((d / 'sequence.json').read_text())
item = sequence[min(n, len(sequence) - 1)]
if item == 'hang':
    time.sleep(10)
elif item == 'error':
    print('PRIVATE_AUTH_URL', file=sys.stderr)
    sys.exit(1)
elif item == 'invalid':
    print('PRIVATE_INVALID_JSON')
else:
    print(json.dumps({'BackendState': item, 'AuthURL': 'PRIVATE_AUTH_URL'}))
''')
        self.cli.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
                            shlex.quote(str(self.program)) + ' "$@"\n')
        self.cli.chmod(0o700)
        # Identity fixtures must remain byte-for-byte unchanged during waits.
        self.state = self.directory / 'tailscaled.state'
        self.key = self.directory / 'ssh_host_ed25519_key'
        self.state.write_bytes(b'fixture-retained-device-identity')
        self.key.write_bytes(b'fixture-retained-ssh-identity')

    def run_backend(self, sequence, seconds=2, final=False):
        (self.directory / 'sequence.json').write_text(json.dumps(sequence))
        script = 'set -euo pipefail\nD=' + shlex.quote(str(self.directory)) + '\nSOCK=fixture.sock\n'
        script += function('backend')
        script += 'state="$(backend ' + str(seconds) + ')"\n'
        if final:
            script += SOURCE[SOURCE.rindex('case "$state" in'):]
        else:
            script += 'printf "%s\\n" "$state"\n'
        started = time.monotonic()
        result = subprocess.run(['bash', '-c', script], capture_output=True,
                                text=True, timeout=seconds + 3)
        result.elapsed = time.monotonic() - started
        self.assertNotIn('PRIVATE_', result.stdout + result.stderr)
        self.assertEqual(self.state.read_bytes(), b'fixture-retained-device-identity')
        self.assertEqual(self.key.read_bytes(), b'fixture-retained-ssh-identity')
        return result

    def test_no_state_starting_running_without_self_id_is_ready(self):
        result = self.run_backend(['NoState', 'Starting', 'Running'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Running\n')
        self.assertEqual((self.directory / 'calls').read_text(), '3')

    def test_query_errors_and_invalid_json_retry_then_running(self):
        result = self.run_backend(['error', 'invalid', 'Running'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Running\n')

    def test_auth_states_return_to_explicit_auth_caller(self):
        for state in ('NeedsLogin', 'NeedsMachineAuth'):
            with self.subTest(state=state):
                result = self.run_backend([state])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), state)

    def test_final_auth_states_have_distinct_codes(self):
        for state, code, message in (('NeedsLogin', 10, 'needs_auth:'),
                                     ('NeedsMachineAuth', 13, 'needs_machine_auth:')):
            with self.subTest(state=state):
                result = self.run_backend([state], final=True)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(message, result.stdout)

    def test_persistent_no_state_has_bounded_deadline(self):
        for state in ('NoState', 'Starting'):
            with self.subTest(state=state):
                result = self.run_backend([state], seconds=0.7)
                self.assertEqual(result.returncode, 11)
                self.assertIn('readiness_timeout: BackendState=' + state, result.stderr)
                self.assertLess(result.elapsed, 1.7)

    def test_slow_query_uses_remaining_total_budget(self):
        result = self.run_backend(['NoState', 'hang'], seconds=2)
        self.assertEqual(result.returncode, 11)
        self.assertIn('readiness_timeout: BackendState=NoState', result.stderr)
        self.assertLess(result.elapsed, 3)
        self.assertEqual((self.directory / 'calls').read_text(), '2')

    def test_query_failure_and_invalid_json_have_query_error_code(self):
        for state in ('error', 'invalid', 'hang', None):
            with self.subTest(state=state):
                result = self.run_backend([state], seconds=0.2)
                self.assertEqual(result.returncode, 12)
                self.assertIn('query_failed:', result.stderr)

    def test_other_backend_state_is_sanitized_not_auto_reauthenticated(self):
        result = self.run_backend(['PRIVATE_UNEXPECTED_BACKEND'])
        self.assertEqual(result.returncode, 11)
        self.assertIn('unavailable: backend is not ready', result.stderr)

    def test_previously_authenticated_missing_state_stops_before_start(self):
        (self.directory / 'installed').touch()
        (self.directory / 'tailscaled').touch(mode=0o700)
        (self.directory / 'authenticated').touch()
        sshd = self.directory / 'sshd'
        sshd.mkdir()
        retained_key = sshd / 'ssh_host_ed25519_key'
        retained_key.write_bytes(b'existing-host-key')
        script = 'set -euo pipefail\nD=' + shlex.quote(str(self.directory)) + '\nS="$D/sshd"\n'
        script += "fail() { printf '%s\\n' \"$*\" >&2; exit 1; }\n"
        script += function('start_all') + '\nstart_all\n'
        result = subprocess.run(['bash', '-c', script], capture_output=True,
                                text=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertIn('previously authenticated state is missing', result.stderr)
        self.assertFalse((self.directory / 'calls').exists())
        self.assertFalse((self.directory / 'state').exists())
        self.assertEqual(retained_key.read_bytes(), b'existing-host-key')


if __name__ == '__main__':
    unittest.main()
