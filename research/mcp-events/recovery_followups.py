"""Bounded recovery reminders; receipts and reminders never grant execution."""
import logging
import secrets
from datetime import datetime, timezone

from event_core import encoded

MAX_FOLLOWUP_RECORDS = 1000
MAX_RECEIPT_RECORDS = 10000


class RecoveryFollowups:
    def _configure_followups(self, interval, maximum, lifetime):
        if interval is not None and (type(interval) is not int or not 300 <= interval <= 3600):
            raise ValueError('unclaimed reminder interval must be None or 300..3600 seconds')
        if type(maximum) is not int or not 1 <= maximum <= 3:
            raise ValueError('unclaimed reminder maximum must be 1..3')
        if type(lifetime) is not int or not 300 <= lifetime <= 86400:
            raise ValueError('unclaimed reminder lifetime must be 300..86400 seconds')
        if interval is not None and lifetime <= interval:
            raise ValueError('unclaimed reminder lifetime must be greater than its interval')
        self.followup_interval = interval
        self.followup_maximum = maximum
        self.followup_lifetime = lifetime

    @staticmethod
    def _followup_schema(db):
        db.executescript('''
        CREATE TABLE IF NOT EXISTS recovery_delivery_receipts(
          event_id TEXT NOT NULL,subscription_id TEXT NOT NULL,received_at REAL NOT NULL,
          PRIMARY KEY(event_id,subscription_id));
        CREATE TABLE IF NOT EXISTS recovery_followups(
          incident_id TEXT PRIMARY KEY,base_event_id TEXT NOT NULL,last_event_id TEXT NOT NULL,
          count INTEGER NOT NULL CHECK(count BETWEEN 0 AND 3),anchor REAL NOT NULL,
          expires_at REAL NOT NULL,last_clock REAL NOT NULL,notified INTEGER NOT NULL DEFAULT 0,
          stopped_reason TEXT);
        ''')

    @staticmethod
    def _base_event(db, incident):
        row = db.execute('SELECT latest_event_id FROM recovery_eligibility WHERE incident_id=?',
                         (incident['incident_id'],)).fetchone()
        return row['latest_event_id'] if row and row['latest_event_id'] else incident['event_id']

    def _delivery_completed(self, db, event_id, subscription_id, state):
        # Called only after this process actually attempted a callback and stored
        # its result. Never infer timestamps from old received rows or status reads.
        if self.followup_interval is None or state != 'received':
            return
        if db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0] >= MAX_RECEIPT_RECORDS:
            return
        db.execute('INSERT OR IGNORE INTO recovery_delivery_receipts VALUES(?,?,?)',
                   (event_id, subscription_id, self.clock()))

    def _delivery_decision(self, db, event_id, subscription_id):
        event = db.execute('SELECT request FROM events WHERE id=? AND owner=?',
                           (event_id, self.owner)).fetchone()
        if not event or ':unclaimed:' not in event['request']:
            return 'allow'  # Original and policy-unblocked events keep their contract.
        iid = event['request'].split(':unclaimed:', 1)[0]
        state = self._state(db, iid)
        row = db.execute('SELECT * FROM recovery_followups WHERE incident_id=?', (iid,)).fetchone()
        if (state['incident'] is None or row is None or row['last_event_id'] != event_id
                or row['base_event_id'] != self._base_event(db, state['incident'])):
            return 'cancel'
        reason = self._followup_gate(db, state, row)
        if reason in ('ready', 'awaiting_delivery'):
            return 'allow'
        if reason in ('disabled', 'no_incident', 'incident_closed', 'claimed',
                      'preflight_reported', 'execution_state_unknown', 'expired', 'exhausted'):
            return 'cancel'
        return 'defer'

    def _followup_gate(self, db, state, row=None, now=None):
        if self.followup_interval is None:
            return 'disabled'
        incident = state['incident']
        if incident is None:
            return 'no_incident'
        if incident['state'] == 'externally_recovered':
            return 'incident_closed'
        if incident['state'] != 'pending':
            return 'claimed'
        if incident['preflight_report'] is not None:
            return 'preflight_reported'
        if incident['first_report'] is not None or incident['execution_contract'] is not None:
            return 'execution_state_unknown'
        now = self.clock() if now is None else now
        if row is not None:
            if row['stopped_reason'] in ('expired', 'exhausted'):
                return row['stopped_reason']
            if row['count'] > self.followup_maximum:
                return 'exhausted'
            if now < row['last_clock'] or now < row['anchor']:
                return 'clock_regressed'
            if now >= row['expires_at']:
                return 'expired'
        if not state['observation_fresh']:
            return 'observation_stale'
        if not state['claimable']:
            return state['eligibility_reason']
        if not db.execute('SELECT 1 FROM subscriptions WHERE owner=? AND active=1 AND expires>? LIMIT 1',
                          (self.owner, now)).fetchone():
            return 'no_active_subscription'
        return 'ready'

    def _cancel_followup(self, db, row):
        if not row or not row['count']:
            return
        requests = [row['incident_id'] + ':unclaimed:' + str(n) for n in range(1, 4)]
        # A later policy receipt may become the scheduling basis. Cancel only
        # exact semantic follow-ups, including older queued generations, and
        # never an original/policy base or an already in-flight/result row.
        events = db.execute('SELECT id FROM events WHERE owner=? AND request IN (?,?,?)',
                            (self.owner, *requests)).fetchall()
        for event in events:
            db.execute("UPDATE outbox SET state='cancelled' WHERE event=? AND state IN ('pending','retry')",
                       (event['id'],))

    def _delivery_evidence(self, db, event_id, now=None):
        now = self.clock() if now is None else now
        rows = db.execute('''SELECT o.state,o.http_status,r.received_at FROM outbox o
            JOIN subscriptions s ON s.id=o.subscription
            LEFT JOIN recovery_delivery_receipts r ON r.event_id=o.event AND r.subscription_id=o.subscription
            WHERE o.event=? AND s.owner=? AND s.active=1 AND s.expires>?''',
                          (event_id, self.owner, now)).fetchall()
        if not rows:
            return None, 'awaiting_delivery'
        if any(row['state'] in ('pending', 'retry', 'sending') for row in rows):
            return None, 'awaiting_delivery'
        receipts = [row['received_at'] for row in rows
                    if row['state'] == 'received' and row['received_at'] is not None]
        if receipts:
            return max(receipts), 'received'
        if any(row['state'] == 'received' for row in rows):
            return None, ('receipt_tracking_full' if db.execute('SELECT count(*) FROM recovery_delivery_receipts').fetchone()[0] >= MAX_RECEIPT_RECORDS else 'legacy_receipt_unavailable')
        return None, 'delivery_unconfirmed'

    def _followup_status(self, db, state, now=None):
        now = self.clock() if now is None else now
        incident = state['incident']
        row = (db.execute('SELECT * FROM recovery_followups WHERE incident_id=?',
                          (incident['incident_id'],)).fetchone() if incident else None)
        reason = self._followup_gate(db, state, row, now=now)
        selected = self._selected_recovery_event(db, incident) if incident else None
        receipt, delivery = (self._delivery_evidence(db, selected, now=now)
                             if selected else (None, 'not_observed'))
        next_at = None
        response_deadline = None
        if reason == 'ready':
            if delivery != 'received':
                reason = delivery
            elif receipt < (row['anchor'] if row else incident['created_at']) or receipt > now:
                reason = 'receipt_time_invalid'
            elif row is None and db.execute('SELECT count(*) FROM recovery_followups').fetchone()[0] >= MAX_FOLLOWUP_RECORDS:
                reason = 'storage_full'
            else:
                anchor = row['anchor'] if row else receipt
                expiry = row['expires_at'] if row else anchor + self.followup_lifetime
                if now >= expiry:
                    reason = 'expired'
                else:
                    next_at = receipt + self.followup_interval
                    if now < next_at:
                        if row and (row['count'] >= self.followup_maximum or next_at >= expiry):
                            reason = 'awaiting_response'
                            response_deadline = min(next_at, expiry)
                        else:
                            reason = 'waiting'
                    elif row and row['count'] >= self.followup_maximum:
                        reason = 'exhausted'
                    else:
                        reason = 'due'
        # A callback may be stuck in durable sending with no trustworthy receipt.
        # Incident age is only an operator-attention bound, never a substitute
        # for received_at, a follow-up anchor, or permission to resend.
        attention_deadline = (row['expires_at'] if row else
                              incident['created_at'] + self.followup_lifetime if incident else None)
        if reason == 'awaiting_delivery' and now >= attention_deadline:
            reason = 'delivery_unconfirmed'
        attention = reason in ('exhausted', 'expired', 'storage_full',
                               'legacy_receipt_unavailable', 'receipt_tracking_full', 'delivery_unconfirmed', 'receipt_time_invalid', 'clock_regressed', 'execution_state_unknown')
        return {'enabled': self.followup_interval is not None,
                'interval_seconds': self.followup_interval,
                'max_reminders': self.followup_maximum,
                'lifetime_seconds': self.followup_lifetime,
                'reminders_created': row['count'] if row else 0,
                'last_delivery_state': delivery, 'last_received_at': receipt,
                'response_state': ('externally_recovered' if incident and incident['state'] == 'externally_recovered'
                                   else 'claimed' if incident and incident['state'] == 'attempted'
                                   else 'preflight_reported' if incident and incident['preflight_report']
                                   else 'not_observed'),
                'next_reminder_at': next_at if reason in ('waiting', 'due') else None,
                'response_deadline_at': response_deadline,
                'expires_at': row['expires_at'] if row else None,
                'reason': reason, 'operator_attention': attention}

    def _sync_followups(self, db, state):
        """Writer transaction only; status reads never create events or notices."""
        incident = state['incident']
        if not incident:
            return None
        iid = incident['incident_id']
        row = db.execute('SELECT * FROM recovery_followups WHERE incident_id=?', (iid,)).fetchone()
        gate = self._followup_gate(db, state, row)
        if gate in ('disabled', 'incident_closed', 'claimed', 'preflight_reported', 'execution_state_unknown', 'expired', 'exhausted'):
            self._cancel_followup(db, row)
        status = self._followup_status(db, state)
        if status['reason'] in ('exhausted', 'expired') and row and not row['notified']:
            db.execute('UPDATE recovery_followups SET notified=1,stopped_reason=?,last_clock=max(last_clock,?) WHERE incident_id=?', (status['reason'], self.clock(), iid))
            logging.warning('recovery_unclaimed_followup_attention reason=%s', status['reason'])
        if gate != 'ready':
            return None
        base = self._base_event(db, incident)
        if row and row['base_event_id'] != base:
            self._cancel_followup(db, row)
        if status['reason'] not in ('waiting', 'awaiting_response', 'due', 'exhausted', 'expired'):
            return None
        receipt = status['last_received_at']
        now = self.clock()
        if row is None:
            db.execute('INSERT INTO recovery_followups VALUES(?,?,?,0,?,?,?,0,NULL)',
                       (iid, base, base, receipt, receipt + self.followup_lifetime, now))
            if status['reason'] == 'expired':
                db.execute('UPDATE recovery_followups SET notified=1,stopped_reason=?,last_clock=max(last_clock,?) WHERE incident_id=?', (status['reason'], self.clock(), iid))
                logging.warning('recovery_unclaimed_followup_attention reason=expired')
        else:
            # Preserve original deadline/count over policy changes and restarts.
            db.execute('UPDATE recovery_followups SET last_clock=? WHERE incident_id=?', (now, iid))
            if row['base_event_id'] != base:
                db.execute('UPDATE recovery_followups SET base_event_id=?,last_event_id=? WHERE incident_id=?',
                           (base, base, iid))
        row = db.execute('SELECT * FROM recovery_followups WHERE incident_id=?', (iid,)).fetchone()
        if status['reason'] != 'due':
            return None
        generation = row['count'] + 1
        eid = 'evt_' + secrets.token_hex(16)
        body = encoded({'eventId': eid, 'name': self.name,
                        'timestamp': datetime.fromtimestamp(now, timezone.utc).isoformat(),
                        'data': {'incident_id': iid, 'target_id': 'dots', 'simulated': False}, 'cursor': None})
        db.execute('INSERT INTO events VALUES(?,?,?,?)', (eid, self.owner, iid + ':unclaimed:' + str(generation), body))
        db.execute("INSERT INTO outbox SELECT ?,id,'pending',0,NULL FROM subscriptions WHERE owner=? AND active=1 AND expires>?",
                   (eid, self.owner, now))
        db.execute('UPDATE recovery_followups SET count=?,last_event_id=?,last_clock=? WHERE incident_id=?',
                   (generation, eid, now, iid))
        return eid

    def _selected_recovery_event(self, db, incident):
        base = self._base_event(db, incident)
        if self.followup_interval is None:
            return base
        row = db.execute('SELECT * FROM recovery_followups WHERE incident_id=?',
                         (incident['incident_id'],)).fetchone()
        return row['last_event_id'] if row and row['base_event_id'] == base else base
