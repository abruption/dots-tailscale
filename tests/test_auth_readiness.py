"""Exercise real explicit-auth orchestration with delayed local API fixtures."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / 'tailscale-setup.sh').read_text()

def function(name):
    return re.search(r'^' + name + r'\(\) \{\n.*?^}\n', SOURCE, re.M | re.S).group()

class AuthReadinessTests(unittest.TestCase):
    def flow(self, mode, before, after='Running', up_exit=0, key=True, budget=2):
        with tempfile.TemporaryDirectory(prefix='codex-auth-ready-') as tmp:
            root = Path(tmp)
            (root / 'fixture.json').write_text(json.dumps({'before': before, 'after': after, 'up_exit': up_exit}))
            dummy = root / 'key'
            dummy.write_text('dummy-never-a-real-credential\n');dummy.chmod(0o600)
            program = root / 'cli.py'
            program.write_text('''import json,pathlib,sys
r=pathlib.Path(__file__).parent
f=json.loads((r/'fixture.json').read_text())
if sys.argv[2]=='status':
 n=int((r/'calls').read_text()) if (r/'calls').exists() else 0
 (r/'calls').write_text(str(n+1))
 value=f['after'] if (r/'up').exists() else f['before'][min(n,len(f['before'])-1)]
 if value=='unavailable':sys.exit(1)
 print(json.dumps({'BackendState':value,'AuthURL':'PRIVATE_AUTH_URL'}))
else:
 assert sys.argv[2]=='up'
 assert (r/'calls').exists(), 'up raced the local API'
 n=int((r/'calls').read_text())
 assert n>=len(f['before']), 'up ran before successful status'
 (r/'up').write_text(str(n))
 sys.exit(f['up_exit'])
''')
            cli = root / 'tailscale'
            cli.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(program))+' "$@"\n');cli.chmod(0o700)
            # Only initialization and the outer timeout executable are fixtures.
            # The actual backend/auth functions and main auth/case flow execute.
            script='set -euo pipefail\n'
            script+='D='+shlex.quote(str(root))+'\nSOCK=fixture.sock\nNODE_HOSTNAME=fixture\nMODE='+mode+'\n'
            script+='fail() { printf "%s\\n" "$*" >&2; exit 1; }\nstart_all() { :; }\ntimeout() { shift; "$@"; }\n'
            script+=function('backend')+function('authenticate_with_key')
            script+=SOURCE[SOURCE.index('AUTH_COMMAND_STATUS=0\nstart_all\n'):]
            script=script.replace('backend 30','backend '+str(budget))
            env=dict(os.environ);env.pop('DOTS_TS_AUTH_KEY_FILE',None)
            if key:env['DOTS_TS_AUTH_KEY_FILE']=str(dummy)
            result=subprocess.run(['bash','-c',script],capture_output=True,text=True,env=env,timeout=8)
            self.assertNotIn('PRIVATE_AUTH_URL', result.stdout+result.stderr)
            self.assertEqual(dummy.read_text(),'dummy-never-a-real-credential\n')
            result.up_called=(root/'up').exists()
            result.authenticated=(root/'authenticated').exists()
            return result

    def test_login_waits_for_api_and_allows_no_state_before_up(self):
        result=self.flow('login',['unavailable','unavailable','NoState'])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(result.up_called)
        self.assertTrue(result.authenticated)

    def test_failed_up_is_followed_by_machine_approval_mapping(self):
        for mode in ('login','auth'):
            for failure in (2,124):
                with self.subTest(mode=mode,failure=failure):
                    result=self.flow(mode,['NeedsLogin'],after='NeedsMachineAuth',up_exit=failure)
                    self.assertEqual(result.returncode,13,result.stderr)
                    self.assertIn('needs_machine_auth:',result.stdout)
                    self.assertTrue(result.up_called)
                    self.assertFalse(result.authenticated)

    def test_existing_approval_skips_up_and_key_read(self):
        for mode in ('login','auth'):
            with self.subTest(mode=mode):
                result=self.flow(mode,['NeedsMachineAuth'],key=False)
                self.assertEqual(result.returncode,13,result.stderr)
                self.assertFalse(result.up_called)

    def test_failed_up_with_running_backend_is_not_reported_successful(self):
        for mode in ('login','auth'):
            with self.subTest(mode=mode):
                result=self.flow(mode,['NoState'],after='Running',up_exit=42)
                self.assertEqual(result.returncode,42,result.stderr)
                self.assertIn('authentication_command_failed:',result.stderr)
                self.assertFalse(result.authenticated)

    def test_inaccessible_api_does_not_start_interactive_up(self):
        result=self.flow('login',['unavailable'],budget=.4)
        self.assertEqual(result.returncode,12,result.stderr)
        self.assertFalse(result.up_called)

if __name__=='__main__':unittest.main()
