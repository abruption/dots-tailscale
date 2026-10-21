"""Offline fixtures for stable release selection and unauthenticated bounded retrieval."""
import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("dots_ts_update_test", str(ROOT / "dots-ts"))
spec = importlib.util.spec_from_loader(loader.name, loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)


def release_fixture(version="0.4.0"):
    manifest = {"schema_version": 1, "package_version": version,
                "files": {name: hashlib.sha256(name.encode()).hexdigest() for name in tool.CORE_FILES}}
    raw = json.dumps(manifest).encode()
    metadata = {"tag_name": "v" + version, "draft": False, "prerelease": False, "id": 27,
                "url": tool.UPDATE_API + "/27", "html_url": tool.UPDATE_WEB + "/releases/tag/v" + version,
                "assets": [{"name": name, "state": "uploaded", "size": len(raw) if name == tool.MANIFEST else len(name),
                            "browser_download_url": tool.UPDATE_WEB + "/releases/download/v" + version + "/" + name}
                           for name in list(tool.CORE_FILES) + [tool.MANIFEST]]}
    return metadata, raw


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="update-check-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        metadata, raw = release_fixture("0.3.9")
        for name, installed in tool.CORE_FILES.items():
            path = self.directory / installed
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        (self.directory / tool.MANIFEST).write_bytes(raw)

    def fetch(self, version="0.4.0", metadata=None, raw_manifest=None, tag=None):
        fixture, raw = release_fixture(version)
        with mock.patch.object(tool, "fetch_bytes", side_effect=[json.dumps(metadata or fixture).encode(), raw_manifest or raw]) as fetch:
            result = tool.fetch_release(tag)
        return result, fetch

    def test_numeric_versions_and_stable_only(self):
        self.assertGreater(tool.stable_version("0.10.0"), tool.stable_version("0.9.99"))
        for value in ("0.4.0-dev", "v0.4.0", "0.04.0", "0.4", "PRIVATE", None):
            with self.assertRaises(tool.UpdateError):
                tool.stable_version(value)

    def test_latest_and_pinned_release_share_validated_manifest(self):
        result, fetch = self.fetch()
        self.assertEqual(result["manifest"]["package_version"], "0.4.0")
        self.assertEqual(fetch.call_args_list[0].args[0], tool.UPDATE_API + "/latest")
        result, fetch = self.fetch(tag="v0.4.0")
        self.assertEqual(fetch.call_args_list[0].args[0], tool.UPDATE_API + "/tags/v0.4.0")
        self.assertEqual(set(result["assets"]), set(tool.CORE_FILES) | {tool.MANIFEST})

    def test_draft_prerelease_and_wrong_repo_tag_rejected(self):
        original, _ = release_fixture()
        for key, value in (("draft", True), ("prerelease", True), ("draft", 0),
                           ("tag_name", "v0.4.0-rc1"), ("url", "https://api.github.com/repos/evil/repo/releases/27"),
                           ("html_url", "https://github.com/evil/repo/releases/tag/v0.4.0")):
            metadata = dict(original, **{key: value})
            with self.assertRaises(tool.UpdateError):
                self.fetch(metadata=metadata)
        with self.assertRaises(tool.UpdateError):
            self.fetch(tag="v0.5.0")

    def test_legacy_releases_are_explicitly_unsupported(self):
        metadata, _ = release_fixture()
        metadata["assets"].pop()
        with self.assertRaises(tool.UpdateError) as error:
            self.fetch(metadata=metadata)
        self.assertEqual(error.exception.code, "unsupported_release")

    def test_duplicate_assets_wrong_urls_sizes_and_manifest_rejected(self):
        original, raw = release_fixture()
        variants = []
        metadata = copy.deepcopy(original)
        metadata["assets"].append(metadata["assets"][0]); variants.append(metadata)
        for key, value in (("browser_download_url", "http://github.com/unsafe"), ("size", 0), ("state", "new")):
            metadata = copy.deepcopy(original); metadata["assets"][0][key] = value; variants.append(metadata)
        for metadata in variants:
            with self.assertRaises(tool.UpdateError): self.fetch(metadata=metadata)
        manifest = json.loads(raw); manifest["files"]["state/tailscaled.state"] = "a" * 64
        bad = json.dumps(manifest).encode()
        metadata = copy.deepcopy(original); metadata["assets"][-1]["size"] = len(bad)
        with self.assertRaises(tool.UpdateError): self.fetch(metadata=metadata, raw_manifest=bad)
        manifest = json.loads(raw); manifest["package_version"] = "0.5.0"
        bad = json.dumps(manifest).encode()
        with self.assertRaises(tool.UpdateError): self.fetch(raw_manifest=bad)

    def test_report_states_and_check_never_writes(self):
        before = {str(path.relative_to(self.directory)): path.read_bytes() for path in self.directory.rglob("*") if path.is_file()}
        for version, expected in (("0.10.0", "update_available"), ("0.3.9", "current"), ("0.3.1", "local_newer")):
            release, _ = self.fetch(version)
            with mock.patch.object(tool, "fetch_release", return_value=release):
                report = tool.update_check(self.directory, "v" + version)
            self.assertEqual(report["status"], expected)
            self.assertEqual(report["selection"], "pinned")
            self.assertEqual(tool.update_check_exit(report), 0)
        after = {str(path.relative_to(self.directory)): path.read_bytes() for path in self.directory.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        (self.directory / "dots-ts").write_text("modified")
        with mock.patch.object(tool, "fetch_release", return_value=release):
            report = tool.update_check(self.directory)
        self.assertEqual(report["status"], "not_comparable")
        self.assertEqual(tool.update_check_exit(report), 1)
        (self.directory / tool.MANIFEST).unlink()
        with mock.patch.object(tool, "fetch_release", return_value=release):
            self.assertEqual(tool.update_check(self.directory)["package"]["integrity"], "unknown")

    def test_offline_rate_tls_and_timeout_remain_sanitized(self):
        for code in ("offline", "rate_limited", "tls_error", "timeout", "invalid_metadata"):
            with mock.patch.object(tool, "fetch_release", side_effect=tool.UpdateError(code)):
                report = tool.update_check(self.directory)
            self.assertEqual(report["status"], "check_failed")
            self.assertEqual(report["error"], code)
            self.assertEqual(tool.update_check_exit(report), 2)
            self.assertNotIn(str(self.directory), json.dumps(report))

    def test_url_and_proxy_allowlists(self):
        for url in ("http://api.github.com/repos/abruption/dots-tailscale/releases/latest", "https://api.github.com.evil/x",
                    tool.UPDATE_API + "/latest?token=PRIVATE", "https://user:PRIVATE@api.github.com/repos/abruption/dots-tailscale/releases/latest"):
            with self.assertRaises(tool.UpdateError): tool.validate_update_url(url)
        for url in ("https://evil.example/a", "http://release-assets.githubusercontent.com/a", "https://release-assets.githubusercontent.com:444/a"):
            with self.assertRaises(tool.UpdateError): tool.validate_update_url(url, redirect=True)
        self.assertEqual(tool.validate_proxy("http://browser-proxy:8889"), "http://browser-proxy:8889")
        for proxy in ("http://user:PRIVATE@browser-proxy:8889", "socks5://localhost:1055", "http://browser-proxy:8889/?token=PRIVATE"):
            with self.assertRaises(tool.UpdateError): tool.validate_proxy(proxy)

    def test_network_ignores_ambient_proxy_and_tokens(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200; response.headers = {}; response.read.return_value = b"{}"
        opener = mock.Mock(); opener.open.return_value = response
        with mock.patch.dict("os.environ", {"HTTPS_PROXY": "http://user:PRIVATE@bad.invalid", "GITHUB_TOKEN": "PRIVATE"}), \
                mock.patch.object(tool.urllib.request, "ProxyHandler", wraps=tool.urllib.request.ProxyHandler) as proxy, \
                mock.patch.object(tool.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(tool._network_fetch(tool.UPDATE_API + "/latest", None, 1024), b"{}")
        self.assertEqual(proxy.call_args.args[0], {})
        request = opener.open.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("PRIVATE", str(request.headers))

    def test_http_error_classification(self):
        for status, headers, expected in ((429, {}, "rate_limited"), (403, {"X-RateLimit-Remaining": "0"}, "rate_limited"),
                                          (404, {}, "not_found"), (503, {}, "http_error")):
            opener = mock.Mock(); opener.open.side_effect = urllib.error.HTTPError("private", status, "PRIVATE", headers, None)
            with mock.patch.object(tool.urllib.request, "build_opener", return_value=opener), self.assertRaises(tool.UpdateError) as error:
                tool._network_fetch(tool.UPDATE_API + "/latest", None, 1024)
            self.assertEqual(error.exception.code, expected)
        opener = mock.Mock(); opener.open.side_effect = urllib.error.URLError(ssl.SSLError("PRIVATE"))
        with mock.patch.object(tool.urllib.request, "build_opener", return_value=opener), self.assertRaises(tool.UpdateError) as error:
            tool._network_fetch(tool.UPDATE_API + "/latest", None, 1024)
        self.assertEqual(error.exception.code, "tls_error")

    def test_whole_request_deadline_and_response_bound(self):
        with mock.patch.object(tool.subprocess, "run", side_effect=subprocess.TimeoutExpired("PRIVATE", 1)), self.assertRaises(tool.UpdateError) as error:
            tool.fetch_bytes(tool.UPDATE_API + "/latest")
        self.assertEqual(error.exception.code, "timeout")
        response = mock.MagicMock(); response.__enter__.return_value = response
        response.status = 200; response.headers = {}; response.read.return_value = b"x" * 1025
        opener = mock.Mock(); opener.open.return_value = response
        with mock.patch.object(tool.urllib.request, "build_opener", return_value=opener), self.assertRaises(tool.UpdateError) as error:
            tool._network_fetch(tool.UPDATE_API + "/latest", None, 1024)
        self.assertEqual(error.exception.code, "response_too_large")

    def test_malformed_child_error_has_fixed_classification(self):
        for value in ([], {}, None, 1):
            self.assertEqual(tool.UpdateError(value).code, "invalid_response")
            result = mock.Mock(returncode=0, stdout=json.dumps({"error": value}).encode())
            with mock.patch.object(tool.subprocess, "run", return_value=result), self.assertRaises(tool.UpdateError) as error:
                tool.fetch_bytes(tool.UPDATE_API + "/latest")
            self.assertEqual(error.exception.code, "invalid_response")

    def test_json_has_no_notice_or_private_errors(self):
        output = io.StringIO()
        with mock.patch.object(tool, "directories", return_value=(self.directory, self.directory)), \
                mock.patch.object(tool, "fetch_release", side_effect=tool.UpdateError("offline")), contextlib.redirect_stdout(output):
            code = tool.main(["update", "check", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"], "offline")


if __name__ == "__main__": unittest.main()
