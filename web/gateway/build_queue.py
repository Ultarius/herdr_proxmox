"""Durable, idempotent build requests with one active or pending heavy job."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time


class BuildQueue:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / 'queue.sqlite3'
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS builds (id TEXT PRIMARY KEY, identity TEXT UNIQUE, state TEXT NOT NULL, data TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.database, timeout=10)

    def enqueue(self, run, policy='scripts-v1', toolchain='flutter-3.44.8', retry=False):
        identity = hashlib.sha256(json.dumps([run['repository'], run['target'], policy, toolchain], separators=(',', ':')).encode()).hexdigest()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT data FROM builds WHERE identity=?', (identity,)).fetchone()
            previous = None
            if existing:
                data = json.loads(existing[0])
                data['event_ids'] = list(dict.fromkeys([*data.get('event_ids', [data['event_id']]), run['event_id']]))
                if not retry or data['state'] not in ('failed', 'error', 'cancelled', 'interrupted'):
                    db.execute('UPDATE builds SET data=? WHERE id=?', (json.dumps(data), data['id']))
                    return data, False
                previous = data
            if db.execute("SELECT 1 FROM builds WHERE state IN ('queued','running')").fetchone():
                raise ValueError('Build queue is full. Wait for the current build to finish.')
            data = dict(run, state='queued', check_policy=policy, toolchain_pin=toolchain,
                        attempt=previous['attempt'] + 1 if previous else 1,
                        event_ids=previous['event_ids'] if previous else [run['event_id']])
            if previous:
                db.execute('DELETE FROM builds WHERE identity=?', (identity,))
            db.execute('INSERT INTO builds VALUES (?,?,?,?)', (run['id'], identity, 'queued', json.dumps(data)))
            return data, True

    def claim(self):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM builds WHERE state='running'").fetchone():
                return None
            row = db.execute("SELECT id,data FROM builds WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            data = dict(json.loads(row[1]), state='running', claimed_at=time.time())
            db.execute('UPDATE builds SET state=?,data=? WHERE id=?', ('running', json.dumps(data), row[0]))
            return data

    def finish(self, run):
        if run.get('state') not in ('complete', 'failed', 'error', 'cancelled', 'interrupted'):
            raise ValueError('Build result must be terminal.')
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT data FROM builds WHERE id=?', (run['id'],)).fetchone()
            if existing:
                run['event_ids'] = json.loads(existing[0]).get('event_ids', [run['event_id']])
            db.execute('UPDATE builds SET state=?,data=? WHERE id=?', (run['state'], json.dumps(run), run['id']))

    def get(self, identifier):
        with closing(self.connect()) as db:
            row = db.execute('SELECT data FROM builds WHERE id=?', (identifier,)).fetchone()
            return json.loads(row[0]) if row else None

    def interrupt_running(self):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            interrupted = []
            for identifier, value in db.execute("SELECT id,data FROM builds WHERE state='running'").fetchall():
                data = dict(json.loads(value), state='interrupted', note='Build service restarted; inspect evidence before retrying.')
                db.execute('UPDATE builds SET state=?,data=? WHERE id=?', ('interrupted', json.dumps(data), identifier))
                interrupted.append(data)
            return interrupted
