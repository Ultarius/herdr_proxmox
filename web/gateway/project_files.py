"""Shared project boundaries and durable background cloning."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
from urllib.parse import urlsplit
import uuid


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


def clone_repository(root, body, prepared=False):
    if not prepared:
        validate_clone(root, body)
    target = root / body['folder']
    url = body['url']
    if not prepared:
        target.mkdir()
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0',
           'GIT_SSH_COMMAND': 'ssh -o BatchMode=yes -o StrictHostKeyChecking=yes'}
    try:
        result = subprocess.run(['git', '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never',
                                 'clone', '--', url, str(target)],
                                capture_output=True, text=True, timeout=120, env=env)
        if result.returncode:
            diagnostic = redact(result.stderr or '')
            logging.warning('Git clone failed: %s', diagnostic)
            reason = 'Authentication failed. Configure container Git credentials.' if 'authentication' in diagnostic.lower() or 'permission denied' in diagnostic.lower() else 'Repository not found. Check the URL and access.' if 'not found' in diagnostic.lower() else 'Git clone failed. Check gateway logs, repository access and connectivity.'
            raise ValueError(reason + ' The destination folder was retained for inspection.')
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Git clone timed out after two minutes. The destination folder was retained for inspection.') from exc
    return dict(cwd=str(target), message='Repository cloned. Select this project when configuring agents.')


class ProjectJobs:
    def __init__(self, root, path):
        self.root = Path(root).resolve()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
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

    def start(self, body):
        # Validation and reservation happen before the response; the Git process
        # runs once in the worker. Retained destinations prevent accidental retry.
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        validate_clone(self.root, body)
        target = self.root / body['folder']
        target.mkdir()
        job = dict(id=uuid.uuid4().hex, folder=body['folder'], cwd=str(target),
                   state='queued', error='', created_at=datetime.now(timezone.utc).isoformat())
        self.save(job)
        self.worker.submit(self.execute, job, dict(body))
        return job

    def execute(self, job, body):
        job.update(state='running')
        self.save(job)
        try:
            clone_repository(self.root, body, prepared=True)
            job.update(state='completed')
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            job.update(state='failed', error=redact(error))
        self.save(job)
