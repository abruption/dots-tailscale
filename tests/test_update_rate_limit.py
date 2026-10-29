"""Offline rate-limit propagation, bounded backoff and read-only bundle checks."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock

from test_update_check import tool, release_fixture


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rate-limit-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.directory.chmod(0o700)

    def test_primary_and_secondary_headers_are_numeric_and_bounded(self):
        for status, headers in ((403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1200"}),
                                (429, {"Retry-After": "125"})):
            opener = mock.Mock()
            opener.open.side_effect = urllib.error.HTTPError("PRIVATE", status, "PRIVATE", headers, None)
            with mock.patch.object(tool.time, "time", return_value=1000), \
                    mock.patch.object(tool.urllib.request, "build_opener", return_value=opener), \
                    self.assertRaises(tool.UpdateError) as caught:
                tool._network_fetch(tool.UPDATE_API + "/latest", None, 100)
            self.assertEqual(caught.exception.code, "rate_limited")
            self.assertGreater(caught.exception.retry["retry_at"], 1000)
            self.assertNotIn("PRIVATE", json.dumps(caught.exception.retry))
        self.assertEqual(tool.retry_headers({"Retry-After": "PRIVATE", "X-RateLimit-Reset": "9999999999999999"}, 1000),
                         {"retry_at": 1060})
        self.assertEqual(tool.validated_retry({"retry_at": True, "secret": "PRIVATE"}, 1000), {})

    def test_child_error_keeps_only_valid_retry_metadata(self):
        reply = {"error": "rate_limited", "retry": {"retry_after_seconds": 60, "retry_at": 1060, "raw": "PRIVATE"}}
        result = subprocess.CompletedProcess([], 0, json.dumps(reply).encode())
        with mock.patch.object(tool.time, "time", return_value=1000), \
                mock.patch.object(tool.subprocess, "run", return_value=result), self.assertRaises(tool.UpdateError) as caught:
            tool.fetch_bytes(tool.UPDATE_API + "/latest")
        self.assertEqual(caught.exception.retry, {"retry_after_seconds": 60, "retry_at": 1060})

    def test_saved_limit_blocks_check_apply_fetch_and_expires_without_success_cache(self):
        limited = tool.UpdateError("rate_limited")
        with mock.patch.object(tool.time, "time", return_value=1000), \
                mock.patch.object(tool, "fetch_release", side_effect=limited) as fetch:
            first = tool.update_check(self.directory, "v0.7.1")
            second = tool.update_check(self.directory, "v0.8.0")
            with self.assertRaises(tool.UpdateError):
                tool.rate_limit_release(self.directory, "v0.7.1")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first["evidence"], "github_live")
        self.assertEqual(second["evidence"], "rate_limit_cache")
        self.assertEqual(second["status"], "check_failed")
        self.assertEqual(tool.update_check_exit(second), 2)
        metadata, raw = release_fixture("0.8.0")
        release = dict(version="0.8.0", tag="v0.8.0")
        with mock.patch.object(tool.time, "time", return_value=1061), \
                mock.patch.object(tool, "fetch_release", return_value=release) as fetch:
            self.assertEqual(tool.update_check(self.directory)["evidence"], "github_live")
            fetch.assert_called_once()

    def test_known_limit_is_scoped_to_explicit_proxy(self):
        with mock.patch.object(tool.time, "time", return_value=1000), \
                mock.patch.object(tool, "fetch_release", side_effect=tool.UpdateError("rate_limited")):
            tool.update_check(self.directory, proxy="http://browser-proxy:8889")
        with mock.patch.object(tool, "fetch_release", side_effect=tool.UpdateError("offline")) as fetch:
            report = tool.update_check(self.directory)
        fetch.assert_called_once()
        self.assertEqual(report["error"], "offline")

    def test_unwritable_store_preserves_error_and_reports_missing_backoff(self):
        with mock.patch.object(tool, "notice_store", side_effect=tool.NoticeError("storage_unavailable")), \
                mock.patch.object(tool, "fetch_release", side_effect=tool.UpdateError("rate_limited")):
            report = tool.update_check(self.directory)
        self.assertEqual(report["error"], "rate_limited")
        self.assertEqual(report["warning"], "backoff_storage_unavailable")

    def test_corrupt_or_future_cache_does_not_authorize_or_report_current(self):
        for cache in ({"bad": "PRIVATE"}, {"observed_at": 2000, "proxy_hash": "a" * 64, "retry": {"retry_at": 3000}}):
            with tool.notice_store(self.directory, create=True) as folder:
                tool.notice_write(folder, "network-limit.json", cache)
            with mock.patch.object(tool.time, "time", return_value=1000), \
                    mock.patch.object(tool, "fetch_release", side_effect=tool.UpdateError("offline")):
                report = tool.update_check(self.directory)
            self.assertEqual(report["status"], "check_failed")
            self.assertEqual(report["error"], "offline")

    def test_bundle_check_never_fetches_executes_or_changes_bundle(self):
        bundle = self.directory / "bundle"
        bundle.mkdir(mode=0o700)
        metadata, manifest = release_fixture("0.7.1")
        for name in tool.CORE_FILES:
            (bundle / name).write_bytes(name.encode())
        (bundle / tool.MANIFEST).write_bytes(manifest)
        before = {p.name: p.read_bytes() for p in bundle.iterdir()}
        with mock.patch.object(tool, "fetch_release", side_effect=AssertionError("network")), \
                mock.patch.object(tool.subprocess, "run", side_effect=AssertionError("execution")):
            report = tool.update_check(self.directory, bundle=bundle)
        self.assertEqual(report["selection"], "local_bundle")
        self.assertEqual(report["release"]["tag"], "v0.7.1")
        self.assertEqual(report["status"], "not_comparable")
        self.assertIn("not_fresh_publication", report["trust"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in bundle.iterdir()})
        self.assertFalse((self.directory / tool.NOTICE_DIRECTORY).exists())
        (bundle / "shim.py").write_bytes(b"modified")
        self.assertEqual(tool.update_check(self.directory, bundle=bundle)["error"], "invalid_metadata")

    def test_bundle_cli_human_evidence_and_selection_conflict(self):
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.directory)), \
                mock.patch.object(tool, "local_bundle_release", return_value={"tag": "v0.7.1", "version": "0.7.1"}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(tool.main(["update", "check", "--bundle", str(self.directory)]), 1)
        self.assertIn("evidence: local_bundle", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tool.main(["update", "check", "--bundle", "x", "--tag", "v0.7.1"]), 2)


if __name__ == "__main__":
    unittest.main()
