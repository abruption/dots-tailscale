#!/usr/bin/env python3
"""Bounded, read-only lifecycle sampling. No recovery or service actions."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import tempfile
import time

MAX_BYTES = 10 * 1024 * 1024
MAX_DURATION = 24 * 3600
ROLES = {'shim', 'tailscaled', 'sshd'}
PUBLIC = {'tailscale-setup.sh', 'shim.py', 'dots-ts', 'dots_proxy.py'}

# stdout contains only selected public facts, never raw status, command lines,
# process environments, credentials or node state. Hash only public source files.
REMOTE = r'''
import hashlib,json,os,pathlib,stat,subprocess,sys
def public_read(path):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  s=os.fstat(f.fileno())
  if not stat.S_ISREG(s.st_mode) or s.st_size>1048576:raise ValueError('invalid_public_source')
  return f.read(1048577)
r=pathlib.Path('/workspace/.dots-tailscale')
boot=hashlib.sha256(pathlib.Path('/proc/sys/kernel/random/boot_id').read_bytes().strip()).hexdigest()
out={'uid':os.getuid(),'boot_sha256':boot,'uptime_s':float(pathlib.Path('/proc/uptime').read_text().split()[0]),'socket_present':pathlib.Path('/tmp/dots-tailscale-1000/tailscaled.sock').exists(),'processes':[],'log_bytes':{}}
paths={'tailscale-setup.sh':'setup.sh','shim.py':'shim/shim.py','dots-ts':'dots-ts','dots_proxy.py':'dots_proxy.py'}
out['source_present']={k:(r/v).is_file() for k,v in paths.items()}
if boot != sys.argv[1]:
 baseline={'source_hashes':{k:hashlib.sha256(public_read(r/v)).hexdigest() for k,v in paths.items() if (r/v).is_file()}}
 if (r/'source-manifest.json').is_file():
  raw=public_read(r/'source-manifest.json');baseline['manifest_sha256']=hashlib.sha256(raw).hexdigest()
  try:baseline['package_version']=json.loads(raw).get('package_version','unknown')
  except Exception:baseline['package_version']='unknown'
 try:baseline['tailscale_version']=subprocess.check_output([str(r/'tailscale'),'version'],timeout=2,text=True).splitlines()[0]
 except Exception:baseline['tailscale_version']='unknown'
 out['baseline']=baseline
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdecimal():continue
 try:
  argv=(p/'cmdline').read_bytes().split(b'\0');exe=os.readlink(p/'exe');role=None
  if exe==str(r/'tailscaled'):role='tailscaled'
  elif str(r/'shim/shim.py').encode() in argv:role='shim'
  elif exe=='/usr/sbin/sshd' and b'listener' in b' '.join(argv) and str(r/'sshd/sshd_config').encode() in b' '.join(argv):role='sshd'
  if role is None:continue
  s={l.split(':')[0]:l.split(':',1)[1].strip() for l in (p/'status').read_text().splitlines() if ':' in l}
  if len(out['processes'])>=32:
   print(json.dumps({'probe_error':'process_row_limit'}));sys.exit(2)
  out['processes'].append({'role':role,'rss_kib':int(s.get('VmRSS','0 kB').split()[0]),'threads':int(s.get('Threads','0')),'fd_count':len(list((p/'fd').iterdir()))})
 except (OSError,ValueError):pass
for name in ['shim','tailscaled','sshd']:
 p=pathlib.Path('/tmp/dots-tailscale-1000')/(name+'.log')
 try:out['log_bytes'][name]=p.stat().st_size
 except OSError:pass
try:
 raw=subprocess.check_output([str(r/'tailscale'),'--socket=/tmp/dots-tailscale-1000/tailscaled.sock','status','--json'],timeout=3,stderr=subprocess.DEVNULL)
 out['backend']=json.loads(raw).get('BackendState','unknown')
except Exception:out['backend']='unavailable'
print(json.dumps(out))
'''


def private_open(path, flags=os.O_RDONLY, mode=0o600):
    fd = os.open(path, flags | os.O_NOFOLLOW, mode)
    s = os.fstat(fd)
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600:
        os.close(fd)
        raise ValueError('unsafe_private_file')
    return fd


def read_private(path):
    with os.fdopen(private_open(path)) as f:
        if os.fstat(f.fileno()).st_size > 65536:
            raise ValueError('oversized_private_file')
        return json.load(f)


def save_private(path, value):
    fd, temp = tempfile.mkstemp(prefix='.collector-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(value, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def integer(value, upper=2**50):
    if type(value) is not int or not 0 <= value <= upper:
        raise ValueError('invalid_metric')
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('invalid_digest')
    return value


def sanitize(row):
    # Explicit schema projection: unknown fields, paths, raw logs and names vanish.
    result = {'uid': integer(row['uid']), 'boot_sha256': digest(row['boot_sha256'])}
    uptime = row['uptime_s']
    if type(uptime) not in (int, float) or not 0 <= uptime < 10**10:
        raise ValueError('invalid_uptime')
    result['uptime_s'] = uptime
    if type(row['socket_present']) is not bool:
        raise ValueError('invalid_socket')
    result['socket_present'] = row['socket_present']
    sources = row['source_present']
    if set(sources) != PUBLIC or any(type(v) is not bool for v in sources.values()):
        raise ValueError('invalid_source_presence')
    result['source_present'] = dict(sources)
    result['backend'] = row['backend'] if row.get('backend') in {'Running', 'NoState', 'NeedsLogin', 'NeedsMachineAuth', 'Stopped', 'Starting', 'unavailable', 'unknown'} else 'unknown'
    result['processes'] = []
    if not isinstance(row['processes'], list) or len(row['processes']) > 32:
        raise ValueError('invalid_processes')
    for p in row['processes']:
        if p['role'] not in ROLES:
            raise ValueError('invalid_role')
        result['processes'].append({'role': p['role'], **{k: integer(p[k]) for k in ('rss_kib', 'threads', 'fd_count')}})
    result['log_bytes'] = {k: integer(v) for k, v in row['log_bytes'].items() if k in ROLES}
    if 'baseline' in row:
        b = row['baseline']
        baseline = {'source_hashes': {k: digest(v) for k, v in b['source_hashes'].items() if k in PUBLIC}}
        for k in ['package_version', 'tailscale_version']:
            v = b.get(k, 'unknown')
            baseline[k] = v if isinstance(v, str) and re.fullmatch(r'(?:unknown|[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9.]+)?)', v) else 'unknown'
        if 'manifest_sha256' in b:
            baseline['manifest_sha256'] = digest(b['manifest_sha256'])
        result['baseline'] = baseline
    return result


def cap_reason(row):
    for p in row.get('processes', []):
        if p['rss_kib'] > 256 * 1024 or p['threads'] > 128 or p['fd_count'] > 512:
            return 'resource_cap'
    if sum(row.get('log_bytes', {}).values()) > 50 * 1024 * 1024:
        return 'runtime_log_cap'
    return None


def initial_state(state, now, duration):
    if state is None:
        return {'started_at': now, 'deadline': now + duration, 'last_boot': '', 'samples': 0, 'status': 'running'}
    if (not isinstance(state, dict) or state.get('status') not in {'running', 'expired', 'resource_cap', 'runtime_log_cap', 'evidence_cap'}
            or type(state.get('started_at')) not in (int, float)
            or type(state.get('deadline')) not in (int, float)
            or not 0 < state['deadline'] - state['started_at'] <= MAX_DURATION
            or type(state.get('samples')) is not int or state['samples'] < 0
            or not isinstance(state.get('last_boot'), str)
            or (state['last_boot'] and not re.fullmatch('[0-9a-f]{64}', state['last_boot']))):
        raise ValueError('invalid_persisted_state')
    # Existing deadline is authoritative: restarting never grants another day.
    return state


def bounded_output(argv, payload, timeout=20, maximum=65536):
    """Drain incrementally; never retain stderr or more than maximum stdout."""
    deadline = time.monotonic() + timeout
    output = bytearray()
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    position = 0
    data = payload.encode()
    outcome = 'ok'
    try:
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, 'input')
        selector.register(process.stdout, selectors.EVENT_READ, 'output')
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = 'timeout'
                break
            for key, _ in selector.select(remaining):
                if key.data == 'input':
                    try:
                        position += os.write(key.fd, data[position:position + 4096])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        position = len(data)
                    if position == len(data):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    try:
                        block = os.read(key.fd, min(4096, maximum - len(output) + 1))
                    except BlockingIOError:
                        continue
                    if not block:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    elif len(output) + len(block) > maximum:
                        outcome = 'oversized_response'
                        break
                    else:
                        output.extend(block)
            if outcome != 'ok':
                break
        if outcome == 'ok':
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                outcome = 'timeout'
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.wait()  # Always reap, including overflow and timeout.
        for stream in (process.stdin, process.stdout):
            if not stream.closed:
                stream.close()
    return outcome, process.returncode, bytes(output)


def ssh_sample(config, previous_boot):
    argv = ['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1', '-o', 'HostName=' + config['ssh_hostname'], config['ssh_host'], 'timeout 15s python3 - ' + (previous_boot or 'none')]
    try:
        outcome, returncode, output = bounded_output(argv, REMOTE)
    except OSError:
        return {'error': 'local_execution_failed'}
    if outcome != 'ok':
        return {'error': outcome}
    if returncode != 0:
        return {'error': 'ssh_or_remote_failed'}
    try:
        return sanitize(json.loads(output))
    except (ValueError, KeyError, TypeError):
        return {'error': 'invalid_response'}


def collect_once(config, state, now, sample=ssh_sample):
    if state['status'] != 'running':
        return state
    if now >= state['deadline']:
        state['status'] = 'expired'
        return state
    row = sample(config, state['last_boot'])
    row['observed_at_epoch'] = now
    payload = (json.dumps(row, sort_keys=True) + '\n').encode()
    fd = private_open(Path(config['data_dir']) / 'samples.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    with os.fdopen(fd, 'ab') as f:
        if os.fstat(f.fileno()).st_size + len(payload) > MAX_BYTES:
            state['status'] = 'evidence_cap'
            return state
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    state['samples'] += 1
    if 'boot_sha256' in row:
        state['last_boot'] = row['boot_sha256']
    state['status'] = cap_reason(row) or 'running'
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--duration', type=int, default=MAX_DURATION)
    args = parser.parse_args()
    if not 1 <= args.duration <= MAX_DURATION:
        raise ValueError('invalid_duration')
    config = read_private(args.config)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', config.get('ssh_host', '')) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', config.get('ssh_hostname', '')):
        raise ValueError('invalid_ssh_target')
    directory = Path(config['data_dir'])
    s = directory.lstat()
    if not directory.is_absolute() or not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise ValueError('unsafe_data_dir')
    lock = private_open(directory / 'collector.lock', os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        return
    try:
        path = directory / 'state.json'
        try:
            state = read_private(path)
        except FileNotFoundError:
            state = None
        state = initial_state(state, time.time(), args.duration)
        save_private(path, state)  # Persist the deadline before any network call.
        while state['status'] == 'running':
            state = collect_once(config, state, time.time())
            save_private(path, state)
            if args.once or state['status'] != 'running':
                break
            time.sleep(max(0, min(300, state['deadline'] - time.time())))
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
