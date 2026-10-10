"""Bounded broadcast invalidation and transactional notification delivery.

Notifications identify records to reread; they never authorize effects.
"""
from collections import deque
from contextlib import closing
import sqlite3
import threading
import time


class Mailbox:
    def __init__(self, topics=None, limit=256):
        self.topics = set(topics) if topics else None
        self.limit = limit
        self.condition = threading.Condition()
        self.pending = set()

    def publish(self, topic, key='', organization=''):
        if self.topics is not None and topic not in self.topics:
            return
        with self.condition:
            if len(self.pending) >= self.limit:
                self.pending = {('resync', '', '')}
            else:
                self.pending.add((topic, str(key), str(organization)))
            self.condition.notify_all()

    def set(self):
        with self.condition:
            self.pending = {('resync', '', '')}
            self.condition.notify_all()

    def clear(self):
        # Compatibility with Event consumers: wait already drains atomically.
        # This must stay a no-op. An Event consumer that waits and then clears
        # (see IntegrationWatcher) would otherwise spin once anything is
        # pending, because clearing is the only thing bounding its loop.
        pass

    def wait(self, timeout=None):
        with self.condition:
            self.condition.wait_for(lambda: bool(self.pending), timeout)
            batch, self.pending = self.pending, set()
            return batch


class Notifications:
    def __init__(self):
        self.lock = threading.Condition()
        self.mailboxes = []
        self.history = deque(maxlen=256)
        self.revision = 0
        self.epoch = str(time.time_ns())
        self.outbox_ready = threading.Event()

    def subscribe(self, topics=None):
        mailbox = Mailbox(topics)
        with self.lock:
            self.mailboxes.append(mailbox)
        return mailbox

    def publish(self, topic, key='', organization=''):
        with self.lock:
            self.revision += 1
            item = dict(revision=self.revision, topic=topic, key=str(key), organization_id=str(organization))
            self.history.append(item)
            for mailbox in self.mailboxes:
                # Resync must reach every consumer regardless of interest filters.
                if topic == 'resync':
                    mailbox.set()
                else:
                    mailbox.publish(topic, key, organization)
            self.lock.notify_all()

    def changes(self, after=0, epoch='', timeout=20):
        if type(after) is not int or after < 0:
            raise ValueError('Invalid notification cursor.')
        with self.lock:
            reset = epoch != self.epoch or after > self.revision
            if not reset:
                self.lock.wait_for(lambda: self.revision > after, min(max(timeout, 0), 20))
            reset = reset or bool(self.history and after < self.history[0]['revision'] - 1)
            return dict(epoch=self.epoch, revision=self.revision, reset=reset,
                        changes=[] if reset else [i for i in self.history if i['revision'] > after])


class Connection(sqlite3.Connection):
    notify = None

    def commit(self):
        super().commit()
        if self.notify:
            self.notify()

    def __exit__(self, *args):
        result = super().__exit__(*args)
        if args[0] is None and self.notify:
            self.notify()
        return result


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS notification_outbox '
               '(id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, '
               'record_id TEXT NOT NULL, organization_id TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0)')


def record(db, topic, key, organization=''):
    db.execute('INSERT INTO notification_outbox(topic,record_id,organization_id) VALUES (?,?,?)',
               (topic, str(key), str(organization or '')))


class OutboxPump:
    def __init__(self, hub, paths):
        self.hub, self.paths = hub, list(dict.fromkeys(map(str, paths)))
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name='notification-outbox')

    def drain(self):
        more = False
        for path in self.paths:
            with closing(sqlite3.connect(path, timeout=2)) as db:
                rows = db.execute('SELECT id,topic,record_id,organization_id FROM notification_outbox '
                                  'WHERE delivered=0 ORDER BY id LIMIT 100').fetchall()
                for identifier, topic, key, org in rows:
                    self.hub.publish(topic, key, org)
                    # Publish before marking: an interruption can duplicate a hint.
                    with db:
                        db.execute('UPDATE notification_outbox SET delivered=1 WHERE id=?', (identifier,))
                with db:
                    db.execute('DELETE FROM notification_outbox WHERE delivered=1 AND id < '
                               '(SELECT COALESCE(MAX(id),0)-1000 FROM notification_outbox)')
                more = more or len(rows) == 100
        return more

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            self.hub.outbox_ready.clear()
            try:
                more = self.drain()
            except (sqlite3.Error, OSError):
                more = False
            if not more:
                self.hub.outbox_ready.wait(2)

    def close(self):
        self.stopped.set()
        self.hub.outbox_ready.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
