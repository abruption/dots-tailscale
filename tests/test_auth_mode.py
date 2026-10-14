"""Test auth-key login locally with mocked Tailscale; never contact a tailnet."""
import os
import pathlib
import re
import subprocess
import tempfile
import unittest


SOURCE_PATH = pathlib.Path(__file__).resolve().parents[1] / "tailscale-setup.sh"
TEMP_ROOT = pathlib.Path(tempfile.gettempdir())
DUMMY_KEY = "test-placeholder-never-a-real-auth-key"


def shell_function(name):
    source = SOURCE_PATH.read_text()
    match = re.search(r"^" + re.escape(name) + r"\(\) \{\n.*?^}\n", source, re.M | re.S)
    if match is None:
        raise AssertionError(f"Missing shell function: {name}")
    return match.group(0)


class AuthModeTests(unittest.TestCase):
    def setUp(self):
        TEMP_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="codex-auth-tests-", dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name).resolve()
        self.key = self.directory / "auth-key"
        self.key.write_text(DUMMY_KEY + "\n")
        self.key.chmod(0o600)

    def authenticate(self, state="NeedsLogin", key=None):
        script = """set -euo pipefail
fail() { printf 'failed: %s\\n' "$*" >&2; exit 1; }
backend() { printf '%s\\n' "$TEST_BACKEND"; }
timeout() { printf '%s\\n' "$@"; }
D=/workspace/.dots-tailscale
SOCK=/tmp/dots-tailscale-1000/tailscaled.sock
NODE_HOSTNAME=dots-sandbox
"""
        if re.search(r"^validate_auth_file\(\) \{", SOURCE_PATH.read_text(), re.M):
            script += shell_function("validate_auth_file")
        script += shell_function("authenticate_with_key") + "\nauthenticate_with_key\n"
        environment = dict(os.environ, TEST_BACKEND=state)
        environment.pop("DOTS_TS_AUTH_KEY_FILE", None)
        if key is not None:
            environment["DOTS_TS_AUTH_KEY_FILE"] = str(key)
        return subprocess.run(["bash", "-c", script], env=environment,
                              text=True, capture_output=True, timeout=10)

    def test_running_does_not_need_a_key(self):
        result = self.authenticate(state="Running")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_key_when_login_required_fails(self):
        result = self.authenticate()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("--auth-key", result.stdout)

    def test_private_key_passed_as_file_not_value(self):
        for mode in (0o600, 0o400):
            with self.subTest(mode=oct(mode)):
                self.key.chmod(mode)
                result = self.authenticate(key=self.key)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = result.stdout.splitlines()
                self.assertIn("--auth-key=file:" + str(self.key), args)
                self.assertIn("--timeout=45s", args)
                self.assertIn("--ssh=false", args)
                self.assertNotIn(DUMMY_KEY, result.stdout + result.stderr)

    def test_group_other_or_executable_permissions_fail(self):
        for mode in (0o644, 0o640, 0o604, 0o700):
            with self.subTest(mode=oct(mode)):
                self.key.chmod(mode)
                result = self.authenticate(key=self.key)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("--auth-key", result.stdout)
                self.assertNotIn(DUMMY_KEY, result.stdout + result.stderr)

    def test_empty_key_fails(self):
        self.key.write_text("")
        result = self.authenticate(key=self.key)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("--auth-key", result.stdout)

    def test_key_symlink_fails(self):
        alias = self.directory / "alias"
        alias.symlink_to(self.key)
        result = self.authenticate(key=alias)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("--auth-key", result.stdout)

    def test_parent_symlink_fails(self):
        real = self.directory / "real"
        real.mkdir(mode=0o700)
        self.key.rename(real / "auth-key")
        alias = self.directory / "alias"
        alias.symlink_to(real, target_is_directory=True)
        result = self.authenticate(key=alias / "auth-key")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("--auth-key", result.stdout)

    def test_directory_is_not_a_key_file(self):
        result = self.authenticate(key=self.directory)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("--auth-key", result.stdout)


if __name__ == "__main__":
    unittest.main()
