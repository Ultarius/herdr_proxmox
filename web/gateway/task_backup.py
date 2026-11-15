"""Consistent, complete backups of the task database, including WAL contents."""
from contextlib import closing, contextmanager
from pathlib import Path
import os
import base64
from datetime import datetime, timezone
import json
import math
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


def export_info(connect, output_format='sqlite'):
    with export_slot(), closing(connect()) as source:
        info = backup_plan(source)[1]
        if output_format == 'json':
            # A conservative transfer estimate chooses native streaming early.
            # Actual JSON disk requirements are measured before writing it.
            info['estimated_bytes'] *= 16
        return info


def _quoted(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _nonstandard_number(value):
    raise ValueError('Nonstandard JSON number')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON number')
    return number


def _readable_row(columns, values, rowid):
    record, storage_types, json_columns = {}, {}, []
    for name, value in zip(columns, values):
        kind = ('null' if value is None else 'blob' if isinstance(value, bytes) else
                'integer' if isinstance(value, int) else 'real' if isinstance(value, float) else 'text')
        storage_types[name] = kind
        if kind == 'blob':
            value = base64.b64encode(value).decode('ascii')
        elif kind == 'real' and not math.isfinite(value):
            value = {'sqlite_real': str(value)}
        elif kind == 'text' and value.lstrip().startswith(('{', '[')):
            try:
                value = json.loads(value, object_pairs_hook=_unique_object, parse_constant=_nonstandard_number, parse_float=_finite_float)
                json_columns.append(name)
            except (ValueError, RecursionError):
                pass  # Preserve non-JSON text, duplicate keys and nonstandard JSON verbatim.
        record[name] = value
    return dict(rowid=rowid, values=record, storage_types=storage_types, json_text_columns=json_columns)


def _json_chunks(snapshot, exported_at):
    def encode(value, depth=0):
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        # Keep Unicode readable while escaping unpaired surrogates in stored JSON.
        return text.encode('utf-8', errors='backslashreplace').decode('utf-8').replace('\n', '\n' + ' ' * depth)
    with closing(sqlite3.connect(snapshot)) as db:
        schema = [dict(type=row[0], name=row[1], table=row[2], sql=row[3]) for row in db.execute(
            'SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name')]
        yield '{\n  "format": "herdr-task-export",\n  "version": 1,\n  "exported_at": ' + encode(exported_at)
        yield ',\n  "database": ' + encode(dict(
            user_version=db.execute('PRAGMA user_version').fetchone()[0],
            application_id=db.execute('PRAGMA application_id').fetchone()[0]), 2)
        yield ',\n  "schema": ' + encode(schema, 2) + ',\n  "tables": {'
        rowid_tables = {row[1] for row in db.execute('PRAGMA table_list') if row[0] == 'main' and not row[4]}
        for index, table in enumerate(item for item in schema if item['type'] == 'table'):
            name = table['name']
            columns = [dict(cid=row[0], name=row[1], type=row[2], not_null=bool(row[3]), default=row[4], primary_key=row[5])
                       for row in db.execute('PRAGMA table_info(' + _quoted(name) + ')')]
            names = [column['name'] for column in columns]
            alias = next((item for item in ('_rowid_', 'rowid', 'oid') if item not in {n.lower() for n in names}), None)
            has_rowid = alias is not None and name in rowid_tables
            query = ('SELECT ' + alias + ', * FROM ' + _quoted(name) + ' ORDER BY ' + alias if has_rowid
                     else 'SELECT * FROM ' + _quoted(name))
            cursor = db.execute(query)
            # cursor.description also retains any generated columns in SELECT *.
            names = [column[0] for column in cursor.description][1 if has_rowid else 0:]
            yield (',' if index else '') + '\n    ' + encode(name) + ': {\n      "columns": ' + encode(columns, 6) + ',\n      "rows": ['
            for row_index, row in enumerate(cursor):
                readable = _readable_row(names, row[1:] if has_rowid else row, row[0] if has_rowid else None)
                yield (',' if row_index else '') + '\n' + '\n'.join('        ' + line for line in encode(readable).splitlines())
            yield '\n      ]\n    }'
        yield '\n  }\n}\n'


def readable_json(snapshot):
    """Pretty-print all records from the immutable backup, without list limits."""
    exported_at = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + COPY_TIMEOUT

    def chunks():
        for chunk in _json_chunks(snapshot, exported_at):
            if time.monotonic() > deadline:
                raise ValueError('The readable task export exceeded its time limit. Retry on faster backup storage.')
            yield chunk

    required = sum(len(chunk.encode('utf-8')) for chunk in chunks())
    reserve = max(RESERVE_BYTES, required // 10)
    storage = build_capacity.storage_status(snapshot.parent, minimum_free=required + reserve)
    if not storage['available'] or storage['free_bytes'] < required + reserve:
        raise ValueError(f'Readable task export needs {required + reserve} additional bytes free on the backup volume. '
                         'Free space or configure HERDR_TASK_BACKUP_DIR on a larger volume.')
    destination = snapshot.with_suffix('.json')
    with destination.open('w', encoding='utf-8', newline='\n') as output:
        output.writelines(chunks())
    return destination


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
