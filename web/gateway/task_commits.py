"""Gateway-owned task revisions backed by Git objects and a durable intent.

Only captured bytes enter a commit. Git and SQLite do not share a transaction:
the persisted intent, retained ref and expected-old-SHA update make retries
recoverable. The live worktree is never rewritten. This is orchestration for
agents sharing a Unix account, not an exclusive filesystem security boundary.
"""
from contextlib import closing
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile

import project_git
from discovery import redact
from operation_metrics import measured
from repository_lock import repository_lock

from task_revision_contract import normalize, MAX_REQUEST


def command(path, *args, data=None, index=None, identity=None):
    """Binary-safe Git plumbing with no inherited repository overrides."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    environment.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1')
    if index is not None:
        environment['GIT_INDEX_FILE'] = str(index)
    if identity:
        environment.update(identity)
    with measured('git.command'):
        try:
            result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
                                     '-c', 'core.hooksPath=' + os.devnull, '-c', 'commit.gpgSign=false',
                                     '-c', 'core.fsync=committed,reference', '-C', str(path), *args],
                                    input=data, capture_output=True, timeout=30, env=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError('Managed commit Git operation is unavailable or timed out.') from error
        if result.returncode:
            diagnostic = redact(result.stderr.decode('utf-8', errors='replace'))[:1000]
            raise ValueError('Managed commit Git operation was refused; inspect the branch and index.' +
                             (' Git: ' + diagnostic if diagnostic else ''))
        if len(result.stdout) > MAX_REQUEST:
            raise ValueError('Managed commit Git output exceeds its limit.')
        return result.stdout


def index_digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Task index is unavailable or unsafe.')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def recover_lock(lock, marker):
    """Called only under the cross-process repository lock.

    Native Git locks are never reclaimed. Our complete marker is linked into
    place atomically, so an abrupt process death cannot leave an ambiguous
    empty or partly-written lock bearing our ownership claim.
    """
    if lock.is_file() and not lock.is_symlink() and lock.stat().st_size == len(marker):
        if lock.read_bytes() == marker:
            lock.unlink()


def claim_index(lock, marker):
    recover_lock(lock, marker)
    descriptor, name = tempfile.mkstemp(prefix='herdr-index-lock-', dir=lock.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(marker)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, lock)
            sync_directory(lock.parent)
        except FileExistsError as error:
            raise ValueError('Task index is locked; inspect the Git operation before retrying.') from error
    finally:
        Path(name).unlink(missing_ok=True)


def replace_index(index, content):
    descriptor, name = tempfile.mkstemp(prefix='herdr-index-', dir=index.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, index)
        sync_directory(index.parent)
    finally:
        Path(name).unlink(missing_ok=True)


class TaskCommits:
    def __init__(self, service):
        self.service = service
        with closing(service.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS task_revisions '
                       '(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, fingerprint TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS task_revisions_task ON task_revisions(task_id)')

    def store(self, record, fingerprint):
        with closing(self.service.connect()) as db, db:
            changed = db.execute('INSERT INTO task_revisions VALUES (?,?,?,?) ON CONFLICT(id) '
                'DO UPDATE SET data=excluded.data WHERE task_revisions.fingerprint=excluded.fingerprint',
                (record['id'], record['task_id'], fingerprint, json.dumps(record)))
            if changed.rowcount != 1:
                raise ValueError('Revision request ID already used for different content.')

    @staticmethod
    def public(record):
        return {k: v for k, v in record.items() if k not in ('payload', 'index_before')}

    def revisions(self, task_id, before=None, *, limit=50):
        self.service.get(task_id)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Invalid revision limit.')
        if before is not None and (not isinstance(before, int) or before < 1):
            raise ValueError('Invalid revision cursor.')
        with closing(self.service.connect()) as db:
            rows = db.execute('SELECT rowid,data FROM task_revisions WHERE task_id=? AND rowid<? '
                              'ORDER BY rowid DESC LIMIT ?',
                              (task_id, before or 9223372036854775807, limit + 1)).fetchall()
        return dict(revisions=[self.public(json.loads(row[1])) for row in rows[:limit]],
                    next_before=rows[limit - 1][0] if len(rows) > limit else None)

    def for_commit(self, task_id, sha):
        with closing(self.service.connect()) as db:
            row = db.execute("SELECT data FROM task_revisions WHERE task_id=? AND json_extract(data,'$.sha')=? "
                             "AND json_extract(data,'$.state')='complete' ORDER BY rowid DESC LIMIT 1", (task_id, sha)).fetchone()
        return self.public(json.loads(row[0])) if row else None

    def submit(self, body):
        payload = normalize(body)
        task_id = payload['task_id']
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        revision_id = hashlib.sha256((task_id + ':' + payload['request_id']).encode()).hexdigest()
        marker = ('Herdr task revision\n' + revision_id + '\n' + fingerprint + '\n').encode()
        with self.service.operation('task:' + task_id):
            task = self.service.get(task_id)
            jobs = self.service.store.snapshot(live_status=False)['jobs']
            run = next((job for job in jobs if job['id'] == task.get('run_id')), None)
            token = body.get('token')
            if (not run or run.get('state') != 'persona_sent' or run.get('task_id') != task_id
                    or run.get('profile_id') != task.get('profile_id') or payload['run_id'] != run['id']
                    or not isinstance(token, str) or not run.get('completion_token')
                    or not hmac.compare_digest(token.encode(), run['completion_token'].encode())):
                raise ValueError('Task revision requires the current task session token.')
            if task['state'] in ('merged', 'closed', 'completed'):
                raise ValueError('This task is terminal; create a new task for further changes.')
            if task.get('branch') != 'herdr/task-' + task_id[:12]:
                raise ValueError('Task branch identity is invalid; inspect the task record.')
            if run.get('alias'):
                from collaboration import current_run
                run = current_run(self.service.store, run, ready=False)
                if not hmac.compare_digest(token.encode(), str(run.get('completion_token', '')).encode()):
                    raise ValueError('Task revision requires the current task session token.')
            with closing(self.service.connect()) as db:
                saved = db.execute('SELECT fingerprint,data FROM task_revisions WHERE id=?', (revision_id,)).fetchone()
            if saved and saved[0] != fingerprint:
                raise ValueError('Revision request ID already used for different content.')
            record = json.loads(saved[1]) if saved else None
            if record and record['state'] == 'complete':
                path = self.service.tree_for(task)
                with repository_lock(path):
                    gitdir = Path(project_git.git(path, 'rev-parse', '--absolute-git-dir').strip())
                    recover_lock(gitdir / 'index.lock', marker)
                self.finish(task, record)
                return self.public(record)  # A replay never moves a branch backwards.
            path = self.service.tree_for(task)
            with repository_lock(path):
                # Another gateway process may have finished while this caller
                # waited for the repository lock. Never overwrite its journal
                # with the intent inferred from an earlier database read.
                with closing(self.service.connect()) as db:
                    fresh = db.execute('SELECT fingerprint,data FROM task_revisions WHERE id=?', (revision_id,)).fetchone()
                if fresh:
                    if fresh[0] != fingerprint:
                        raise ValueError('Revision request ID already used for different content.')
                    record = json.loads(fresh[1])
                    if record['state'] == 'complete':
                        gitdir = Path(project_git.git(path, 'rev-parse', '--absolute-git-dir').strip())
                        recover_lock(gitdir / 'index.lock', marker)
                        self.finish(task, record)
                        return self.public(record)
                if project_git.merge_state(path):
                    raise ValueError('Resolve the unfinished Git operation before submitting a revision.')
                branch, _, gitdir = project_git.checkout_identity(path, self.service.path_for(task))
                if branch != task['branch']:
                    raise ValueError('Task branch changed before revision submission.')
                index = gitdir / 'index'
                # Respect native Git locks; reclaim only this exact submission's
                # marker after a killed gateway has released the repository lock.
                lock = gitdir / 'index.lock'
                claim_index(lock, marker)
                try:
                    if record is None:
                        if command(path, 'rev-parse', 'HEAD').decode().strip() != payload['parent']:
                            raise ValueError('Task parent changed; inspect the new HEAD and submit a new revision.')
                        command(path, 'merge-base', '--is-ancestor', task['base_sha'], payload['parent'])
                        if command(path, 'ls-files', '-u'):
                            raise ValueError('Resolve index conflicts before submitting a revision.')
                        # Never discard unrelated staged changes while reconciling
                        # the index. The new workflow does not require staging.
                        if command(path, 'diff', '--no-ext-diff', '--no-textconv', '--cached', '--name-only', '-z', payload['parent'], '--'):
                            raise ValueError('Unstage existing changes before managed submission; working files will be preserved.')
                        record = dict(id=revision_id, task_id=task_id, run_id=run['id'], request_id=payload['request_id'],
                            parent=payload['parent'], branch=task['branch'], state='intent', payload=payload,
                            author='Herdr agent ' + str(task['profile_id']), committer='Herdr',
                            message=payload['message'],
                            files=[{k: v for k, v in item.items() if k != 'content'} for item in payload['files']],
                            created_at=datetime.now(timezone.utc).isoformat(), index_before=index_digest(index),
                            policy_digest=(run.get('toolchain_policy') or {}).get('digest'),
                            policy_review='Required checks and protected-input review remain separate.')
                        self.store(record, fingerprint)
                    replace_index(index, self.advance(path, record, fingerprint, index))
                    record['state'] = 'complete'
                    record.pop('error', None)
                    self.store(record, fingerprint)
                except (ValueError, OSError) as error:
                    if record and record['state'] != 'complete':
                        record['error'] = str(error)[:2000]
                        self.store(record, fingerprint)
                    raise
                finally:
                    recover_lock(lock, marker)
            # The task is still implementing until normal candidate capture.
            # Historical revisions live in their own table, not in every task poll.
            self.finish(task, record)
            return self.public(record)

    def finish(self, task, record):
        summary = dict(id=record['id'], sha=record['sha'], parent=record['parent'])
        if task.get('managed_revision') == summary:
            return
        # A completion checkpoint may precede the task save. Repair that gap on
        # retry, but never replace a newer task summary with an old replay.
        path = self.service.tree_for(task)
        if command(path, 'rev-parse', 'HEAD').decode().strip() == record['sha']:
            task['managed_revision'] = summary
            self.service.save(task, 'revision_committed', 'task_agent')

    def advance(self, path, record, fingerprint, index):
        with tempfile.TemporaryDirectory(prefix='herdr-commit-') as directory:
            prepared = Path(directory) / 'index'
            if record['state'] == 'intent':
                payload = record['payload']
                command(path, 'read-tree', record['parent'], index=prepared)
                changes = []
                for item in payload['files']:
                    name = item['path']
                    existing = command(path, 'ls-tree', record['parent'], '--', ':(literal)' + name)
                    if existing and not existing.startswith((b'100644 blob ', b'100755 blob ')):
                        raise ValueError('Submitted path replaces a non-regular entry; inspect it explicitly.')
                    # Refuse implicit removal of a parent file by a nested add.
                    for parent in PurePosixPath(name).parents:
                        if str(parent) != '.':
                            ancestor = command(path, 'ls-tree', record['parent'], '--', ':(literal)' + str(parent))
                            if ancestor and not ancestor.startswith(b'040000 tree '):
                                raise ValueError('Submitted path has a non-directory ancestor in its parent commit.')
                    if item['mode'] == 'deleted':
                        if not existing:
                            raise ValueError('Submitted deletion is absent from the parent commit.')
                        blob = '0' * len(record['parent'])
                        mode = '0'
                    else:
                        blob = command(path, 'hash-object', '-w', '--stdin',
                                       data=base64.b64decode(item['content'])).decode().strip()
                        mode = item['mode']
                    command(path, 'update-index', '-z', '--index-info', index=prepared,
                            data=(mode + ' ' + blob + '\t' + name + '\0').encode())
                    changes.append({k: v for k, v in dict(item, blob=blob if mode != '0' else None).items() if k != 'content'})
                tree = command(path, 'write-tree', index=prepared).decode().strip()
                if tree == command(path, 'rev-parse', record['parent'] + '^{tree}').decode().strip():
                    raise ValueError('Submitted revision has no changes; use a no-changes receipt when appropriate.')
                identity = dict(GIT_AUTHOR_NAME=record['author'], GIT_AUTHOR_EMAIL='agent@herdr.local',
                    GIT_COMMITTER_NAME='Herdr', GIT_COMMITTER_EMAIL='commits@herdr.local',
                    GIT_AUTHOR_DATE=record['created_at'], GIT_COMMITTER_DATE=record['created_at'])
                sha = command(path, 'commit-tree', tree, '-p', record['parent'],
                              data=(payload['message'] + '\n').encode(), identity=identity).decode().strip()
                record.update(sha=sha, tree=tree, files=changes, message=payload['message'], state='constructed')
                # Pin before discarding bytes from the intent. A crash before
                # this checkpoint reconstructs the identical commit and ref.
                retained = 'refs/herdr/revisions/' + record['id']
                try:
                    command(path, 'update-ref', retained, sha, '0' * len(sha))
                except ValueError:
                    command(path, 'update-ref', retained, sha, sha)
                record.pop('payload')
                self.store(record, fingerprint)
            command(path, 'read-tree', record['tree'], index=prepared)
            current = command(path, 'rev-parse', 'HEAD').decode().strip()
            if command(path, 'symbolic-ref', '--short', 'HEAD').decode().strip() != record['branch']:
                raise ValueError('Task branch changed during submission; inspect before retrying.')
            if current not in (record['parent'], record['sha']):
                raise ValueError('Task branch advanced during submission; retain this revision and inspect before retrying.')
            before = index_digest(index)
            # read-tree index serialization is stable for this empty-stat index.
            prepared_bytes = prepared.read_bytes()
            after = hashlib.sha256(prepared_bytes).hexdigest()
            if before not in (record['index_before'], after):
                raise ValueError('Task index changed during submission; preserve it and inspect before retrying.')
            if current == record['parent']:
                command(path, 'update-ref', 'refs/heads/' + record['branch'], record['sha'], record['parent'])
            return prepared_bytes
