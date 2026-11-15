"""Shared project boundaries and durable background cloning."""
from contextlib import closing, nullcontext
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import uuid


def contains_mount(path):
    if sys.platform.startswith('linux'):
        # ismount() cannot identify bind mounts on the same filesystem. The
        # gateway runs in Linux containers, where this is the authoritative list.
        try:
            lines = Path('/proc/self/mountinfo').read_text().splitlines()
        except (OSError, UnicodeError):
            raise ValueError('Could not inspect filesystem mounts. The folder was preserved.') from None
        for line in lines:
            fields = line.split()
            if len(fields) < 6:
                raise ValueError('Could not inspect filesystem mounts. The folder was preserved.')
            mount = Path(re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4]))
            if mount.is_relative_to(path):
                return True
        return False
    return path.is_mount() or any((Path(parent) / name).is_mount()
        for parent, directories, _ in os.walk(path, followlinks=False) for name in directories)


def project_directory(root, value):
    path = Path(value).expanduser().resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_dir():
        raise ValueError('Select an existing directory inside the projects folder.')
    return path


def redact(value):
    value = re.sub(r'https?://[^\s]+', '[repository URL]', str(value))
    value = re.sub(r'(?i)(token|password|authorization)[=: ]+\S+', r'\1=[redacted]', value)
    return ''.join(c for c in value if c in '\n\t' or ord(c) >= 32)[-2000:]


def validate_clone(root, body):
    if not isinstance(body, dict):
        raise ValueError('Expected a JSON object.')
    url, folder = body.get('url'), body.get('folder')
    if not isinstance(folder, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', folder):
        raise ValueError('Use a folder name with letters, numbers, dots, underscores or dashes.')
    if not isinstance(url, str) or len(url) > 2000 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError('Enter an HTTPS or SSH Git repository URL.')
    parsed = urlsplit(url)
    https = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    ssh = bool(re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', url))
    if not (https or ssh):
        raise ValueError('Use HTTPS without embedded credentials, or git@host:path SSH syntax.')
    root.mkdir(parents=True, exist_ok=True)
    target = root / folder
    if target.exists() or target.is_symlink():
        raise ValueError('That project folder already exists. Choose a new folder name.')
    if target.resolve().parent != root:
        raise ValueError('Project folder must be directly inside the projects directory.')


def clone_repository(root, body, prepared=False, connections=None):
    if not prepared:
        validate_clone(root, body)
    target = root / body['folder']
    url = body['url']
    if not prepared:
        target.mkdir()
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0',
           'GIT_SSH_COMMAND': 'ssh -o BatchMode=yes -o StrictHostKeyChecking=yes'}
    try:
        connection_id = body.get('connection_id')
        if connection_id and connections is None:
            raise ValueError('Repository connections are unavailable on this gateway.')
        credentials = connections.credentials(connection_id, url) if connection_id else nullcontext(env)
        with credentials as environment:
            result = subprocess.run(['git', '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never',
                                     'clone', '--', url, str(target)],
                                    capture_output=True, text=True, timeout=120, env=environment)
        if result.returncode:
            diagnostic = redact(result.stderr or '')
            logging.warning('Git clone failed: %s', diagnostic)
            reason = 'Authentication failed. Configure container Git credentials.' if 'authentication' in diagnostic.lower() or 'permission denied' in diagnostic.lower() else 'Repository not found. Check the URL and access.' if 'not found' in diagnostic.lower() else 'Git clone failed. Check gateway logs, repository access and connectivity.'
            raise ValueError(reason + ' The destination folder was retained for inspection.')
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Git clone timed out after two minutes. The destination folder was retained for inspection.') from exc
    if body.get('connection_id'):
        connections.bind(body['connection_id'], target)
    return dict(cwd=str(target), message='Repository cloned. Select this project when configuring agents.')


class ProjectJobs:
    def __init__(self, root, path, connections=None):
        self.connections = connections
        self.root = Path(root).resolve()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='project-clone')
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS clones (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            for row in db.execute('SELECT id, data FROM clones').fetchall():
                job = json.loads(row[1])
                if job['state'] in ('queued', 'running'):
                    job.update(state='interrupted', error='Gateway restarted. Inspect the retained folder before retrying.')
                    db.execute('UPDATE clones SET data=? WHERE id=?', (json.dumps(job), job['id']))

    def connect(self):
        return sqlite3.connect(self.path)

    def close(self):
        self.worker.shutdown(wait=True)

    def save(self, job):
        job['updated_at'] = datetime.now(timezone.utc).isoformat()
        with self.lock, closing(self.connect()) as db, db:
            db.execute('INSERT INTO clones VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (job['id'], json.dumps(job)))

    def snapshot(self):
        with self.lock, closing(self.connect()) as db:
            return {'jobs': [json.loads(row[0]) for row in db.execute('SELECT data FROM clones ORDER BY rowid DESC LIMIT 20')]}

    def remove(self, body, in_use=None, prepare_usage=None):
        """Remove a terminal entry; prepare slow usage reads before the Git lock.

        prepare_usage returns a checker that rechecks durable assignments under
        that lock. The reservation lock also serializes gateway workspace create.
        """
        if (not isinstance(body, dict) or set(body) - {'id', 'delete_folder', 'confirm_folder'}
                or not isinstance(body.get('id'), str) or not re.fullmatch(r'[a-f0-9]{32}', body['id'])
                or type(body.get('delete_folder', False)) is not bool):
            raise ValueError('Select a clone history entry and valid removal options.')
        delete_folder = body.get('delete_folder', False)
        with self.lock, closing(self.connect()) as db, db:
            row = db.execute('SELECT data, rowid FROM clones WHERE id=?', (body['id'],)).fetchone()
            if row is None:
                return dict(id=body['id'], removed=True, folder_deleted=False,
                            message='Clone history entry already removed. No additional folders were deleted.')
            job = json.loads(row[0])
            if job['state'] not in ('completed', 'failed', 'interrupted'):
                raise ValueError('Wait for the clone to finish before removing it.')
            deleted = False
            if delete_folder:
                folder = job.get('folder')
                if (not isinstance(folder, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', folder)
                        or body.get('confirm_folder') != folder):
                    raise ValueError('Type the exact folder name to confirm permanent deletion.')
                target = self.root / folder
                if any(json.loads(later[0]).get('folder') == folder for later in
                       db.execute('SELECT data FROM clones WHERE rowid>?', (row[1],))):
                    raise ValueError('A newer clone owns this folder. Remove only this old history entry.')
                # The durable job, never a client-supplied path, selects the
                # direct child. Refuse aliases, replaced roots and mount points.
                if (self.root.resolve() != self.root or target.is_symlink() or target.resolve() != target
                        or Path(job.get('cwd', '')).resolve() != target or target.is_mount()):
                    raise ValueError('The clone folder changed or is not a direct project directory. Inspect it before deleting.')
                saved_identity = job.get('destination_identity')
                if (not isinstance(saved_identity, list) or len(saved_identity) != 2
                        or any(type(value) is not int or value < 0 for value in saved_identity)
                        or saved_identity[1] == 0):
                    raise ValueError('This clone has no verified original folder identity. Remove its history entry only; inspect the folder before deleting it manually.')
                if in_use is None and prepare_usage is None:
                    raise ValueError('Project usage checks are unavailable. The folder was preserved.')
                if target.exists():
                    if not target.is_dir():
                        raise ValueError('The clone destination is no longer a directory.')
                    identity = target.stat()
                    if saved_identity != [identity.st_dev, identity.st_ino]:
                        raise ValueError('The original clone folder was replaced. Remove only its history entry.')
                    from repository_lock import repository_lock
                    metadata = target / '.git'
                    if metadata.is_symlink() or (metadata.exists() and not metadata.is_dir()):
                        raise ValueError('The clone uses external Git metadata. Inspect it before deleting.')
                    check_usage = prepare_usage() if prepare_usage else in_use
                    if not callable(check_usage):
                        raise ValueError('Project usage checks are unavailable. The folder was preserved.')
                    claim = repository_lock(target) if metadata.is_dir() else nullcontext()
                    with claim:
                        if check_usage(target):
                            raise ValueError('This project is in use. Close its workspaces and remove project assignments before deleting it.')
                        if self.connections and any(Path(item['path']).resolve() == target
                                for item in self.connections.snapshot()['bindings']):
                            raise ValueError('Unassign the repository connection from this project before deleting its folder.')
                        worktrees = metadata / 'worktrees'
                        if worktrees.exists() and any(worktrees.iterdir()):
                            raise ValueError('Remove linked worktrees before deleting the main clone.')
                        # Never traverse a mounted subtree. Ordinary symlinks
                        # inside a checkout are unlinked by rmtree, not followed.
                        if contains_mount(target):
                            raise ValueError('The clone contains a mounted directory. Unmount it before deleting.')
                        current = target.stat()
                        if (target.is_symlink() or target.resolve() != target or target.parent != self.root
                                or self.root.resolve() != self.root or target.is_mount()
                                or (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino)):
                            raise ValueError('The clone folder changed. Refresh before deleting.')
                        shutil.rmtree(target)
                        deleted = True
            db.execute('DELETE FROM clones WHERE id=?', (body['id'],))
        return dict(id=body['id'], removed=True, folder_deleted=deleted,
                    message='Clone folder and history entry deleted.' if deleted else
                    'Clone history entry removed. Its folder was already absent.' if delete_folder else
                    'Clone history entry removed. Existing files and repository connections were preserved.')

    def start(self, body):
        # Validation and reservation happen before the response; the Git process
        # runs once in the worker. Retained destinations prevent accidental retry.
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        with self.lock:
            validate_clone(self.root, body)
            if body.get('connection_id'):
                if self.connections is None:
                    raise ValueError('Repository connections are unavailable on this gateway.')
                with self.connections.credentials(body['connection_id'], body['url']):
                    pass
            target = self.root / body['folder']
            target.mkdir()
            identity = target.stat()
            job = dict(id=uuid.uuid4().hex, folder=body['folder'], cwd=str(target),
                       destination_identity=[identity.st_dev, identity.st_ino],
                       state='queued', error='', url=body['url'], connection_id=body.get('connection_id'),
                       created_at=datetime.now(timezone.utc).isoformat())
            self.save(job)
        self.worker.submit(self.execute, job, dict(body))
        return job

    def execute(self, job, body):
        job.update(state='running')
        self.save(job)
        try:
            clone_repository(self.root, body, prepared=True, connections=self.connections)
            job.update(state='completed')
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            job.update(state='failed', error=redact(error))
        self.save(job)
