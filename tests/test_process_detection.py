"""Exercise the actual embedded process detector without inspecting host /proc."""
import contextlib
import io
import pathlib
import re
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


if __name__ == "__main__":
    unittest.main()
