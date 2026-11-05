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
from recovery_budget_diagnostics import (FOLLOWUP_REASON_CODES,
                                         sanitize_recovery_diagnostics)

GRACE_SECONDS = 180
REPEAT_SECONDS = 3600
SOURCE_SECONDS = 150
HEALTH_SECONDS = 90
SAFE_FAILURES = frozenset(('auth_required', 'authentication_failed', 'host_key_rejected',
    'tls_failed', 'timeout', 'network_unreachable', 'transport_failed', 'redirect_rejected',
    'invalid_configuration_or_response', 'local_execution_failed', 'http_rejected',
    'invalid_acknowledgement', 'invalid_http_response'))
WARNING_KEYS = ('claim_budget_near_limit', 'claim_budget_exhausted', 'followup_operator_attention')
WARNING_OUTCOMES = frozenset(('sent', 'payload_rejected', 'notification_rejected',
    'notification_response_invalid', 'notification_tls_failed', 'notification_timeout',
    'notification_configuration_or_response_invalid', 'notification_transport_failed', 'unknown'))


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
    enabled = config.get('operator_recovery_warnings', False)
    percent = config.get('recovery_budget_warning_percent', 80)
    if type(enabled) is not bool or type(percent) is not int or not 50 <= percent <= 99:
        raise ValueError('invalid_operator_warning_configuration')
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
    relay_health = None
    try:
        relay_health = read_file(config['relay_health_file'])
        checked = parse_time(relay_health.get('checked_at'), now)
        if now-checked > HEALTH_SECONDS:
            reasons.add('relay_health_stale')
            starts.append(checked+HEALTH_SECONDS)
        delivered = relay_health.get('delivered_at')
        if delivered is None:
            reasons.add('delivery_missing')
        else:
            delivered = parse_time(delivered, now)
            delivery_age = max(0, int(now-delivered))
            if now-delivered > SOURCE_SECONDS:
                reasons.add('delivery_stale')
                starts.append(delivered+SOURCE_SECONDS)
        if relay_health.get('subscription_active') is False:
            reasons.add('subscription_inactive')
        failure = relay_health.get('failure_class')
        if failure in SAFE_FAILURES:
            reasons.add('relay_' + failure)
            try:
                starts.append(parse_time(relay_health.get('first_failure_at'), now))
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
    warnings = assess_recovery_warnings(config, relay_health, now)
    if config.get('operator_recovery_warnings', False) is True:
        if warnings.get('status') == 'unsupported':
            reasons.add('recovery_diagnostics_unsupported')
            starts.append(now)
        elif warnings.get('status') == 'unknown':
            reasons.add('recovery_diagnostics_unavailable')
            starts.append(now)
        elif warnings.get('followup_status') == 'unknown':
            reasons.add('followup_diagnostics_unavailable')
            starts.append(now)
    return {'reasons': sorted(reasons), 'last_delivery_age_seconds': delivery_age,
            'failure_since': min(starts) if starts else now,
            'recovery_warnings': warnings}


def assess_recovery_warnings(config, relay_health, now):
    """Compute alert levels only from a fresh, validated authenticated ACK."""
    if config.get('operator_recovery_warnings', False) is not True:
        return {'status': 'disabled'}
    if not isinstance(relay_health, dict):
        return {'status': 'unknown'}
    diagnostics_status = relay_health.get('recovery_diagnostics_status')
    if diagnostics_status == 'unsupported':
        return {'status': 'unsupported'}
    if diagnostics_status != 'current':
        return {'status': 'unknown'}
    try:
        summary = sanitize_recovery_diagnostics(relay_health.get('recovery_diagnostics'), now)
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return {'status': 'unknown'}
    cap = summary['cap']
    used = summary['claims_issued']
    percent = config.get('recovery_budget_warning_percent', 80)
    threshold = (cap * percent + 99) // 100
    budget_level = ('exhausted' if used >= cap else
                    'near_limit' if used >= threshold else 'normal')
    followup = summary['unclaimed_followup']
    followup_status = ('unknown' if not followup['available'] or followup['operator_attention'] is None
                       else 'attention' if followup['operator_attention'] else 'clear')
    return {'status': 'current', 'as_of': summary['as_of'],
            'claims_issued': used, 'cap': cap, 'warning_threshold': threshold,
            'budget_level': budget_level, 'externally_closed': summary['externally_closed'],
            'sustained_confirmed': summary['sustained_confirmed'],
            'budget_release_at': summary['budget_release_at'],
            'first_reports': summary['first_reports'], 'current_outcomes': summary['current_outcomes'],
            'followup_status': followup_status,
            'followup_reason': followup['reason_code'] if followup_status == 'attention' else None}


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


def recovery_warning_text(warnings, due):
    lines = ['[Dots recovery operator warning]']
    if 'claim_budget_near_limit' in due or 'claim_budget_exhausted' in due:
        level = 'exhausted' if 'claim_budget_exhausted' in due else 'near_limit'
        lines.append('claim_issuance_budget: ' + level)
        lines.append('claim_grants_issued_in_rolling_24h: %d/%d' %
                     (warnings['claims_issued'], warnings['cap']))
        lines.append('warning_threshold: %d' % warnings['warning_threshold'])
        reports = warnings['first_reports']
        lines.append('first_report_outcomes_success_failed_blocked_unknown_missing_other: ' +
                     '/'.join(str(reports[key]) for key in
                              ('success', 'failed', 'blocked', 'unknown', 'missing', 'other')))
        lines.append('current_outcome_labels_success_failed_blocked_unknown_missing_other: ' +
                     '/'.join(str(warnings['current_outcomes'][key]) for key in
                              ('success', 'failed', 'blocked', 'unknown', 'missing', 'other')))
        lines.append('externally_closed_vs_sustained_confirmed: %d/%d' %
                     (warnings['externally_closed'], warnings['sustained_confirmed']))
        lines.append('reported success is not an independent execution trace')
        if 'claim_budget_exhausted' in due:
            lines.append('earliest_budget_release_reference_utc: ' +
                         str(warnings['budget_release_at']))
            lines.append('after this reference, review current eligibility; time alone is not authorization')
    if 'followup_operator_attention' in due:
        lines.append('unclaimed_followup_operator_attention: ' +
                     str(warnings['followup_reason'] or 'attention_other'))
    lines.append('snapshot_utc: ' + warnings['as_of'])
    return '\n'.join(lines)


def _empty_operator_warning_state():
    return {key: {'active': False, 'last_attempt_at': None,
                  'last_outcome': None, 'reason_code': None}
            for key in WARNING_KEYS}


def _operator_warning_state(state, now=None):
    now = time.time() if now is None else now
    raw = state.get('operator_warning_state')
    if raw is None:
        return _empty_operator_warning_state()
    if type(raw) is not dict or set(raw) != set(WARNING_KEYS):
        return None
    clean = {}
    for key in WARNING_KEYS:
        entry = raw[key]
        if (type(entry) is not dict
                or set(entry) != {'active', 'last_attempt_at', 'last_outcome', 'reason_code'}
                or type(entry['active']) is not bool):
            return None
        last = entry['last_attempt_at']
        if last is not None:
            try:
                parse_time(last, now)
            except (ValueError, TypeError):
                return None
        outcome = entry['last_outcome']
        if outcome is not None and outcome not in WARNING_OUTCOMES:
            return None
        reason = entry['reason_code']
        if key == 'followup_operator_attention':
            if reason is not None and reason not in FOLLOWUP_REASON_CODES:
                return None
        elif reason is not None:
            return None
        clean[key] = dict(entry)
    return clean


def process_operator_warnings(config, state, warnings, now, sender=telegram_send):
    """Use the existing sender with persistent per-category deduplication."""
    if config.get('operator_recovery_warnings', False) is not True:
        return state, 'disabled'
    if not isinstance(warnings, dict) or warnings.get('status') != 'current':
        # Missing/stale diagnostics are not an attempted notification. Keep
        # this distinct from a sender whose delivery outcome is unknown.
        return state, 'diagnostics_unknown'
    warning_state = _operator_warning_state(state, now)
    if warning_state is None:
        return state, 'state_unavailable'

    active = {
        'claim_budget_near_limit': warnings['budget_level'] == 'near_limit',
        'claim_budget_exhausted': warnings['budget_level'] == 'exhausted',
        'followup_operator_attention': warnings['followup_status'] == 'attention',
    }
    followup_known = warnings['followup_status'] in ('attention', 'clear')
    reason = warnings.get('followup_reason')
    due = []
    for key in WARNING_KEYS:
        entry = warning_state[key]
        if key == 'followup_operator_attention' and not followup_known:
            # A current budget snapshot can lack #79 follow-up state. Preserve
            # its prior alert episode rather than treating absence as clear.
            continue
        if not active[key]:
            entry['active'] = False
            entry['reason_code'] = None
            continue
        last = entry['last_attempt_at']
        if last is None:
            due.append(key)
        else:
            try:
                elapsed = now - parse_time(last, now)
            except (ValueError, TypeError):
                return state, 'state_unavailable'
            if elapsed >= REPEAT_SECONDS:
                due.append(key)
        entry['active'] = True
        entry['reason_code'] = reason if key == 'followup_operator_attention' else None

    state['operator_warning_state'] = warning_state
    if not due:
        save_private(config['state_file'], state)
        if any(entry['active'] for entry in warning_state.values()):
            return state, 'operator_warning_cooldown'
        return state, 'not_due'

    # Save attempts before sending. An ambiguous send is not retried immediately.
    for key in due:
        warning_state[key]['last_attempt_at'] = iso(now)
        warning_state[key]['last_outcome'] = 'unknown'
    save_private(config['state_file'], state)
    try:
        outcome = sender(config, recovery_warning_text(warnings, due))
    except Exception:
        outcome = 'unknown'
    if outcome not in WARNING_OUTCOMES:
        outcome = 'unknown'
    for key in due:
        warning_state[key]['last_outcome'] = outcome
    state['operator_warning_state'] = warning_state
    save_private(config['state_file'], state)
    return state, outcome


def _diagnostics_incomplete(warnings):
    return (not isinstance(warnings, dict) or warnings.get('status') != 'current'
            or warnings.get('followup_status') == 'unknown')


def run_once(config, now=None, sender=telegram_send):
    now = time.time() if now is None else now
    validate_config(config)
    path = Path(config['state_file'])
    try:
        previous = read_file(path, private=True)
    except FileNotFoundError:
        previous = {}
    report = assess(config, now)
    if (config.get('operator_recovery_warnings', False) is True
            and _operator_warning_state(previous, now) is None):
        # Keep the corrupt/future state intact, but surface a fixed monitor
        # fault through the existing grace/repeat notification path.
        report['reasons'] = sorted(set(report['reasons']) | {'operator_warning_state_unavailable'})
    previous_operator_warnings = previous.get('operator_warning_state')
    if not report['reasons']:
        state = {'checked_at': iso(now), 'degraded': False,
                 'last_attempt_at': previous.get('last_attempt_at'),
                 'last_notification_outcome': previous.get('last_notification_outcome')}
        if previous_operator_warnings is not None:
            state['operator_warning_state'] = previous_operator_warnings
        save_private(path, state)
        state, warning_outcome = process_operator_warnings(
            config, state, report['recovery_warnings'], now, sender)
        if config.get('operator_recovery_warnings', False) is True:
            if _diagnostics_incomplete(report['recovery_warnings']):
                return 'recovery_diagnostics_unknown'
            if warning_outcome not in ('disabled', 'not_due'):
                return warning_outcome
        return 'healthy'
    since = report['failure_since']
    if previous.get('degraded') is True:
        try:
            since = min(since, parse_time(previous.get('first_failure_at'), now))
        except (TypeError, ValueError):
            pass
    state = dict(previous, checked_at=iso(now), degraded=True, first_failure_at=iso(since),
                 reasons=report['reasons'], last_delivery_age_seconds=report['last_delivery_age_seconds'])
    if previous_operator_warnings is not None:
        state['operator_warning_state'] = previous_operator_warnings
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
    state, warning_outcome = process_operator_warnings(
        config, state, report['recovery_warnings'], now, sender)
    if config.get('operator_recovery_warnings', False) is True:
        # Actual send results outrank wait/unknown-data markers. Retain the
        # primary send failure if both paths attempted a notification.
        if outcome in WARNING_OUTCOMES - {'sent'}:
            return outcome
        if warning_outcome in WARNING_OUTCOMES:
            return 'operator_warning_' + warning_outcome
        if outcome == 'sent':
            return outcome
        if _diagnostics_incomplete(report['recovery_warnings']):
            if 'operator_warning_state_unavailable' in report['reasons']:
                return outcome
            return outcome if outcome not in ('grace_period', 'cooldown') else 'recovery_diagnostics_unknown'
        if warning_outcome == 'operator_warning_cooldown':
            return (outcome if outcome not in ('grace_period', 'cooldown')
                    else 'operator_warning_cooldown')
        if warning_outcome in ('disabled', 'not_due', 'state_unavailable'):
            return outcome
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
