"""Durable recovery authorization. This module never executes a shell command."""
import json
import secrets
import time
from datetime import datetime, timezone
from event_core import EventCore, encoded, identity

NAME = 'dots.recovery_requested'
COMMAND = 'bash /workspace/.dots-tailscale/start.sh'
FRESH_SECONDS = 150
PREFLIGHT_REASONS = frozenset(('computer_unavailable', 'missing_installation', 'missing_state',
                               'permission_denied', 'approval_required', 'unexpected_startup_change'))
MAX_PREFLIGHT_REPORTS = 2147483647


class RecoveryCore(EventCore):
    def __init__(self, db_path, allowed_origins, owner, transport=None, clock=time.time):
        self.owner = identity(owner)
        super().__init__(db_path, allowed_origins, transport=transport, clock=clock, event_name=NAME)
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS recovery_observation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),stamp REAL,ssh TEXT,ping TEXT,streak INTEGER);
            CREATE TABLE IF NOT EXISTS recovery_incidents(id TEXT PRIMARY KEY,event_id TEXT,created REAL,closed REAL,claim_id TEXT,claimed REAL,outcome TEXT);
            CREATE TABLE IF NOT EXISTS preflight_reports(incident_id TEXT PRIMARY KEY,reason TEXT NOT NULL,reported_at REAL NOT NULL,report_count INTEGER NOT NULL);
            ''')

    def _owner(self, principal):
        if identity(principal) != self.owner:
            raise ValueError('unauthorized recovery principal')

    def catalog(self, principal):
        self._owner(principal)
        return {'name': NAME, 'description': 'External monitor requests one explicitly claimed recovery attempt; verify fresh status before execution.',
                'delivery': ['webhook'], 'payloadSchema': {'type': 'object', 'properties': {'incident_id': {'type': 'string'}, 'target_id': {'const': 'dots'}, 'simulated': {'const': False}}, 'required': ['incident_id', 'target_id', 'simulated'], 'additionalProperties': False},
                'inputSchema': {'type': 'object', 'properties': {'target_id': {'const': 'dots'}}, 'required': ['target_id'], 'additionalProperties': False}}

    @staticmethod
    def _failed(obs):
        return obs['ssh'] in ('timeout', 'network_unreachable') and obs['ping'] == 'failed'

    def ingest(self, observation):
        """Trusted monitor only; HTTP layer must authenticate independently of Dots."""
        if not isinstance(observation, dict) or set(observation) != {'observed_at', 'state', 'ssh', 'peer_ping'}:
            raise ValueError('invalid observation fields')
        try:
            dt = datetime.fromisoformat(observation['observed_at'].replace('Z','+00:00'))
            if dt.tzinfo is None:
                raise ValueError('timezone required')
            stamp = dt.timestamp()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('invalid observation timestamp') from None
        if not self.clock()-FRESH_SECONDS <= stamp <= self.clock():
            raise ValueError('observation is stale or future dated')
        a, b = observation['ssh'], observation['peer_ping']
        if not isinstance(a,dict) or not isinstance(b,dict) or any(type(v) is not bool for v in (a.get('command_succeeded'),a.get('probe_valid'),b.get('responded'))):
            raise ValueError('invalid observation booleans')
        if a['command_succeeded'] and a['probe_valid'] and type(a.get('exit_code')) is int and a['exit_code'] == 0:
            ssh = 'ok'
        else:
            ssh = a.get('error_class')
            if ssh not in ('timeout','network_unreachable') or a['command_succeeded'] or a['probe_valid'] or type(a.get('exit_code')) is not int or a['exit_code'] == 0:
                ssh = 'local_error'
        ping = 'ok' if b['responded'] and type(b.get('exit_code')) is int and b['exit_code'] == 0 else ('failed' if b['responded'] is False and type(b.get('exit_code')) is int and b['exit_code'] != 0 and not b.get('error_class') else 'local_error')
        # Only the monitor's explicit external-unreachable classification permits failure counting.
        if observation['state'] != 'external_unreachable' and ssh != 'ok':
            ssh = 'local_error'
        observation = {'observed_at':stamp,'ssh':ssh,'ping':ping}
        new_event = None
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM recovery_observation WHERE singleton=1').fetchone()
            if old and stamp == old['stamp'] and ssh == old['ssh'] and ping == old['ping']:
                result = self._state(db)
                result['new_event_id'] = None
                return result
            if old and stamp <= old['stamp']:
                raise ValueError('observation must be distinct and chronological')
            streak = (old['streak'] if old and stamp-old['stamp'] <= FRESH_SECONDS else 0)+1 if self._failed(observation) else 0
            db.execute('INSERT OR REPLACE INTO recovery_observation VALUES(1,?,?,?,?)', (stamp, ssh, ping, streak))
            active = db.execute('SELECT * FROM recovery_incidents WHERE closed IS NULL').fetchone()
            if ssh == 'ok' and ping == 'ok':
                db.execute('UPDATE recovery_incidents SET closed=? WHERE closed IS NULL', (stamp,))
            elif streak >= 3 and active is None:
                iid, eid = 'inc_'+secrets.token_hex(16), 'evt_'+secrets.token_hex(16)
                db.execute('INSERT INTO recovery_incidents VALUES(?,?,?,NULL,NULL,NULL,NULL)', (iid,eid,self.clock()))
                body = encoded({'eventId': eid, 'name': NAME, 'timestamp': datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(), 'data': {'incident_id': iid, 'target_id': 'dots', 'simulated': False}, 'cursor': None})
                db.execute('INSERT INTO events VALUES(?,?,?,?)', (eid,self.owner,iid,body))
                db.execute("INSERT INTO outbox SELECT ?,id,'pending',0,NULL FROM subscriptions WHERE owner=? AND active=1 AND expires>?", (eid,self.owner,self.clock()))
                new_event = eid
        result = self.get_current_recovery(self.owner)
        result['new_event_id'] = new_event
        return result

    def _state(self, db, incident_id=None):
        obs = db.execute('SELECT * FROM recovery_observation WHERE singleton=1').fetchone()
        row = db.execute('SELECT * FROM recovery_incidents WHERE id=?', (incident_id,)).fetchone() if incident_id else db.execute('SELECT * FROM recovery_incidents WHERE closed IS NULL').fetchone()
        fresh = bool(obs and 0 <= self.clock()-obs['stamp'] <= FRESH_SECONDS)
        result = {'target_id': 'dots', 'observation_fresh': fresh, 'observation': None if obs is None else {'observed_at':obs['stamp'],'ssh':obs['ssh'],'ping':obs['ping'],'failure_streak':obs['streak']}, 'incident': None, 'claimable': False}
        if row:
            state = 'externally_recovered' if row['closed'] is not None else ('pending' if row['claim_id'] is None else 'attempted')
            result['incident'] = {'incident_id': row['id'], 'event_id':row['event_id'], 'created_at':row['created'], 'state':state, 'outcome':row['outcome'] or ('unknown' if row['claim_id'] else None), 'closed_at':row['closed']}
            preflight = db.execute('SELECT reason,reported_at,report_count FROM preflight_reports WHERE incident_id=?', (row['id'],)).fetchone()
            result['incident']['preflight_report'] = dict(preflight) if preflight else None
            recent = db.execute('SELECT count(*),max(claimed) FROM recovery_incidents WHERE claimed>?', (self.clock()-86400,)).fetchone()
            result['claimable'] = bool(state == 'pending' and fresh and self._failed(obs) and obs['streak'] >= 3 and recent[0] < 3 and (recent[1] is None or self.clock()-recent[1] >= 3600))
        return result

    def get_current_recovery(self, principal, incident_id=None):
        self._owner(principal)
        if incident_id is not None and (not isinstance(incident_id,str) or len(incident_id)>128):
            raise ValueError('invalid incident ID')
        with self._db() as db:
            return self._state(db,incident_id)

    def claim_recovery(self, principal, incident_id):
        self._owner(principal)
        if not isinstance(incident_id,str) or not incident_id:
            raise ValueError('exact incident ID required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._state(db,incident_id)
            if not state['claimable']:
                raise ValueError('incident is not eligible for a recovery attempt')
            claim = 'claim_'+secrets.token_hex(16)
            now = self.clock()
            db.execute('UPDATE recovery_incidents SET claim_id=?,claimed=?,outcome=? WHERE id=?', (claim,now,'unknown',incident_id))
        return {'incident_id':incident_id, 'claim_id':claim, 'command':COMMAND, 'expires_at':now+120, 'instructions':'Run the fixed command once immediately in your cloud computer. Never reuse this claim or execute after expires_at. Report only a sanitized outcome. Success is not external recovery confirmation.'}

    def report_preflight(self, principal, incident_id, reason):
        """Record why execution was blocked before a claim, without authorizing a retry."""
        self._owner(principal)
        if not isinstance(incident_id, str) or not incident_id or len(incident_id) > 128:
            raise ValueError('exact incident ID required')
        if not isinstance(reason, str) or reason not in PREFLIGHT_REASONS:
            raise ValueError('invalid preflight reason')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT closed,claim_id FROM recovery_incidents WHERE id=?', (incident_id,)).fetchone()
            if row is None or row['closed'] is not None or row['claim_id'] is not None:
                raise ValueError('preflight report requires an open unclaimed incident')
            # A single bounded row avoids storing free-form diagnostics or a report history.
            db.execute('''INSERT INTO preflight_reports VALUES(?,?,?,1)
                          ON CONFLICT(incident_id) DO UPDATE SET
                          reason=excluded.reason,reported_at=excluded.reported_at,
                          report_count=min(preflight_reports.report_count+1,?)''',
                       (incident_id, reason, self.clock(), MAX_PREFLIGHT_REPORTS))
            return self._state(db, incident_id)

    def report_recovery(self, principal, incident_id, claim_id, outcome):
        self._owner(principal)
        if outcome not in ('success','failed','blocked','unknown'):
            raise ValueError('invalid outcome')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM recovery_incidents WHERE id=? AND claim_id=?',(incident_id,claim_id)).fetchone()
            if row is None:
                raise ValueError('unknown claim')
            if row['outcome'] not in ('unknown',outcome):
                raise ValueError('attempt already reported')
            db.execute('UPDATE recovery_incidents SET outcome=? WHERE id=?',(outcome,incident_id))
        return self.get_current_recovery(principal,incident_id)

    def pending_delivery_ids(self, principal):
        """Bind late subscriptions; return eligible outbox work without transmitting."""
        self._owner(principal)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._state(db)
            incident = state['incident']
            if not incident or incident['state'] != 'pending' or not state['observation_fresh']:
                return []
            obs = state['observation']
            if not self._failed(obs) or obs['failure_streak'] < 3:
                return []
            eid = incident['event_id']
            db.execute("INSERT OR IGNORE INTO outbox SELECT ?,id,'pending',0,NULL FROM subscriptions WHERE owner=? AND active=1 AND expires>?", (eid,principal,self.clock()))
            row = db.execute("SELECT 1 FROM outbox WHERE event=? AND state IN ('pending','retry') AND attempts<3",(eid,)).fetchone()
            return [eid] if row else []
