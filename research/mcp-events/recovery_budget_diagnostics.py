"""Small aggregate-only recovery diagnostics for the authenticated relay ACK."""
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import sqlite3
from pathlib import Path

WINDOW_SECONDS = 86400
REPORT_OUTCOMES = ('success', 'failed', 'blocked', 'unknown')
COUNT_BUCKETS = frozenset((*REPORT_OUTCOMES, 'missing', 'other'))
FOLLOWUP_REASON_CODES = frozenset((
    'exhausted', 'expired', 'storage_full', 'legacy_receipt_unavailable',
    'receipt_tracking_full', 'delivery_unconfirmed', 'receipt_time_invalid',
    'clock_regressed', 'execution_state_unknown', 'attention_other',
))
FOLLOWUP_ATTENTION_REASONS = FOLLOWUP_REASON_CODES - {'attention_other'}
DIAGNOSTIC_KEYS = frozenset((
    'schema_version', 'as_of', 'window_seconds', 'cap', 'claims_issued',
    'budget_release_at',
    'first_reports', 'current_outcomes', 'externally_closed',
    'sustained_confirmed', 'unclaimed_followup',
))


def _strict_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('invalid diagnostics timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('naive diagnostics timestamp')
    try:
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError('out-of-range diagnostics timestamp') from exc


def _strict_count(value, maximum=1_000_000_000):
    return type(value) is int and 0 <= value <= maximum


def _counts(row, prefix):
    result = {key: row[f'{prefix}_{key}'] or 0 for key in REPORT_OUTCOMES}
    result['missing'] = row[f'{prefix}_missing'] or 0
    result['other'] = row[f'{prefix}_other'] or 0
    return result


def _followup_projection(state, now):
    """Return only fixed status values; never forward incident or event IDs."""
    if not isinstance(state, dict):
        return {'available': False, 'operator_attention': None, 'reason_code': None,
                'awaiting_response': None, 'response_deadline_at': None}
    followup = state.get('unclaimed_followup')
    if not isinstance(followup, dict) or type(followup.get('operator_attention')) is not bool:
        return {'available': False, 'operator_attention': None, 'reason_code': None,
                'awaiting_response': None, 'response_deadline_at': None}
    reason = followup.get('reason')
    attention = followup['operator_attention']
    if reason in FOLLOWUP_ATTENTION_REASONS and not attention:
        return {'available': False, 'operator_attention': None, 'reason_code': None,
                'awaiting_response': None, 'response_deadline_at': None}
    reason_code = (reason if reason in FOLLOWUP_REASON_CODES else 'attention_other') if attention else None
    awaiting = reason == 'awaiting_response'
    deadline = followup.get('response_deadline_at')
    if awaiting:
        if (attention or isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                or not math.isfinite(deadline) or deadline <= now
                or deadline > now + WINDOW_SECONDS + 5):
            return {'available': False, 'operator_attention': None, 'reason_code': None,
                    'awaiting_response': None, 'response_deadline_at': None}
        deadline = datetime.fromtimestamp(deadline, timezone.utc).isoformat()
    elif deadline is not None:
        return {'available': False, 'operator_attention': None, 'reason_code': None,
                'awaiting_response': None, 'response_deadline_at': None}
    return {'available': True, 'operator_attention': attention, 'reason_code': reason_code,
            'awaiting_response': awaiting, 'response_deadline_at': deadline}


@contextmanager
def recovery_read_snapshot(core):
    """Open one query-only snapshot for current status and aggregate reads."""
    # Absolute without resolving symlinks, matching sqlite's normal CWD-based
    # meaning for relative config paths while keeping the URI read-only.
    path = Path(core.db_path).absolute()
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        # Establish the read snapshot before callers sample the policy clock.
        db.execute('SELECT stamp FROM recovery_observation WHERE singleton=1').fetchone()
        yield db
    finally:
        db.rollback()
        db.close()


def build_recovery_diagnostics(core, db=None, now=None):
    """Project a single rolling-window SQL aggregate into a bounded safe summary.

    With no `db`, status and aggregate reads share one read-only SQLite snapshot.
    Claim membership uses the same strict `claimed > now - 86400` predicate as
    RecoveryCore._state. Outcomes never affect membership, so unknown and
    unreported claims continue consuming the cap.
    """
    if db is None:
        with recovery_read_snapshot(core) as snapshot:
            return build_recovery_diagnostics(core, snapshot, now)
    now = core.clock() if now is None else now
    state = core._state(db, now=now)
    cap = core.max_claims_per_24h
    if type(cap) is not int or not 1 <= cap <= 24:
        raise ValueError('invalid claim budget')
    cutoff = now - WINDOW_SECONDS
    row = db.execute('''
        SELECT COUNT(*) AS issued,
          SUM(CASE WHEN r.outcome='success' THEN 1 ELSE 0 END) AS first_success,
          SUM(CASE WHEN r.outcome='failed' THEN 1 ELSE 0 END) AS first_failed,
          SUM(CASE WHEN r.outcome='blocked' THEN 1 ELSE 0 END) AS first_blocked,
          SUM(CASE WHEN r.outcome='unknown' THEN 1 ELSE 0 END) AS first_unknown,
          SUM(CASE WHEN r.incident_id IS NULL OR r.outcome IS NULL THEN 1 ELSE 0 END) AS first_missing,
          SUM(CASE WHEN r.incident_id IS NOT NULL AND r.outcome NOT IN
                    ('success','failed','blocked','unknown') THEN 1 ELSE 0 END) AS first_other,
          SUM(CASE WHEN i.outcome='success' THEN 1 ELSE 0 END) AS current_success,
          SUM(CASE WHEN i.outcome='failed' THEN 1 ELSE 0 END) AS current_failed,
          SUM(CASE WHEN i.outcome='blocked' THEN 1 ELSE 0 END) AS current_blocked,
          SUM(CASE WHEN i.outcome='unknown' THEN 1 ELSE 0 END) AS current_unknown,
          SUM(CASE WHEN i.outcome IS NULL THEN 1 ELSE 0 END) AS current_missing,
          SUM(CASE WHEN i.outcome IS NOT NULL AND i.outcome NOT IN
                    ('success','failed','blocked','unknown') THEN 1 ELSE 0 END) AS current_other,
          SUM(CASE WHEN i.closed IS NOT NULL THEN 1 ELSE 0 END) AS externally_closed,
          SUM(CASE WHEN c.confirmed_at IS NOT NULL THEN 1 ELSE 0 END) AS sustained_confirmed
        FROM recovery_incidents AS i
        LEFT JOIN recovery_reports AS r ON r.incident_id=i.id
        LEFT JOIN recovery_confirmations AS c ON c.incident_id=i.id
        WHERE i.claimed IS NOT NULL AND i.claimed > ?
    ''', (cutoff,)).fetchone()
    issued = row['issued'] or 0
    policy = state.get('policy') if isinstance(state, dict) else None
    if (isinstance(policy, dict) and type(policy.get('claims_in_window')) is int
            and policy['claims_in_window'] != issued):
        raise ValueError('rolling claim snapshot disagrees with policy status')
    release_at = None
    if issued >= cap:
        release = db.execute('''
            SELECT claimed FROM recovery_incidents
            WHERE claimed IS NOT NULL AND claimed > ?
            ORDER BY claimed ASC LIMIT 1 OFFSET ?
        ''', (cutoff, issued - cap)).fetchone()
        if release is None:
            raise ValueError('claim budget release record unavailable')
        release_at = datetime.fromtimestamp(
            release['claimed'] + WINDOW_SECONDS, timezone.utc).isoformat()
    return {
        'schema_version': 1,
        'as_of': datetime.fromtimestamp(now, timezone.utc).isoformat(),
        'window_seconds': WINDOW_SECONDS,
        'cap': cap,
        'claims_issued': issued,
        'budget_release_at': release_at,
        'first_reports': _counts(row, 'first'),
        'current_outcomes': _counts(row, 'current'),
        # `externally_closed` is a successful connection observation, not
        # proof that a recovery command ran. Sustained confirmation has its
        # separate, stricter evidence slot.
        'externally_closed': row['externally_closed'] or 0,
        'sustained_confirmed': row['sustained_confirmed'] or 0,
        'unclaimed_followup': _followup_projection(state, now),
    }


def sanitize_recovery_diagnostics(value, now, *, max_age=150, future_skew=5):
    """Validate and copy the optional authenticated-ingest summary."""
    if type(value) is not dict or set(value) != DIAGNOSTIC_KEYS:
        raise ValueError('invalid diagnostics schema')
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['window_seconds']) is not int
            or value['window_seconds'] != WINDOW_SECONDS
            or type(value['cap']) is not int or not 1 <= value['cap'] <= 24
            or not _strict_count(value['claims_issued'])):
        raise ValueError('invalid diagnostics metadata')
    as_of = _strict_timestamp(value['as_of'])
    age = now - as_of
    if age > max_age or age < -future_skew:
        raise ValueError('stale or future diagnostics')
    if value['claims_issued'] > 1_000_000_000:
        raise ValueError('diagnostics count out of range')
    release_at = value['budget_release_at']
    if release_at is None:
        if value['claims_issued'] >= value['cap']:
            raise ValueError('exhausted budget needs its earliest release reference')
    else:
        release_timestamp = _strict_timestamp(release_at)
        if (value['claims_issued'] < value['cap'] or release_timestamp < as_of
                or release_timestamp > as_of + WINDOW_SECONDS + 5):
            raise ValueError('invalid budget release reference')
    for name in ('first_reports', 'current_outcomes'):
        bucket = value[name]
        if type(bucket) is not dict or set(bucket) != COUNT_BUCKETS:
            raise ValueError('invalid diagnostics outcome buckets')
        if any(not _strict_count(count) for count in bucket.values()):
            raise ValueError('invalid diagnostics outcome count')
        if sum(bucket.values()) != value['claims_issued']:
            raise ValueError('diagnostics outcome totals do not match issued claims')
    for name in ('externally_closed', 'sustained_confirmed'):
        if not _strict_count(value[name]) or value[name] > value['claims_issued']:
            raise ValueError('invalid diagnostics confirmation count')
    followup = value['unclaimed_followup']
    if (type(followup) is not dict
            or set(followup) != {'available', 'operator_attention', 'reason_code',
                                 'awaiting_response', 'response_deadline_at'}
            or type(followup['available']) is not bool):
        raise ValueError('invalid diagnostics followup status')
    attention, reason = followup['operator_attention'], followup['reason_code']
    awaiting, deadline = followup['awaiting_response'], followup['response_deadline_at']
    if attention is None:
        if (followup['available'] or reason is not None or awaiting is not None
                or deadline is not None):
            raise ValueError('inconsistent unavailable followup status')
    elif type(attention) is not bool or not followup['available']:
        raise ValueError('inconsistent followup status')
    elif attention:
        if (reason not in FOLLOWUP_REASON_CODES or awaiting is not False
                or deadline is not None):
            raise ValueError('invalid followup reason')
    elif reason is not None or type(awaiting) is not bool:
        raise ValueError('unexpected followup reason/state')
    if awaiting:
        if not isinstance(deadline, str):
            raise ValueError('awaiting response requires deadline')
        deadline_timestamp = _strict_timestamp(deadline)
        if (deadline_timestamp < as_of or deadline_timestamp > as_of + WINDOW_SECONDS + 5):
            raise ValueError('invalid followup response deadline')
    elif deadline is not None:
        raise ValueError('unexpected followup response deadline')
    # Return a new dictionary with only the allowlisted fields and scalar types.
    return {
        'schema_version': 1,
        'as_of': value['as_of'],
        'window_seconds': WINDOW_SECONDS,
        'cap': value['cap'],
        'claims_issued': value['claims_issued'],
        'budget_release_at': release_at,
        'first_reports': dict(value['first_reports']),
        'current_outcomes': dict(value['current_outcomes']),
        'externally_closed': value['externally_closed'],
        'sustained_confirmed': value['sustained_confirmed'],
        'unclaimed_followup': dict(followup),
    }
