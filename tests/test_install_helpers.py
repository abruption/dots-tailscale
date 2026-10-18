"""Exercise installer companion staging with inert pre-existing identity fixtures."""
import sys
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "tailscale-setup.sh").read_text()


@unittest.skipUnless(sys.platform.startswith("linux"), "installer requires Linux GNU utilities")
class InstallHelpersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="install-helpers-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.source, self.d, self.r = (root / n for n in ("source", "install", "runtime"))
        for p in (self.source, self.d, self.r):
            p.mkdir(mode=0o700)
        for n in ("shim", "sshd", "state"):
            (self.d / n).mkdir(mode=0o700)
        for n in ("shim.py", "dots-ts", "dots_proxy.py"):
            shutil.copyfile(ROOT / n, self.source / n)
        for n in ("tailscale", "tailscaled"):
            p = self.d / n
            p.write_text("#!/bin/sh\nexit 0\n")
            p.chmod(0o700)
        self.identity = ("shim/ca.key", "shim/ca.crt", "shim/bundle.crt",
                         "sshd/ssh_host_ed25519_key", "sshd/authorized_keys",
                         "state/tailscaled.state")
        for n in self.identity:
            (self.d / n).write_text("inert test placeholder; never a credential\n")
        self.before = {n: (self.d / n).read_bytes() for n in self.identity}
        functions = []
        for name in ("private_dir", "place", "install_all"):
            functions.append(re.search(r"^" + name + r"\(\) \{\n.*?^}\n", SOURCE, re.M | re.S).group())
        self.entry = root / "install-test.sh"
        self.entry.write_text("set -euo pipefail\numask 077\n"
                              "fail() { printf '%s\\n' \"$*\" >&2; exit 1; }\n"
                              "require() { command -v \"$1\" >/dev/null; }\n" +
                              "\n".join(functions) + "\n" +
                              "\n".join(k + "=" + shlex.quote(str(v)) for k, v in
                                        (("SOURCE", self.source), ("D", self.d),
                                         ("R", self.r), ("S", self.d / "sshd"))) +
                              "\ninstall_all\n")

    def install(self):
        return subprocess.run(["bash", str(self.entry)], text=True, capture_output=True, timeout=10)

    def assert_identity_preserved(self):
        for n, value in self.before.items():
            self.assertEqual((self.d / n).read_bytes(), value, n)

    def test_install_places_executable_helpers_and_wrapper(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in ("dots-ts", "dots_proxy.py"):
            self.assertEqual((self.d / n).read_bytes(), (ROOT / n).read_bytes())
            self.assertEqual((self.d / n).stat().st_mode & 0o777, 0o700)
        self.assertIn('"$DOTS_TS_DIR/setup.sh"', (self.d / "start.sh").read_text())
        self.assertFalse((self.d / "outbound-proxy.json").exists())
        self.assert_identity_preserved()

    def test_missing_companion_stops_without_install_marker(self):
        (self.source / "dots_proxy.py").unlink()
        r = self.install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("missing companions", r.stderr)
        self.assertFalse((self.d / "installed").exists())
        self.assert_identity_preserved()

    def test_conflicting_helper_is_not_overwritten(self):
        (self.d / "dots-ts").write_text("existing custom helper\n")
        r = self.install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("existing file differs", r.stderr)
        self.assertEqual((self.d / "dots-ts").read_text(), "existing custom helper\n")
        self.assert_identity_preserved()


if __name__ == "__main__":
    unittest.main()
