#!/usr/bin/env python3
"""Pin a reviewed, non-secret payload before root-only security migration.

This bootstrap itself must be launched from trusted root-owned code or verified
in memory against an independently pinned digest by the privileged launcher.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import subprocess
import tempfile

STAGING = Path('/var/lib/dots-events-security-staging')
UNITS = Path('/etc/systemd/system')
UNIT = 'dots-events-ipv6-guard.service'
MAX_FILES = 256
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_MANIFEST = 128 * 1024
SOURCE_NAMES = {
    'security_bootstrap.py', 'security_deployment.py', 'security_ipv6.py',
    'apply_routes.py', 'server.py', 'oauth_adapter.py', 'event_core.py',
    'recovery_core.py', 'monitor_ingest.py', 'bounded_network.py',
    'callback_transport.py', 'monitor_relay.py', 'health_notifier.py',
    'offline_replay.py', 'requirements.lock', 'offline-wheel.lock',
}
UNIT_NAMES = {'dots-events-recovery.service', 'dots-events-recovery.socket', UNIT}


def allowed_path(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or str(path) != name or '\\' in name:
        return False
    if len(path.parts) == 1:
        return name in SOURCE_NAMES or bool(re.fullmatch(r'test_[a-z0-9_]+\.py', name))
    if path.parts[:2] == ('services', 'security') and len(path.parts) == 3:
        return path.name in UNIT_NAMES
    return (len(path.parts) == 2 and path.parts[0] == 'wheelhouse'
            and bool(re.fullmatch(r'[A-Za-z0-9_.+%-]+\.whl', path.name)))


def open_directory(path, owner):
    """Walk using dirfd/O_NOFOLLOW so ancestor swaps cannot redirect reads."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('payload must use an absolute canonical path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            info = os.fstat(fd)
            if info.st_uid not in (0, owner) or info.st_mode & 0o022:
                raise ValueError('payload ancestors must be root/operator-owned and not writable by others')
        if os.fstat(fd).st_uid != owner:
            raise ValueError('payload directory must belong to operator')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_owned(directory_fd, name, owner, limit):
    parts = PurePosixPath(name).parts
    fd = os.dup(directory_fd)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            info = os.fstat(fd)
            if info.st_uid != owner or info.st_mode & 0o022:
                raise ValueError('payload subdirectory ownership/mode mismatch')
        item = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(item)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
                raise ValueError('payload file ownership/type/mode mismatch')
            if info.st_size > limit:
                raise ValueError('payload file exceeds size limit')
            chunks = []
            size = 0
            while True:
                chunk = os.read(item, min(65536, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ValueError('payload grew beyond size limit')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            os.close(item)
    finally:
        os.close(fd)


def snapshot_payload(path, digest, owner):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('manifest digest must be SHA256 hex')
    fd = open_directory(path, owner)
    try:
        raw = read_owned(fd, 'manifest.json', owner, MAX_MANIFEST)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('manifest digest mismatch')
        def no_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate manifest path')
                result[key] = value
            return result
        manifest = json.loads(raw, object_pairs_hook=no_duplicates)
        if not isinstance(manifest, dict) or not 1 <= len(manifest) <= MAX_FILES:
            raise ValueError('invalid manifest size')
        snapshot = {}
        total = 0
        for name, expected in manifest.items():
            if not isinstance(name, str) or not allowed_path(name):
                raise ValueError('unapproved payload path')
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('invalid payload digest')
            body = read_owned(fd, name, owner, min(MAX_FILE, MAX_TOTAL - total))
            total += len(body)
            if hashlib.sha256(body).hexdigest() != expected:
                raise ValueError('payload digest mismatch')
            snapshot[name] = body
        required = {'security_deployment.py', 'security_ipv6.py', 'apply_routes.py',
                    'services/security/'+UNIT}
        if not required.issubset(snapshot):
            raise ValueError('missing migration payload')
        return snapshot
    finally:
        os.close(fd)


def root_directory(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('staging ancestry must be root-owned and not writable by others')


def freeze_payload(snapshot, digest, root=STAGING):
    root.mkdir(mode=0o700, exist_ok=True)
    root_directory(root)
    target = root/digest
    if target.exists() or target.is_symlink():
        root_directory(target)
        entries = list(target.rglob('*'))
        for item in entries:
            info = item.lstat()
            if (info.st_uid != 0 or info.st_mode & 0o022 or
                    not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                raise ValueError('existing staging contains untrusted entries')
        files = {str(p.relative_to(target)) for p in entries if p.is_file()}
        expected_dirs = {str(parent) for name in snapshot for parent in PurePosixPath(name).parents if str(parent) != '.'}
        actual_dirs = {str(p.relative_to(target)) for p in entries if p.is_dir()}
        if files != set(snapshot) or actual_dirs != expected_dirs:
            raise ValueError('existing staging contents differ')
        for name, data in snapshot.items():
            path = target/name
            root_directory(path.parent)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or path.read_bytes() != data:
                raise ValueError('existing staging file differs')
        return target
    target.mkdir(mode=0o700)
    for name, data in snapshot.items():
        path = target/name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(data).digest():
            raise ValueError('frozen payload verification failed')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload-dir', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', args.operator):
        parser.error('invalid operator')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', args.host):
        parser.error('invalid host')
    account = pwd.getpwnam(args.operator)
    snapshot = snapshot_payload(args.payload_dir, args.manifest_sha256, account.pw_uid)
    if not args.apply:
        print(json.dumps({'applied':False, 'payload_verified':True, 'files':len(snapshot),
                          'plan':['freeze reviewed payload','isolated IPv6 validation','MCP isolation','IPv6 guard']}))
        return
    if os.geteuid() != 0:
        parser.error('--apply requires root')
    os.umask(0o077)
    stage = freeze_payload(snapshot, args.manifest_sha256)
    record = Path(tempfile.mkdtemp(prefix='record-', dir=STAGING))
    unit = UNITS/UNIT
    unit_body = snapshot['services/security/'+UNIT]
    if unit.exists() or unit.is_symlink():
        info = unit.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or unit.read_bytes() != unit_body:
            raise ValueError('existing IPv6 unit is not the reviewed unit')
    def run(argv, timeout=120):
        with (record/'commands.log').open('ab') as log:
            subprocess.run(argv, check=True, stdout=log, stderr=log, timeout=timeout, cwd=stage,
                           env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8',
                                'PYTHONDONTWRITEBYTECODE':'1'})
    # Execute all three operations inside one fresh network namespace. The
    # helper lock path is shared but no host firewall command can escape it.
    isolated = ("import subprocess,sys,json; p=sys.argv[1]; "
                "results=[json.loads(subprocess.run(['/usr/bin/python3',p,m],check=True,"
                "capture_output=True,text=True).stdout) for m in ('--apply','--apply','--remove')]; "
                "assert [r['changed'] for r in results]==[True,False,True]")
    result = {'applied':False,'mcp_isolated':False,'ipv6_guard':False,'private_record':str(record)}
    try:
        run(['/usr/bin/unshare','--net','--','/usr/bin/python3','-c',isolated,str(stage/'security_ipv6.py')])
        run(['/usr/bin/python3',str(stage/'security_deployment.py'),'--source-dir',str(stage),
             '--operator',args.operator,'--host',args.host,'--apply'],timeout=600)
        result['mcp_isolated'] = True
        # Core migration installs the same reviewed IPv6 helper in /opt.
        if not unit.exists():
            fd = os.open(unit, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(unit_body)
            unit.chmod(0o644)
        run(['/usr/bin/systemctl','daemon-reload'])
        run(['/usr/bin/systemctl','enable','--now',UNIT])
        run(['/usr/bin/systemctl','is-active','--quiet',UNIT])
        result.update(applied=True, ipv6_guard=True)
    except (OSError, subprocess.SubprocessError, ValueError):
        # Preserve successful MCP migration and uncertain firewall state for
        # explicit inspection. No broad rule rollback or unrelated unit removal.
        result['partial'] = result['mcp_isolated']
        # Expose only structured command metadata, never raw validation logs.
        for line in (record/'commands.log').read_text(errors='replace').splitlines():
            try:
                detail = json.loads(line)
            except (ValueError, TypeError):
                continue
            if (isinstance(detail, dict) and isinstance(detail.get('failed_command'), str)
                    and re.fullmatch(r'[a-zA-Z0-9_.-]+', detail['failed_command'])
                    and isinstance(detail.get('exit_code'), int)):
                result['failed_command'] = detail['failed_command']
                result['exit_code'] = detail['exit_code']
        print(json.dumps(result))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
