#!/usr/bin/env python3
"""Forward fresh, minimized external observations over authenticated HTTPS or operator-configured SSH.

No observer modification, recovery command, or raw SSH output is exposed here.
The receiver deduplicates by observation time. Failed transports retry that same
observation while fresh; an ambiguous success is therefore safe to retry.
"""
import argparse
import datetime as dt
import fcntl
import json
import http.client
import io
import ssl
import sys
from dataclasses import dataclass, asdict
from urllib.parse import urlsplit
from bounded_network import bounded_call, ERROR_KINDS
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import time
import threading


def capture_runtime_paths(prefix, executable):
    """Freeze resolved installation identities before package-manager alias changes."""
    paths = []
    for value in (prefix, executable):
        try:
            paths.append(str(Path(value).resolve(strict=True)))
        except (OSError, RuntimeError):
            paths.append(None)
    return tuple(paths)


_RUNTIME_PATHS = capture_runtime_paths(sys.prefix, sys.executable)

STATES = frozenset('verified_running external_unreachable peer_reachable_ssh_failed probe_failed service_degraded ssh_ready_ping_unverified run_missing identity_missing prior_boot process_dead process_suspended heartbeat_missing heartbeat_identity_mismatch recovery_in_progress heartbeat_stale not_running metadata_invalid'.split())
ERRORS = frozenset('ok host_key_rejected authentication_failed connection_refused timeout network_unreachable remote_command_failed transport_failed invalid_probe_json local_execution_failed timeout_or_local_failure'.split())


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('invalid_timestamp')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('naive_timestamp')
    return result.timestamp()


def sanitize(row, now):
    if not isinstance(row, dict) or row.get('state') not in STATES:
        raise ValueError('invalid_observation')
    age = now - timestamp(row.get('observed_at'))
    if age > 150 or age < -5:
        raise ValueError('stale_or_future_observation')
    clean = {'observed_at': row['observed_at'], 'state': row['state']}
    for name, flags in [('ssh', ('command_succeeded', 'probe_valid')), ('peer_ping', ('responded',))]:
        source = row.get(name)
        if not isinstance(source, dict):
            raise ValueError('invalid_probe')
        result = {}
        for key in flags:
            if type(source.get(key)) is not bool:
                raise ValueError('invalid_probe_flag')
            result[key] = source[key]
        code = source.get('exit_code')
        if code is not None:
            if type(code) is not int or not -255 <= code <= 255:
                raise ValueError('invalid_exit_code')
            result['exit_code'] = code
        error = source.get('error_class')
        if error is not None:
            if error not in ERRORS:
                raise ValueError('invalid_error_class')
            result['error_class'] = error
        clean[name] = result
    return clean


def read_private(path, maximum=131072):
    # O_NOFOLLOW also prevents a check/open symlink race.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > maximum:
            raise ValueError('unsafe_input_file')
        return json.loads(stream.read(maximum + 1))


def save_private(path, value):
    target = Path(path)
    temp = target.with_name(target.name + '.new')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, target)


def ssh_argv(config):
    host = config['ssh_host']
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+', host):
        raise ValueError('invalid_ssh_host')
    paths = [config[k] for k in ('remote_python', 'remote_server', 'remote_config')]
    if any(not isinstance(p, str) or not p.startswith('/') or '\n' in p for p in paths):
        raise ValueError('invalid_remote_path')
    command = shlex.join([paths[0], paths[1], 'ingest-observation', '--config', paths[2]])
    return ['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o',
            'ServerAliveCountMax=1', host, command]


@dataclass(frozen=True)
class TransportResult:
    accepted: bool
    failure_class: str = 'ok'
    subscription_active: bool | None = None
    worker_error_kind: str | None = None
    worker_exit_code: int | None = None

    def __bool__(self):
        return self.accepted


def https_target(config):
    url = config.get('ingest_url')
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) <= 32 for c in url):
        raise ValueError('invalid_ingest_url')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path != '/monitor/observations'):
        raise ValueError('invalid_ingest_url')
    port = parsed.port or 443
    if not 1 <= port <= 65535:
        raise ValueError('invalid_ingest_url')
    token_path = config.get('bearer_token_file')
    if not isinstance(token_path, str) or not Path(token_path).is_absolute():
        raise ValueError('invalid_token_path')
    return parsed.hostname, port, parsed.path


def read_token(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 512):
            raise ValueError('unsafe_token_file')
        raw = stream.read(513)
    # One trailing newline is allowed for operator-created files. No header injection.
    token = raw.removesuffix(b'\n').decode('ascii')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise ValueError('invalid_token_file')
    return token


class DeadlineSocket:
    """Apply one elapsed deadline to request writes and every response read."""
    def __init__(self, sock, deadline):
        self.socket = sock
        self.deadline = deadline

    def remaining(self):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError()
        self.socket.settimeout(value)

    def sendall(self, data):
        self.remaining()
        return self.socket.sendall(data)

    def makefile(self, mode):
        owner = self
        raw = self.socket.makefile(mode, buffering=0)
        class Reader(io.RawIOBase):
            def close(self):
                try:
                    raw.close()
                finally:
                    super().close()
            def readable(self):
                return True
            def readinto(self, buffer):
                owner.remaining()
                return raw.readinto(buffer)
        return io.BufferedReader(Reader())

    def __getattr__(self, name):
        return getattr(self.socket, name)


def https_transmit_inline(config, observation, connection_factory=http.client.HTTPSConnection):
    connection = None
    response = None
    try:
        host, port, path = https_target(config)
        token = read_token(config['bearer_token_file'])
        body = json.dumps(observation, separators=(',', ':')).encode()
        if len(body) > 4096:
            return TransportResult(False, 'payload_too_large')
        connection = connection_factory(host, port, timeout=10, context=ssl.create_default_context())
        deadline = time.monotonic() + 15
        connection.connect()
        connection.sock = DeadlineSocket(connection.sock, deadline)
        connection.request('POST', path, body=body,
                           headers={'Authorization': 'Bearer ' + token,
                                    'Content-Type': 'application/json', 'Accept': 'application/json'})
        if connection.sock is not None:
            connection.sock.settimeout(max(0.01, deadline-time.monotonic()))
        response = connection.getresponse()
        if response.status in (401, 403):
            return TransportResult(False, 'authentication_failed')
        if 300 <= response.status < 400:
            return TransportResult(False, 'redirect_rejected')
        if response.status != 200:
            return TransportResult(False, 'http_rejected')
        chunks = bytearray()
        while len(chunks) <= 4096:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(1024, 4097-len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > 4096:
            return TransportResult(False, 'response_too_large')
        data = json.loads(chunks)
        if (not isinstance(data, dict) or data.get('accepted') is not True
                or data.get('observed_at') != observation['observed_at']):
            return TransportResult(False, 'invalid_acknowledgement')
        subscription_active = data.get('subscription_active')
        if 'subscription_active' in data and type(subscription_active) is not bool:
            return TransportResult(False, 'invalid_acknowledgement')
        return TransportResult(True, subscription_active=subscription_active)
    except ssl.SSLError:
        return TransportResult(False, 'tls_failed')
    except (TimeoutError, subprocess.TimeoutExpired):
        return TransportResult(False, 'timeout')
    except (ValueError, UnicodeError, KeyError):
        return TransportResult(False, 'invalid_configuration_or_response')
    except http.client.HTTPException:
        return TransportResult(False, 'invalid_http_response')
    except OSError:
        return TransportResult(False, 'network_unreachable')
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()


def https_transmit(config, observation, connection_factory=None):
    if connection_factory is not None:
        # Only explicit test doubles use the inline path. Live DNS, connect,
        # TLS and body processing all run within the supervised wall deadline.
        return https_transmit_inline(config, observation, connection_factory)
    result = bounded_call(lambda: asdict(https_transmit_inline(config, observation)))
    error_kind = getattr(result, 'error_kind', None)
    if not isinstance(error_kind, str) or error_kind not in ERROR_KINDS:
        error_kind = None
    exit_code = getattr(result, 'exit_code', None)
    if type(exit_code) is not int or not -255 <= exit_code <= 255:
        exit_code = None
    if result.status == 'timeout':
        return TransportResult(False, 'timeout', worker_error_kind=error_kind,
                               worker_exit_code=exit_code)
    if result.status != 'completed' or not isinstance(result.value, dict):
        return TransportResult(False, 'transport_failed', worker_error_kind=error_kind or 'invalid_worker_result',
                               worker_exit_code=exit_code)
    value = result.value
    if type(value.get('accepted')) is not bool or not isinstance(value.get('failure_class'), str):
        return TransportResult(False, 'transport_failed', worker_error_kind='invalid_worker_result', worker_exit_code=exit_code)
    subscription_active = value.get('subscription_active')
    if subscription_active is not None and type(subscription_active) is not bool:
        return TransportResult(False, 'transport_failed', worker_error_kind='invalid_worker_result', worker_exit_code=exit_code)
    return TransportResult(value['accepted'], value['failure_class'], subscription_active,
                           worker_exit_code=exit_code if not value['accepted'] else None)


def ssh_failure(stderr):
    # Inspect only bounded diagnostic text; never persist it or authentication URLs.
    text = (stderr or '')[:8192].lower()
    if any(x in text for x in ('tailscale ssh requires', 'additional authentication',
                              'check mode', 'https://login.tailscale.com/a/')):
        return 'auth_required'
    if any(x in text for x in ('host key verification failed', 'remote host identification has changed')):
        return 'host_key_rejected'
    if 'permission denied' in text:
        return 'authentication_failed'
    if 'connection refused' in text:
        return 'connection_refused'
    if any(x in text for x in ('timed out', 'timeout')):
        return 'timeout'
    return 'transport_failed'


def bounded_ssh_run(argv, **kwargs):
    captured = bytearray()
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE) as process:
        def drain():
            while True:
                block = process.stderr.read(1024)
                if not block:
                    return
                if len(captured) < 8192:
                    captured.extend(block[:8192-len(captured)])
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            try:
                process.stdin.write(kwargs['input'].encode())
                process.stdin.close()
            except BrokenPipeError:
                pass
            process.wait(timeout=kwargs['timeout'])
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            reader.join(timeout=1)
            raise subprocess.TimeoutExpired('ssh', kwargs['timeout'], stderr=bytes(captured))
        finally:
            reader.join(timeout=1)
        return subprocess.CompletedProcess(argv, process.returncode,
                                           stderr=bytes(captured).decode(errors='replace'))


def transmit(config, observation, runner=subprocess.run):
    transport = config.get('transport', 'ssh')
    if transport == 'https':
        return https_transmit(config, observation)
    if transport != 'ssh':
        return TransportResult(False, 'invalid_configuration')
    try:
        # Drain diagnostic output continuously, retaining at most 8 KiB.
        run = bounded_ssh_run if runner is subprocess.run else runner
        result = run(ssh_argv(config), input=json.dumps(observation) + '\n',
                        text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        timeout=20, check=False)
        return TransportResult(result.returncode == 0,
                               'ok' if result.returncode == 0 else ssh_failure(getattr(result, 'stderr', '')))
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr
        reason = ssh_failure(stderr)
        return TransportResult(False, reason if reason != 'transport_failed' else 'timeout')
    except OSError:
        return TransportResult(False, 'local_execution_failed')


def runtime_diagnostics():
    # Check startup identities, not aliases which may now resolve to a new runtime.
    # Paths remain process-local; health/transition output contains booleans only.
    def present(path, kind):
        try:
            return path is not None and getattr(Path(path), kind)()
        except (OSError, RuntimeError):
            return False
    return {'runtime_prefix_present': present(_RUNTIME_PATHS[0], 'is_dir'),
            'runtime_executable_present': present(_RUNTIME_PATHS[1], 'is_file'),
            'runtime_version': '.'.join(str(v) for v in sys.version_info[:3])}


def worker_diagnostics(kind, code):
    return {'worker_error_kind': kind if isinstance(kind, str) and kind in ERROR_KINDS else None,
            'worker_exit_code': code if type(code) is int and -255 <= code <= 255 else None}


def step(config, state, now=None, sender=transmit, diagnostics=None):
    now = time.time() if now is None else now
    if diagnostics is not None:
        diagnostics.update(runtime_diagnostics())
    try:
        row = sanitize(read_private(config['observation_file']), now)
    except (OSError, ValueError, TypeError):
        return state, 'observation_unavailable'
    previous = state.get('delivered_at')
    if previous and timestamp(row['observed_at']) <= timestamp(previous):
        return state, 'already_delivered'
    result = sender(config, row)
    if diagnostics is not None:
        diagnostics.update(last_attempt_at=dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                           failure_class=getattr(result, 'failure_class', 'ok' if result else 'transport_failed'),
                           subscription_active=getattr(result, 'subscription_active', None))
        diagnostics.update(worker_diagnostics(
            getattr(result, 'worker_error_kind', None) if not result else None,
            getattr(result, 'worker_exit_code', None) if not result else None))
    if result:
        return {'delivered_at': row['observed_at']}, 'delivered'
    return state, 'transport_failed'


def update_health(previous, state, outcome, diagnostics, now):
    checked = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()
    failed = outcome in ('transport_failed', 'observation_unavailable')
    first = previous.get('first_failure_at') if failed else None
    if failed and not first:
        first = checked
    delivered = state.get('delivered_at')
    stale = delivered is None or now - timestamp(delivered) > 150
    return {'checked_at': checked, 'outcome': outcome,
            'consecutive_transport_failures': previous.get('consecutive_transport_failures', 0)+1
                if outcome == 'transport_failed' else 0,
            'delivered_at': delivered, 'first_failure_at': first,
            'last_attempt_at': diagnostics.get('last_attempt_at', previous.get('last_attempt_at')),
            'failure_class': diagnostics.get('failure_class',
                'observation_unavailable' if outcome == 'observation_unavailable' else 'ok'),
            'stale_delivery': stale,
            'subscription_active': diagnostics.get('subscription_active', previous.get('subscription_active')),
            **worker_diagnostics(diagnostics.get('worker_error_kind') if failed else None,
                                 diagnostics.get('worker_exit_code') if failed else None),
            **runtime_diagnostics()}


def emit_health_transition(previous, current):
    old = (previous.get('stale_delivery'), previous.get('failure_class'))
    new = (current['stale_delivery'], current['failure_class'])
    old_worker = worker_diagnostics(previous.get('worker_error_kind'), previous.get('worker_exit_code'))
    new_worker = worker_diagnostics(current.get('worker_error_kind'), current.get('worker_exit_code'))
    runtime = runtime_diagnostics()
    keys = ('runtime_prefix_present', 'runtime_executable_present')
    old_runtime = tuple(previous.get(key) for key in keys)
    new_runtime = tuple(runtime[key] for key in keys)
    runtime_missing = not all(new_runtime)
    if (old != new or old_worker != new_worker or old_runtime != new_runtime) and (
            new != (False, 'ok') or previous or runtime_missing):
        print(json.dumps({'relay_health': 'degraded' if new != (False, 'ok') or runtime_missing else 'restored',
                          'failure_class': current['failure_class'],
                          'stale_delivery': current['stale_delivery'], **new_worker,
                          'previous_worker_error_kind': old_worker['worker_error_kind'],
                          'previous_worker_exit_code': old_worker['worker_exit_code'],
                          **runtime}), file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    config = read_private(args.config)
    if config.get('transport', 'ssh') == 'https':
        https_target(config)
    elif config.get('transport', 'ssh') == 'ssh':
        ssh_argv(config)
    else:
        raise ValueError('invalid_transport')
    interval = config.get('poll_seconds', 15)
    if interval not in (15, 30):
        raise ValueError('poll_seconds must be 15 or 30')
    state_path = Path(config['state_file'])
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with state_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            state = read_private(state_path)
            if not isinstance(state, dict):
                raise ValueError('invalid_state')
            if state.get('delivered_at'):
                timestamp(state['delivered_at'])
        except FileNotFoundError:
            state = {}
        try:
            health = read_private(state_path.with_suffix('.health.json'))
        except FileNotFoundError:
            health = {}
        failures = 0
        while True:
            diagnostics = {}
            checked_at = time.time()
            updated, outcome = step(config, state, checked_at, diagnostics=diagnostics)
            if updated != state:
                save_private(state_path, updated)
                state = updated
            failures = failures + 1 if outcome == 'transport_failed' else 0
            new_health = update_health(health, state, outcome, diagnostics, checked_at)
            emit_health_transition(health, new_health)
            save_private(state_path.with_suffix('.health.json'), new_health)
            health = new_health
            if args.once:
                print(json.dumps({'outcome': outcome}))
                return
            time.sleep(min(60, interval * 2 ** min(failures, 2)))


if __name__ == '__main__':
    main()
