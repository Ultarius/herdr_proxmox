"""Owned, rebuildable workspace storage, with fail-closed reclamation.

Only directories allocated below ``root/objects`` are managed automatically.
Source checkouts, SDK receipts, validation records and existing dependency trees
are not inferred to be disposable. The one legacy importer requires a reviewed,
unchanged preview of an exact worktree output declared by an SDK adapter.

Callers must provide authoritative active execution IDs (or a callable returning
them). Unknown activity prevents deletion. Kernel leases additionally coordinate
cooperating cache users; this is not a sandbox against hostile project code.
Inventory walks files and belongs in an explicit/background scan, not a health
endpoint. No project code is executed by this module.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import time
import uuid

GIB = 1024 ** 3
OWNER_FILE = '.herdr-storage-owner.json'
KINDS = {'build-output', 'execution-cache', 'dependency-tree', 'download-cache'}
STATES = {'active', 'closed', 'shared', 'quarantined'}
EXECUTION = re.compile(r'[0-9a-f]{32}')
ANCHORED_DELETION = (os.name == 'posix' and os.open in os.supports_dir_fd
                     and os.rename in os.supports_dir_fd and shutil.rmtree.avoids_symlink_attacks)


@contextmanager
def _directory_fd(path):
    """Pin every ancestor; a concurrent rename cannot redirect an open parent."""
    if not ANCHORED_DELETION:
        raise StorageError('Deletion requires descriptor-anchored Linux filesystem APIs.')
    path = _absolute(path)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _rename_owned(source, destination, measured):
    with _directory_fd(source.parent) as parent, _directory_fd(destination.parent) as target:
        info = os.stat(source.name, dir_fd=parent, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (measured['device'], measured['inode']):
            raise StorageError('Storage root changed before quarantine.')
        try:
            os.stat(destination.name, dir_fd=target, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise StorageError('The quarantine location already exists.')
        os.rename(source.name, destination.name, src_dir_fd=parent, dst_dir_fd=target)


def _remove_owned(path, measured, owner):
    with _directory_fd(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != (measured['device'], measured['inode']):
                raise StorageError('Quarantined storage identity changed before deletion.')
            marker = os.open(OWNER_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            with os.fdopen(marker, 'rb') as stream:
                try:
                    actual_owner = json.loads(stream.read(8193))
                except (ValueError, UnicodeError):
                    raise StorageError('The anchored ownership receipt is unreadable.') from None
                if os.fstat(stream.fileno()).st_nlink != 1 or actual_owner != owner:
                    raise StorageError('The anchored ownership receipt changed.')
            for name in os.listdir(descriptor):
                if name == OWNER_FILE:
                    continue
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    shutil.rmtree(name, dir_fd=descriptor)
                else:
                    os.unlink(name, dir_fd=descriptor)
            os.unlink(OWNER_FILE, dir_fd=descriptor)
            try:
                current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (measured['device'], measured['inode']):
                    raise StorageError('Quarantined storage moved during deletion.')
                os.rmdir(path.name, dir_fd=parent)
            except (OSError, StorageError):
                marker = os.open(OWNER_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                 0o600, dir_fd=descriptor)
                with os.fdopen(marker, 'w', encoding='utf-8') as stream:
                    json.dump(owner, stream, sort_keys=True)
                raise
        finally:
            os.close(descriptor)


class StorageError(ValueError):
    """The directory cannot be proved to be safe managed storage."""


class StorageBusy(StorageError):
    """A cooperating user or another registry operation holds the lease."""


class StorageMetadataError(StorageError):
    """Registry metadata is invalid; reviewed archival never deletes files."""


class StorageOwnerError(StorageMetadataError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt_digest = hashlib.sha256(receipt).hexdigest()


def _absolute(value):
    value = os.fspath(value)
    if not value or any(c in value for c in ('\x00', '\r', '\n')):
        raise StorageError('Invalid storage path.')
    path = Path(value)
    if '..' in path.parts:
        raise StorageError('Parent traversal is not a storage path.')
    return path.absolute()


def _reparse(info):
    return bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _safe_chain(path):
    """Check every existing ancestor without following links or junctions."""
    for entry in reversed((path, *path.parents)):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or _reparse(info):
            raise StorageError('Storage paths must not contain symlinks or reparse points.')
        if entry != path and not stat.S_ISDIR(info.st_mode):
            raise StorageError('A storage ancestor is not a directory.')
    # The independent resolved check catches unexpected platform path aliases.
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)):
        raise StorageError('The resolved storage path differs from its declared path.')


def _regular(path, maximum=None):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or _reparse(info) or info.st_nlink != 1:
        raise StorageError('Storage metadata must be a regular file with one link.')
    if maximum is not None and info.st_size > maximum:
        raise StorageError('Storage metadata exceeds its size limit.')
    return info


def _identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(ord(c) < 32 for c in value):
        raise StorageError('Invalid managed storage identity.')
    return value


def _key(identity):
    return hashlib.sha256(_identity(identity).encode()).hexdigest()


def _active(provider):
    values = provider() if callable(provider) else provider
    if values is None:
        return None
    if isinstance(values, (str, bytes)):
        raise StorageError('Active execution IDs must be a collection, not a string.')
    return set(values)


class _FileLock:
    def __init__(self, path, shared=False):
        self.path, self.shared, self.file = path, shared, None

    def __enter__(self):
        _safe_chain(self.path)
        if self.path.exists():
            _regular(self.path)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or _reparse(info):
                raise StorageError('Unsafe storage lock file.')
            self.file = os.fdopen(fd, 'r+b', buffering=0)
            fd = None
            if os.name == 'nt':
                import msvcrt
                if not info.st_size:
                    self.file.write(b'\0')
                self.file.seek(0)
                # Windows leases serialize readers; they still never block GC.
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                mode = fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX
                fcntl.flock(self.file.fileno(), mode | fcntl.LOCK_NB)
            return self
        except OSError as exc:
            if self.file:
                self.file.close()
            self.file = None
            raise StorageBusy('Managed storage is in use; retry after its user finishes.') from exc
        finally:
            if fd is not None:
                os.close(fd)

    def __exit__(self, *unused):
        if self.file:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()


def _scan(path, *, allow_internal_links=False):
    """Measure allocated bytes and fingerprint metadata, without following links."""
    _safe_chain(path)
    root = path.lstat()
    if not stat.S_ISDIR(root.st_mode) or _reparse(root):
        raise StorageError('A managed storage root must be a directory.')
    digest, size, files = hashlib.sha256(), 0, 0
    inodes = {}
    pending = [path]
    while pending:
        directory = pending.pop()
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            found = Path(entry.path)
            # Windows DirEntry.stat caches zero inode/link counts. Path.lstat
            # obtains the native identity needed for safety and race checks.
            info = found.lstat()
            if stat.S_ISLNK(info.st_mode) and allow_internal_links and os.name != 'nt':
                if Path(os.readlink(found)).is_absolute():
                    raise StorageError('Managed contents contain an absolute symlink.')
                try:
                    destination = found.resolve()
                except (OSError, RuntimeError) as exc:
                    raise StorageError('Managed contents contain an unresolved symlink.') from exc
                if not destination.is_relative_to(path):
                    raise StorageError('Managed contents contain a symlink outside their owned root.')
                # Package-manager .bin links are legitimate. Record the link,
                # never descend it; rmtree unlinks it without following it.
            elif stat.S_ISLNK(info.st_mode) or _reparse(info):
                raise StorageError('Managed contents contain a symlink or reparse point.')
            elif stat.S_ISREG(info.st_mode):
                if found.name == OWNER_FILE and found.parent == path:
                    if info.st_nlink != 1:
                        raise StorageError('The ownership receipt must have exactly one link.')
                else:
                    identity = (info.st_dev, info.st_ino)
                    group = inodes.setdefault(identity, dict(seen=0, links=info.st_nlink))
                    if info.st_nlink < 1 or group['links'] != info.st_nlink:
                        raise StorageError('Managed hard-link membership changed during its scan.')
                    if not group['seen']:
                        size += info.st_blocks * 512 if hasattr(info, 'st_blocks') else info.st_size
                    group['seen'] += 1
                    files += 1
            elif stat.S_ISDIR(info.st_mode):
                pending.append(found)
            else:
                raise StorageError('Managed contents contain a special file.')
            digest.update(json.dumps([found.relative_to(path).as_posix(), info.st_mode,
                                      info.st_dev, info.st_ino, info.st_size, info.st_nlink,
                                      info.st_mtime_ns], separators=(',', ':')).encode())
            if files > 1000000:
                raise StorageError('Managed storage scan exceeded its file limit.')
    # Compilers may link friendly output names to hashed dependencies. Links are
    # rebuildable only when the entire native inode group is inside this root;
    # links into package stores or arbitrary source/evidence remain protected.
    if any(group['seen'] != group['links'] for group in inodes.values()):
        raise StorageError('Managed contents contain a hard-linked file outside their owned root.')
    digest.update(json.dumps([root.st_dev, root.st_ino, root.st_mtime_ns]).encode())
    return dict(bytes=size, files=files, fingerprint=digest.hexdigest(),
                device=root.st_dev, inode=root.st_ino)


class Manager:
    def __init__(self, root, projects):
        self.root, self.projects = _absolute(root), _absolute(projects)
        self.database = self.root / 'registry.sqlite3'

    def _ensure(self):
        _safe_chain(self.root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in ('objects', '.locks', '.trash'):
            path = self.root / name
            _safe_chain(path)
            path.mkdir(mode=0o700, exist_ok=True)
        _safe_chain(self.database)
        if self.database.exists():
            _regular(self.database)

    @contextmanager
    def _write(self):
        self._ensure()
        with _FileLock(self.root / 'registry.lock'):
            # SQLite's rollback-journal mode avoids unowned WAL sidecars.
            for suffix in ('', '-journal', '-wal', '-shm'):
                path = Path(str(self.database) + suffix)
                _safe_chain(path)
                if path.exists():
                    _regular(path)
            if not self.database.exists():
                descriptor = os.open(self.database, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                os.close(descriptor)
            db = sqlite3.connect(self.database, timeout=0)
            try:
                db.execute('CREATE TABLE IF NOT EXISTS storage (identity TEXT PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except sqlite3.OperationalError as exc:
                db.rollback()
                raise StorageBusy('Managed storage registry is busy.') from exc
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def _project(self, project, *, verify_filesystem=True):
        if not project:
            return ''
        if not isinstance(project, (str, os.PathLike)):
            raise StorageError('The project must be a directory path.')
        path = _absolute(project if Path(project).is_absolute() else self.projects / project)
        if path == self.projects or not path.is_relative_to(self.projects):
            raise StorageError('The project is outside the configured projects directory.')
        if verify_filesystem:
            _safe_chain(self.projects)
            _safe_chain(path)
        return str(path)

    def _path(self, identity):
        return self.root / 'objects' / _key(identity)

    def binding(self, identity):
        """Resolve a single registered owner for grants or broker operations."""
        records = self._read(ids=[identity])
        if not records:
            raise StorageError('Managed storage identity is not registered.')
        record = records[0]
        path = self._validate(record)
        self._check_owner(record, path)
        return {key: value for key, value in record.items() if key != '_row_digest'}

    def _legacy_contract(self, adapter=None, output=None):
        import environment_adapters
        contracts = [(name, relative) for name, relative in environment_adapters.legacy_output_contracts()
                     if (adapter is None or name == adapter) and (output is None or relative == output)]
        if len(contracts) != 1:
            raise StorageError('Select one supported adapter output for legacy adoption.')
        name, relative = contracts[0]
        from pathlib import PurePosixPath
        path = PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in relative or ':' in relative:
            raise StorageError('The adapter legacy output contract is invalid.')
        return name, path

    def _legacy_path(self, execution_id, adapter=None, output=None):
        if not isinstance(execution_id, str) or not EXECUTION.fullmatch(execution_id):
            raise StorageError('Legacy storage requires an exact Herdr execution ID.')
        _, relative = self._legacy_contract(adapter, output)
        return self.projects / '.herdr-worktrees' / execution_id / Path(*relative.parts)

    def _metadata(self, record):
        if record.get('registry_error') or not {'id', 'kind', 'state', 'owner', 'generation', 'path', 'execution_id', 'project'} <= record.keys():
            raise StorageError('Malformed managed storage record; its files are retained.')
        _identity(record['id'])
        if record['kind'] not in KINDS or record['state'] not in STATES:
            raise StorageError('Unknown managed storage record type or state.')
        if not re.fullmatch(r'[0-9a-f]{32}', record['owner']):
            raise StorageError('Invalid managed storage owner.')
        if type(record['generation']) is not int or record['generation'] < 1:
            raise StorageError('Invalid managed storage generation.')
        if record.get('project'):
            self._project(record['project'], verify_filesystem=False)
        path = _absolute(record['path'])
        expected = (self._legacy_path(record['execution_id'], record.get('adapter'), record.get('output'))
                    if record.get('legacy') else self._path(record['id']))
        if path != expected:
            raise StorageError('The registered storage path does not match its identity.')
        if record.get('legacy') and record['kind'] != 'build-output':
            raise StorageError('Only legacy build outputs can be adopted.')
        return path

    def _validate(self, record):
        try:
            path = self._metadata(record)
        except (ValueError, KeyError, TypeError) as error:
            raise StorageMetadataError(str(error)) from error
        if record.get('project'):
            self._project(record['project'])
        _safe_chain(path)
        return path

    @staticmethod
    def _decode(identity, data):
        try:
            record = json.loads(data)
            if (not isinstance(record, dict) or record.get('id') != identity or
                    not {'kind', 'state', 'owner', 'generation', 'path', 'execution_id', 'project'} <= record.keys() or
                    not isinstance(record['kind'], str) or record['kind'] not in KINDS or
                    not isinstance(record['state'], str) or record['state'] not in STATES or
                    not isinstance(record['owner'], str) or not re.fullmatch(r'[0-9a-f]{32}', record['owner']) or
                    type(record['generation']) is not int or record['generation'] < 1 or
                    any(not isinstance(record[key], str) for key in ('path', 'execution_id', 'project')) or
                    not record['path'] or not Path(record['path']).is_absolute() or
                    (record['execution_id'] and not EXECUTION.fullmatch(record['execution_id']))):
                raise ValueError()
            _identity(identity)
            for field in ('created_at', 'last_used', 'closed_at'):
                value = record.get(field)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError()
            return dict(record, _row_digest=hashlib.sha256(str(data).encode()).hexdigest())
        except (ValueError, TypeError, OverflowError):
            return dict(id=identity, registry_error=True, row_digest=hashlib.sha256(str(data).encode()).hexdigest(),
                        state='invalid', kind='unknown', execution_id='', generation=0, path=None)

    @classmethod
    def _record(cls, db, identity):
        row = db.execute('SELECT data FROM storage WHERE identity=?', (identity,)).fetchone()
        return cls._decode(identity, row[0]) if row else None

    @classmethod
    def _records(cls, db):
        # Pagination bounds fetch memory without making row 10,001 an outage.
        cursor = db.execute('SELECT identity,data FROM storage ORDER BY identity')
        result = []
        while batch := cursor.fetchmany(256):
            result.extend(cls._decode(identity, data) for identity, data in batch)
        return result

    @staticmethod
    def _save(db, record):
        db.execute('INSERT OR REPLACE INTO storage(identity,data) VALUES (?,?)',
                   (record['id'], json.dumps({key: value for key, value in record.items() if key != '_row_digest'},
                                            sort_keys=True, separators=(',', ':'))))

    @staticmethod
    def _owner(record):
        fields = ('id', 'owner', 'kind', 'project', 'execution_id') + (('adapter', 'output') if record.get('legacy') else ())
        return {key: record[key] for key in fields}

    def _check_owner(self, record, path):
        marker = path / OWNER_FILE
        _regular(marker, 8192)
        with marker.open('rb') as stream:
            contents = stream.read(8193)
        if len(contents) > 8192:
            raise StorageError('The managed storage ownership receipt exceeds its size limit.')
        try:
            owner = json.loads(contents)
        except (ValueError, UnicodeError):
            raise StorageOwnerError('The managed storage ownership receipt is unreadable.', contents) from None
        if owner != self._owner(record):
            raise StorageOwnerError('The managed storage ownership receipt does not match the registry.', contents)

    def _new_record(self, identity, path, kind, project, execution_id, state, legacy=False):
        if kind not in KINDS or state not in {'active', 'closed', 'shared'}:
            raise StorageError('Invalid managed storage kind or initial state.')
        if execution_id and (not isinstance(execution_id, str) or not EXECUTION.fullmatch(execution_id)):
            raise StorageError('Invalid managed storage execution ID.')
        if kind != 'download-cache' and (not project or not execution_id):
            raise StorageError('Build outputs and installed dependency trees need a project and execution owner.')
        now = time.time()
        return dict(id=_identity(identity), path=str(path), kind=kind, project=self._project(project),
                    execution_id=execution_id, state=state, owner=uuid.uuid4().hex, generation=1,
                    created_at=now, last_used=now, closed_at=now if state == 'closed' else None,
                    legacy=legacy)

    def register(self, identity, path, *, kind='build-output', project='', execution_id='', state='active'):
        """Claim a new/empty exact managed root; existing contents are never adopted."""
        path = _absolute(path)
        if path != self._path(identity):
            raise StorageError('New managed storage must use its exact allocated objects directory.')
        with self._write() as db:
            existing = self._record(db, identity)
            if existing:
                self._validate(existing)
                if existing['state'] == 'quarantined':
                    raise StorageError('Managed storage is quarantined; reclaim it before reuse.')
                self._check_owner(existing, path)
                if (existing['kind'], existing['project'], existing['execution_id']) != (kind, self._project(project), execution_id):
                    raise StorageError('Managed storage identity is already owned by another execution or purpose.')
                return {key: value for key, value in existing.items() if key != '_row_digest'}
            return self._register(db, identity, path, kind, project, execution_id, state)

    def _register(self, db, identity, path, kind, project, execution_id, state):
        record = self._new_record(identity, path, kind, project, execution_id, state)
        _safe_chain(path)
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise StorageError('Existing unmanaged contents cannot be registered automatically.')
        if ANCHORED_DELETION:
            with _directory_fd(path.parent) as parent:
                try:
                    os.mkdir(path.name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                directory = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    if os.listdir(directory):
                        raise StorageError('Existing unmanaged contents cannot be registered automatically.')
                    descriptor = os.open(OWNER_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                         0o600, dir_fd=directory)
                    with os.fdopen(descriptor, 'w', encoding='utf-8') as marker:
                        json.dump(self._owner(record), marker, sort_keys=True)
                finally:
                    os.close(directory)
        else:
            # Non-Linux clients may inspect/allocate, but never qualify deletion.
            path.mkdir(mode=0o700, exist_ok=True)
            descriptor = os.open(path / OWNER_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as marker:
                json.dump(self._owner(record), marker, sort_keys=True)
        self._save(db, record)
        return record

    def allocate(self, identity, *, kind='build-output', project='', execution_id='', shared=False):
        """Allocate an owned root or reopen that identity for cooperating writers."""
        path = self._path(identity)
        state = 'shared' if shared or kind == 'download-cache' else 'active'
        with self._write() as db:
            record = self._record(db, identity)
            if record:
                self._validate(record)
                if record['state'] == 'quarantined':
                    raise StorageError('Managed storage is quarantined; reclaim it before reuse.')
                self._check_owner(record, path)
                if (record['kind'], record['project'], record['execution_id']) != (kind, self._project(project), execution_id):
                    raise StorageError('Managed storage identity is already owned by another execution or purpose.')
                record.update(state=state, last_used=time.time(), closed_at=None, generation=record['generation'] + 1)
                self._save(db, record)
            else:
                self._register(db, identity, path, kind, project, execution_id, state)
        return path

    def close_execution(self, execution_id):
        """Caller has closed the execution/session; active-ID checks still apply."""
        if not isinstance(execution_id, str) or not EXECUTION.fullmatch(execution_id):
            raise StorageError('Invalid execution ID.')
        changed = []
        with self._write() as db:
            for record in self._records(db):
                if record.get('execution_id') == execution_id and record.get('state') == 'active':
                    self._validate(record)
                    record.update(state='closed', closed_at=time.time(), last_used=time.time(), generation=record['generation'] + 1)
                    self._save(db, record)
                    changed.append(record['id'])
        return changed

    @contextmanager
    def lease(self, identity):
        """Hold while using a root, including a shared publisher/download cache."""
        with self._write() as db:
            record = self._record(db, identity)
            if not record:
                raise StorageError('Managed storage identity is not registered.')
            path = self._validate(record)
            self._check_owner(record, path)
        with _FileLock(self.root / '.locks' / (_key(identity) + '.lock'), shared=True):
            # Recheck after acquiring the lock in case GC won between lookups.
            self._check_owner(record, path)
            yield path

    def _read(self, ids=None):
        _safe_chain(self.database)
        if not self.database.exists():
            return []
        _regular(self.database)
        db = sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=0)
        try:
            return (self._records(db) if ids is None else
                    [record for identity in ids if (record := self._record(db, identity)) is not None])
        except sqlite3.OperationalError as exc:
            raise StorageBusy('Managed storage registry is busy or unavailable.') from exc
        finally:
            db.close()

    @staticmethod
    def _reason(record, active, records):
        if active is None:
            return 'Execution activity is unknown.'
        if record['execution_id'] and record['execution_id'] in active:
            return 'Its execution is active.'
        if record['state'] == 'active':
            return 'Its execution has not been closed.'
        return ''

    def inventory(self, active_execution_ids=None, *, ids=None):
        """Return measured roots and preview generations; no directory is created."""
        records, active = self._read(ids), _active(active_execution_ids)
        entries = []
        for record in records:
            item = {key: record.get(key) for key in ('id', 'path', 'kind', 'project', 'execution_id', 'state',
                                                    'generation', 'created_at', 'last_used', 'closed_at', 'legacy')}
            item.update(bytes=0, files=0, eligible=False, reason='')
            if record.get('registry_error'):
                item.update(repairable=True, row_digest=record['row_digest'])
            try:
                path = self._validate(record)
                path = self._quarantine_path(record) if record['state'] == 'quarantined' else path
                self._check_owner(record, path)
                item.update(_scan(path, allow_internal_links=not record.get('legacy')))
                reason = self._reason(record, active, records)
                if not reason:
                    with _FileLock(self.root / '.locks' / (_key(record['id']) + '.lock')):
                        self._tracked(path, record['project'], private_cache=self._private_cache(record))
                item.update(reason=reason, eligible=not reason)
            except (OSError, StorageError, KeyError, TypeError) as exc:
                item['reason'] = str(exc)
                if isinstance(exc, StorageMetadataError) and not record.get('registry_error'):
                    item.update(repairable=True, row_digest=self._repair_digest(record, exc))
            entries.append(item)
        return dict(entries=entries, total_bytes=sum(row['bytes'] for row in entries),
                    output_bytes=sum(row['bytes'] for row in entries if row['kind'] != 'download-cache'),
                    download_bytes=sum(row['bytes'] for row in entries if row['kind'] == 'download-cache'),
                    reclaimable_bytes=sum(row['bytes'] for row in entries if row['eligible']),
                    activity_known=active is not None, sampled_at=time.time())

    @staticmethod
    def _repair_digest(record, error):
        digest = record['_row_digest']
        if isinstance(error, StorageOwnerError):
            digest = hashlib.sha256((digest + ':' + error.receipt_digest).encode()).hexdigest()
        return digest

    def forget_invalid(self, identity, row_digest, *, reviewed=False):
        """Archive only invalid metadata; never delete its filesystem contents."""
        if reviewed is not True:
            raise StorageError('Review the invalid registry record before forgetting it.')
        if not isinstance(identity, str) or not isinstance(row_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', row_digest):
            raise StorageError('Select an exact invalid registry row and its preview digest.')
        with self._write() as db:
            row = db.execute('SELECT data FROM storage WHERE identity=?', (identity,)).fetchone()
            if row is None:
                raise StorageError('The registry record changed; inspect it again.')
            record = self._decode(identity, row[0])
            digest = record.get('row_digest') if record.get('registry_error') else None
            if not digest:
                try:
                    path = self._validate(record)
                    if record['state'] == 'quarantined':
                        path = self._quarantine_path(record)
                    self._check_owner(record, path)
                except StorageMetadataError as error:
                    digest = self._repair_digest(record, error)
                except (StorageError, OSError):
                    raise StorageError('Filesystem failures cannot be bypassed through registry repair.') from None
            if not digest:
                raise StorageError('A valid storage owner cannot be forgotten through registry repair.')
            if digest != row_digest:
                raise StorageError('The registry or ownership receipt changed; inspect it again.')
            db.execute('CREATE TABLE IF NOT EXISTS registry_repairs (identity TEXT, data TEXT, repaired_at REAL)')
            db.execute('INSERT INTO registry_repairs VALUES (?,?,?)', (identity, row[0], time.time()))
            db.execute('DELETE FROM storage WHERE identity=?', (identity,))
        return dict(id=identity, forgotten=True, files_deleted=False)

    @staticmethod
    def _private_cache(record):
        return not record.get('legacy') and record['kind'] in ('execution-cache', 'dependency-tree')

    def _tracked(self, path, project, *, private_cache=False):
        """Protect enclosing repositories and repositories inside output.

        The configured project is an owner, not the filesystem's Git boundary.
        A store may be in another checkout or a nested repository.
        """
        path = _absolute(path)
        for candidate in (path, *path.parents):
            if candidate.name == '.git' or ((candidate / 'HEAD').is_file() and
                    (candidate / 'objects').is_dir() and (candidate / 'refs').is_dir()):
                raise StorageError('Managed contents inside Git metadata cannot be reclaimed.')
        # Explicitly allocated private caches may contain dependency checkouts
        # and Git object databases. Output/adopted roots must never contain repos.
        for parent, directories, files in (() if private_cache else os.walk(path, followlinks=False)):
            if '.git' in directories or '.git' in files or ('HEAD' in files and 'objects' in directories and 'refs' in directories):
                raise StorageError('A Git repository inside managed contents cannot be reclaimed.')
            directories[:] = [name for name in directories if not Path(parent, name).is_symlink()]
        for candidate in (path, *path.parents):
            marker = candidate / '.git'
            if marker.exists() or marker.is_symlink():
                top = Path(self._git(candidate, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
                if not path.is_relative_to(top):
                    raise StorageError('Managed contents have an inconsistent Git boundary.')
                if self._git(top, 'ls-files', '-z', '--', path.relative_to(top).as_posix()):
                    raise StorageError('Committed project files cannot be reclaimed as build storage.')
                # A nested repository can cover files still tracked by its
                # outer checkout. Every enclosing index must agree, not just
                # the nearest Git boundary.

    @staticmethod
    def _git(checkout, *arguments):
        try:
            _safe_chain(Path(checkout) / '.git')
            environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
            environment.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
            # ls-files can invoke a configured fsmonitor hook unless disabled;
            # storage qualification must never execute project-supplied code.
            result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(checkout), *arguments],
                                    capture_output=True, timeout=15, check=False, env=environment)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StorageError('Could not verify the project Git ownership.') from exc
        if result.returncode:
            raise StorageError('Could not verify the project Git ownership.')
        return result.stdout

    def _quarantine_path(self, record):
        original = self._validate(record)
        expected = (original.parent if record.get('legacy') else self.root / '.trash') / ('.herdr-trash-' + _key(record['id']) + '-' + record['owner'])
        if record.get('quarantine') != str(expected):
            raise StorageMetadataError('Invalid managed storage quarantine location.')
        _safe_chain(expected)
        return expected

    def reclaim(self, ids, *, active_execution_ids=None, dry_run=True, expected=None):
        """Delete exact reviewed identities only; preview generations can be bound."""
        if isinstance(ids, (str, bytes)):
            raise StorageError('Reclamation requires a list of storage identities.')
        requested = list(dict.fromkeys(_identity(value) for value in ids))
        expected = expected or {}
        result = dict(dry_run=bool(dry_run), reclaimed=[], candidates=[], skipped=[], reclaimed_bytes=0)
        if not requested or not self.database.exists():
            return result
        with self._write() as db:
            records = self._records(db)
            by_id = {row['id']: row for row in records}
            for identity in requested:
                record = by_id.get(identity)
                try:
                    if not record:
                        raise StorageError('Storage identity is no longer registered.')
                    if identity in expected and record['generation'] != expected[identity]:
                        raise StorageError('Storage changed since the preview; scan it again.')
                    original = self._validate(record)
                    path = self._quarantine_path(record) if record['state'] == 'quarantined' else original
                    reason = self._reason(record, _active(active_execution_ids), records)
                    if reason:
                        raise StorageError(reason)
                    with _FileLock(self.root / '.locks' / (_key(identity) + '.lock')):
                        self._check_owner(record, path)
                        measured = _scan(path, allow_internal_links=not record.get('legacy'))
                        self._tracked(original, record['project'], private_cache=self._private_cache(record))
                        if path != original:
                            self._tracked(path, record['project'], private_cache=self._private_cache(record))
                        # The activity source may change while a large tree is scanned.
                        reason = self._reason(record, _active(active_execution_ids), records)
                        if reason:
                            raise StorageError(reason)
                        result['candidates'].append(identity)
                        if dry_run:
                            continue
                        if record['state'] != 'quarantined':
                            quarantine = (path.parent if record.get('legacy') else self.root / '.trash') / ('.herdr-trash-' + _key(identity) + '-' + record['owner'])
                            _safe_chain(quarantine)
                            if quarantine.exists():
                                raise StorageError('The quarantine location already exists.')
                            before = path.lstat()
                            if (before.st_dev, before.st_ino) != (measured['device'], measured['inode']):
                                raise StorageError('Storage root changed during reclamation.')
                            _rename_owned(path, quarantine, measured)
                            record.update(state='quarantined', quarantine=str(quarantine), generation=record['generation'] + 1)
                            self._save(db, record)
                            # Commit the recoverable location before removing any contents.
                            db.commit()
                            db.execute('BEGIN IMMEDIATE')
                            path = quarantine
                        self._check_owner(record, path)
                        self._tracked(path, record['project'], private_cache=self._private_cache(record))
                        after = _scan(path, allow_internal_links=not record.get('legacy'))
                        if (after['device'], after['inode'], after['fingerprint']) != (measured['device'], measured['inode'], measured['fingerprint']):
                            raise StorageError('Storage contents changed during reclamation; quarantine retained.')
                        if self._reason(record, _active(active_execution_ids), records):
                            raise StorageError('Execution became active; quarantine retained for recovery.')
                        _remove_owned(path, measured, self._owner(record))
                        db.execute('DELETE FROM storage WHERE identity=?', (identity,))
                        result['reclaimed'].append(identity)
                        result['reclaimed_bytes'] += measured['bytes']
                        by_id.pop(identity, None)
                except (OSError, StorageError, KeyError, TypeError) as exc:
                    result['skipped'].append(dict(id=identity, reason=str(exc)))
        return result

    def maintain(self, *, active_execution_ids=None, pressure=False, output_budget_bytes=8 * GIB,
                 shared_budget_bytes=4 * GIB, max_closed_run_bytes=GIB, idle_seconds=86400, kinds=None):
        """Apply retention budgets to eligible inactive roots only."""
        for value in (output_budget_bytes, shared_budget_bytes, max_closed_run_bytes, idle_seconds):
            if value < 0:
                raise StorageError('Managed storage retention values must not be negative.')
        scan = self.inventory(active_execution_ids)
        outputs = sum(row['bytes'] for row in scan['entries'] if row['eligible'] and row['kind'] != 'download-cache')
        downloads = sum(row['bytes'] for row in scan['entries'] if row['eligible'] and row['kind'] == 'download-cache')
        executions = {}
        for entry in scan['entries']:
            if entry['eligible'] and entry['kind'] != 'download-cache':
                executions[entry['execution_id']] = executions.get(entry['execution_id'], 0) + entry['bytes']
        selected, expected, now = [], {}, time.time()
        for entry in sorted(scan['entries'], key=lambda row: (row.get('last_used') or 0, row['id'])):
            if not entry['eligible'] or kinds is not None and entry['kind'] not in kinds:
                continue
            download = entry['kind'] == 'download-cache'
            idle = now - (entry.get('last_used') or now) >= idle_seconds
            over = downloads > shared_budget_bytes if download else (outputs > output_budget_bytes or executions[entry['execution_id']] > max_closed_run_bytes)
            if pressure or idle or over or entry['state'] == 'quarantined':
                selected.append(entry['id'])
                expected[entry['id']] = entry['generation']
                if download:
                    downloads -= entry['bytes']
                else:
                    outputs -= entry['bytes']
                    executions[entry['execution_id']] -= entry['bytes']
        result = self.reclaim(selected, active_execution_ids=active_execution_ids, dry_run=False, expected=expected)
        result['inventory'] = scan
        return result

    def preview_legacy(self, execution_id, *, project, active_execution_ids=None, adapter=None, output=None):
        """Read-only review for one adapter-declared existing worktree output."""
        if not isinstance(execution_id, str) or not EXECUTION.fullmatch(execution_id):
            raise StorageError('Legacy storage requires an exact Herdr execution ID.')
        active = _active(active_execution_ids)
        if active is None or execution_id in active:
            raise StorageError('Legacy adoption requires authoritative proof that its execution is inactive.')
        project = self._project(project)
        if not project:
            raise StorageError('Legacy adoption requires its source project.')
        adapter, output = self._legacy_contract(adapter, output)
        path = self._legacy_path(execution_id, adapter, output.as_posix())
        _safe_chain(path)
        checkout = self.projects / '.herdr-worktrees' / execution_id
        common = self._git(checkout, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()
        source_common = self._git(Path(project), 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()
        if _absolute(common) != _absolute(source_common):
            raise StorageError('The legacy worktree belongs to a different project repository.')
        self._tracked(path, project)
        if (path / OWNER_FILE).exists():
            raise StorageError('Legacy storage already has an ownership receipt; inspect its registry.')
        return dict(id='legacy:' + adapter + ':' + _key(output.as_posix())[:16] + ':' + execution_id,
                    adapter=adapter, output=output.as_posix(), path=str(path), kind='build-output',
                    project=project, execution_id=execution_id, **_scan(path))

    def adopt_legacy(self, preview, *, reviewed=False, active_execution_ids=None):
        """Explicitly adopt an unchanged preview; never used by automatic discovery."""
        if reviewed is not True or not isinstance(preview, dict):
            raise StorageError('Review the legacy storage preview before adopting it.')
        fresh = self.preview_legacy(preview.get('execution_id'), project=preview.get('project'),
                                    active_execution_ids=active_execution_ids, adapter=preview.get('adapter'), output=preview.get('output'))
        if preview != fresh:
            raise StorageError('Legacy contents changed since the preview; review a new preview.')
        with self._write() as db:
            identity, path = fresh['id'], Path(fresh['path'])
            if any(row['id'] == identity for row in self._records(db)):
                raise StorageError('Legacy storage identity is already registered.')
            with _FileLock(self.root / '.locks' / (_key(identity) + '.lock')):
                # Recheck both activity and the complete directory just before writing ownership.
                current = self.preview_legacy(fresh['execution_id'], project=fresh['project'],
                                              active_execution_ids=active_execution_ids, adapter=fresh['adapter'], output=fresh['output'])
                if current != fresh:
                    raise StorageError('Legacy contents changed during adoption.')
                record = self._new_record(identity, path, 'build-output', fresh['project'],
                                          fresh['execution_id'], 'closed', legacy=True)
                record.update(adapter=fresh['adapter'], output=fresh['output'])
                with _directory_fd(path) as directory:
                    actual = os.fstat(directory)
                    if (actual.st_dev, actual.st_ino) != (fresh['device'], fresh['inode']):
                        raise StorageError('Legacy storage changed before its ownership receipt was written.')
                    descriptor = os.open(OWNER_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                         0o600, dir_fd=directory)
                    with os.fdopen(descriptor, 'w', encoding='utf-8') as marker:
                        json.dump(self._owner(record), marker, sort_keys=True)
                self._save(db, record)
                return record
