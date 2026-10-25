#!/usr/bin/env python3
"""Root-owned reviewed staging only; update policy without resetting ledgers."""
import fcntl
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import tempfile

APP = Path('/opt/dots-events-research')
CONFIG = Path('/var/lib/dots-events/config.json')
FILES = ('recovery_core.py', 'server.py')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--max-claims', type=int, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not 1 <= args.max_claims <= 24:
        parser.error('root and a limit from 1 to 24 are required')
    stage = args.source_dir.resolve()
    for item in (stage, *stage.parents, *(stage/name for name in FILES)):
        st = item.lstat()
        if st.st_uid != 0 or st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode):
            raise ValueError('source must be root-owned and protected')
    for name in FILES:
        if not stat.S_ISREG((stage/name).lstat().st_mode):
            raise ValueError('source files must be regular')
    os.umask(0o077)
    lock = os.open(APP/'.policy-update.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    info = os.fstat(lock)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1:
        raise ValueError('unsafe deployment lock')
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    record = Path(tempfile.mkdtemp(prefix='policy-', dir='/var/lib/dots-events-security-deploy'))
    cfg = json.loads(CONFIG.read_text())
    original = CONFIG.read_bytes()
    (record/'config.before.json').write_bytes(original)
    for name in FILES:
        shutil.copyfile(APP/name, record/(name+'.before'))
    # Service identity needs a traversable, immutable candidate package.
    candidate = Path(tempfile.mkdtemp(prefix='.policy-', dir=APP))
    candidate.chmod(0o755)
    for name in FILES:
        shutil.copyfile(stage/name, candidate/name)
        (candidate/name).chmod(0o644)
    account = pwd.getpwnam('dots-events')

    def run(command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=45,
                                cwd='/', env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin',
                                              'PYTHONDONTWRITEBYTECODE':'1'})
        with (record/'commands.log').open('a') as log:
            log.write(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('command failed: '+Path(command[0]).name+'; '+str(record))
        return result.stdout

    def state(seed=False):
        script = """import sys,json
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from server import load_recovery
cfg=json.loads(Path(sys.argv[3]).read_text())
core=load_recovery(cfg)
if sys.argv[4]=='seed': core.pending_delivery_ids(core.owner)
s=core.get_current_recovery(core.owner)
print(json.dumps({k:s.get(k) for k in ('claimable','eligibility_reason','retry_at','policy','observation_fresh')}))
"""
        return json.loads(run(['runuser','-u','dots-events','--',str(APP/'.venv/bin/python'),
                               '-I','-B','-c',script,str(candidate),str(APP),str(CONFIG),
                               'seed' if seed else 'read']))

    def replace(path, body, uid=0, gid=0, mode=0o644):
        fd, name = tempfile.mkstemp(prefix='.policy-write-', dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(body); stream.flush(); os.fsync(stream.fileno())
                os.fchown(stream.fileno(),uid,gid); os.fchmod(stream.fileno(),mode)
            os.replace(name,path)
        finally:
            if os.path.exists(name): os.unlink(name)

    stopped = False
    try:
        # Seed deferred policy notifications using the previous policy first.
        # This makes no claim, sends no callback, and retains all original rows.
        before = state(seed=True)
        if not before['observation_fresh']:
            raise ValueError('fresh monitor observation required before policy change')
        stopped = True
        run(['systemctl','stop','dots-events-recovery.socket','dots-events-recovery.service'])
        for name in FILES:
            replace(APP/name,(candidate/name).read_bytes())
        cfg.update(recovery_max_claims_per_24h=args.max_claims,recovery_cooldown_seconds=3600)
        replace(CONFIG,json.dumps(cfg).encode(),account.pw_uid,account.pw_gid,0o600)
        run(['systemctl','start','dots-events-recovery.socket','dots-events-recovery.service'])
        run(['runuser','-u','caddy','--','curl','--fail','--silent','--max-time','10',
             '--unix-socket','/run/dots-events-recovery.sock','-H','Host: '+cfg['origin'].split('://',1)[1],
             'http://localhost/healthz'])
        after = state()
        print(json.dumps({'applied':True,'before':before,'after':after,'private_record':str(record)}))
    except BaseException:
        if stopped:
            run(['systemctl','stop','dots-events-recovery.socket','dots-events-recovery.service'])
            for name in FILES:
                replace(APP/name,(record/(name+'.before')).read_bytes())
            replace(CONFIG,original,account.pw_uid,account.pw_gid,0o600)
            run(['systemctl','start','dots-events-recovery.socket','dots-events-recovery.service'])
        raise
    finally:
        shutil.rmtree(candidate)
        os.close(lock)


if __name__ == '__main__':
    main()
