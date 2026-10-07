"""Durable task candidates, explicit no-force publication and draft pull requests.

This is the outbound contribution lifecycle, not upstream worktree integration.
Network operations never hold SQLite transactions; pending requests can be
reconciled after restart by repeating the same administrator request ID.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import threading
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import threading
from urllib.parse import quote
import uuid

from github_api import GitHubError
from project_files import project_directory
import project_git
from repository_lock import repository_lock

SHA = re.compile(r'[a-f0-9]{40}|[a-f0-9]{64}')
BRANCH = re.compile(r'herdr/task-[a-f0-9]{12}')
REQUEST = re.compile(r'[A-Za-z0-9_-]{1,64}')


def task_tool_guidance():
    """Share tool acquisition policy without issuing a merge request to a task.

    The detailed policy lives once in a reference file. Pass its location, not
    its contents, so development tasks load it only when a tool is missing.
    """
    resource = Path(__file__).parent / 'skills/herdr-worktree-integration/references/tools.md'
    if resource.is_symlink() or not resource.is_file():
        raise ValueError('Worker skill tool reference is missing or unsafe; reinstall the dashboard release.')
    return ('Before obtaining missing task tools, read ' + resource.resolve().as_posix() + '. '
            'Pin and verify tools within existing permissions in task-specific environments; '
            'agents share the herdr account. Report actual checks and installation failures. '
            'If the reference cannot be read, report its exact path; do not broaden permissions.')

# A background status poll must never hold a task long, and must never wait
# long enough to delay validation feedback delivery.
POLL_WAIT_SECONDS = 5


def stamp():
    return datetime.now(timezone.utc).isoformat()


def remote_info(repository):
    value = project_git.git(repository, 'remote', 'get-url', 'origin').strip()
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?', value)
    if not match or any(p in ('.', '..') for p in match[1].split('/')):
        raise ValueError('Publishing requires a credential-free github.com origin.')
    return match[1]


TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})')


def push_detail(folder):
    """A short, redacted tail of Git's own push output for the operator."""
    try:
        with open(Path(folder) / 'push.log', 'rb') as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 2048))
            tail = log.read().decode('utf-8', 'replace')
    except OSError:
        return ''
    tail = ' '.join(TOKEN.sub('redacted', tail).split())
    return (' Git reported: ' + tail[-300:]) if tail else ''


def push(repository, branch, sha, github):
    if not BRANCH.fullmatch(branch) or not SHA.fullmatch(sha):
        raise ValueError('Only a task branch and full reviewed commit may be published.')
    remote = remote_info(repository)
    github.allow(remote)
    if sys.platform == 'win32':
        raise ValueError('Authenticated publishing runs on the Linux LXC.')
    # Only resolve the object store under the repository lock. Holding it across
    # the network push would fail every other gateway Git operation for a minute.
    with repository_lock(repository):
        objects = Path(project_git.git(repository, 'rev-parse', '--git-path', 'objects').strip())
        if not objects.is_absolute():
            objects = Path(repository) / objects
        if '\n' in str(objects) or '\r' in str(objects):
            raise ValueError('Invalid repository object path.')
    with tempfile.TemporaryDirectory(prefix='herdr-publish-') as folder:
        helper = Path(folder) / 'askpass'
        script = Path(__file__).with_name('github_askpass.py')
        helper.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(script)) + ' "$@"\n')
        helper.chmod(0o700)
        environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        environment.update(GIT_ASKPASS=str(helper), GIT_TERMINAL_PROMPT='0',
                           HERDR_GITHUB_CONFIG=str(github.path.resolve()), GIT_CONFIG_NOSYSTEM='1',
                           GIT_CONFIG_GLOBAL=os.devnull)
        # A repository can define url.insteadOf or helpers. Push from a fresh
        # bare configuration using only its immutable objects, not its config.
        bare = Path(folder) / 'publisher.git'
        initialized = subprocess.run(['git', 'init', '--bare', str(bare)], env=environment,
                                     capture_output=True, timeout=15)
        if initialized.returncode:
            raise ValueError('Could not prepare the isolated Git publisher.')
        (bare / 'objects/info/alternates').write_bytes((objects.resolve().as_posix() + '\n').encode())
        # Exact SHA refspec avoids racing an agent's next commit. Disable hooks
        # and credential helpers; token-bearing URLs are never persisted.
        arguments = ['git', '-c', 'credential.helper=', '-c', 'core.hooksPath=' + folder,
                     '-c', 'http.followRedirects=false', '--git-dir=' + str(bare), 'push', '--no-force',
                     'https://github.com/' + remote + '.git', sha + ':refs/heads/' + branch]
        try:
            # Capture to a file so a large transfer cannot grow gateway memory,
            # and report a bounded, redacted tail on failure.
            with open(Path(folder) / 'push.log', 'wb') as log:
                result = subprocess.run(arguments, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Publish outcome is uncertain; repeat this request to reconcile the remote.') from None
        if result.returncode:
            raise ValueError('Branch push was rejected or authentication failed. '
                             'Refresh GitHub status; never force-push.' + push_detail(folder))


class Busy(ValueError):
    """Another operation on the same task or request is still running."""


class Contributions:
    def __init__(self, path, projects, store, github):
        self.path, self.projects = Path(path), Path(projects).resolve()
        self.store, self.github = store, github
        self.lock = threading.RLock()
        self.operation_locks = {}
        self.held = {}
        self.cursor = 0
        self.stopped = threading.Event()
        self.thread = None
        self.validation = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS build_events (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS audit (task_id TEXT NOT NULL, data TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @contextmanager
    def operation(self, key, timeout=30):
        """Bound a critical section so one slow publication cannot stall others."""
        with self.lock:
            if len(self.operation_locks) > 512:
                # Only drop keys nobody is using; a waiter keeps its entry.
                self.operation_locks = {k: v for k, v in self.operation_locks.items() if k in self.held}
            entry = self.operation_locks.setdefault(key, threading.RLock())
            self.held[key] = self.held.get(key, 0) + 1
        if not entry.acquire(timeout=timeout):
            with self.lock:
                self.held[key] -= 1
            raise Busy('Another operation on this task is still running. Try again shortly.')
        try:
            yield
        finally:
            with self.lock:
                self.held[key] -= 1
                if not self.held[key]:
                    del self.held[key]
            entry.release()


    def start(self):
        self.thread = threading.Thread(target=self.poll, daemon=True, name='contribution-status')
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)

    def poll(self):
        while not self.stopped.wait(60):
            try:
                self.poll_once()
            except (ValueError, OSError, sqlite3.Error):
                # A transient storage or API failure must never end the poller;
                # the next cycle reconciles again. Never redispatch a push or a
                # pull request creation from here.
                continue

    def poll_once(self):
        """Refresh the pull requests of open tasks once. Safe to call directly."""
        try:
            # Closed-but-unmerged pull requests are revisited so a reopen on
            # GitHub is noticed. A merged task needs no further polling.
            tasks = [t for t in self.snapshot()['tasks'] if t.get('pull') and t.get('state') != 'merged']
            if not tasks:
                return False
            cursor = self.cursor % len(tasks)
            self.cursor = cursor + min(20, len(tasks))
            for task in (tasks[cursor:] + tasks[:cursor])[:20]:
                if self.stopped.is_set():
                    return True
                try:
                    with self.operation('task:' + task['id'], timeout=POLL_WAIT_SECONDS):
                        self.perform('refresh', {}, task['id'], 'github_status_poll')
                except Busy:
                    continue  # An operator action owns this task right now.
                except (ValueError, OSError, sqlite3.Error) as error:
                    self.record_error(task['id'], error, 'refresh', 'github_status_poll')
            return True
        except (ValueError, OSError, sqlite3.Error):
            return False

    def record_error(self, task_id, error, action, actor):
        """Surface a background failure once, without masking storage faults."""
        try:
            task = self.get(task_id)
            if task.get('error') != str(error)[:500]:
                task['error'] = str(error)[:500]
                self.save(task, action + '_error', actor)
        except (ValueError, OSError, sqlite3.Error):
            pass


    def get(self, task_id):
        with closing(self.connect()) as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('Task not found.')
        return json.loads(row[0])

    def build_event(self, event_id):
        with closing(self.connect()) as db:
            row = db.execute('SELECT data FROM build_events WHERE id=?', (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_build(self, event_id, summary):
        event = self.build_event(event_id)
        if not event:
            return False
        with self.operation('task:' + event['task_id']):
            task = self.get(event['task_id'])
            if summary.get('target') != event['target']:
                # Consume the delivery and record the mismatch. Raising here is
                # swallowed by feedback delivery and retried forever unseen.
                task['error'] = 'Build evidence does not match the task commit.'
                self.save(task, 'build_evidence_mismatch', summary.get('actor', 'build_runner'))
                return True
            task.setdefault('builds', {})[event['target']] = summary
            self.save(task, 'build_result', summary.get('actor', 'build_runner'))
        return True


    def save(self, task, action, actor):
        task['updated_at'] = stamp()
        entry = dict(action=action, actor=actor, at=task['updated_at'],
                     state=task['state'], head_sha=task.get('head_sha'))
        task.setdefault('audit', []).append(entry)
        task['audit'] = task['audit'][-100:]  # UI history; SQLite retains the latest 500 entries.
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO audit VALUES (?,?)', (task['id'], json.dumps(entry)))
            # Background pull-request polling must not grow the durable audit
            # table without bound; the task record keeps the recent trail.
            db.execute('DELETE FROM audit WHERE task_id=? AND rowid NOT IN '
                       '(SELECT rowid FROM audit WHERE task_id=? ORDER BY rowid DESC LIMIT 500)',
                       (task['id'], task['id']))
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (task['id'], json.dumps(task)))
        return task


    def patch(self, task_id, limit=20 * 1024 * 1024):
        """Export immutable candidate trees, never a truncated review diff."""
        import subprocess
        task = self.get(task_id)
        base, head = task.get('base_sha'), task.get('head_sha')
        if not base or not head or not SHA.fullmatch(base) or not SHA.fullmatch(head):
            raise ValueError('Capture a candidate before downloading its patch.')
        path = self.path_for(task)
        # Verify the exact commits under the repository lock, then stream the
        # diff without it: holding the lock for up to 30 seconds would fail
        # concurrent launches, base updates and candidate capture.
        with repository_lock(path):
            for sha in (base, head):
                try:
                    project_git.git(path, 'cat-file', '-e', sha + '^{commit}')
                except ValueError:
                    raise ValueError('Candidate commits are no longer available; fetch the repository '
                                     'and capture the candidate again.') from None
        environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        process = subprocess.Popen(['git', '-C', str(path), 'diff', '--binary', '--full-index',
                                    '--no-ext-diff', '--no-textconv', base, head, '--'],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment)
        timer = threading.Timer(30, process.kill)
        timer.start()
        try:
            content = bytearray()
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > limit:
                    raise ValueError('Candidate patch exceeds the download size limit; it was not truncated.')
            if process.wait() != 0:
                raise ValueError('Candidate patch generation failed or timed out.')
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
        checksum = hashlib.sha256(content).hexdigest()
        metadata = f'# Herdr task {task_id}\n# Base {base}\n# Candidate {head}\n# Diff SHA256 {checksum}\n'
        return metadata.encode() + bytes(content), f'herdr-{task_id}-{head[:12]}.patch'

    def snapshot(self, role='admin'):
        with closing(self.connect()) as db:
            tasks = [json.loads(row[0]) for row in db.execute('SELECT data FROM tasks ORDER BY rowid DESC LIMIT 100')]
        for task in tasks:
            task.pop('diff', None)  # Fetch the candidate only when reviewing it.
            task['audit'] = task.get('audit', [])[-20:]
        try:
            github = self.github.snapshot()
        except ValueError as error:
            github = dict(configured=False, error=str(error))
        if role != 'admin':
            # Operators can inspect task/repository evidence, as elsewhere in
            # the dashboard. Publishing account configuration is admin-only.
            github = dict(configured=bool(github.get('configured')))
        return dict(tasks=tasks, github=github)

    def detail(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id):
            raise ValueError('Select a valid task.')
        return self.get(task_id)

    def path_for(self, task):
        return project_directory(self.projects, str(self.projects / task['repository']))

    def tree_for(self, task):
        path = project_directory(self.projects, task['worktree'])
        if project_git.git(path, 'symbolic-ref', '--short', 'HEAD').strip() != task['branch']:
            raise ValueError('Task checkout changed branch; inspect it first.')
        repository = self.path_for(task)
        if project_git.git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip() != project_git.git(
                repository, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip():
            raise ValueError('Task checkout belongs to a different repository.')
        return path

    def action(self, action, body, actor, role):
        if role != 'admin':
            raise ValueError('Only an administrator can manage contribution tasks.')
        if not isinstance(body, dict) or not REQUEST.fullmatch(str(body.get('request_id', ''))):
            raise ValueError('A stable request ID is required.')
        if action != 'create' and (not isinstance(body.get('task_id'), str)
                                  or not re.fullmatch(r'[a-f0-9]{32}', body['task_id'])):
            raise ValueError('Select a valid task.')
        fingerprint = hashlib.sha256(json.dumps([action, body, actor], sort_keys=True).encode()).hexdigest()
        with self.operation('request:' + body['request_id']), self.operation('task:' + str(body.get('task_id', body['request_id']))):
            with closing(self.connect()) as db:
                saved = db.execute('SELECT fingerprint,data FROM requests WHERE id=?', (body['request_id'],)).fetchone()
            if saved:
                if saved[0] != fingerprint:
                    raise ValueError('Request ID already used for different content.')
                record = json.loads(saved[1])
                if record['state'] == 'complete':
                    return record['task']
                # The task ID survives a lost response or process restart.
            else:
                record = dict(state='pending', task_id=uuid.uuid4().hex if action == 'create' else body.get('task_id'))
                with closing(self.connect()) as db, db:
                    db.execute('INSERT INTO requests VALUES (?,?,?)', (body['request_id'], fingerprint, json.dumps(record)))
            try:
                task = self.perform(action, body, record['task_id'], actor)
            except (ValueError, OSError) as error:
                # Keep the request pending: explicit retry rechecks reality,
                # especially a push/PR whose HTTP response was lost.
                if action != 'create':
                    task = self.get(record['task_id'])
                    task['error'] = str(error)[:500]
                    self.save(task, action + '_error', actor)
                raise
            record = dict(state='complete', task=self.lightweight(task))
            with closing(self.connect()) as db, db:
                db.execute('UPDATE requests SET data=? WHERE id=?', (json.dumps(record), body['request_id']))
                # Completed requests only exist for replay. Keeping every task
                # blob would store each diff again for every action.
                db.execute('DELETE FROM requests WHERE id IN (SELECT id FROM requests '
                           "WHERE json_extract(data, '$.state')='complete' ORDER BY rowid LIMIT -1 "
                           'OFFSET 500)')
            return task

    @staticmethod
    def lightweight(task):
        """Replay view of a task without bulk review or build evidence."""
        return {k: v for k, v in task.items() if k not in ('diff', 'diff_stat', 'builds', 'checks',
                                                           'statuses', 'reviews', 'audit')}

    def comparable(self, task):
        """Task content without refresh timestamps, used to detect real changes."""
        return json.dumps({k: v for k, v in task.items() if k not in ('last_sync', 'updated_at')},
                          sort_keys=True, default=str)

    def perform(self, action, body, task_id, actor):
        if action == 'create':
            try:
                return self.get(task_id)
            except ValueError:
                pass
            for key, limit in [('title', 120), ('description', 8000), ('repository', 2000), ('base_ref', 250)]:
                value = body.get(key)
                if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
                    raise ValueError('Invalid task ' + key + '.')
            repository = project_directory(self.projects, str(self.projects / body['repository']))
            if repository != Path(project_git.git(repository, 'rev-parse', '--show-toplevel').strip()).resolve():
                raise ValueError('Select the repository root.')
            base = body['base_ref']
            if not base.startswith('refs/remotes/origin/') or base.endswith('/HEAD'):
                raise ValueError('Select an explicit origin base branch, not origin/HEAD.')
            project_git.git(repository, 'check-ref-format', base)
            remote = remote_info(repository)
            sha = project_git.git(repository, 'rev-parse', '--verify', base + '^{commit}').strip()
            profiles = self.store.snapshot(live_status=False)['profiles']
            profile = next((p for p in profiles if p['id'] == body.get('profile_id') and not p.get('archived')), None)
            if not profile or Path(profile['project']).resolve() != repository or not profile.get('use_worktree', True):
                raise ValueError('Select a worktree agent assigned to this repository.')
            if profile.get('group_id'):
                raise ValueError('Assign an individual worker, not a discussion group.')
            project_git.configured_base(repository, base)
            task = dict(id=task_id, title=body['title'].strip(), description=body['description'],
                        repository=repository.relative_to(self.projects).as_posix(), github_repository=remote,
                        base_ref=base, base_sha=sha, branch='herdr/task-' + task_id[:12],
                        profile_id=profile['id'], organization_id=profile['organization_id'], state='draft',
                        created_at=stamp(), checks=[], audit=[])
            return self.save(task, 'create', actor)
        task = self.get(task_id)
        had_error = task.pop('error', None) is not None
        # An unchanged background refresh must not rewrite the task or its audit.
        unchanged = self.comparable(task) if action == 'refresh' else None
        if task['state'] in ('merged', 'closed') and action not in ('refresh', 'build'):
            raise ValueError('This task pull request is closed. Create a new task for further changes.')
        repository = self.path_for(task)
        if remote_info(repository) != task['github_repository']:
            raise ValueError('Repository remote changed; inspect the task before continuing.')
        if action == 'launch':
            if task.get('run_id'):
                return task
            run = self.store.action('launch', dict(request_id='task-' + task['id'],
                organization_id=task['organization_id'], profile_id=task['profile_id'],
                task_id=task['id'], worktree_branch=task['branch'], start_sha=task['base_sha'],
                task_prompt='Task: ' + task['title'] + '\n' + task['description'] +
                    '\nWork only in your assigned task branch. Implement, run required checks, and commit your changes. '
                    'Commit using git -c user.name=Herdr-Agent -c user.email=agent@herdr.local commit. Never infer the operator identity or change global Git config. '
                    'Never push, open a pull request, update the shared checkout or deploy. '
                    'Report commands and actual results; missing checks are not passes. Publishing is a dashboard administrator action.\n\n'
                    'Task-local tool acquisition policy:\n' + task_tool_guidance()))
            task.update(run_id=run['id'], worktree=str((self.projects / '.herdr-worktrees' / run['id']).resolve()),
                        state='implementing')
        elif action == 'candidate':
            path = self.tree_for(task)
            run = next((j for j in self.store.snapshot(live_status=False)['jobs'] if j['id'] == task.get('run_id')), None)
            if not run or run['state'] != 'persona_sent':
                raise ValueError('Task agent launch is not ready; inspect its job.')
            # Capturing a candidate is explicit operator review, not a claim the
            # worker passed validation. The immutable SHA remains the evidence.
            with repository_lock(path):
                if project_git.merge_state(path) or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip():
                    raise ValueError('Commit task changes and resolve operations before reviewing.')
                head = project_git.git(path, 'rev-parse', 'HEAD').strip()
                if head == task['base_sha']:
                    raise ValueError('The task has no committed changes to review.')
                project_git.git(path, 'merge-base', '--is-ancestor', task['base_sha'], head)
                diff = project_git.git(path, 'diff', '--no-ext-diff', '--no-textconv', task['base_sha'], head, '--')
                task.update(head_sha=head, diff=diff[:65536], diff_truncated=len(diff) > 65536,
                            diff_stat=project_git.git(path, 'diff', '--stat', task['base_sha'], head, '--')[:12000],
                            state='review_ready', candidate_changed=bool(task.get('pull') and task['pull']['head_sha'] != head))
        elif action == 'publish':
            self.github.allow(task['github_repository'])
            head = body.get('head_sha')
            if not SHA.fullmatch(str(head)) or head != task.get('head_sha'):
                raise ValueError('Refresh and review the exact candidate commit before publishing.')
            path = self.tree_for(task)
            with repository_lock(path):
                if (project_git.git(path, 'rev-parse', 'HEAD').strip() != head or project_git.merge_state(path)
                        or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip()):
                    raise ValueError('Candidate changed or checkout is not clean; review it again.')
            remote_head = self.remote_head(task)
            if remote_head != head:
                published = task.get('publish', {}).get('head_sha')
                if published and remote_head not in (None, published):
                    # The branch moved on the remote. A force-push would destroy
                    # that history, so this task can only be closed.
                    raise ValueError('The published branch moved on the remote. This task cannot be '
                                     'republished; close it and create a new task.')
                push(path, task['branch'], head, self.github)
                remote_head = self.remote_head(task)
            if remote_head != head:
                raise ValueError('Remote branch does not match the reviewed commit; refresh before retrying.')
            task.update(publish=dict(state='complete', head_sha=head, at=stamp()), candidate_changed=False)
            # A task that already has a pull request stays in its pull state.
            task['state'] = 'pr_open' if task.get('pull') else 'published'
        elif action == 'pull':
            published = task.get('publish', {}).get('head_sha')
            if not published or published != task.get('head_sha') or self.remote_head(task) != published:
                raise ValueError('Publish the current reviewed candidate before opening a pull request.')
            self.github.allow(task['github_repository'])
            found = self.github.find_pull(task['github_repository'], task['branch'])
            if not isinstance(found, list):
                raise ValueError('GitHub returned an invalid pull request list.')
            if found:
                pull = found[0]
            else:
                evidence = task.get('builds', {}).get(published)
                checks = ('\n\n| Check | Status |\n| --- | --- |\n' + '\n'.join(
                    '| ' + str(c.get('id', 'unknown')).replace('|', '/')[:120] + ' | ' +
                    str(c.get('status', 'not_run')).replace('|', '/')[:40] + ' |' for c in evidence.get('checks', []))) if evidence else ''
                payload = dict(title=task['title'], head=task['branch'], base=task['base_ref'].removeprefix('refs/remotes/origin/'),
                    draft=True, body=task['description'] + '\n\nTask: ' + task['id'] + '\nSource: `' + published +
                    '`\n\nValidation: not verified by this publication operation. Review checks and evidence before merging.' + checks)
                try:
                    pull = self.github.request('/repos/' + task['github_repository'] + '/pulls', 'POST', payload)
                except GitHubError as error:
                    if error.status != 422:
                        raise
                    found = self.github.find_pull(task['github_repository'], task['branch'])
                    if not isinstance(found, list) or not found:
                        raise
                    pull = found[0]
            self.record_pull(task, pull)
        elif action == 'build':
            target = task.get('merge_sha') if task['state'] == 'merged' else task.get('head_sha')
            if not self.validation or not SHA.fullmatch(str(target)):
                raise ValueError('Capture a candidate or refresh a merged PR before building.')
            if body.get('target') != target:
                raise ValueError('Build selection changed; review the exact resulting commit.')
            try:
                project_git.git(repository, 'cat-file', '-e', target + '^{commit}')
            except ValueError:
                raise ValueError('Fetch this repository in Project explorer before building the merged result.') from None
            event_id = hashlib.sha256(('task:' + task['id'] + ':' + target).encode()).hexdigest()
            event = dict(id=event_id, task_id=task['id'], repository=task['repository'], path=task['repository'], target=target)
            with closing(self.connect()) as db, db:
                db.execute('INSERT OR IGNORE INTO build_events VALUES (?,?)', (event_id, json.dumps(event)))
            previous = task.get('builds', {}).get(target)
            run = self.validation.submit(dict(id=event_id, retry=bool(previous and previous.get('state') in ('failed', 'interrupted'))), actor=actor)
            task.setdefault('builds', {})[target] = dict(run_id=run['id'], state=run['state'], target=target, checks=run.get('checks', []))
        elif action == 'refresh':
            previous = task.get('pull'), task.get('state'), task.get('merge_sha')
            if not task.get('pull'):
                task['remote_head'] = self.remote_head(task)
            else:
                self.github.allow(task['github_repository'])
                prefix = '/repos/' + task['github_repository']
                pull = self.github.request(prefix + '/pulls/' + str(task['pull']['number']))
                self.record_pull(task, pull)
                # Merge identity must survive a missing Checks/Reviews scope.
                # Ancillary evidence failing cannot hide a completed PR merge.
                if (task.get('pull'), task.get('state'), task.get('merge_sha')) != previous:
                    self.save(task, 'pull_status', actor)
                check_response = self.github.request(prefix + '/commits/' + task['pull']['head_sha'] + '/check-runs?per_page=100')
                status_response = self.github.request(prefix + '/commits/' + task['pull']['head_sha'] + '/status?per_page=100')
                if not isinstance(check_response, dict) or not isinstance(status_response, dict):
                    raise ValueError('GitHub returned an invalid check evidence response.')
                checks = check_response.get('check_runs', [])
                statuses = status_response.get('statuses', [])
                reviews = self.github.request(prefix + '/pulls/' + str(task['pull']['number']) + '/reviews?per_page=100')
                if not isinstance(checks, list) or not isinstance(statuses, list) or not isinstance(reviews, list):
                    raise ValueError('GitHub returned an invalid check evidence list.')
                if (any(not isinstance(c, dict) for c in checks + statuses + reviews)
                        or any(not isinstance(r.get('user'), dict) for r in reviews)):
                    raise ValueError('GitHub returned an invalid check evidence record.')
                task['checks'] = [{k: c.get(k) for k in ('id', 'name', 'status', 'conclusion')} for c in checks]
                task['statuses'] = [{k: c.get(k) for k in ('context', 'state')} for c in statuses]
                task['reviews'] = [dict(id=r.get('id'), state=r.get('state'), commit_id=r.get('commit_id'),
                                         user=dict(login=r.get('user', {}).get('login'))) for r in reviews]
                task['last_sync'] = stamp()
        else:
            raise ValueError('Unsupported contribution action.')
        if action == 'refresh' and not had_error and self.comparable(task) == unchanged:
            return task
        return self.save(task, action, actor)

    def remote_head(self, task):
        self.github.allow(task['github_repository'])
        try:
            data = self.github.request('/repos/' + task['github_repository'] + '/git/ref/heads/' + quote(task['branch'], safe='/'))
        except GitHubError as error:
            if error.status == 404:
                return None
            raise
        sha = data.get('object', {}).get('sha')
        if not SHA.fullmatch(str(sha)):
            raise ValueError('GitHub returned an invalid branch commit.')
        return sha

    def record_pull(self, task, pull):
        if (not isinstance(pull, dict) or pull.get('state') not in ('open', 'closed')
                or not isinstance(pull.get('head'), dict) or not isinstance(pull.get('base'), dict)
                or not isinstance(pull['head'].get('repo'), dict)):
            raise ValueError('GitHub returned an invalid pull request record.')
        if (pull.get('head', {}).get('ref') != task['branch']
                or pull.get('base', {}).get('ref') != task['base_ref'].removeprefix('refs/remotes/origin/')
                or pull.get('head', {}).get('repo', {}).get('full_name', '').lower() != task['github_repository'].lower()):
            raise ValueError('Pull request does not match this task repository and branches.')
        head = pull['head']['sha']
        if not SHA.fullmatch(str(head)) or not isinstance(pull.get('number'), int):
            raise ValueError('GitHub returned an invalid pull request identity.')
        if task.get('pull', {}).get('head_sha') != head:
            task.update(checks=[], statuses=[], reviews=[], last_sync=None)
        task['pull'] = dict(number=pull['number'], url='https://github.com/' + task['github_repository'] + '/pull/' + str(pull['number']),
                            state=pull['state'], draft=pull.get('draft', False), head_sha=head,
                            mergeable=pull.get('mergeable'), mergeable_state=pull.get('mergeable_state'))
        task['candidate_changed'] = head != task.get('head_sha')
        # The pull request list reports a merge as merged_at and omits the
        # resulting commit; only the single record carries it. Never report an
        # open pull request that GitHub has already merged.
        if pull.get('merged') is True or pull.get('merged_at'):
            merged = pull.get('merge_commit_sha')
            if not SHA.fullmatch(str(merged)):
                detail = self.github.request('/repos/' + task['github_repository'] + '/pulls/' + str(pull['number']))
                merged = detail.get('merge_commit_sha') if isinstance(detail, dict) else None
            task['state'] = 'merged'
            task.pop('merge_sha', None)
            if SHA.fullmatch(str(merged)):
                task['merge_sha'] = merged
            else:
                task['error'] = ('GitHub reports this pull request merged without a usable '
                                 'resulting commit. Read the merge result on GitHub.')
        else:
            task['state'] = 'pr_open' if pull['state'] == 'open' else 'closed'
            task.pop('merge_sha', None)
