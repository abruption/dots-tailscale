"""Real, offline OpenSSH host-key onboarding with disposable credentials.

sshd runs in inetd mode behind ProxyCommand: no listener, DNS, tailnet, or live
service is used. Both SSH configurations, identities, and known_hosts are
explicit fixture files. An internal SFTP REALPATH command proves authenticated
execution without running the account's shell or user SSH startup scripts.
"""

import json
import os
import pathlib
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest

try:
    import pwd
except ImportError:
    pwd = None


SSH = shutil.which("ssh")
SSHD = shutil.which("sshd") or (
    "/usr/sbin/sshd" if os.access("/usr/sbin/sshd", os.X_OK) else None
)
KEYGEN = shutil.which("ssh-keygen")
HOST = "ssh-onboarding.invalid"
TIMEOUT = 10


@unittest.skipUnless(pwd and SSH and SSHD and KEYGEN,
                     "requires POSIX and OpenSSH client/server tools")
class SSHOnboardingTests(unittest.TestCase):
    def setUp(self):
        if os.geteuid() == 0:
            self.skipTest("run the isolated sshd fixture as an unprivileged user")
        temporary = tempfile.TemporaryDirectory(prefix="ssh-onboarding-")
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.username = pwd.getpwuid(os.getuid()).pw_name
        self.host_key = self.make_key("host")
        self.client_key = self.make_key("client")
        self.known_hosts = self.root / "known_hosts"
        self.known_hosts.touch(mode=0o600)
        self.authorized_keys = self.root / "authorized_keys"
        self.authorized_keys.write_text(
            "restrict " + self.client_key.with_suffix(".pub").read_text()
        )
        self.config = self.root / "sshd_config"
        self.write_server_config(self.host_key)
        self.audit = self.root / "askpass.jsonl"
        self.askpass = self.root / "askpass"
        self.askpass.write_text(f"#!{sys.executable}\n" + '''
import json
import os
import re
import sys

prompt = sys.argv[1]
match = re.search(r"ED25519 key fingerprint is (SHA256:[A-Za-z0-9+/]+)\\.", prompt)
actual = match.group(1) if match else None
expected = os.environ["ONBOARDING_EXPECTED_FINGERPRINT"]
answer = "yes" if actual == expected else "no"
with open(os.environ["ONBOARDING_AUDIT"], "a") as audit:
    audit.write(json.dumps({"actual": actual, "expected": expected,
                            "answer": answer, "prompt": prompt}) + "\\n")
print(answer)
''')
        self.askpass.chmod(0o700)
        # This models obtaining the host fingerprint through a trusted channel,
        # independently of the SSH connection and the key it presents.
        self.expected_fingerprint = self.fingerprint(self.host_key.with_suffix(".pub"))

    def make_key(self, name):
        key = self.root / name
        subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       check=True, capture_output=True, timeout=TIMEOUT)
        return key

    def fingerprint(self, path):
        result = subprocess.run([KEYGEN, "-lf", str(path), "-E", "sha256"],
                                check=True, capture_output=True, text=True,
                                timeout=TIMEOUT)
        return result.stdout.split()[1]

    def write_server_config(self, host_key):
        self.config.write_text(f"""HostKey {host_key}
AuthorizedKeysFile {self.authorized_keys}
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes yes
UseDNS no
PermitUserEnvironment no
PermitUserRC no
DisableForwarding yes
PermitTTY no
LoginGraceTime 10
LogLevel ERROR
Subsystem sftp internal-sftp -d {self.root}
ForceCommand internal-sftp -d {self.root}
""")
        subprocess.run([SSHD, "-t", "-f", str(self.config)], check=True,
                       capture_output=True, timeout=TIMEOUT)

    def prompts(self):
        if not self.audit.exists():
            return []
        return [json.loads(line) for line in self.audit.read_text().splitlines()]

    @staticmethod
    def packets(data):
        """Return complete SFTP packet payloads, retaining any partial suffix."""
        packets = []
        while len(data) >= 4:
            length = struct.unpack(">I", data[:4])[0]
            if length > 65536:
                raise AssertionError("unexpectedly large SFTP response")
            if len(data) < length + 4:
                break
            packets.append(data[4:4 + length])
            data = data[4 + length:]
        return packets, data

    def ssh(self, strict, expected=None):
        options = {
            "ProxyCommand": shlex.join([SSHD, "-i", "-e", "-f", str(self.config)]),
            "UserKnownHostsFile": str(self.known_hosts),
            "GlobalKnownHostsFile": "/dev/null",
            "StrictHostKeyChecking": strict,
            "HashKnownHosts": "yes",
            "UpdateHostKeys": "no",
            "VerifyHostKeyDNS": "no",
            "CheckHostIP": "no",
            "CanonicalizeHostname": "no",
            "HostKeyAlgorithms": "ssh-ed25519",
            "IdentitiesOnly": "yes",
            "IdentityAgent": "none",
            "PreferredAuthentications": "publickey",
            "PasswordAuthentication": "no",
            "KbdInteractiveAuthentication": "no",
            "ControlMaster": "no",
            "ControlPath": "none",
            "ClearAllForwardings": "yes",
            "StdinNull": "no",
            "ConnectTimeout": "5",
        }
        command = [SSH, "-F", "/dev/null", "-T", "-i", str(self.client_key)]
        for key, value in options.items():
            command.extend(["-o", f"{key}={value}"])
        command.extend(["-s", f"{self.username}@{HOST}", "sftp"])
        # Do not inherit SSH_AUTH_SOCK, SSH_* configuration, or shell startup
        # variables. Askpass is used only to emulate the human verification step.
        environment = {
            "PATH": os.defpath,
            "HOME": str(self.root),
            "LANG": "C",
            "LC_ALL": "C",
            "DISPLAY": "isolated:0",
            "SSH_ASKPASS": str(self.askpass),
            "SSH_ASKPASS_REQUIRE": "force",
            "ONBOARDING_EXPECTED_FINGERPRINT": expected or self.expected_fingerprint,
            "ONBOARDING_AUDIT": str(self.audit),
        }
        # SFTP v3 INIT followed by REALPATH(".") with request ID 1.
        request = struct.pack(">IBI", 5, 1, 3) + struct.pack(">IBII", 10, 16, 1, 1) + b"."
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=errors,
                                       env=environment, start_new_session=True)
            output = b""
            try:
                process.stdin.write(request)
                process.stdin.flush()
                deadline = time.monotonic() + TIMEOUT
                # Keep stdin open until the responses arrive. Closing it before
                # the SSH session opens can discard buffered subsystem input.
                while len(self.packets(output)[0]) < 2:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                        self.fail("isolated SSH command did not finish before its deadline")
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    output += chunk
                tail, _ = process.communicate(timeout=TIMEOUT)
                output += tail
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate(timeout=3)
                process.stdin.close()
                process.stdout.close()
            errors.seek(0)
            return subprocess.CompletedProcess(command, process.returncode, output,
                                               errors.read().decode(errors="replace"))

    def assert_authenticated_command(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        packets, partial = self.packets(result.stdout)
        self.assertEqual(partial, b"")
        self.assertEqual(len(packets), 2, result.stderr)
        self.assertEqual(packets[0][:5], struct.pack(">BI", 2, 3))  # VERSION 3
        self.assertEqual(packets[1][:9], struct.pack(">BII", 104, 1, 1))  # NAME, ID, count
        size = struct.unpack(">I", packets[1][9:13])[0]
        self.assertEqual(packets[1][13:13 + size].decode(), str(self.root))

    def enroll(self):
        self.assertEqual(self.known_hosts.read_bytes(), b"")
        self.assert_authenticated_command(self.ssh("ask"))
        prompts = self.prompts()
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["actual"], self.expected_fingerprint)
        self.assertEqual(prompts[0]["expected"], self.expected_fingerprint)
        self.assertEqual(prompts[0]["answer"], "yes")
        self.assertEqual(self.fingerprint(self.known_hosts), self.expected_fingerprint)

    def test_verified_first_use_then_strict_authenticated_command(self):
        # The normal strict setting cannot silently bootstrap an unknown host.
        rejected = self.ssh("yes")
        self.assertEqual(rejected.returncode, 255, rejected.stderr)
        self.assertIn("Host key verification failed", rejected.stderr)
        self.assertEqual(self.known_hosts.read_bytes(), b"")
        self.assertEqual(self.prompts(), [])

        self.enroll()
        enrolled = self.known_hosts.read_bytes()
        self.assert_authenticated_command(self.ssh("yes"))
        self.assertEqual(self.known_hosts.read_bytes(), enrolled)
        self.assertEqual(len(self.prompts()), 1)  # No second trust prompt.

    def test_wrong_independent_fingerprint_declines_without_enrollment(self):
        other_key = self.make_key("other-host")
        wrong = self.fingerprint(other_key.with_suffix(".pub"))
        self.assertNotEqual(wrong, self.expected_fingerprint)
        result = self.ssh("ask", expected=wrong)
        self.assertEqual(result.returncode, 255, result.stderr)
        self.assertIn("Host key verification failed", result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(self.known_hosts.read_bytes(), b"")
        prompts = self.prompts()
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["actual"], self.expected_fingerprint)
        self.assertEqual(prompts[0]["expected"], wrong)
        self.assertEqual(prompts[0]["answer"], "no")

    def test_changed_host_key_rejected_without_replacing_trusted_key(self):
        self.enroll()
        enrolled = self.known_hosts.read_bytes()
        replacement = self.make_key("replacement-host")
        self.write_server_config(replacement)
        for strict in ("yes", "ask"):
            with self.subTest(strict=strict):
                result = self.ssh(strict)
                self.assertEqual(result.returncode, 255, result.stderr)
                self.assertIn("REMOTE HOST IDENTIFICATION HAS CHANGED", result.stderr)
                self.assertIn("Host key verification failed", result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(self.known_hosts.read_bytes(), enrolled)
                self.assertEqual(len(self.prompts()), 1)


if __name__ == "__main__":
    unittest.main()
