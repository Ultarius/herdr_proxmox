"""Bounded process-local timings, never commands, paths, prompts or credentials.

These are observations, not admission evidence or a distributed tracing service.
The durable build worker has its own process; validation phase timings also
travel in run.json so that worker activity remains inspectable after completion.
"""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import math
import os
import threading
import time

NAMES = frozenset(('git.command', 'repository.lock_wait', 'repository.lock_hold',
                  'workspace.prepare', 'dependencies.restore', 'execution.prepare', 'execution.materialize',
                  'validation.checks', 'repository.query', 'http.read', 'task.submit', 'task.construct'))
COUNTERS = frozenset(('repository.cache_hit', 'repository.cache_miss',
                      'task.submit_accepted', 'task.submit_limited'))
_request = ContextVar('repository_query_metrics', default=None)


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.series = {}
        self.counters = dict.fromkeys(COUNTERS, 0)

    def observe(self, name, seconds, failed=False):
        if name not in NAMES:
            raise ValueError('Unknown operation metric.')
        milliseconds = max(0.0, seconds * 1000)
        with self.lock:
            row = self.series.setdefault(name, dict(count=0, failed=0, total_ms=0.0, max_ms=0.0,
                                                    recent=deque(maxlen=256)))
            row['count'] += 1
            row['failed'] += int(failed)
            row['total_ms'] += milliseconds
            row['max_ms'] = max(row['max_ms'], milliseconds)
            row['recent'].append(milliseconds)
        if name == 'git.command':
            for current in _request.get() or ():
                current['git_calls'] += 1
                current['git_ms'] += milliseconds

    def increment(self, name):
        if name not in COUNTERS:
            raise ValueError('Unknown operation counter.')
        with self.lock:
            self.counters[name] += 1

    def snapshot(self):
        with self.lock:
            operations = {}
            for name, row in self.series.items():
                recent = sorted(row['recent'])
                operations[name] = {key: round(value, 3) if isinstance(value, float) else value
                                    for key, value in row.items() if key != 'recent'}
                operations[name].update(samples=len(recent),
                    p50_ms=round(recent[math.ceil(len(recent) * .50) - 1], 3),
                    p95_ms=round(recent[math.ceil(len(recent) * .95) - 1], 3))
            return dict(scope='process', pid=os.getpid(), started_at=self.started_at,
                        sample_window=256, operations=operations, counters=dict(self.counters))


METRICS = Metrics()


@contextmanager
def measured(name):
    record = dict(status='running')
    started = time.monotonic()
    try:
        yield record
    except BaseException:
        record['status'] = 'failed'
        raise
    else:
        if record['status'] == 'running':
            record['status'] = 'complete'
    finally:
        seconds = time.monotonic() - started
        record['duration_ms'] = round(max(0.0, seconds * 1000), 3)
        METRICS.observe(name, seconds, record['status'] == 'failed')


@contextmanager
def query_measurement(name='repository.query'):
    stats = dict(git_calls=0, git_ms=0.0)
    token = _request.set((*(_request.get() or ()), stats))
    try:
        with measured(name) as timing:
            yield stats
        stats.update(timing)
        stats['git_ms'] = round(stats['git_ms'], 3)
    finally:
        _request.reset(token)
