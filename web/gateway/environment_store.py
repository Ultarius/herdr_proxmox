"""Persisted environment plans, addressed by their immutable digest.

A plan is stored so that the same inputs can be recognised as the same
environment later, and so that a changed plan is a new identity rather than a
silent overwrite of a running one. Nothing here executes anything: writing a plan
records an intent that a later worker may authorise, and the digest is the only
key that identifies it.

Plans are append-only by digest. Re-planning the same repository after a commit
produces a different digest and a new row, so the history of what was planned and
when stays inspectable. `latest` reports the most recently stored plan for a
project without deciding which one is active; activation is a worker concern that
this module deliberately does not implement.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from contextlib import closing

import environment_plan

DIGEST = re.compile(r'[0-9a-f]{64}')
PROJECT = re.compile(r'[^\\\x00\r\n:]{1,1000}')

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS environment_plans ('
    ' digest TEXT PRIMARY KEY,'
    ' project TEXT NOT NULL,'
    ' revision TEXT NOT NULL,'
    ' preview INTEGER NOT NULL,'
    ' states TEXT NOT NULL,'
    ' created_at TEXT NOT NULL,'
    ' data TEXT NOT NULL);'
    'CREATE INDEX IF NOT EXISTS environment_plans_project'
    ' ON environment_plans(project, created_at);')

JOBS = '''
CREATE TABLE IF NOT EXISTS environment_jobs (
 id TEXT PRIMARY KEY, digest TEXT NOT NULL, generation INTEGER NOT NULL,
 state TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', lease REAL NOT NULL DEFAULT 0,
 data TEXT NOT NULL, UNIQUE(digest, generation));
CREATE INDEX IF NOT EXISTS environment_jobs_queue ON environment_jobs(state, lease);
CREATE TABLE IF NOT EXISTS environment_waiters (
 id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, intent_digest TEXT NOT NULL,
 state TEXT NOT NULL, preparation_id TEXT, owner TEXT NOT NULL DEFAULT '',
 lease REAL NOT NULL DEFAULT 0, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS environment_waiters_queue ON environment_waiters(state, lease);
CREATE INDEX IF NOT EXISTS environment_waiters_intent ON environment_waiters(json_extract(data,'$.intent.preparation_key'));
CREATE TABLE IF NOT EXISTS environment_assistance (
 id TEXT PRIMARY KEY, waiter_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL, data TEXT NOT NULL);
'''


class EnvironmentPlanStore:
    """Plan storage keyed by digest. Absent schema is created on first use."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(SCHEMA)
            db.executescript(JOBS)

    def save(self, plan):
        """Store a plan by its digest. Re-saving the same plan is idempotent."""
        digest = plan.get('digest')
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError('An environment plan requires a valid digest.')
        if environment_plan.digest(plan) != digest:
            raise ValueError('The environment plan does not match its digest.')
        project = plan.get('project') or ''
        if (not isinstance(project, str) or not PROJECT.fullmatch(project)
                or project.startswith('/') or any(part in ('', '.', '..') for part in project.split('/'))):
            raise ValueError('An environment plan requires a managed project name.')
        states = ','.join(sorted({item['state'] for item in plan.get('requirements', [])
                                  if isinstance(item, dict) and item.get('state')}))
        data = json.dumps(plan, sort_keys=True, separators=(',', ':'), default=str)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR IGNORE INTO environment_plans VALUES (?,?,?,?,?,?,?)',
                       (digest, project, str(plan.get('revision') or ''),
                        1 if plan.get('preview') else 0, states,
                        plan.get('at') or _stamp(), data))
        return digest

    def get(self, digest):
        """One plan by digest, or None. An unrecognised digest is not an error."""
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            return None
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT data FROM environment_plans WHERE digest=?', (digest,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest(self, project):
        """The most recently stored plan for a project, or None."""
        if not isinstance(project, str) or not project:
            return None
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT data FROM environment_plans WHERE project=?'
                             ' ORDER BY created_at DESC, rowid DESC LIMIT 1', (project,)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, project, limit=20):
        """Stored plans for a project, newest first, as `(digest, revision, states)`."""
        if not isinstance(project, str) or not project:
            return []
        limit = min(100, max(1, int(limit)))
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute('SELECT digest, revision, states, created_at FROM environment_plans'
                              ' WHERE project=? ORDER BY created_at DESC, rowid DESC LIMIT ?',
                              (project, int(limit))).fetchall()
        return [dict(digest=row[0], revision=row[1], states=row[2].split(',') if row[2] else [],
                     created_at=row[3]) for row in rows]

    def digest_for(self, project, revision):
        """The digest of a stored plan for an exact project and revision, or None."""
        if not isinstance(project, str) or not project:
            return None
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT digest FROM environment_plans WHERE project=? AND revision=?'
                             ' ORDER BY rowid DESC LIMIT 1', (project, str(revision or ''))).fetchone()
        return row[0] if row else None

    def enqueue(self, digest, retry=False, waiter=None, owner=None):
        plan = self.get(digest)
        if not plan or plan.get('preview') or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', plan.get('revision', '')):
            raise ValueError('Preparation requires a stored plan for an exact committed revision.')
        if not plan.get('requirements') or any(item['state'] not in ('ready', 'acquire')
                                               for item in plan['requirements']):
            raise ValueError('Resolve the plan blockers before preparing its environment.')
        if plan.get('preparation_version') and any(item.get('activation') != 'available'
                                                  for item in plan['requirements']):
            raise ValueError('Resolve unavailable environment activation before preparation.')
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT data FROM environment_jobs WHERE digest=? ORDER BY generation DESC LIMIT 1',
                                  (digest,)).fetchone()
            previous = json.loads(previous[0]) if previous else None
            if previous and (not retry or previous['state'] in ('queued', 'running')):
                data = previous
            else:
                data = dict(id=uuid.uuid4().hex, digest=digest, project=plan['project'],
                            revision=plan['revision'], generation=previous['generation'] + 1 if previous else 1,
                            state='queued', scope='sdk_preparation', dependencies='not_run', checks='not_run',
                            created_at=_stamp(), receipts=[], error='')
                db.execute('INSERT INTO environment_jobs(id,digest,generation,state,data) VALUES (?,?,?,?,?)',
                           (data['id'], digest, data['generation'], data['state'], json.dumps(data)))
            if waiter is not None:
                row = db.execute("SELECT data FROM environment_waiters WHERE id=? AND state='planning' AND owner=? AND lease>?",
                                 (waiter, owner, time.time())).fetchone()
                if not row:
                    raise ValueError('Environment planning ownership expired; no continuation was attached.')
                bound = dict(json.loads(row[0]), state='waiting', preparation_id=data['id'],
                             plan_digest=digest, generation=data['generation'])
                db.execute('UPDATE environment_waiters SET state=?,preparation_id=?,owner=?,lease=0,data=? WHERE id=?',
                           ('waiting', data['id'], '', json.dumps(bound), waiter))
            return data

    def waiter(self, identity):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT data FROM environment_waiters WHERE identity=?', (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def wait(self, identity, intent):
        """Persist admission before any planning/download; repeating it is read-only."""
        if not re.fullmatch(r'[a-f0-9]{32}', str(identity)):
            raise ValueError('A launch waiter requires a valid execution identity.')
        fingerprint = environment_plan.digest(intent)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM environment_waiters WHERE identity=?', (identity,)).fetchone()
            if row:
                saved = json.loads(row[0])
                if saved['intent_digest'] != fingerprint:
                    raise ValueError('This execution already waits for a different environment intent.')
                return saved
            data = dict(id=uuid.uuid4().hex, identity=identity, intent=intent,
                        intent_digest=fingerprint, state='pending', created_at=_stamp())
            db.execute('INSERT INTO environment_waiters(id,identity,intent_digest,state,data) VALUES (?,?,?,?,?)',
                       (data['id'], identity, fingerprint, 'pending', json.dumps(data)))
            return data

    def claim_waiter(self, owner, planning=False, lease_seconds=120):
        """Recover expired claims too; only the exact ready generation may resume."""
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if planning:
                row = db.execute("SELECT data FROM environment_waiters WHERE state='pending' OR "
                                 "(state='planning' AND lease<=?) ORDER BY rowid LIMIT 1", (time.time(),)).fetchone()
                state = 'planning'
            else:
                row = db.execute("SELECT w.data FROM environment_waiters w JOIN environment_jobs j ON j.id=w.preparation_id "
                    "WHERE (w.state='waiting' OR (w.state='claimed' AND w.lease<=?)) "
                    "AND j.state IN ('ready','failed','cancelled') ORDER BY w.rowid LIMIT 1", (time.time(),)).fetchone()
                state = 'claimed'
            if not row:
                return None
            data = dict(json.loads(row[0]), state=state)
            db.execute('UPDATE environment_waiters SET state=?,owner=?,lease=?,data=? WHERE id=?',
                       (state, owner, time.time() + lease_seconds, json.dumps(data), data['id']))
            return data

    def renew_waiter(self, identifier, owner):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            return bool(db.execute("UPDATE environment_waiters SET lease=? WHERE id=? AND owner=? "
                "AND state IN ('planning','claimed') AND lease>?", (time.time() + 120, identifier, owner, time.time())).rowcount)

    def settle_waiter(self, identifier, owner, state, **fields):
        if state not in ('resumed', 'failed', 'cancelled', 'superseded', 'waiting', 'pending'):
            raise ValueError('Invalid environment continuation state.')
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT data FROM environment_waiters WHERE id=? AND owner=? AND lease>? "
                             "AND state IN ('planning','claimed')", (identifier, owner, time.time())).fetchone()
            if not row:
                return False
            data = dict(json.loads(row[0]), **fields, state=state, updated_at=_stamp())
            db.execute('UPDATE environment_waiters SET state=?,owner=?,lease=0,data=? WHERE id=?',
                       (state, '', json.dumps(data), identifier))
            return True

    def reusable(self, preparation_key):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT w.data FROM environment_waiters w JOIN environment_jobs j ON j.id=w.preparation_id "
                             "WHERE json_extract(w.data,'$.intent.preparation_key')=? "
                             "AND j.state IN ('queued','running','ready') ORDER BY w.rowid LIMIT 1",
                             (preparation_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def assistance(self, waiter_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT data FROM environment_assistance WHERE waiter_id=?', (waiter_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def assist(self, waiter, owner, states, **fields):
        """Fence bootstrap effects with the same lease as their waiting launch.

        Starting/prompting are persisted before CLI calls. A crash there is
        ambiguous and must never cause the preparation prompt to be replayed.
        """
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM environment_waiters WHERE id=? AND state='planning' AND owner=? AND lease>?",
                              (waiter['id'], owner, time.time())).fetchone():
                raise ValueError('Assisted preparation ownership expired.')
            row = db.execute('SELECT data FROM environment_assistance WHERE waiter_id=?', (waiter['id'],)).fetchone()
            previous = json.loads(row[0]) if row else None
            if (previous['state'] if previous else None) not in states:
                raise ValueError('Assisted preparation changed; inspect its recorded execution.')
            data = dict(previous or dict(id=uuid.uuid4().hex, waiter_id=waiter['id'], created_at=_stamp()),
                        **fields, updated_at=_stamp())
            db.execute('INSERT INTO environment_assistance VALUES (?,?,?,?) ON CONFLICT(waiter_id) DO UPDATE SET state=excluded.state,data=excluded.data',
                       (data['id'], waiter['id'], data['state'], json.dumps(data)))
            return data

    def claim(self, owner, lease_seconds=120):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            # One acquisition writer per store: unrelated readers never wait
            # for network work. Expired owners lose permission to publish jobs.
            if db.execute("SELECT 1 FROM environment_jobs WHERE state='running' AND lease>?", (time.time(),)).fetchone():
                return None
            row = db.execute("SELECT id,data FROM environment_jobs WHERE state='queued' OR "
                             "(state='running' AND lease<=?) ORDER BY rowid LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return None
            data = dict(json.loads(row[1]), state='running', started_at=_stamp())
            db.execute('UPDATE environment_jobs SET state=?,owner=?,lease=?,data=? WHERE id=?',
                       ('running', owner, time.time() + lease_seconds, json.dumps(data), row[0]))
            return data

    def heartbeat(self, identifier, owner, lease_seconds=120):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            return bool(db.execute("UPDATE environment_jobs SET lease=? WHERE id=? AND owner=? AND state='running' AND lease>?",
                                   (time.time() + lease_seconds, identifier, owner, time.time())).rowcount)

    def owns(self, identifier, owner):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT 1 FROM environment_jobs WHERE id=? AND owner=? AND state='running' AND lease>?",
                              (identifier, owner, time.time())).fetchone() is not None

    def finish(self, identifier, owner, state, **fields):
        if state not in ('ready', 'failed'):
            raise ValueError('Invalid environment completion state.')
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT data FROM environment_jobs WHERE id=? AND owner=? AND state='running' AND lease>?",
                             (identifier, owner, time.time())).fetchone()
            if not row:
                return False
            data = dict(json.loads(row[0]), **fields, state=state, finished_at=_stamp())
            db.execute('UPDATE environment_jobs SET state=?,lease=0,data=? WHERE id=?',
                       (state, json.dumps(data), identifier))
            return True

    def requeue(self, identifier, owner):
        """A graceful gateway stop interrupts work; it is not an install failure."""
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT data FROM environment_jobs WHERE id=? AND owner=? AND state='running' AND lease>?",
                             (identifier, owner, time.time())).fetchone()
            if not row:
                return False
            data = dict(json.loads(row[0]), state='queued', interrupted_at=_stamp())
            db.execute("UPDATE environment_jobs SET state='queued',owner='',lease=0,data=? WHERE id=?",
                       (json.dumps(data), identifier))
            return True

    def job(self, identifier):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT data FROM environment_jobs WHERE id=?', (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def status_records(self, project, revision):
        """One read snapshot for the proposal, active runs and prepared binding."""
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT data FROM environment_plans WHERE project=? AND revision=? '
                             'ORDER BY rowid DESC LIMIT 1', (project, revision)).fetchone()
            if row is None:
                row = db.execute('SELECT data FROM environment_plans WHERE project=? ORDER BY rowid DESC LIMIT 1',
                                 (project,)).fetchone()
            plan = json.loads(row[0]) if row else None
            row = db.execute('SELECT data FROM environment_jobs WHERE digest=? ORDER BY generation DESC LIMIT 1',
                             (plan['digest'],)).fetchone() if plan else None
            job = json.loads(row[0]) if row else None
            active = db.execute("SELECT j.data FROM environment_jobs j JOIN environment_plans p ON p.digest=j.digest "
                                "WHERE p.project=? AND p.revision=? AND j.state IN ('queued','running') "
                                'ORDER BY j.rowid', (project, revision)).fetchall()
            row = db.execute("SELECT p.data,j.data FROM environment_jobs j JOIN environment_plans p ON p.digest=j.digest "
                             "WHERE p.project=? AND j.state='ready' ORDER BY (p.revision=?) DESC,j.rowid DESC LIMIT 1",
                             (project, revision)).fetchone()
            prepared_plan, prepared_job = (json.loads(row[0]), json.loads(row[1])) if row else (None, None)
        return plan, job, [json.loads(row[0]) for row in active], prepared_plan, prepared_job

    def prepared(self, project, revision):
        """Latest successful binding for this exact approved source, not a candidate."""
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT j.data,p.data FROM environment_jobs j JOIN environment_plans p ON p.digest=j.digest "
                              "WHERE p.project=? AND p.revision=? AND j.state='ready' ORDER BY j.rowid DESC LIMIT 1",
                              (project, revision)).fetchone()
        return (json.loads(rows[1]), json.loads(rows[0])) if rows else (None, None)

    def cancel(self, identifier):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM environment_jobs WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise ValueError('No environment preparation job has that identity.')
            data = json.loads(row[0])
            if data['state'] in ('queued', 'running'):
                data.update(state='cancelled', finished_at=_stamp())
                db.execute('UPDATE environment_jobs SET state=?,lease=0,data=? WHERE id=?',
                           ('cancelled', json.dumps(data), identifier))
            return data


def _stamp():
    return datetime.now(timezone.utc).isoformat()
