"""Execute bilingual staging examples in toy repos; mock only the Tailscale CLI.

No installed files, real keys, live daemons, or external services are used.
"""
import hashlib
import os
import pathlib
import re
import shutil
import socket
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GUIDES = [ROOT / 'docs/usage.md', ROOT / 'docs/usage.ko.md']


def staging_block(path):
    return re.search(r'<!-- update-staging:start -->\n```bash\n(.*?)\n```',
                     path.read_text(), re.S).group(1)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='usage-staging-')
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        self.remote = self.root / 'remote'
        self.remote.mkdir()
        self.git('init', '-q', '-b', 'main', cwd=self.remote)
        self.commits = []
        for n in (1, 2, 3):
            (self.remote / 'tailscale-setup.sh').write_text(f'#!/bin/bash\necho version{n}\n')
            (self.remote / 'shim.py').write_text(f'VERSION = {n}\n')
            self.git('add', '.', cwd=self.remote)
            self.git('commit', '-qm', f'version {n}', cwd=self.remote)
            self.commits.append(self.git('rev-parse', 'HEAD', cwd=self.remote).stdout.strip())
        self.checkout = self.root / 'checkout'
        self.git('clone', '-q', str(self.remote), str(self.checkout), cwd=self.root)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.checkout, env=self.env,
                              text=True, capture_output=True, check=True, timeout=10)

    def run_block(self, commit, guide=GUIDES[0], fail=None):
        script = staging_block(guide).replace('/workspace/dots-tailscale', str(self.checkout))
        script = script.replace('<REVIEWED_COMMIT>', commit)
        if fail:
            # Fail only the selected subcommand; preserve real Git for other steps.
            script = f'git() {{ if [[ "$1" = {fail} ]]; then return 73; fi; command git "$@"; }}\n' + script
        return subprocess.run(['bash', '-c', script], env=self.env, text=True,
                              capture_output=True, timeout=15)

    def test_guides_have_identical_executable_staging_flow(self):
        self.assertEqual(staging_block(GUIDES[0]), staging_block(GUIDES[1]))

    def test_repeated_updates_from_branch_then_detached_head(self):
        for guide in GUIDES:
            self.git('switch', 'main')
            for commit in self.commits[:2]:
                with self.subTest(guide=guide.name, commit=commit):
                    result = self.run_block(commit, guide)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), commit)
                    self.assertIn(commit, result.stdout)
                    for name in ('tailscale-setup.sh', 'shim.py'):
                        digest = hashlib.sha256((self.checkout / name).read_bytes()).hexdigest()
                        self.assertIn(digest + '  ' + name, result.stdout)
                    self.assertEqual(self.git('status', '--porcelain').stdout, '')

    def test_dirty_tree_is_preserved_and_stops_before_fetch(self):
        for kind in ('tracked', 'staged', 'untracked'):
            with self.subTest(kind=kind):
                file = self.checkout / ('local.txt' if kind == 'untracked' else 'shim.py')
                old = file.read_bytes() if file.exists() else None
                file.write_text('local work\n')
                if kind == 'staged':
                    self.git('add', 'shim.py')
                before = self.git('status', '--porcelain').stdout
                result = self.run_block(self.commits[0], fail='fetch')
                self.assertEqual(result.returncode, 1)
                self.assertIn('preserve and review local changes', result.stderr)
                self.assertEqual(file.read_text(), 'local work\n')
                self.assertEqual(self.git('status', '--porcelain').stdout, before)
                self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), self.commits[-1])
                if old is None:
                    file.unlink()
                else:
                    file.write_bytes(old)
                    self.git('add', 'shim.py')

    def test_fetch_and_switch_failures_do_not_print_success_hashes(self):
        for fail in ('fetch', 'switch'):
            with self.subTest(fail=fail):
                result = self.run_block(self.commits[0], fail=fail)
                self.assertEqual(result.returncode, 73, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), self.commits[-1])

    def test_unknown_commit_and_placeholder_stop(self):
        for commit in ('0' * 40, '<REVIEWED_COMMIT>', 'main'):
            with self.subTest(commit=commit):
                result = self.run_block(commit)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), self.commits[-1])

    def test_source_validation_failure_stops_before_hash_report(self):
        for name, content in (('tailscale-setup.sh', 'if broken\n'), ('shim.py', 'def broken(\n')):
            with self.subTest(name=name):
                file = self.remote / name
                old = file.read_text()
                file.write_text(content)
                self.git('add', '.', cwd=self.remote)
                self.git('commit', '-qm', 'invalid fixture', cwd=self.remote)
                commit = self.git('rev-parse', 'HEAD', cwd=self.remote).stdout.strip()
                result = self.run_block(commit)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                file.write_text(old)

    def test_proxy_is_scoped_to_fetch_and_tls_is_not_disabled(self):
        block = staging_block(GUIDES[0])
        self.assertIn('HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \\\n    git fetch --tags origin', block)
        self.assertNotIn('sslVerify', block)
        self.assertNotIn('GIT_SSL_NO_VERIFY', block)


class AddressLookupTests(unittest.TestCase):
    def test_installed_wrapper_uses_private_socket_without_global_cli(self):
        with tempfile.TemporaryDirectory(prefix='usage-socket-') as directory:
            root = pathlib.Path(directory)
            install = root / 'install'
            install.mkdir()
            runtime = root / 'runtime'
            runtime.mkdir()
            source = (ROOT / 'tailscale-setup.sh').read_text()
            # Relocate only the runtime root so no real per-user socket is touched.
            source = source.replace('R="/tmp/dots-tailscale-$(id -u)"', f'R="{runtime}"')
            (install / 'setup.sh').write_text(source)
            wrapper = re.search(r"<<'START'\n(.*?)\nSTART", source, re.S).group(1)
            (install / 'start.sh').write_text(wrapper)
            cli = install / 'tailscale'
            cli.write_text('#!/bin/bash\n[[ "$1" = "--socket=' + str(runtime / 'tailscaled.sock')
                           + '" && "$2" = status && "$3" = --peers=false ]] || exit 91\n'
                           'printf "100.64.0.1 test-node\\n"\n')
            cli.chmod(0o700)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            for name in ('bash', 'dirname', 'timeout'):
                (bin_dir / name).symlink_to(shutil.which(name))
            env = dict(os.environ, PATH=str(bin_dir))
            self.assertIsNone(shutil.which('tailscale', path=env['PATH']))
            with socket.socket(socket.AF_UNIX) as sock:
                sock.bind(str(runtime / 'tailscaled.sock'))
                result = subprocess.run([shutil.which('bash'), str(install / 'start.sh'), 'check'],
                                        env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('100.64.0.1 test-node', result.stdout)
            for guide in GUIDES:
                self.assertIn('bash /workspace/.dots-tailscale/start.sh check', guide.read_text())


if __name__ == '__main__':
    unittest.main()
