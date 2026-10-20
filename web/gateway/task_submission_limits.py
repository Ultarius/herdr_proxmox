"""Bounded, nonblocking task-session admission before Git or payload decoding.

Limits are process-local load shedding, not durable quotas or authorization.
Only authenticated sessions enter this table. Keys are digests, never raw tokens;
neither keys nor task identities enter diagnostics. A refused request can replay
its same captured bytes and request ID after Retry-After.
"""
from contextlib import contextmanager
import hashlib
import math
import os
import threading
import time

from operation_metrics import METRICS


class SubmissionBusy(ValueError):
    def __init__(self, retry_after=1):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__('Task submission is busy. Retry the same captured request after ' +
                         str(self.retry_after) + ' seconds; do not recapture files or change the request ID.')


class SubmissionLimits:
    def __init__(self, *, burst=8, refill_seconds=5, max_active=4, per_session=2,
                 max_sessions=2048, clock=time.monotonic):
        if (any(type(value) is not int or value < 1 for value in
                (burst, max_active, per_session, max_sessions))
                or not math.isfinite(refill_seconds) or refill_seconds <= 0):
            raise ValueError('Task submission limits must be positive.')
        self.burst, self.refill_seconds = burst, refill_seconds
        self.max_active, self.per_session, self.max_sessions = max_active, per_session, max_sessions
        self.clock, self.lock, self.sessions, self.active = clock, threading.Lock(), {}, 0

    @classmethod
    def configured(cls):
        fields = dict(burst=('BURST', 8, 128), refill_seconds=('REFILL_SECONDS', 5, 3600),
                      max_active=('MAX_ACTIVE', 4, 32), per_session=('PER_SESSION', 2, 8))
        settings = {}
        for field, (suffix, default, maximum) in fields.items():
            value = int(os.environ.get('HERDR_TASK_SUBMIT_' + suffix, default))
            if not 1 <= value <= maximum:
                raise ValueError('Invalid task submission limit: HERDR_TASK_SUBMIT_' + suffix)
            settings[field] = value
        return cls(**settings)

    @contextmanager
    def claim(self, run_id, token):
        key = hashlib.sha256((run_id + '\0' + token).encode()).digest()
        with self.lock:
            now = self.clock()
            row = self.sessions.get(key)
            if row is None:
                # Evict only inactive, fully replenished buckets. Rotating IDs
                # cannot discard an active claim or a still-limited session.
                if len(self.sessions) >= self.max_sessions:
                    self.sessions = {k: r for k, r in self.sessions.items()
                        if r['active'] or r['tokens'] + (now - r['updated']) / self.refill_seconds < self.burst}
                if len(self.sessions) >= self.max_sessions:
                    METRICS.increment('task.submit_limited')
                    raise SubmissionBusy(self.refill_seconds)
                row = self.sessions[key] = dict(tokens=float(self.burst), updated=now, active=0)
            row['tokens'] = min(self.burst, row['tokens'] + max(0, now - row['updated']) / self.refill_seconds)
            row['updated'] = now
            if self.active >= self.max_active or row['active'] >= self.per_session:
                METRICS.increment('task.submit_limited')
                raise SubmissionBusy()
            if row['tokens'] < 1:
                METRICS.increment('task.submit_limited')
                raise SubmissionBusy((1 - row['tokens']) * self.refill_seconds)
            row['tokens'] -= 1
            row['active'] += 1
            self.active += 1
        METRICS.increment('task.submit_accepted')
        try:
            yield
        finally:
            with self.lock:
                row['active'] -= 1
                self.active -= 1
