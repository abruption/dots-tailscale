"""Exercise installer companion staging with inert pre-existing identity fixtures."""
import sys
import hashlib
import json
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
            p.write_text("#!/bin/sh\necho 1.104.4\n")
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
        self.entry = self.source / "tailscale-setup.sh"
        self.entry.write_text("set -euo pipefail\numask 077\nTS_VERSION=1.104.4\nSSH_PORT=22\nUPSTREAM_PROXY=http://fixture.invalid:8889\n"
                              "fail() { printf '%s\\n' \"$*\" >&2; exit 1; }\n"
                              "require() { command -v \"$1\" >/dev/null; }\n" +
                              "\n".join(functions) + "\n" +
                              "\n".join(k + "=" + shlex.quote(str(v)) for k, v in
                                        (("SOURCE", self.source), ("D", self.d),
                                         ("R", self.r), ("S", self.d / "sshd"))) +
                              '\nSOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\ninstall_all\n')
        files = ("tailscale-setup.sh", "shim.py", "dots-ts", "dots_proxy.py")
        manifest = {"schema_version": 1, "package_version": "0.4.0-dev",
                    "files": {name: hashlib.sha256((self.source / name).read_bytes()).hexdigest() for name in files}}
        (self.source / "source-manifest.json").write_text(json.dumps(manifest))

    def install(self):
        return subprocess.run(["bash", str(self.entry)], text=True, capture_output=True, timeout=10)

    def select_ssh_port(self, port):
        self.entry.write_text(self.entry.read_text().replace('SSH_PORT=22\n', 'SSH_PORT='+str(port)+'\n'))
        path=self.source/'source-manifest.json'
        metadata=json.loads(path.read_text())
        metadata['files']['tailscale-setup.sh']=hashlib.sha256(self.entry.read_bytes()).hexdigest()
        path.write_text(json.dumps(metadata))

    def test_selected_2222_is_generated_from_saved_intent_without_identity_changes(self):
        setting=self.d/'ssh-port.json'
        setting.write_text('{"schema_version":1,"port":2222}\n');setting.chmod(0o600)
        before={n:((self.d/n).stat().st_ino,(self.d/n).read_bytes()) for n in self.identity}
        intent=(setting.stat().st_ino,setting.read_bytes())
        self.select_ssh_port(2222)
        result=self.install()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((setting.stat().st_ino,setting.read_bytes()),intent)
        config=(self.d/'sshd/sshd_config').read_text()
        self.assertTrue(config.startswith('Port 2222\nListenAddress 127.0.0.1\n'))
        self.assertIn('PasswordAuthentication no\n',config)
        self.assertIn('PubkeyAuthentication yes\n',config)
        self.assertEqual(before,{n:((self.d/n).stat().st_ino,(self.d/n).read_bytes()) for n in self.identity})

    def test_conflicting_saved_port_stops_before_identity_or_source_placement(self):
        setting=self.d/'ssh-port.json'
        setting.write_text('{"schema_version":1,"port":2222}\n');setting.chmod(0o600)
        before={n:((self.d/n).stat().st_ino,(self.d/n).read_bytes()) for n in self.identity}
        self.select_ssh_port(2223)
        result=self.install()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('SSH port intent changed',result.stderr)
        self.assertFalse((self.d/'installed').exists())
        self.assertFalse((self.d/'setup.sh').exists())
        self.assertEqual(before,{n:((self.d/n).stat().st_ino,(self.d/n).read_bytes()) for n in self.identity})

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
        self.assertEqual((self.d / "source-manifest.json").read_bytes(),
                         (self.source / "source-manifest.json").read_bytes())
        self.assert_identity_preserved()

    def fresh_download(self, archive, checksum, success=False):
        for name in ("tailscale", "tailscaled"):
            (self.d / name).unlink()
        bin_dir = self.source / 'bin'; bin_dir.mkdir()
        curl = bin_dir / 'curl'
        curl.write_text('#!' + sys.executable + '\nimport pathlib,sys\n'
                        + 'args=sys.argv[1:]; out=pathlib.Path(args[args.index("-o")+1])\n'
                        + 'out.write_bytes(' + repr(checksum.encode()) + ' if any(a.endswith(".sha256") for a in args) else '
                        + repr(archive) + ')\n')
        curl.chmod(0o700)
        import os
        result = subprocess.run(["bash", str(self.entry)], text=True, capture_output=True,
                                timeout=10, env=dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH']))
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((self.d / 'installed').is_file())
            for name in ('tailscale', 'tailscaled'):
                observed = subprocess.check_output([str(self.d / name), '--version'], text=True)
                self.assertEqual(observed.strip(), '1.104.4')
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.d / 'tailscale').exists())
            self.assertFalse((self.d / 'tailscaled').exists())
            self.assertFalse((self.d / 'dots-ts').exists())
            self.assertFalse((self.d / 'installed').exists())
        self.assert_identity_preserved()
        return result

    def test_fresh_install_validates_and_places_both_real_version_probes(self):
        compiler = shutil.which('cc')
        if compiler is None:
            self.skipTest('a C compiler is needed for the executable binary fixture')
        import io, tarfile
        source = self.source / 'version.c'
        source.write_text('#include <stdio.h>\nint main(void) { puts("1.104.4"); return 0; }\n')
        binary = self.source / 'version-probe'
        subprocess.run([compiler, str(source), '-o', str(binary)], check=True,
                       capture_output=True, timeout=15)
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz', format=tarfile.USTAR_FORMAT) as package:
            for name in ('tailscale', 'tailscaled'):
                data = binary.read_bytes()
                entry = tarfile.TarInfo('tailscale_1.104.4_amd64/' + name)
                entry.mode = 0o700; entry.size = len(data)
                package.addfile(entry, io.BytesIO(data))
        archive = output.getvalue()
        self.fresh_download(archive, hashlib.sha256(archive).hexdigest(), success=True)

    def call_install_in_condition(self):
        text = self.entry.read_text()
        self.assertTrue(text.endswith('install_all\n'))
        self.entry.write_text(text[:-len('install_all\n')] + 'if install_all; then exit 0; else exit 1; fi\n')
        manifest_path = self.source / 'source-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files']['tailscale-setup.sh'] = hashlib.sha256(self.entry.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))

    def test_pair_failure_stops_even_inside_shell_condition(self):
        self.call_install_in_condition()
        for name in ('tailscale', 'tailscaled'):
            (self.d / name).write_text('#!/bin/sh\necho 1.102.4\n')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('baseline_upgrade_required', result.stderr)
        self.assertFalse((self.d / 'installed').exists())
        self.assert_identity_preserved()

    def test_archive_failure_stops_even_inside_shell_condition(self):
        self.call_install_in_condition()
        result = self.fresh_download(b'bad archive fixture', '0'*64)
        self.assertIn('checksum_mismatch', result.stderr)

    def test_fresh_download_checksum_failure_never_places_pair(self):
        result = self.fresh_download(b'bad archive fixture', '0'*64)
        self.assertIn('checksum_mismatch', result.stderr)

    def test_fresh_download_matching_checksum_still_validates_archive(self):
        archive = b'not a gzip or tar archive'
        result = self.fresh_download(archive, hashlib.sha256(archive).hexdigest())
        self.assertIn('invalid_archive', result.stderr)

    def test_existing_mismatched_pair_refused_without_identity_changes(self):
        (self.d / "tailscaled").write_text("#!/bin/sh\necho 1.104.2\n")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("binary_version_mismatch", result.stderr)
        self.assertFalse((self.d / "installed").exists())
        self.assert_identity_preserved()

    def test_existing_old_pair_requires_explicit_runtime_upgrade(self):
        for name in ("tailscale", "tailscaled"):
            (self.d / name).write_text("#!/bin/sh\necho 1.102.4\n")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("baseline_upgrade_required", result.stderr)
        self.assertFalse((self.d / "installed").exists())
        self.assert_identity_preserved()

    def test_unsafe_existing_pair_is_rejected_without_executing_it(self):
        import os
        marker = self.source / 'executed'
        original = (self.d / 'tailscale').read_bytes()
        for unsafe in ('hardlink', 'writable'):
            with self.subTest(unsafe=unsafe):
                binary = self.d / 'tailscale'
                binary.write_text('#!/bin/sh\ntouch ' + shlex.quote(str(marker)) + '\necho 1.104.4\n')
                link = self.source / 'hardlink'
                if unsafe == 'hardlink':
                    os.link(binary, link)
                else:
                    binary.chmod(0o777)
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unsafe_binary_file', result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((self.d / 'installed').exists())
                if link.exists():
                    link.unlink()
                binary.write_bytes(original); binary.chmod(0o700)
                self.assert_identity_preserved()

    def test_dangling_pair_symlink_is_rejected_before_download(self):
        for name in ('tailscale', 'tailscaled'):
            (self.d / name).unlink()
        (self.d / 'tailscale').symlink_to(self.source / 'absent-victim')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('partial or symlink binary installation', result.stderr)
        self.assertFalse((self.source / 'absent-victim').exists())
        self.assertFalse((self.d / 'tailscaled').exists())
        self.assert_identity_preserved()

    def test_partial_pair_is_not_completed_implicitly(self):
        (self.d / "tailscaled").unlink()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("partial or symlink binary installation", result.stderr)
        self.assertFalse((self.d / "tailscaled").exists())
        self.assert_identity_preserved()

    def test_missing_companion_stops_without_install_marker(self):
        (self.source / "dots_proxy.py").unlink()
        r = self.install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid source bundle", r.stderr)
        self.assertFalse((self.d / "installed").exists())
        self.assert_identity_preserved()

    def test_conflicting_helper_is_not_overwritten(self):
        (self.d / "dots-ts").write_text("existing custom helper\n")
        r = self.install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("existing file differs", r.stderr)
        self.assertEqual((self.d / "dots-ts").read_text(), "existing custom helper\n")
        self.assert_identity_preserved()

    def test_modified_bundle_fails_before_any_code_is_placed(self):
        (self.source / "shim.py").write_text("mixed release fixture")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.d / "dots-ts").exists())
        self.assertFalse((self.d / "source-manifest.json").exists())
        self.assertFalse((self.d / "installed").exists())
        self.assert_identity_preserved()

    def test_manifest_conflict_prevents_partial_payload_placement(self):
        (self.d / "source-manifest.json").write_text("existing metadata")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.d / "shim/shim.py").exists())
        self.assertFalse((self.d / "dots-ts").exists())
        self.assertEqual((self.d / "source-manifest.json").read_text(), "existing metadata")
        self.assert_identity_preserved()

    def test_missing_metadata_rejects_new_install(self):
        (self.source / "source-manifest.json").unlink()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata_missing", result.stderr)
        self.assertFalse((self.d / "installed").exists())
        self.assert_identity_preserved()

    def test_reinstall_uses_verified_installed_layout(self):
        self.assertEqual(self.install().returncode, 0)
        (self.d / "shim.py").write_text("unverified stray source file")
        result = subprocess.run(["bash", str(self.d / "setup.sh")], text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.d / "shim/shim.py").read_bytes(), (self.source / "shim.py").read_bytes())
        self.assert_identity_preserved()


if __name__ == "__main__":
    unittest.main()


class PortableInstallerPortFixtures(unittest.TestCase):
    """Placement regression without a daemon; adapt unused platform prerequisites."""
    setUp = InstallHelpersTests.setUp
    install = InstallHelpersTests.install
    select_ssh_port = InstallHelpersTests.select_ssh_port
    assert_identity_preserved = InstallHelpersTests.assert_identity_preserved

    def adapt_fixture(self):
        text=self.entry.read_text().replace('require() { command -v "$1" >/dev/null; }',
             'require() { :; }\nchmod() { if [[ "${2:-}" == -- ]]; then command chmod "$1" "${@:3}"; else command chmod "$@"; fi; }')
        self.entry.write_text(text);self.select_ssh_port(22)

    def test_default_placement_never_needs_installed_helper_before_copy(self):
        self.adapt_fixture()
        InstallHelpersTests.test_install_places_executable_helpers_and_wrapper(self)

    def test_2222_placement_never_needs_installed_helper_before_copy(self):
        self.adapt_fixture()
        InstallHelpersTests.test_selected_2222_is_generated_from_saved_intent_without_identity_changes(self)

    def test_conflicting_intent_stops_without_placing_source_or_identity(self):
        self.adapt_fixture()
        InstallHelpersTests.test_conflicting_saved_port_stops_before_identity_or_source_placement(self)
