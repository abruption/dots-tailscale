"""Real Bash launch environment with inert daemon and OS-service fixtures."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'tailscale-setup.sh').read_text()


@unittest.skipUnless(sys.platform.startswith("linux"), "startup requires Linux Bash semantics")
class StartupProxyEnvironmentTests(unittest.TestCase):
    def test_conflicting_inherited_aliases_cannot_reach_new_daemon(self):
        with tempfile.TemporaryDirectory(prefix='codex-proxy-start-') as tmp:
            d = Path(tmp) / 'install'
            r = Path(tmp) / 'runtime'
            r.mkdir()
            for sub in ('shim', 'sshd', 'state'):
                (d / sub).mkdir(parents=True, exist_ok=True)
            files = ('installed', 'tailscale', 'dots-ts', 'dots_proxy.py', 'shim/shim.py',
                     'shim/ca.crt', 'shim/ca.key', 'shim/bundle.crt', 'sshd/sshd_config',
                     'sshd/ssh_host_ed25519_key', 'sshd/authorized_keys', 'state/tailscaled.state')
            for name in files:
                (d / name).write_bytes(b'inert fixture; not a credential\n')
                (d / name).chmod(0o700)
            captured = d / 'captured.json'
            daemon = d / 'tailscaled'
            capture = ('import json,os,sys;from pathlib import Path;'
                       'keys=("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","SSL_CERT_FILE","RETAIN");'
                       'Path(sys.argv[1]).write_text(json.dumps({k:os.environ.get(k) for k in keys}))')
            daemon.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' +
                              shlex.quote(capture) + ' ' + shlex.quote(str(captured)) + '\n')
            daemon.chmod(0o700)
            identities = {name:(d/name).read_bytes() for name in
                          ('state/tailscaled.state', 'sshd/ssh_host_ed25519_key', 'shim/ca.key')}
            start = re.search(r'^start_all\(\) \{\n.*?^}\n', SOURCE, re.M | re.S).group()
            # The real service syntax check is outside this isolated launch fixture.
            start = start.replace('/usr/sbin/sshd -t -f "$S/sshd_config"', ':')
            script = 'set -euo pipefail\nD=' + shlex.quote(str(d)) + '\nR=' + shlex.quote(str(r)) + '\n'
            script += 'S="$D/sshd"\nSOCK="$R/tailscaled.sock"\nUPSTREAM_PROXY=http://platform.invalid:8889\n'
            script += 'fail() { echo "$*" >&2; exit 1; }\nprivate_dir() { :; }\nrunning() { return 0; }\n'
            script += 'launch_needed() { [[ "$1" == "$D/tailscaled" ]]; }\ntimeout() { return 1; }\nrotate() { :; }\nnohup() { "$@"; }\n'
            script += 'python3() { if [[ "$1" == "$D/dots_proxy.py" ]]; then return 0; fi; '
            script += 'if [[ "$#" == 1 && "$1" == - ]]; then cat >/dev/null; return 0; fi; '
            script += shlex.quote(sys.executable) + ' "$@"; }\n'
            script += start + '\nstart_all\nwait\n'
            env = dict(os.environ, HTTP_PROXY='http://upper.invalid:8889', HTTPS_PROXY='http://upper.invalid:8889',
                       http_proxy='http://lower.invalid:8889', https_proxy='http://lower.invalid:8889',
                       RETAIN='unchanged')
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(captured.exists(), result.stderr + ((r/'tailscaled.log').read_text() if (r/'tailscaled.log').exists() else ' no daemon log'))
            actual = json.loads(captured.read_text())
            for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
                self.assertEqual(actual[name], 'http://127.0.0.1:18089')
            self.assertEqual(actual['SSL_CERT_FILE'], str(d/'shim/bundle.crt'))
            self.assertEqual(actual['RETAIN'], 'unchanged')
            for name, body in identities.items():
                self.assertEqual((d/name).read_bytes(), body)
