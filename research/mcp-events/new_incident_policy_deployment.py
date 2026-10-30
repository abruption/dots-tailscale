#!/usr/bin/env python3
"""Apply a pinned recovery policy without replacing local connector extensions.

Run only from root-protected reviewed staging. Preflight uses a read-only DB
connection; candidate schema adoption happens only after the broker is stopped.
Rollback restores code/configuration, never the authorization/event ledger.
"""
import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit

APP = Path('/opt/dots-events-research')
DATA = Path('/var/lib/dots-events')
CONFIG = DATA / 'config.json'
RECORDS = Path('/var/lib/dots-events-security-deploy')
UNITS = ('dots-events-recovery.socket', 'dots-events-recovery.service')
LIMIT = 262144
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
       'PYTHONDONTWRITEBYTECODE': '1'}

SUMMARY = '''import json,sqlite3,time
from pathlib import Path
from contextlib import closing
now=time.time(); path=Path('/var/lib/dots-events/recovery.sqlite')
with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
 obs=db.execute('SELECT stamp,ssh,ping FROM recovery_observation WHERE singleton=1').fetchone()
 result={'observation_fresh':bool(obs and 0<=now-obs[0]<=150),
 'external_healthy':bool(obs and obs[1]=='ok' and obs[2]=='ok'),
 'open_incidents':db.execute('SELECT count(*) FROM recovery_incidents WHERE closed IS NULL').fetchone()[0],
 'claims':db.execute('SELECT count(*) FROM recovery_incidents WHERE claim_id IS NOT NULL').fetchone()[0],
 'subscriptions':db.execute('SELECT count(*) FROM subscriptions').fetchone()[0],
 'active_subscriptions':db.execute('SELECT count(*) FROM subscriptions WHERE active=1 AND expires>?',(now,)).fetchone()[0],
 'sending_deliveries':db.execute("SELECT count(*) FROM outbox WHERE state='sending'").fetchone()[0]}
print(json.dumps(result))
'''

ADOPT = '''import sys,json
from pathlib import Path
sys.path.insert(0,'/opt/dots-events-research')
from server import load_recovery
cfg=json.loads(Path('/var/lib/dots-events/config.json').read_text())
core=load_recovery(cfg)
if core is None:raise RuntimeError('recovery disabled')
state=core.get_current_recovery(core.owner)
print(json.dumps({key:state.get(key) for key in ('policy','observation_fresh','claimable','eligibility_reason')}))
'''


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def trusted_directory(path, owner=0):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('canonical absolute directory required')
    for item in (path, *path.parents):
        info = item.lstat()
        expected = owner if item == path else 0
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected or info.st_mode & 0o022:
            raise ValueError('untrusted directory ancestry')


def read_regular(path, owner=0, limit=LIMIT):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > limit):
            raise ValueError('unsafe file')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            body = stream.read(limit + 1)
        if len(body) > limit:
            raise ValueError('file exceeds bound')
        return body, info
    finally:
        os.close(descriptor)


def pinned_body(path, expected, owner=0):
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('invalid source digest')
    body, info = read_regular(path, owner)
    if sha256(body) != expected:
        raise ValueError('source digest mismatch')
    return body, info


def atomic_replace(path, body, metadata):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.new-incident-policy-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _expression(node):
    return ast.dump(node, include_attributes=False)


def patch_server(body):
    """Add one reviewed load_recovery keyword, preserving every other byte."""
    text = body.decode('utf-8')
    tree = ast.parse(text)
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == 'load_recovery']
    if len(functions) != 1:
        raise ValueError('unique load_recovery function required')
    calls = [node for node in ast.walk(functions[0]) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'RecoveryCore']
    if len(calls) != 1 or len(calls[0].args) != 3:
        raise ValueError('unique supported RecoveryCore call required')
    call = calls[0]
    names = [keyword.arg for keyword in call.keywords]
    if len(set(names)) != len(names) or None in names:
        raise ValueError('duplicate or expanded policy keyword')
    expected = {
        'max_claims_per_24h': "cfg.get('recovery_max_claims_per_24h', 3)",
        'cooldown_seconds': "cfg.get('recovery_cooldown_seconds', 3600)",
        'new_incident_cooldown_seconds': "cfg.get('recovery_new_incident_cooldown_seconds')",
    }
    if set(names) not in (set(expected) - {'new_incident_cooldown_seconds'}, set(expected)):
        raise ValueError('unexpected recovery policy call shape')
    for keyword in call.keywords:
        if _expression(keyword.value) != _expression(ast.parse(expected[keyword.arg], mode='eval').body):
            raise ValueError('unexpected recovery policy expression')
    if 'new_incident_cooldown_seconds' in names:
        compile(text, '<existing-server>', 'exec')
        return body
    lines = body.splitlines(keepends=True)

    def offset(line, column):
        return sum(len(part) for part in lines[:line - 1]) + column

    closing = offset(call.end_lineno, call.end_col_offset) - 1
    last = call.keywords[-1]
    last_end = offset(last.end_lineno, last.end_col_offset)
    suffix = body[last_end:closing].strip()
    if body[closing:closing + 1] != b')' or suffix not in (b'', b','):
        raise ValueError('unexpected RecoveryCore closing syntax')
    insertion = (b',' if not suffix else b'') + b'\n' + b' ' * last.col_offset
    insertion += b"new_incident_cooldown_seconds=cfg.get('recovery_new_incident_cooldown_seconds')"
    result = body[:closing] + insertion + body[closing:]
    compile(result, '<patched-server>', 'exec')
    return result


def config_candidate(body, seconds):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate configuration key')
            result[key] = value
        return result
    value = json.loads(body, object_pairs_hook=unique)
    if not isinstance(value, dict) or value.get('enable_recovery') is not True:
        raise ValueError('enabled recovery configuration required')
    # SUMMARY, schema adoption, and the running server must address the same
    # ledger. Do not resolve an alternate/aliased path into the expected one.
    if type(value.get('runtime_dir')) is not str or value['runtime_dir'] != str(DATA):
        raise ValueError('runtime_dir must be the exact supported data directory')
    budget = value.get('recovery_max_claims_per_24h', 3)
    cooldown = value.get('recovery_cooldown_seconds', 3600)
    if type(budget) is not int or not 1 <= budget <= 24:
        raise ValueError('invalid existing claim budget')
    if type(cooldown) is not int or not 300 <= cooldown <= 86400:
        raise ValueError('invalid existing global cooldown')
    if type(seconds) is not int or not 300 <= seconds <= cooldown:
        raise ValueError('new incident interval must be from 300 to the existing cooldown')
    value['recovery_new_incident_cooldown_seconds'] = seconds
    return value, {'max_claims_per_24h': budget, 'cooldown_seconds': cooldown,
                   'new_incident_cooldown_seconds': seconds}


def safe_summary(value):
    if (not value['observation_fresh'] or not value['external_healthy']
            or value['open_incidents'] or value['sending_deliveries']):
        raise ValueError('recovery must be fresh, healthy and quiescent')


def acquire_lock(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('unsafe deployment lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--expected-core-sha256', required=True)
    parser.add_argument('--expected-server-sha256', required=True)
    parser.add_argument('--candidate-core-sha256', required=True)
    parser.add_argument('--new-incident-cooldown-seconds', type=int, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('root required')
    account = pwd.getpwnam('dots-events')
    os.umask(0o077)
    for path in (APP, RECORDS, args.source_dir, APP / '.venv', APP / '.venv/bin'):
        trusted_directory(path)
    trusted_directory(DATA, account.pw_uid)
    candidate, _ = pinned_body(args.source_dir / 'recovery_core.py', args.candidate_core_sha256)
    compile(candidate, '<candidate-recovery-core>', 'exec')
    # Reject a different ledger before even creating deployment locks/records.
    # The protected-ancestry check above separately rejects a symlink at DATA.
    initial_config, initial_meta = read_regular(CONFIG, account.pw_uid)
    if stat.S_IMODE(initial_meta.st_mode) != 0o600:
        raise ValueError('private configuration permissions required')
    config_candidate(initial_config, args.new_incident_cooldown_seconds)
    locks = []
    try:
        # The release updater owns the first lock; earlier policy helpers own the
        # second. Nonblocking acquisition in this fixed order avoids deadlocks.
        for path in (RECORDS / 'v0.5.0-code-update.lock', APP / '.policy-update.lock'):
            locks.append(acquire_lock(path))
        core, core_meta = pinned_body(APP / 'recovery_core.py', args.expected_core_sha256)
        server, server_meta = pinned_body(APP / 'server.py', args.expected_server_sha256)
        original, config_meta = read_regular(CONFIG, account.pw_uid)
        if stat.S_IMODE(config_meta.st_mode) != 0o600:
            raise ValueError('private configuration permissions required')
        cfg, policy = config_candidate(original, args.new_incident_cooldown_seconds)
        patched = patch_server(server)
        parsed_origin = urlsplit(cfg['origin'])
        if (parsed_origin.scheme != 'https' or not parsed_origin.hostname
                or parsed_origin.username or parsed_origin.password
                or parsed_origin.path not in ('', '/') or parsed_origin.query or parsed_origin.fragment
                or not re.fullmatch('[a-z0-9.-]+', parsed_origin.hostname)):
            raise ValueError('supported HTTPS origin required')
        host = parsed_origin.netloc
        record = Path(tempfile.mkdtemp(prefix='new-incident-policy-', dir=RECORDS))
        phase = 'preflight'

        def run(argv, check=True):
            result = subprocess.run(argv, capture_output=True, text=True, timeout=45,
                                    cwd='/', env=ENV)
            with (record / 'commands.log').open('a') as stream:
                stream.write((result.stdout + result.stderr)[-16384:])
            if check and result.returncode:
                raise RuntimeError('bounded deployment command failed')
            return result

        def service_python(script):
            return run(['runuser', '-u', 'dots-events', '--', str(APP / '.venv/bin/python'),
                        '-I', '-B', '-c', script]).stdout

        def summary():
            return json.loads(service_python(SUMMARY))

        def health():
            # systemctl is-active with multiple units succeeds when ANY unit is
            # active. Require each component independently before proceeding.
            for unit in (*UNITS, 'caddy.service'):
                run(['systemctl', 'is-active', '--quiet', unit])
            run(['runuser', '-u', 'caddy', '--', '/usr/bin/curl', '--fail', '--silent',
                 '--max-time', '10', '--unix-socket', '/run/dots-events-recovery.sock',
                 '-H', 'Host: ' + host, 'http://localhost/healthz'])

        try:
            before = summary()
            safe_summary(before)
            health()
            # Backups contain no DB snapshot: rolling back an authorization
            # ledger could replay a claim. These files are code/config only.
            for name, body in (('recovery_core.py.before', core), ('server.py.before', server),
                               ('config.before.json', original)):
                (record / name).write_bytes(body)
            (record / 'before.json').write_text(json.dumps(before))
        except Exception:
            result = {'applied': False, 'deferred': True, 'failed_phase': phase,
                      'services_unchanged': True, 'private_record': str(record)}
            (record / 'result.json').write_text(json.dumps(result))
            print(json.dumps(result))
            raise SystemExit(2) from None
        replacement = (json.dumps(cfg, sort_keys=True, indent=2) + '\n').encode()
        if core == candidate and patched == server and json.loads(original) == cfg:
            result = {'applied': False, 'already_current': True, 'state': before,
                      'policy': policy, 'private_record': str(record)}
            (record / 'result.json').write_text(json.dumps(result))
            print(json.dumps(result))
            return
        stop_intended = False
        writes_intended = False
        try:
            phase = 'stop_broker'
            stop_intended = True
            run(['systemctl', 'stop', *UNITS])
            safe_summary(summary())
            for path, body, owner in ((APP / 'recovery_core.py', core, 0),
                                      (APP / 'server.py', server, 0),
                                      (CONFIG, original, account.pw_uid)):
                if read_regular(path, owner)[0] != body:
                    raise ValueError('deployment inputs changed')
            phase = 'replace_code_and_config'
            writes_intended = True
            atomic_replace(APP / 'recovery_core.py', candidate, core_meta)
            atomic_replace(APP / 'server.py', patched, server_meta)
            atomic_replace(CONFIG, replacement, config_meta)
            phase = 'schema_adoption'
            observed = json.loads(service_python(ADOPT))
            if observed.get('policy', {}).get('new_incident_cooldown_seconds') != policy['new_incident_cooldown_seconds']:
                raise ValueError('new policy not exposed by installed server')
            for key in ('max_claims_per_24h', 'cooldown_seconds'):
                if observed['policy'].get(key) != policy[key]:
                    raise ValueError('existing policy changed')
            phase = 'start_broker'
            run(['systemctl', 'start', *UNITS])
            health()
            phase = 'verify'
            after = summary()
            for key in ('claims', 'subscriptions'):
                if after[key] < before[key]:
                    raise ValueError('authorization ledger count decreased')
            for path, expected, owner in ((APP / 'recovery_core.py', candidate, 0),
                                          (APP / 'server.py', patched, 0),
                                          (CONFIG, replacement, account.pw_uid)):
                if read_regular(path, owner)[0] != expected:
                    raise ValueError('post-deployment source mismatch')
            result = {'applied': True, 'before': before, 'after': after, 'policy': policy,
                      'mail_extensions_preserved': True, 'ledger_restored': False,
                      'private_record': str(record)}
            (record / 'result.json').write_text(json.dumps(result))
            print(json.dumps(result))
        except BaseException:
            failed_phase = phase
            rollback = 'not_needed'
            rollback_reason = None
            if stop_intended:
                try:
                    run(['systemctl', 'stop', *UNITS])
                    stopped_state = summary()
                    if stopped_state['claims'] != before['claims']:
                        # A restarted candidate might already have issued a
                        # grant. Older code cannot attest that grant's new
                        # provenance semantics. Leave the candidate and ledger
                        # intact, with the broker stopped for operator review.
                        rollback = 'operator_attention'
                        rollback_reason = ('new_claims_during_deployment'
                                           if stopped_state['claims'] > before['claims']
                                           else 'claim_ledger_changed_during_deployment')
                    else:
                        if writes_intended:
                            atomic_replace(APP / 'recovery_core.py', core, core_meta)
                            atomic_replace(APP / 'server.py', server, server_meta)
                            atomic_replace(CONFIG, original, config_meta)
                        # Additive evidence tables may remain. Never restore DB.
                        run(['systemctl', 'start', *UNITS])
                        health()
                        rollback = ('code_and_config_restored' if writes_intended
                                    else 'services_restarted_without_code_changes')
                except BaseException:
                    rollback = 'operator_attention'
                    rollback_reason = 'rollback_state_or_restore_unavailable'
            result = {'applied': False, 'failed_phase': failed_phase, 'rollback': rollback,
                      'ledger_restored': False, 'private_record': str(record)}
            if rollback_reason is not None:
                result['rollback_reason'] = rollback_reason
            (record / 'result.json').write_text(json.dumps(result))
            print(json.dumps(result))
            raise RuntimeError('deployment failed; inspect the root-private record') from None
    finally:
        for descriptor in reversed(locks):
            os.close(descriptor)


if __name__ == '__main__':
    main()
