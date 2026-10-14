"""Exercise the actual embedded process detector without inspecting host /proc."""
import contextlib
import io
import pathlib
import re
import subprocess
import types
import unittest
from unittest import mock


SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "tailscale-setup.sh").read_text()
MATCH = re.search(r"running\(\) \{\n.*?<<'PY'\n(.*?)\nPY\n", SOURCE, re.S)
if MATCH is None:
    raise RuntimeError("embedded running() detector was not found")
DETECTOR = compile(MATCH.group(1), "tailscale-setup.sh:running", "exec")
TARGET = "/workspace/.dots-tailscale/tailscaled"


class ProcPath:
    def __init__(self, name, records):
        self.path = pathlib.PurePosixPath(name)
        self.records = records

    @property
    def name(self):
        return self.path.name

    def __truediv__(self, part):
        return ProcPath(str(self.path / part), self.records)

    def __fspath__(self):
        return str(self.path)

    def iterdir(self):
        return iter(ProcPath("/proc/" + pid, self.records) for pid in self.records)

    def value(self, key):
        pid = self.path.parts[2]
        value = self.records[pid][key]
        if isinstance(value, Exception):
            raise value
        return value

    def stat(self):
        return types.SimpleNamespace(st_uid=self.value("uid"))

    def read_bytes(self):
        return self.value("cmdline")


def process(args=(), *, uid=1000, exe=PermissionError(), cmdline=None):
    return dict(uid=uid, exe=exe, cmdline=(
        b"\0".join(arg.encode() for arg in args) + b"\0"
        if cmdline is None else cmdline
    ))


class ProcessDetectionTests(unittest.TestCase):
    def detect(self, records, needle=TARGET):
        stderr = io.StringIO()
        with mock.patch("pathlib.Path", side_effect=lambda p: ProcPath(p, records)), \
                mock.patch("os.getuid", return_value=1000), \
                mock.patch("os.getpid", return_value=999), \
                mock.patch("os.readlink", side_effect=lambda p: p.value("exe")), \
                mock.patch("sys.argv", ["-", needle]), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as result:
                exec(DETECTOR, {})
        self.assertEqual(stderr.getvalue(), "")
        return result.exception.code

    def test_unreadable_pid_two_does_not_hide_later_match(self):
        for blocked in (process(cmdline=PermissionError()), process(["unrelated"])):
            with self.subTest(blocked=blocked):
                self.assertEqual(self.detect({
                    "2": blocked, "42": process([TARGET]),
                }), 0)

    def test_matching_argv_with_unreadable_executable(self):
        self.assertEqual(self.detect({"42": process([TARGET, "--port=0"])}), 0)

    def test_matching_executable(self):
        self.assertEqual(self.detect({"42": process(["renamed"], exe=TARGET)}), 0)

    def test_process_disappeared_during_inspection(self):
        self.assertEqual(self.detect({
            "2": process(cmdline=FileNotFoundError()), "42": process([TARGET]),
        }), 0)

    def test_other_user_and_non_numeric_entry_are_ignored(self):
        self.assertEqual(self.detect({
            "self": {}, "42": process([TARGET], uid=1001),
        }), 1)

    def test_self_is_excluded(self):
        self.assertEqual(self.detect({"999": process(["python3", "-", TARGET])}), 1)

    def test_no_match(self):
        self.assertEqual(self.detect({"2": process(["unrelated"])}), 1)

    def test_sshd_rewritten_title_with_unreadable_executable(self):
        config = "/workspace/.dots-tailscale/sshd/sshd_config"
        self.assertEqual(self.detect({"42": process([
            "sshd: /usr/sbin/sshd -f " + config + " [listener] 0 of 10-100 startups"
        ])}, config), 0)

    def test_regular_argv_substring_is_not_a_match(self):
        for arg in (TARGET + ".old", "echo " + TARGET, "--path=" + TARGET):
            with self.subTest(arg=arg):
                self.assertEqual(self.detect({"42": process([arg])}), 1)

    def test_data_arguments_are_not_services(self):
        for target in (TARGET, "/workspace/.dots-tailscale/shim/shim.py",
                       "/workspace/.dots-tailscale/sshd/sshd_config"):
            for argv in (["vim", target], ["less", target], ["other", target],
                         ["python3", "-c", "import time; time.sleep(4)", target],
                         ["python3", "-m", "module", target],
                         ["python3", "another.py", target]):
                with self.subTest(target=target, argv=argv):
                    self.assertEqual(self.detect({"42": process(argv)}, target), 1)

    def test_python_script_operand(self):
        target = "/workspace/.dots-tailscale/shim/shim.py"
        for prefix in (["python3"], ["/usr/bin/python3.13", "-u", "-B"],
                       ["python3", "-W", "ignore", "-X", "dev", "--"]):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.detect({"42": process(prefix + [target])}, target), 0)
        self.assertEqual(self.detect({"42": process(["python3", target], exe="/usr/bin/vim")}, target), 1)

    def test_sshd_config_operand(self):
        target = "/workspace/.dots-tailscale/sshd/sshd_config"
        for argv in (["/usr/sbin/sshd", "-D", "-f", target],
                     ["sshd", "-E", "/tmp/log", "-f" + target]):
            with self.subTest(argv=argv):
                self.assertEqual(self.detect({"42": process(argv)}, target), 0)
        for argv in (["sshd", target], ["sshd", "-o", "-f", target],
                     ["sshd", "-f", target + ".old"],
                     ["sshd: /usr/sbin/sshd -f " + target + " [session]"],
                     ["sshd: /usr/sbin/sshd -o " + target + " [listener]"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.detect({"42": process(argv)}, target), 1)

    def test_readable_executable_overrules_spoofed_argv_zero(self):
        self.assertEqual(self.detect({"42": process([TARGET], exe="/usr/bin/less")}), 1)

    def test_unreadable_and_disappearing_records(self):
        for record in (process(uid=PermissionError()), process(uid=ProcessLookupError()),
                       process(cmdline=ProcessLookupError(), exe=ProcessLookupError())):
            self.assertEqual(self.detect({"2": record, "42": process([TARGET])}), 0)

    def test_false_positive_only_allows_launch(self):
        launch = re.search(r"(launch_needed\(\) \{.*?\n\})", SOURCE, re.S).group(1)
        for target in (TARGET, "/workspace/.dots-tailscale/shim/shim.py",
                       "/workspace/.dots-tailscale/sshd/sshd_config"):
            with self.subTest(target=target):
                rc = self.detect({"42": process(["less", target])}, target)
                result = subprocess.run(
                    ["bash", "-c", "running() { return " + str(rc) + "; }; " +
                     launch + '; if launch_needed "$1"; then echo launched; fi', "-", target],
                    capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout, "launched\n")


if __name__ == "__main__":
    unittest.main()
