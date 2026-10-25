#!/usr/bin/env python3
"""Independent delivery-health alerting; never emits credentials or raw errors."""
import argparse
import datetime as dt
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import stat
import time

from monitor_relay import DeadlineSocket, save_private, timestamp
from bounded_network import bounded_call

GRACE_SECONDS = 180
REPEAT_SECONDS = 3600
SOURCE_SECONDS = 150
HEALTH_SECONDS = 90
SAFE_FAILURES = frozenset(('auth_required', 'authentication_failed', 'host_key_rejected',
    'tls_failed', 'timeout', 'network_unreachable', 'transport_failed', 'redirect_rejected',
    'invalid_configuration_or_response', 'local_execution_failed', 'http_rejected',
    'invalid_acknowledgement', 'invalid_http_response'))


def iso(now):
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()


def read_file(path, *, private=False, maximum=65536):
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute():
        raise ValueError('invalid_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_size > maximum
                or private and stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('unsafe_file')
        raw = stream.read(maximum+1)
    if len(raw) > maximum:
        raise ValueError('oversized_file')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('invalid_file')
    return value


def validate_config(config):
    for key in ('relay_health_file', 'observation_file', 'state_file', 'telegram_config_file'):
        value = config.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('invalid_configuration')
    return config


def parse_time(value, now):
    parsed = timestamp(value)
    if parsed > now+5:
        raise ValueError('future_time')
    return parsed


def assess(config, now):
    reasons = set()
    starts = []
    delivery_age = None
    try:
        health = read_file(config['relay_health_file'])
        checked = parse_time(health.get('checked_at'), now)
        if now-checked > HEALTH_SECONDS:
            reasons.add('relay_health_stale')
            starts.append(checked+HEALTH_SECONDS)
        delivered = health.get('delivered_at')
        if delivered is None:
            reasons.add('delivery_missing')
        else:
            delivered = parse_time(delivered, now)
            delivery_age = max(0, int(now-delivered))
            if now-delivered > SOURCE_SECONDS:
                reasons.add('delivery_stale')
                starts.append(delivered+SOURCE_SECONDS)
        if health.get('subscription_active') is False:
            reasons.add('subscription_inactive')
        failure = health.get('failure_class')
        if failure in SAFE_FAILURES:
            reasons.add('relay_' + failure)
            try:
                starts.append(parse_time(health.get('first_failure_at'), now))
            except (ValueError, TypeError):
                pass
    except (OSError, ValueError, TypeError):
        reasons.add('relay_health_unavailable')
    try:
        observation = read_file(config['observation_file'])
        observed = parse_time(observation.get('observed_at'), now)
        if now-observed > SOURCE_SECONDS:
            reasons.add('observer_stale')
            starts.append(observed+SOURCE_SECONDS)
    except (OSError, ValueError, TypeError):
        reasons.add('observation_unavailable')
    return {'reasons': sorted(reasons), 'last_delivery_age_seconds': delivery_age,
            'failure_since': min(starts) if starts else now}


def read_telegram(path):
    config = read_file(path, private=True, maximum=4096)
    token = config.get('token')
    chat_id = config.get('chat_id')
    if (not isinstance(token, str) or not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,128}', token)
            or isinstance(chat_id, bool) or not isinstance(chat_id, (str, int))
            or not re.fullmatch(r'-?[0-9]{1,24}|@[A-Za-z0-9_]{5,64}', str(chat_id))):
        raise ValueError('invalid_notification_credentials')
    return token, str(chat_id)


def telegram_send_inline(config, text, connection_factory=http.client.HTTPSConnection):
    connection = None
    response = None
    try:
        token, chat_id = read_telegram(config['telegram_config_file'])
        body = json.dumps({'chat_id': chat_id, 'text': text,
                           'disable_web_page_preview': True}).encode()
        if len(body) > 4096:
            return 'payload_rejected'
        connection = connection_factory('api.telegram.org', 443, timeout=10,
                                        context=ssl.create_default_context())
        deadline = time.monotonic()+15
        connection.connect()
        connection.sock = DeadlineSocket(connection.sock, deadline)
        connection.request('POST', '/bot'+token+'/sendMessage', body=body,
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            return 'notification_rejected'
        raw = bytearray()
        while len(raw) <= 4096:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            block = response.read1(min(1024,4097-len(raw)))
            if not block:
                break
            raw.extend(block)
        if len(raw) > 4096:
            return 'notification_response_invalid'
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('ok') is not True:
            return 'notification_rejected'
        return 'sent'
    except ssl.SSLError:
        return 'notification_tls_failed'
    except TimeoutError:
        return 'notification_timeout'
    except (ValueError, TypeError, KeyError):
        return 'notification_configuration_or_response_invalid'
    except (OSError, http.client.HTTPException):
        return 'notification_transport_failed'
    finally:
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()


def telegram_send(config, text, connection_factory=None):
    if connection_factory is not None:
        return telegram_send_inline(config, text, connection_factory)
    result = bounded_call(lambda: telegram_send_inline(config, text))
    if result.status == 'timeout':
        return 'notification_timeout'
    if result.status != 'completed' or not isinstance(result.value, str):
        return 'notification_transport_failed'
    return result.value


def notification_text(report):
    age = report['last_delivery_age_seconds']
    return ('[Dots recovery monitoring degraded]\n'
            + 'reasons: ' + ', '.join(report['reasons']) + '\n'
            + 'last_delivery_age_seconds: ' + ('unknown' if age is None else str(age)))


def run_once(config, now=None, sender=telegram_send):
    now = time.time() if now is None else now
    validate_config(config)
    path = Path(config['state_file'])
    try:
        previous = read_file(path, private=True)
    except FileNotFoundError:
        previous = {}
    report = assess(config, now)
    if not report['reasons']:
        state = {'checked_at': iso(now), 'degraded': False,
                 'last_attempt_at': previous.get('last_attempt_at'),
                 'last_notification_outcome': previous.get('last_notification_outcome')}
        save_private(path, state)
        return 'healthy'
    since = report['failure_since']
    if previous.get('degraded') is True:
        try:
            since = min(since, parse_time(previous.get('first_failure_at'), now))
        except (TypeError, ValueError):
            pass
    state = dict(previous, checked_at=iso(now), degraded=True, first_failure_at=iso(since),
                 reasons=report['reasons'], last_delivery_age_seconds=report['last_delivery_age_seconds'])
    outcome = 'grace_period'
    if now-since >= GRACE_SECONDS:
        last_attempt = None
        try:
            last_attempt = parse_time(previous.get('last_attempt_at'), now)
        except (TypeError, ValueError):
            pass
        # A last attempt survives process restarts, even when delivery was unknown.
        if last_attempt is not None and now-last_attempt < REPEAT_SECONDS:
            outcome = 'cooldown'
        else:
            state['last_attempt_at'] = iso(now)
            state['last_notification_outcome'] = 'unknown'
            save_private(path, state)
            try:
                outcome = sender(config, notification_text(report))
            except Exception:
                # Delivery may already have happened. Preserve attempt/cooldown.
                outcome = 'unknown'
            if outcome not in ('sent', 'payload_rejected', 'notification_rejected',
                    'notification_response_invalid', 'notification_tls_failed',
                    'notification_timeout', 'notification_configuration_or_response_invalid',
                    'notification_transport_failed'):
                outcome = 'unknown'
            state['last_notification_outcome'] = outcome
    save_private(path, state)
    return outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = validate_config(read_file(args.config, private=True))
        path = Path(config['state_file'])
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path.with_suffix('.notifier.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('unsafe_lock')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({'outcome': 'already_running'}))
                return
            outcome = run_once(config)
        print(json.dumps({'outcome': outcome}))
    except (OSError, ValueError, TypeError):
        print(json.dumps({'outcome': 'notifier_configuration_or_state_unavailable'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
