#!/usr/bin/env python3
"""Operator-run, bounded addition of one trial host to existing Caddy/Authelia.

No default-policy changes. Backups and validation output stay root-private on the
server. Requires sudo; never accepts or stores a password. Run --help first.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request

CADDY = Path('/etc/caddy/Caddyfile')
AUTHELIA = Path('/etc/authelia/configuration.yml')
ROOT = Path('/var/lib/dots-events-research-deploy')
CONSENT_RESOURCE = r'^/oauth/consent(?:/[^?]*)?(?:\?.*)?$'
AUTHORIZE_RESOURCE = r'^/authorize(?:\?.*)?$'


def candidates(caddy, authelia, hostname, username, repair_consent_query=False):
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', hostname):
        raise ValueError('invalid hostname')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', username):
        raise ValueError('invalid account')
    if repair_consent_query:
        old = ("    # Dots MCP Events research #43: only the browser consent path\n"
               f"    - domain: '{hostname}'\n"
               "      resources: ['^/oauth/consent(?:/.*)?$']\n"
               "      policy: 'two_factor'\n"
               f"      subject: ['user:{username}']\n")
        if authelia.count(old) != 1 or caddy.count(f'https://{hostname} {{') != 1:
            raise ValueError('exact previously installed trial rule not found; nothing repaired')
        new = old.replace('^/oauth/consent(?:/.*)?$', CONSENT_RESOURCE)
        return caddy, authelia.replace(old, new, 1)
    if hostname in caddy or hostname in authelia:
        raise ValueError('hostname already appears; review manually')
    if not re.search(r'^\(tls_origin_dev\)\s*\{', caddy, re.M):
        raise ValueError('expected TLS snippet missing')
    block = f'''
# Dots MCP Events research #43: synthetic probe only
https://{hostname} {{
    import tls_origin_dev
    route {{
        request_header -Remote-User
        request_header -Remote-Groups
        request_header -Remote-Email
        request_header -Remote-Name
        @consent path /oauth/consent /oauth/consent/* /authorize
        handle @consent {{
            forward_auth 127.0.0.1:9091 {{
                uri /api/authz/forward-auth
                copy_headers Remote-User
            }}
            reverse_proxy 127.0.0.1:18743
        }}
        handle {{
            reverse_proxy 127.0.0.1:18743
        }}
    }}
}}
'''
    section = re.search(r'^access_control:\s*(?:#.*)?\n(?:(?:[ \t].*|\s*)\n)*', authelia, re.M)
    if not section:
        raise ValueError('expected access_control mapping missing')
    match = re.search(r'^  rules:\s*(?:#.*)?\n', section.group(), re.M)
    if not match:
        raise ValueError('expected block rules sequence missing')
    offset = section.start() + match.end()
    rule = (f"    # Dots MCP Events research #43: only the browser consent path\n"
            f"    - domain: '{hostname}'\n"
            f"      resources: ['{AUTHORIZE_RESOURCE}', '{CONSENT_RESOURCE}']\n"
            "      policy: 'two_factor'\n"
            f"      subject: ['user:{username}']\n")
    return caddy.rstrip()+'\n'+block, authelia[:offset]+rule+authelia[offset:]


def snapshot(path):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0:
        raise ValueError('expected root-owned regular configuration file')
    return path.read_bytes(), st


def atomic(path, value, metadata):
    fd, name = tempfile.mkstemp(prefix='.dots-events-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
            os.fchown(f.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(f.fileno(), stat.S_IMODE(metadata.st_mode))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(command, log):
    with log.open('ab') as f:
        result = subprocess.run(command, stdout=f, stderr=f, timeout=60)
    if result.returncode:
        raise RuntimeError(f'{Path(command[0]).name} failed; inspect root-private {log}')


def auth_ready():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with opener.open('http://127.0.0.1:9091/api/health', timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError('Authelia health check did not become ready')


def authelia_validation_command(binary, candidate):
    # systemd reads the existing service's secret file; values never enter argv.
    return ['systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--property=RuntimeMaxSec=45s',
            '--property=EnvironmentFile=/etc/authelia/authelia.env',
            '--', binary, 'validate-config', '--config', str(candidate)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--user', required=True)
    p.add_argument('--repair-consent-query', action='store_true', help='repair only the exact original trial consent regex to accept OAuth ticket queries')
    p.add_argument('--apply', action='store_true', help='validate, back up, add only this host, restart Authelia and reload Caddy')
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error('run through sudo; do not send passwords to the agent')
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    if ROOT.is_symlink() or ROOT.stat().st_uid != 0 or ROOT.stat().st_mode & 0o077:
        raise ValueError('deployment record directory must be root-private')
    with (ROOT/'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old_c, st_c = snapshot(CADDY)
        old_a, st_a = snapshot(AUTHELIA)
        new_c, new_a = candidates(old_c.decode(), old_a.decode(), args.host, args.user, args.repair_consent_query)
        origin_request = urllib.request.Request('http://127.0.0.1:18743/healthz', headers={'Host': args.host})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(origin_request, timeout=3) as r:
            if json.load(r).get('mode') != 'synthetic_only':
                raise ValueError('unexpected trial server')
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        folder = ROOT/stamp
        folder.mkdir(mode=0o700)
        (folder/'Caddyfile.before').write_bytes(old_c)
        (folder/'authelia.before.yml').write_bytes(old_a)
        (folder/'Caddyfile.candidate').write_text(new_c)
        (folder/'authelia.candidate.yml').write_text(new_a)
        authelia_binary = shutil.which('authelia')
        if not authelia_binary:
            raise ValueError('authelia binary not found')
        run(['/usr/bin/caddy', 'validate', '--config', str(folder/'Caddyfile.candidate'), '--adapter', 'caddyfile'], folder/'validation.log')
        run(authelia_validation_command(authelia_binary, folder/'authelia.candidate.yml'), folder/'validation.log')
        if not args.apply:
            print(json.dumps({'validated': True, 'applied': False, 'private_record': str(folder)}))
            return
        for service in ('caddy', 'authelia'):
            run(['systemctl', 'is-active', '--quiet', service], folder/'service.log')
        if CADDY.read_bytes() != old_c or AUTHELIA.read_bytes() != old_a:
            raise ValueError('configuration changed during validation; nothing applied')
        try:
            atomic(AUTHELIA, new_a.encode(), st_a)
            atomic(CADDY, new_c.encode(), st_c)
            run(['systemctl', 'restart', 'authelia'], folder/'service.log')
            auth_ready()
            run(['systemctl', 'reload', 'caddy'], folder/'service.log')
            for service in ('authelia', 'caddy'):
                run(['systemctl', 'is-active', '--quiet', service], folder/'service.log')
        except BaseException:
            failures = []
            for target, data, metadata in ((AUTHELIA, old_a, st_a), (CADDY, old_c, st_c)):
                try:
                    atomic(target, data, metadata)
                except Exception:
                    failures.append(str(target))
            for command in (['systemctl', 'restart', 'authelia'], ['systemctl', 'reload', 'caddy']):
                try:
                    run(command, folder/'rollback.log')
                except Exception:
                    failures.append(' '.join(command))
            if failures:
                raise RuntimeError('Rollback requires operator attention: '+', '.join(failures)) from None
            raise
        (folder/'result.json').write_text(json.dumps({'host': args.host, 'applied': True,
            'caddy_sha256': hashlib.sha256(new_c.encode()).hexdigest(),
            'authelia_sha256': hashlib.sha256(new_a.encode()).hexdigest()}))
        print(json.dumps({'applied': True, 'private_record': str(folder)}))


if __name__ == '__main__':
    main()
