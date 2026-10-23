#!/usr/bin/env python3
"""One-time root migration. Default prints a read-only plan; --apply performs it.

Stage reviewed code + locked wheels in a ROOT-OWNED directory first. Never run
this helper from a writable application directory as root. A failed rollback
leaves both services stopped and points to a root-private journal.
"""
import argparse
import datetime
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import urllib.request
from contextlib import closing

from apply_routes import atomic, snapshot, authelia_validation_command, auth_ready

APP = Path('/opt/dots-events-research')
DATA = Path('/var/lib/dots-events')
RECORDS = Path('/var/lib/dots-events-security-deploy')
CADDY = Path('/etc/caddy/Caddyfile')
AUTH = Path('/etc/authelia/configuration.yml')
UNIT = 'dots-events-recovery'
DBS = ('oauth.sqlite', 'events.sqlite', 'recovery.sqlite')
CODE = ('server.py', 'oauth_adapter.py', 'event_core.py', 'recovery_core.py',
        'monitor_ingest.py', 'bounded_network.py', 'callback_transport.py',
        'security_ipv6.py', 'requirements.lock', 'offline-wheel.lock')



SOCKET_DENIED_PROBE = """import socket
s=socket.socket(socket.AF_UNIX)
try:
    s.connect('/run/dots-events-recovery.sock')
except PermissionError:
    raise SystemExit(0)
except OSError:
    raise SystemExit(2)
raise SystemExit(1)
"""


def create_application_directory(path):
    path.mkdir(mode=0o755)
    # Deployment uses umask 077; service users must still traverse app code.
    path.chmod(0o755)


def authelia_binary():
    # The privileged launcher intentionally excludes user-controlled PATH entries.
    # Distribution-independent installs may live in root-owned /usr/local/bin.
    binary = shutil.which('authelia', path='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    if binary is None:
        raise ValueError('Authelia executable not found in system directories')
    return binary


def lock_pins(path):
    pins={}
    for line in path.read_text().splitlines():
        text=line.strip()
        text=re.sub(r'\s+--hash=sha256:[0-9a-fA-F]{64}', '', text)
        if not text or text.startswith('#') or text.startswith('--hash=sha256:'):
            continue
        match=re.fullmatch(r'([a-zA-Z0-9_.-]+)==([^ ;\\]+)(?:\s+\\)?',text)
        if not match:
            raise ValueError('unsupported lock syntax')
        name=re.sub(r'[-_.]+','-',match[1]).lower()
        if name in pins:
            raise ValueError('duplicate package pin')
        pins[name]=match[2]
    if not pins:
        raise ValueError('empty dependency lock')
    return pins


def validate_offline_lock(original, wheels):
    if lock_pins(original) != lock_pins(wheels):
        raise ValueError('offline wheel versions differ from reviewed dependency lock')


def ledger_counts(root):
    result={}
    for name in DBS:
        with closing(sqlite3.connect((root/name).as_uri()+'?mode=ro',uri=True)) as db:
            tables=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            result[name]={table:db.execute('SELECT count(*) FROM "'+table.replace('"','""')+'"').fetchone()[0] for table in tables}
    return result


def trusted_tree(path):
    """Require every ancestor and staged item root-owned, no writable group/world."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('source must be absolute')
    for item in (path, *path.parents, *path.rglob('*')):
        st = item.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('source tree/ancestors must be root-owned and not writable by others')
        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            raise ValueError('source contains special file')
    for name in (*CODE, 'services/security/dots-events-recovery.service', 'services/security/dots-events-recovery.socket'):
        if not (path/name).is_file():
            raise ValueError('missing reviewed source: '+name)
    if not (path/'wheelhouse').is_dir():
        raise ValueError('offline wheelhouse is required')


def candidates(caddy, auth, host, operator):
    if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', host):
        raise ValueError('invalid hostname')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', operator):
        raise ValueError('invalid operator')
    marker = 'https://'+host+' {'
    start = caddy.find(marker)
    if start < 0 or caddy.count(marker) != 1:
        raise ValueError('expected unique Caddy host block')
    depth, end = 0, None
    for i in range(start+len(marker)-1, len(caddy)):
        depth += (caddy[i] == '{') - (caddy[i] == '}')
        if depth == 0:
            end = i+1
            break
    if end is None:
        raise ValueError('unbalanced host block')
    block = caddy[start:end]
    oldmatch = '@consent path /oauth/consent /oauth/consent/*'
    newmatch = oldmatch+' /authorize'
    matches = [line.strip() for line in block.splitlines() if line.strip().startswith('@consent ')]
    if matches not in ([oldmatch], [newmatch]) or block.count('reverse_proxy 127.0.0.1:18743') != 2:
        raise ValueError('unexpected existing proxy layout; manual review required')
    if matches == [oldmatch]:
        block = block.replace(oldmatch, newmatch)
    block = block.replace('reverse_proxy 127.0.0.1:18743', 'reverse_proxy unix//run/dots-events-recovery.sock')
    oldrule = ("    - domain: '"+host+"'\n"
               "      resources: ['^/oauth/consent(?:/[^?]*)?(?:\\?.*)?$']\n"
               "      policy: 'two_factor'\n"
               f"      subject: ['user:{operator}']\n")
    newrule = oldrule.replace("resources: ['", "resources: ['^/authorize(?:\\?.*)?$', '", 1)
    if auth.count(oldrule) == 1:
        auth = auth.replace(oldrule,newrule,1)
    elif auth.count(newrule) != 1:
        raise ValueError('exact single-user two-factor rule not found')
    return caddy[:start]+block+caddy[end:], auth


def open_incidents(root):
    with closing(sqlite3.connect((root/'recovery.sqlite').as_uri()+'?mode=ro', uri=True)) as db:
        return db.execute('SELECT count(*) FROM recovery_incidents WHERE closed IS NULL').fetchone()[0]


def copy_database(source, destination):
    if source.is_symlink() or not source.is_file() or destination.exists():
        raise ValueError('database source/target unsafe')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite integrity check failed')
            dst.commit()
    destination.chmod(0o600)


def copy_state(source, dest, uid=0, gid=0, on_created=None):
    dest.mkdir(mode=0o700)
    if on_created is not None:
        on_created()
    for name in DBS:
        copy_database(source/name, dest/name)
    cfgpath = source/'config.json'
    if cfgpath.is_symlink() or not cfgpath.is_file():
        raise ValueError('config source unsafe')
    shutil.copyfile(cfgpath, dest/'config.json')
    for path in dest.iterdir():
        path.chmod(0o600)
        os.chown(path,uid,gid)
    os.chown(dest,uid,gid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', required=True, type=Path)
    parser.add_argument('--operator', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    olduser = pwd.getpwnam(args.operator)
    OLD = Path(olduser.pw_dir)/'.local/share/dots-events-research'
    plan = {'service_user':'dots-events', 'application':str(APP), 'runtime':str(DATA),
            'socket':'/run/dots-events-recovery.sock', 'old_runtime':str(OLD),
            'requires':'root-owned reviewed stage with offline wheelhouse; no active incident',
            'ipv6':'not changed; separate interface-specific firewall review required'}
    if not args.apply:
        print(json.dumps({'applied':False,'plan':plan})); return
    if os.geteuid() != 0:
        parser.error('--apply requires root')
    os.umask(0o077)
    trusted_tree(args.source_dir)
    validate_offline_lock(args.source_dir/'requirements.lock',args.source_dir/'offline-wheel.lock')
    for path in (APP,DATA,Path('/etc/systemd/system/'+UNIT+'.service'),Path('/etc/systemd/system/'+UNIT+'.socket')):
        if path.exists() or path.is_symlink():
            raise ValueError('one-time migration target already exists: '+str(path))
    try:
        pwd.getpwnam('dots-events')
    except KeyError:
        pass
    else:
        raise ValueError('dedicated account already exists; review manually')
    if OLD.is_symlink() or not OLD.is_dir():
        raise ValueError('old runtime must be real directory')
    for name in (*DBS, 'config.json'):
        item=OLD/name
        st=item.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != olduser.pw_uid or st.st_mode & 0o077:
            raise ValueError('old state must be private regular files owned by existing service user')
    previous_config=json.loads((OLD/'config.json').read_text())
    if previous_config.get('origin') != 'https://'+args.host or previous_config.get('runtime_dir') != str(OLD):
        raise ValueError('operator/host do not match the existing broker configuration')
    grp.getgrnam('caddy')
    RECORDS.mkdir(mode=0o700,exist_ok=True)
    st=RECORDS.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077:
        raise ValueError('deployment records must be root-private')
    with (RECORDS/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        record=Path(tempfile.mkdtemp(prefix=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=RECORDS))
        def run(argv,timeout=120):
            with (record/'commands.log').open('ab') as output:
                result=subprocess.run(argv,stdout=output,stderr=output,timeout=timeout,cwd='/',
                                      env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8',
                                           'PYTHONDONTWRITEBYTECODE':'1'})
            if result.returncode:
                print(json.dumps({'failed_command':Path(argv[0]).name,'exit_code':result.returncode,'private_record':str(record)}),flush=True)
                raise RuntimeError('command failed; inspect root-private '+str(record))
        def userctl(*argv):
            run(['runuser','-u',args.operator,'--','env','XDG_RUNTIME_DIR=/run/user/'+str(olduser.pw_uid), 'systemctl','--user',*argv])
        oldc,cs=snapshot(CADDY); olda,aus=snapshot(AUTH)
        newc,newa=candidates(oldc.decode(),olda.decode(),args.host,args.operator)
        for name,data in [('Caddyfile.before',oldc),('authelia.before.yml',olda),('Caddyfile.candidate',newc.encode()),('authelia.candidate.yml',newa.encode())]:
            (record/name).write_bytes(data)
        run(['/usr/bin/caddy','validate','--config',str(record/'Caddyfile.candidate'),'--adapter','caddyfile'])
        run(authelia_validation_command(authelia_binary(),record/'authelia.candidate.yml'))
        if open_incidents(OLD):
            raise ValueError('active incident: defer cutover')
        # Prepare all dependencies while old service remains available.
        create_application_directory(APP)
        created_account=False
        try:
            for name in CODE:
                shutil.copyfile(args.source_dir/name,APP/name); (APP/name).chmod(0o644)
            run(['/usr/bin/python3','-m','venv',str(APP/'.venv')])
            run([str(APP/'.venv/bin/python'),'-I','-m','pip','--isolated','install','--only-binary=:all:','--no-index','--find-links',str(args.source_dir/'wheelhouse'),'--require-hashes','-r',str(APP/'offline-wheel.lock')],timeout=300)
            for item in APP.rglob('*'):
                if not item.is_symlink():
                    item.chmod(0o755 if item.is_dir() or item.stat().st_mode & 0o111 else 0o644)
            run(['useradd','--system','--user-group','--home-dir',str(DATA),'--no-create-home','--shell','/usr/sbin/nologin','dots-events'])
            created_account=True
            service=pwd.getpwnam('dots-events')
            run(['systemd-analyze','verify',str(args.source_dir/'services/security'/ (UNIT+'.service')),str(args.source_dir/'services/security'/(UNIT+'.socket'))])
        except BaseException:
            if created_account:
                run(['userdel','dots-events'])
            shutil.rmtree(APP)
            raise
        stopped=False; new_started=False; old_secured=False; units_installed=False; data_created=False
        try:
            if CADDY.read_bytes()!=oldc or AUTH.read_bytes()!=olda:
                raise ValueError('configuration changed during preparation')
            userctl('stop',UNIT+'.service'); stopped=True
            if open_incidents(OLD):
                raise ValueError('incident opened during preparation; defer')
            copy_state(OLD,record/'state.before')
            def created_data():
                nonlocal data_created
                data_created=True
            copy_state(OLD,DATA,service.pw_uid,service.pw_gid,on_created=created_data)
            cfg=json.loads((DATA/'config.json').read_text())
            cfg.update(runtime_dir=str(DATA),listen_fd=3,trusted_proxy_transport='unix')
            (DATA/'config.json').write_text(json.dumps(cfg))
            for suffix in ('service','socket'):
                target=Path('/etc/systemd/system/'+UNIT+'.'+suffix)
                shutil.copyfile(args.source_dir/'services/security'/target.name,target); target.chmod(0o644)
            units_installed=True
            run(['systemctl','daemon-reload'])
            run(['systemctl','enable','--now',UNIT+'.socket'])
            # Mark before starting: even failed startup may write durable state.
            new_started=True
            run(['systemctl','start',UNIT+'.service'])
            run(['runuser','-u','caddy','--','/usr/bin/curl','--fail','--silent','--max-time','5','--unix-socket','/run/dots-events-recovery.sock','-H','Host: '+args.host,'http://localhost/healthz'])
            run(['runuser','-u',args.operator,'--','/usr/bin/python3','-c', SOCKET_DENIED_PROBE])
            (record/'ledger-counts.json').write_text(json.dumps({'before':ledger_counts(record/'state.before'),'after':ledger_counts(DATA)}))
            atomic(AUTH,newa.encode(),aus); atomic(CADDY,newc.encode(),cs)
            run(['systemctl','restart','authelia']); auth_ready()
            run(['systemctl','reload','caddy'])
            run(['systemctl','is-active','--quiet',UNIT+'.service','caddy','authelia'])
            userctl('disable',UNIT+'.service')
            # Keep evidence in place but remove old shared account's secret access.
            os.chown(OLD,0,0); OLD.chmod(0o700); old_secured=True
            print(json.dumps({'applied':True,'private_record':str(record)}))
        except BaseException:
            if stopped:
                try:
                    if units_installed:
                        run(['systemctl','stop',UNIT+'.socket',UNIT+'.service'])
                        run(['systemctl','disable',UNIT+'.socket'])
                    if new_started:
                        latest=record/'state.rollback-latest'
                        copy_state(DATA,latest)
                        # All snapshots verified before replacing any old ledger.
                        for name in DBS:
                            for suffix in ('-wal','-shm'):
                                side=OLD/(name+suffix)
                                if side.exists(): side.unlink()
                            content=(latest/name).read_bytes()
                            metadata=(OLD/name).stat()
                            atomic(OLD/name,content,metadata)
                    if old_secured:
                        os.chown(OLD,olduser.pw_uid,olduser.pw_gid)
                    atomic(AUTH,olda,aus); atomic(CADDY,oldc,cs)
                    run(['systemctl','restart','authelia']); auth_ready()
                    run(['systemctl','reload','caddy'])
                    userctl('enable','--now',UNIT+'.service')
                except BaseException:
                    raise RuntimeError('rollback stopped; preserve both runtimes and inspect '+str(record)) from None
            if not new_started:
                if data_created and DATA.exists():
                    shutil.rmtree(DATA)
                if units_installed:
                    for suffix in ('service','socket'):
                        Path('/etc/systemd/system/'+UNIT+'.'+suffix).unlink()
                    run(['systemctl','daemon-reload'])
                run(['userdel','dots-events'])
                shutil.rmtree(APP)
            raise

if __name__=='__main__':
    main()
