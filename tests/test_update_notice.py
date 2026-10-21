"""Opt-in cache-only notices, conservative storage, and successful-start isolation."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_update_check import tool, release_fixture

ROOT = Path(__file__).resolve().parents[1]


class InteractiveOutput(io.StringIO):
    def isatty(self): return True


class UpdateNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="update-notice-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        _, raw = release_fixture("0.3.9")
        for source, installed in tool.CORE_FILES.items():
            path = self.directory / installed
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.encode())
        (self.directory / tool.MANIFEST).write_bytes(raw)
        self.release = {"tag": "v0.4.0", "version": "0.4.0"}

    def enable_and_refresh(self):
        tool.notification_configure(self.directory, True, 24)
        with mock.patch.object(tool, "fetch_release", return_value=self.release):
            report = tool.notification_refresh(self.directory)
        self.assertEqual(report["status"], "refreshed")
        return report

    def notify(self, interactive=True):
        output = InteractiveOutput() if interactive else io.StringIO()
        with contextlib.redirect_stderr(output), mock.patch.object(tool, "fetch_release", side_effect=AssertionError("notice network")):
            tool.notification_notify(self.directory)
        return output.getvalue()

    def test_default_is_disabled_and_has_no_storage_side_effect(self):
        report = tool.notification_show(self.directory)
        self.assertFalse(report["enabled"])
        self.assertTrue(report["stale"])
        self.assertEqual(tool.notification_refresh(self.directory)["status"], "disabled")
        self.assertEqual(self.notify(), "")
        self.assertFalse((self.directory / tool.NOTICE_DIRECTORY).exists())

    def test_new_release_notice_deduplication_and_disable(self):
        self.enable_and_refresh()
        self.assertIn("v0.4.0", self.notify())
        self.assertEqual(self.notify(), "")
        self.assertEqual(tool.notification_show(self.directory)["last_notified"], "v0.4.0")
        tool.notification_configure(self.directory, False)
        self.assertEqual(self.notify(), "")
        with mock.patch.object(tool, "fetch_release", side_effect=AssertionError("disabled network")):
            self.assertEqual(tool.notification_refresh(self.directory)["status"], "disabled")

    def test_interval_expiry_suppresses_stale_notice_and_force_refresh(self):
        before = self.enable_and_refresh()
        with mock.patch.object(tool, "fetch_release", side_effect=AssertionError("fresh network")):
            self.assertEqual(tool.notification_refresh(self.directory)["status"], "cached")
        with mock.patch.object(tool.time, "time", return_value=before["checked_at"] + 24 * 3600):
            self.assertTrue(tool.notification_show(self.directory)["stale"])
            self.assertEqual(self.notify(), "")
        with mock.patch.object(tool, "fetch_release", return_value=self.release) as fetch:
            self.assertEqual(tool.notification_refresh(self.directory, force=True)["status"], "refreshed")
            fetch.assert_called_once()

    def test_failed_refresh_retains_last_success_and_marks_stale(self):
        before = self.enable_and_refresh()
        for code in ("offline", "rate_limited", "tls_error"):
            with mock.patch.object(tool.time, "time", return_value=before["checked_at"] + 10), \
                    mock.patch.object(tool, "fetch_release", side_effect=tool.UpdateError(code)):
                after = tool.notification_refresh(self.directory, force=True)
            self.assertEqual(after["checked_at"], before["checked_at"])
            self.assertEqual(after["release"], before["release"])
            self.assertEqual(after["last_error"], code)
            self.assertTrue(after["stale"])
            self.assertEqual(after["status"], "refresh_failed")
            self.assertEqual(self.notify(), "")

    def test_redirected_stderr_json_and_raw_cli_remain_clean(self):
        self.enable_and_refresh()
        self.assertEqual(self.notify(interactive=False), "")
        self.assertIsNone(tool.notification_show(self.directory)["last_notified"])
        output = io.StringIO()
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.directory)), \
                mock.patch.object(tool, "notification_notify", side_effect=AssertionError("unexpected notice")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(tool.main(["update", "notifications", "show", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["release"], self.release)
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.directory)), \
                mock.patch.object(tool, "notification_notify", side_effect=AssertionError("unexpected notice")), \
                mock.patch.object(tool.os, "execv", side_effect=RuntimeError("exec boundary")):
            with self.assertRaisesRegex(RuntimeError, "exec boundary"): tool.main(["cli", "status", "--json"])

    def test_readonly_storage_failure_is_silent_and_successful(self):
        self.enable_and_refresh()
        with mock.patch.object(tool, "notice_write", side_effect=OSError("PRIVATE readonly")):
            self.assertEqual(self.notify(), "")
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.directory)), \
                mock.patch.object(tool, "notice_store", side_effect=tool.NoticeError("storage_unavailable")):
            self.assertEqual(tool.main(["update", "notifications", "notify"]), 0)
        self.assertIsNone(tool.notification_show(self.directory)["last_notified"])

    def test_unexpected_notice_error_is_silent(self):
        self.enable_and_refresh()
        with mock.patch.object(tool, "package_report", side_effect=TypeError("PRIVATE error")):
            self.assertEqual(self.notify(), "")

    def test_storage_rejects_symlinks_hardlinks_modes_and_hostile_cache(self):
        self.enable_and_refresh()
        folder = self.directory / tool.NOTICE_DIRECTORY
        cache = folder / "cache.json"
        original = cache.read_bytes()
        cache.unlink(); cache.symlink_to(self.directory / tool.MANIFEST)
        self.assertEqual(self.notify(), "")
        cache.unlink(); cache.write_bytes(original); cache.chmod(0o600)
        os.link(cache, folder / "hardlink")
        self.assertEqual(self.notify(), "")
        (folder / "hardlink").unlink()
        cache.chmod(0o644)
        self.assertEqual(self.notify(), "")
        cache.chmod(0o600)
        hostile = json.loads(original); hostile["last_error"] = {"PRIVATE": "TOKEN"}
        cache.write_text(json.dumps(hostile))
        self.assertEqual(self.notify(), "")
        cache.write_bytes(original)
        folder.chmod(0o755)
        self.assertEqual(self.notify(), "")

    def test_interval_validation_and_safe_json_errors(self):
        for interval in (0, 721, True):
            with self.assertRaises(tool.NoticeError): tool.notification_configure(self.directory, True, interval)
        output = io.StringIO()
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.directory)), contextlib.redirect_stdout(output):
            self.assertEqual(tool.main(["update", "notifications", "configure", "--enable", "--interval-hours", "0", "--json"]), 1)
        self.assertEqual(json.loads(output.getvalue()), {"error": "invalid_settings"})

    def test_unknown_or_modified_package_does_not_get_update_notice(self):
        self.enable_and_refresh()
        (self.directory / "dots-ts").write_text("modified source")
        self.assertEqual(self.notify(), "")
        (self.directory / tool.MANIFEST).unlink()
        self.assertEqual(self.notify(), "")

    def test_hook_is_only_after_success_for_interactive_start_and_has_hard_timeout(self):
        source = (ROOT / "tailscale-setup.sh").read_text()
        hook = source[source.index("# Opt-in cached notice only."):]
        self.assertIn('if [[ "$MODE" = start && -t 2 ]]', hook)
        self.assertIn('timeout --foreground --kill-after=1s 1s python3 "$D/dots-ts" update notifications notify >/dev/null || true', hook)
        self.assertNotIn(" refresh", hook)
        self.assertLess(source.index("if [[ \"${AUTH_COMMAND_STATUS:-0}\" != 0 ]]"), source.index("# Opt-in cached notice only."))


if __name__ == "__main__": unittest.main()
