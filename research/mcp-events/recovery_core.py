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
MAX_ELIGIBILITY_NOTIFICATIONS = 3


class RecoveryIneligible(ValueError):
    def __init__(self, state):
        self.reason = state['eligibility_reason']
        self.retry_at = state['retry_at']
        super().__init__('incident is not eligible: '+self.reason)



class RecoveryCore(EventCore):
    def __init__(self, db_path, allowed_origins, owner, transport=None, clock=time.time, *,
                 max_claims_per_24h=3, cooldown_seconds=3600):
        if type(max_claims_per_24h) is not int or not 1 <= max_claims_per_24h <= 24:
            raise ValueError('max_claims_per_24h must be an integer from 1 to 24')
        if type(cooldown_seconds) is not int or not 300 <= cooldown_seconds <= 86400:
            raise ValueError('cooldown_seconds must be an integer from 300 to 86400')
        self.max_claims_per_24h = max_claims_per_24h
        self.cooldown_seconds = cooldown_seconds
        self.owner = identity(owner)
        super().__init__(db_path, allowed_origins, transport=transport, clock=clock, event_name=NAME)
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS recovery_observation(singleton INTEGER PRIMARY KEY CHECK(singleton=1),stamp REAL,ssh TEXT,ping TEXT,streak INTEGER);
            CREATE TABLE IF NOT EXISTS recovery_incidents(id TEXT PRIMARY KEY,event_id TEXT,created REAL,closed REAL,claim_id TEXT,claimed REAL,outcome TEXT);
            CREATE TABLE IF NOT EXISTS recovery_eligibility(incident_id TEXT PRIMARY KEY,policy_blocked INTEGER NOT NULL,notification_count INTEGER NOT NULL,latest_event_id TEXT);
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
        ping = 'ok' if b['responded'] and type(b.get('exit_code')) is int and b['exit_code'] == 0 else ('failed' if b['responded'] is False and type(b.get('exit_code')) is int and b['exit_code'] != 0 and b.get('error_class') in (None, 'ok') else 'local_error')
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
                result['new_event_id'] = self._sync_eligibility(db, result)
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
            result = self._state(db)
            reminder = self._sync_eligibility(db, result)
            result['new_event_id'] = reminder or new_event
        return result

    def _state(self, db, incident_id=None):
        now = self.clock()
        obs = db.execute('SELECT * FROM recovery_observation WHERE singleton=1').fetchone()
        row = db.execute('SELECT * FROM recovery_incidents WHERE id=?', (incident_id,)).fetchone() if incident_id else db.execute('SELECT * FROM recovery_incidents WHERE closed IS NULL').fetchone()
        recent = [r[0] for r in db.execute('SELECT claimed FROM recovery_incidents WHERE claimed>? ORDER BY claimed', (now-86400,))]
        fresh = bool(obs and 0 <= now-obs['stamp'] <= FRESH_SECONDS)
        daily_blocked = len(recent) >= self.max_claims_per_24h
        cooldown_until = recent[-1]+self.cooldown_seconds if recent else None
        cooldown_blocked = cooldown_until is not None and now < cooldown_until
        policy_retry_at = max(
            recent[len(recent)-self.max_claims_per_24h]+86400 if daily_blocked else now,
            cooldown_until if cooldown_blocked else now)
        result = {'target_id': 'dots', 'observation_fresh': fresh,
                  'observation': None if obs is None else {'observed_at':obs['stamp'],'ssh':obs['ssh'],'ping':obs['ping'],'failure_streak':obs['streak']},
                  'incident': None, 'claimable': False, 'eligibility_reason':'no_incident', 'retry_at':None,
                  'policy': {'window_seconds':86400, 'max_claims_per_24h':self.max_claims_per_24h,
                             'cooldown_seconds':self.cooldown_seconds, 'claims_in_window':len(recent)}}
        if row:
            state = 'externally_recovered' if row['closed'] is not None else ('pending' if row['claim_id'] is None else 'attempted')
            result['incident'] = {'incident_id': row['id'], 'event_id':row['event_id'], 'created_at':row['created'], 'state':state, 'outcome':row['outcome'] or ('unknown' if row['claim_id'] else None), 'closed_at':row['closed']}
            preflight = db.execute('SELECT reason,reported_at,report_count FROM preflight_reports WHERE incident_id=?', (row['id'],)).fetchone()
            result['incident']['preflight_report'] = dict(preflight) if preflight else None
            if state == 'externally_recovered':
                reason = 'externally_recovered'
            elif state == 'attempted':
                reason = 'already_claimed'
            elif obs is None:
                reason = 'observation_missing'
            elif not fresh:
                reason = 'observation_stale'
            elif not self._failed(obs):
                reason = 'failure_not_confirmed'
            elif obs['streak'] < 3:
                reason = 'insufficient_failure_streak'
            elif daily_blocked:
                reason = 'daily_limit'
            elif cooldown_blocked:
                reason = 'cooldown'
            else:
                reason = 'eligible'
            result['eligibility_reason'] = reason
            result['claimable'] = reason == 'eligible'
            if reason in ('daily_limit','cooldown'):
                result['retry_at'] = policy_retry_at
        return result

    def _sync_eligibility(self, db, state):
        """Persist observed policy transitions; no network and no execution grant.

        Call only under the writer transaction in ingest/background delivery.
        Stale/error observations neither arm nor clear a policy-blocked marker.
        """
        incident = state['incident']
        if not incident or incident['state'] != 'pending':
            return None
        iid = incident['incident_id']
        db.execute('INSERT OR IGNORE INTO recovery_eligibility VALUES(?,0,0,NULL)', (iid,))
        if state['eligibility_reason'] in ('daily_limit','cooldown'):
            db.execute('UPDATE recovery_eligibility SET policy_blocked=1 WHERE incident_id=?', (iid,))
            return None
        if not state['claimable']:
            return None
        previous = db.execute('SELECT * FROM recovery_eligibility WHERE incident_id=?', (iid,)).fetchone()
        if not previous['policy_blocked']:
            return None
        db.execute('UPDATE recovery_eligibility SET policy_blocked=0 WHERE incident_id=?', (iid,))
        if previous['notification_count'] >= MAX_ELIGIBILITY_NOTIFICATIONS:
            return None
        generation = previous['notification_count']+1
        eid = 'evt_'+secrets.token_hex(16)
        body = encoded({'eventId':eid,'name':NAME,
                        'timestamp':datetime.fromtimestamp(self.clock(),timezone.utc).isoformat(),
                        'data':{'incident_id':iid,'target_id':'dots','simulated':False},'cursor':None})
        db.execute('INSERT INTO events VALUES(?,?,?,?)', (eid,self.owner,iid+':eligibility:'+str(generation),body))
        db.execute("INSERT INTO outbox SELECT ?,id,'pending',0,NULL FROM subscriptions WHERE owner=? AND active=1 AND expires>?", (eid,self.owner,self.clock()))
        db.execute('UPDATE recovery_eligibility SET notification_count=?,latest_event_id=? WHERE incident_id=?', (generation,eid,iid))
        return eid

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
                raise RecoveryIneligible(state)
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
            self._sync_eligibility(db, state)
            incident = state['incident']
            if not incident or incident['state'] != 'pending' or not state['observation_fresh']:
                return []
            obs = state['observation']
            if not self._failed(obs) or obs['failure_streak'] < 3:
                return []
            transition = db.execute('SELECT latest_event_id FROM recovery_eligibility WHERE incident_id=?', (incident['incident_id'],)).fetchone()
            eid = transition['latest_event_id'] if transition and transition['latest_event_id'] else incident['event_id']
            db.execute("INSERT OR IGNORE INTO outbox SELECT ?,id,'pending',0,NULL FROM subscriptions WHERE owner=? AND active=1 AND expires>?", (eid,principal,self.clock()))
            row = db.execute("SELECT 1 FROM outbox WHERE event=? AND state IN ('pending','retry') AND attempts<3",(eid,)).fetchone()
            return [eid] if row else []
