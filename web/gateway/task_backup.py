"""Consistent, complete backups of the task database, including WAL contents."""
from contextlib import closing, contextmanager
from pathlib import Path
import os
import sqlite3
import tempfile
import threading
import time
import build_capacity

COPY_TIMEOUT = 300
LOCK_TIMEOUT = 5
RESERVE_BYTES = 64 * 1024 ** 2
_export_lock = threading.Lock()


class BackupBusy(ValueError):
    pass


@contextmanager
def export_slot():
    if not _export_lock.acquire(blocking=False):
        raise BackupBusy('Another task export is in progress. Wait for its download to finish, then retry.')
    try:
        yield
    finally:
        _export_lock.release()


def backup_plan(source):
    # Keep metadata reads bounded as well as the backup API's busy retries.
    source.execute('PRAGMA busy_timeout=1000')
    try:
        database = next(row[2] for row in source.execute('PRAGMA database_list') if row[1] == 'main')
        estimated = source.execute('PRAGMA page_count').fetchone()[0] * source.execute('PRAGMA page_size').fetchone()[0]
    except sqlite3.OperationalError as error:
        if getattr(error, 'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise BackupBusy('Task storage is locked. Retry the export when its current writer finishes.') from None
        raise
    configured = os.environ.get('HERDR_TASK_BACKUP_DIR')
    directory = Path(configured) if configured else Path(database).resolve().parent / 'task-backups'
    if not database or not directory.is_absolute() or directory.is_symlink():
        raise ValueError('Task backup storage must be a plain absolute directory on a writable volume.')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    required = estimated + max(RESERVE_BYTES, estimated // 10)
    storage = build_capacity.storage_status(directory, minimum_free=required)
    if not storage['available']:
        raise ValueError('Could not measure task backup storage. Check its volume and permissions before retrying.')
    if storage['free_bytes'] < required or storage.get('free_inodes') is not None and storage['free_inodes'] < 4:
        mib = 1024 ** 2
        raise ValueError(f'Task backup needs {(required + mib - 1) // mib} MiB free on the backup volume '
                         f'(database: {(estimated + mib - 1) // mib} MiB plus reserve); '
                         f'{storage["free_bytes"] // mib} MiB is available. Free space or configure HERDR_TASK_BACKUP_DIR on a larger volume.')
    return directory, dict(estimated_bytes=estimated, required_free_bytes=required, free_bytes=storage['free_bytes'])


def export_info(connect):
    with export_slot(), closing(connect()) as source:
        return backup_plan(source)[1]


@contextmanager
def export_database(connect, *, timeout=None, lock_timeout=None):
    # A raw copy of tasks.sqlite3 can omit committed WAL transactions. SQLite's
    # backup API also preserves every table, index and migration marker, rather
    # than exporting the bounded/trimmed dashboard projection.
    with export_slot(), closing(connect()) as source:
        directory, _ = backup_plan(source)
        with tempfile.TemporaryDirectory(prefix='herdr-task-backup-', dir=directory) as scratch:
            destination = Path(scratch) / 'tasks.sqlite3'
            deadline = time.monotonic() + (COPY_TIMEOUT if timeout is None else timeout)
            busy_since = None

            def progress(status, remaining, total):
                nonlocal busy_since
                now = time.monotonic()
                if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    busy_since = now if busy_since is None else busy_since
                    if now - busy_since >= (LOCK_TIMEOUT if lock_timeout is None else lock_timeout):
                        raise BackupBusy('Task storage is locked. Retry the export when its current writer finishes.')
                else:
                    busy_since = None
                if now > deadline:
                    raise ValueError('The task backup copy exceeded its time limit. The database may be large or its storage slow; retry on faster backup storage.')

            with closing(sqlite3.connect(destination)) as target:
                source.backup(target, pages=256, progress=progress, sleep=0.05)
            # Hold the single-flight lease through streaming, not just copying.
            yield destination
