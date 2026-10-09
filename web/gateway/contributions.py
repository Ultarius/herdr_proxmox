"""Durable task candidates, explicit no-force publication and draft pull requests.

This is the outbound contribution lifecycle, not upstream worktree integration.
Network operations never hold SQLite transactions; pending requests can be
reconciled after restart by repeating the same administrator request ID.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
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
# An execution occupies its agent only while it is genuinely active. `finished`
# (evidence-verified) and `released` (manual recovery) hand the slot back.
TERMINAL_EXECUTIONS = ('finished', 'released')
HANDOFF_TASK_STATES = ('review_ready', 'published', 'pr_open', 'merged', 'closed', 'completed')
# Independent of the build-service limit: sessions are launched concurrently.
MAX_ACTIVE_EXECUTIONS = 4
# Organization follow-up automation is opt-in and fail-closed. Proposals that
# qualify are created and queued; everything else stays a draft for the operator.
AUTOMATION_DEFAULTS = dict(auto_queue_proposals=False, max_per_meeting=1, max_open_per_agent=2,
                           max_follow_up_depth=1, daily_cap=5, paused=False)
AUTOMATION_LIMITS = (('max_per_meeting', 0, 10), ('max_open_per_agent', 0, 50),
                     ('max_follow_up_depth', 0, 10), ('daily_cap', 0, 100))


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


def task_base(repository, requested):
    """Use explicit configuration or origin's recorded default, without fetching."""
    base = requested.strip()
    if not base:
        base = project_git.configured_base(repository)
        if not base:
            try:
                base = project_git.git(repository, 'symbolic-ref', 'refs/remotes/origin/HEAD').strip()
            except ValueError:
                available = []
                for candidate in ('refs/remotes/origin/main', 'refs/remotes/origin/master'):
                    try:
                        project_git.git(repository, 'rev-parse', '--verify', candidate + '^{commit}')
                        available.append(candidate)
                    except ValueError:
                        pass
                if len(available) != 1:
                    raise ValueError('Choose an explicit origin base branch. Fetch remote updates if remote branches are missing.')
                base = available[0]
    if not base.startswith('refs/remotes/origin/') or base.endswith('/HEAD'):
        raise ValueError('Select an explicit origin base branch, not origin/HEAD.')
    project_git.git(repository, 'check-ref-format', base)
    try:
        sha = project_git.git(repository, 'rev-parse', '--verify', base + '^{commit}').strip()
    except ValueError as error:
        raise ValueError('Base branch ' + base + ' is unavailable. Fetch remote updates or select an existing origin branch.') from error
    return base, sha


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
            db.execute('CREATE TABLE IF NOT EXISTS assignments (task_id TEXT PRIMARY KEY, position INTEGER NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS policies (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        from discovery import Discovery
        self.discovery = Discovery(self)

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
        cycles = 0
        while not self.stopped.wait(10):
            try:
                self.advance_automatic()
                self.dispatch_assignments()
                self.advance_proposals()
                self.discovery.advance()
                cycles += 1
                if cycles % 6 == 0:
                    self.reconcile_completed()
                    self.advance_reviews()
                    self.poll_once()
            except (ValueError, OSError, sqlite3.Error):
                # A transient storage or API failure must never end the poller;
                # the next cycle reconciles again. Never redispatch a push or a
                # pull request creation from here.
                continue

    def automatic_tasks(self):
        """Ids and states only, so a ten-second reconcile never parses stored diffs."""
        with closing(self.connect()) as db:
            return [dict(id=row[0], state=row[1]) for row in db.execute(
                "SELECT json_extract(data, '$.id'), json_extract(data, '$.state') FROM tasks "
                "WHERE json_extract(data, '$.auto_validate')=1 LIMIT 100")]

    def advance_automatic(self):
        tasks = [t for t in self.automatic_tasks() if t.get('state') in ('implementing', 'review_ready')]
        if not tasks:
            return
        if not self.validation or self.validation.snapshot().get('executor') != 'service':
            return
        runs = self.store.snapshot(live_status=False)['jobs']
        for listed in tasks:
            try:
                with self.operation('task:' + listed['id'], timeout=0):
                    task = self.get(listed['id'])
                    path = self.tree_for(task)
                    run = next((j for j in runs if j['id'] == task.get('run_id')), None)
                    if not run or run['state'] not in ('persona_sent', 'finished'):
                        raise ValueError('Awaiting the original task session.')
                    if run['state'] == 'persona_sent':
                        from collaboration import current_run
                        current_run(self.store, run)
                        if any(j['state'] in ('queued', 'running') and task['profile_id'] in
                               j.get('participants', [j.get('profile_id')]) for j in runs):
                            raise ValueError('Awaiting queued or running agent work.')
                    with repository_lock(path):
                        if project_git.git(path, 'symbolic-ref', '--short', 'HEAD').strip() != task['branch']:
                            raise ValueError('Task branch changed; inspect before continuing.')
                        head = project_git.git(path, 'rev-parse', 'HEAD').strip()
                        no_changes = False
                        receipt = None
                        if run['state'] == 'finished' and (task['state'] == 'implementing' or head != task.get('head_sha')
                                or task.get('completion_receipt', {}).get('commit') != head):
                            raise ValueError('Finished execution no longer matches its verified candidate.')
                        if head != task.get('head_sha') or task['state'] == 'implementing':
                            receipt = self.read_receipt(task, run, head, path)
                            if receipt['outcome'] == 'no_changes':
                                if project_git.merge_state(path) or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip():
                                    raise ValueError('A no-changes receipt requires a clean checkout without unfinished Git operations.')
                                task.update(state='completed', auto_validate=False, auto_review=False, completion=dict(
                                    outcome='no_changes', candidate=head, upstream=head, validation='not_applicable',
                                    reason=str(receipt.get('reason') or 'The assigned agent verified that no code changes are required.')[:2000],
                                    actor='automatic_task_policy', at=stamp()),
                                    completion_receipt=dict(commit=head, tests=receipt['tests'], verified_at=stamp()))
                                self.save(task, 'complete', 'automatic_task_policy')
                                no_changes = True
                            else:
                                self.perform('candidate', {}, task['id'], 'automatic_task_policy')
                                task = self.get(task['id'])
                                if task.get('head_sha') != head:
                                    raise ValueError('Task advanced during capture; awaiting its new completion receipt.')
                                task['completion_receipt'] = dict(commit=head, tests=receipt['tests'], verified_at=stamp())
                                self.save(task, 'completion_verified', 'automatic_task_policy')
                    if no_changes:
                        try:
                            self.finish_execution(task, run, 'no-changes-' + task['id'], 'automatic_task_policy')
                        except ValueError as error:
                            # The task result stands; the session can be released manually.
                            self.record_error(task['id'], error, 'finish_execution', 'automatic_task_policy')
                        continue
                    if receipt is not None:
                        self.materialize_follow_up(task, receipt, 'automatic_task_policy')
                    # Do not automatically retry failures. One durable identity per task+SHA.
                    if head not in task.get('builds', {}):
                        self.perform('build', {'target': head}, task['id'], 'automatic_task_policy')
                    task = self.get(task['id'])
                    if task.pop('automation_error', None) is not None:
                        self.save(task, 'automation_resumed', 'automatic_task_policy')
            except Busy:
                continue
            except (ValueError, OSError, sqlite3.Error) as error:
                try:
                    with self.operation('task:' + listed['id'], timeout=0):
                        task = self.get(listed['id'])
                        message = str(error)[:500]
                        if task.get('automation_error') != message:
                            task['automation_error'] = message
                            self.save(task, 'automation_waiting', 'automatic_task_policy')
                except (ValueError, OSError, sqlite3.Error):
                    continue

    def reconcile_completed(self):
        with closing(self.connect()) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM tasks WHERE json_extract(data, '$.state')='review_ready' LIMIT 100")]
        for task_id in ids:
            try:
                with self.operation('task:' + task_id, timeout=0):
                    task = self.get(task_id)
                    if task.get('pull') or task.get('publish'):
                        continue
                    head = task.get('head_sha')
                    evidence = task.get('builds', {}).get(head, {})
                    if evidence.get('target') != head or evidence.get('state') != 'complete' or evidence.get('required_checks_verified') is not True:
                        continue
                    jobs = self.store.snapshot(live_status=False).get('jobs', [])
                    if any(j.get('state') in ('queued', 'running') and task['profile_id'] in j.get('participants', [j.get('profile_id')]) for j in jobs):
                        continue
                    run = next((j for j in jobs if j.get('id') == task.get('run_id')), None)
                    if run and run.get('alias'):
                        from collaboration import current_run
                        current_run(self.store, run)
                    repository = self.path_for(task)
                    with repository_lock(repository):
                        path = self.tree_for(task)
                        if (project_git.git(path, 'rev-parse', 'HEAD').strip() != head or project_git.merge_state(path)
                                or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip()):
                            continue
                        upstream = project_git.git(repository, 'rev-parse', '--verify', task['base_ref'] + '^{commit}').strip()
                        if project_git.git(repository, 'rev-parse', head + '^{tree}').strip() != project_git.git(repository, 'rev-parse', upstream + '^{tree}').strip():
                            continue
                    task.update(state='completed', auto_validate=False, completion=dict(
                        outcome='incorporated_upstream', candidate=head, upstream=upstream, validation='verified',
                        reason='Exact candidate file tree matches the fetched configured base and required checks passed.',
                        actor='task_reconciler', at=stamp()))
                    self.save(task, 'complete', 'task_reconciler')
            except (ValueError, OSError, sqlite3.Error):
                continue

    @staticmethod
    def review_signature(task):
        """Evidence identity for automatic reviews; transient states collapse."""
        build = task.get('builds', {}).get(task.get('head_sha'), {})
        state = build.get('state') if build.get('state') in ('complete', 'failed', 'error', 'interrupted') else 'pending'
        return hashlib.sha256(json.dumps([task.get('head_sha'), task.get('state'),
                                          task.get('automation_error'), state,
                                          bool(build.get('required_checks_verified'))]).encode()).hexdigest()[:20]

    def advance_reviews(self):
        with closing(self.connect()) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM tasks WHERE json_extract(data, '$.auto_review')=1 LIMIT 100")]
        for task_id in ids:
            try:
                with self.operation('task:' + task_id, timeout=0):
                    task = self.get(task_id)
                    if task['state'] not in ('implementing', 'review_ready'):
                        continue
                    group_id = task.get('review_group_id')
                    if not group_id:
                        raise ValueError('Automatic review needs an active review group.')
                    head = task.get('head_sha')
                    build = task.get('builds', {}).get(head, {})
                    if not (task.get('automation_error') or build.get('state') in ('failed', 'error') or task['state'] == 'review_ready'):
                        continue
                    signature = self.review_signature(task)
                    if task.get('review_signature') == signature:
                        continue
                    jobs = self.store.snapshot(live_status=False).get('jobs', [])
                    if any(j.get('task_id') == task_id and j.get('review_signature') == signature for j in jobs):
                        # A meeting for this exact evidence already exists (for
                        # example after a restart between creation and save).
                        task['review_signature'] = signature
                        task.pop('review_error', None)
                        self.save(task, 'review_linked', 'task_review_policy')
                        continue
                    self.perform('discuss', dict(group_id=group_id, signature=signature), task_id, 'task_review_policy')
            except (ValueError, OSError, sqlite3.Error) as error:
                try:
                    with self.operation('task:' + task_id, timeout=0):
                        task = self.get(task_id)
                        if task.get('review_error') != str(error)[:500]:
                            task['review_error'] = str(error)[:500]
                            self.save(task, 'review_waiting', 'task_review_policy')
                except (ValueError, OSError, sqlite3.Error):
                    continue

    @staticmethod
    def discussion_proposals(job):
        if job.get('state') != 'artifact_ready':
            return []
        result = job.get('result')
        if not isinstance(result, str) or not result:
            return []
        match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
        try:
            data = json.loads(match[1] if match else result)
        except (ValueError, TypeError):
            return []
        proposals = data.get('task_proposals', []) if isinstance(data, dict) else []
        if not isinstance(proposals, list) or len(proposals) > 10:
            return []
        valid = []
        for proposal in proposals:
            if (not isinstance(proposal, dict) or not isinstance(proposal.get('title'), str) or not 1 <= len(proposal['title'].strip()) <= 120
                    or not isinstance(proposal.get('description'), str) or not 1 <= len(proposal['description'].strip()) <= 7000
                    or not isinstance(proposal.get('profile_id'), str)):
                continue
            key = hashlib.sha256(json.dumps(proposal, sort_keys=True).encode()).hexdigest()[:24]
            valid.append(dict(proposal, key=key))
        return valid

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

    def read_receipt(self, task, run, head, path):
        """Read and validate the completion receipt for this execution attempt."""
        receipt_path = path / '.ci-cache/herdr-guidance' / ('task-' + task['id']) / 'receipt.json'
        if any(parent.is_symlink() for parent in (receipt_path.parent, receipt_path.parent.parent, path / '.ci-cache')):
            raise ValueError('Completion receipt directories must not be symlinks.')
        from collaboration import read_contribution
        receipt = json.loads(read_contribution(receipt_path, label='task completion receipt'))
        if not isinstance(receipt, dict) or receipt.get('outcome') not in ('complete', 'no_changes'):
            raise ValueError('Completion receipt does not declare a finished outcome.')
        if not run.get('completion_token') or receipt.get('token') != run.get('completion_token'):
            raise ValueError('Completion receipt does not match this session token.')
        if receipt.get('run_id') != task.get('run_id'):
            raise ValueError('Completion receipt does not match this execution.')
        if not isinstance(receipt.get('tests'), list):
            raise ValueError('Completion receipt must include a tests array.')
        if receipt.get('commit') != head:
            raise ValueError('Completion receipt does not match the checked-out HEAD.')
        if receipt['outcome'] == 'no_changes' and (not isinstance(receipt.get('reason'), str) or not receipt['reason'].strip()):
            raise ValueError('A no-changes receipt must explain why no changes are required.')
        if receipt['outcome'] == 'no_changes' and head != task.get('base_sha'):
            raise ValueError('A no-changes receipt requires the unchanged recorded base commit.')
        return receipt

    def active_execution(self, profile_id, jobs=None):
        """The launch job that currently occupies an agent, if any."""
        jobs = self.store.snapshot(live_status=False)['jobs'] if jobs is None else jobs
        return next((j for j in jobs if j.get('kind') == 'launch' and j.get('profile_id') == profile_id
                     and j.get('state') not in TERMINAL_EXECUTIONS), None)

    def execution_finished(self, task, run):
        """Evidence that an execution finished. An idle pane alone is never evidence."""
        if not run or run.get('state') != 'persona_sent':
            return False, 'The previous execution is not an active session.'
        if task.get('state') not in HANDOFF_TASK_STATES:
            return False, "The previous task is still '" + str(task.get('state', 'unknown')) + "'."
        if not task.get('worktree'):
            return False, 'The previous task has no recorded checkout to verify.'
        try:
            path = self.tree_for(task)
        except (ValueError, OSError) as error:
            return False, str(error)
        with repository_lock(path):
            if project_git.merge_state(path) or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip():
                return False, 'The previous checkout has uncommitted changes or an unfinished Git operation.'
            if project_git.git(path, 'symbolic-ref', '--short', 'HEAD').strip() != task['branch']:
                return False, 'The previous checkout is not on its assigned task branch.'
            head = project_git.git(path, 'rev-parse', 'HEAD').strip()
        expected = task.get('completion', {}).get('candidate') if task.get('state') == 'completed' else task.get('head_sha')
        if head != expected:
            return False, 'The previous checkout HEAD changed since its recorded candidate or completion.'
        if task.get('state') != 'completed':
            try:
                self.read_receipt(task, run, head, path)
            except ValueError as error:
                return False, str(error)
        from collaboration import current_run
        try:
            current_run(self.store, run)
        except ValueError as error:
            return False, str(error)
        return True, ''

    def finish_execution(self, task, run, request_id, actor):
        """Archive, close and mark a verified-finished execution. Idempotent by request ID."""
        self.store.manage_session(dict(mode='finish', job_id=run['id'], organization_id=task['organization_id'],
                                       inspected=True, request_id=request_id[:64]), actor)

    def materialize_follow_up(self, task, receipt, actor):
        """Record a worker-reported follow-up as a draft task, once per commit."""
        follow = receipt.get('follow_up')
        if not isinstance(follow, dict):
            return
        title, description = follow.get('title'), follow.get('description')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            return
        if not isinstance(description, str) or not 5 <= len(description.strip()) <= 7000:
            return
        key = hashlib.sha256(json.dumps([task['id'], receipt.get('commit'), title.strip()]).encode()).hexdigest()[:24]
        marker = 'receipt:' + key
        if (task.get('follow_up_tasks') or {}).get(marker):
            return
        new_id = uuid.uuid5(uuid.NAMESPACE_URL, 'herdr:' + task['id'] + ':' + marker).hex
        try:
            created = self.perform('create', dict(title=title.strip(), description=description.strip(),
                repository=task['repository'], base_ref=task['base_ref'], profile_id=task['profile_id']), new_id, actor)
        except (ValueError, OSError) as error:
            self.record_error(task['id'], error, 'follow_up', actor)
            return
        created['source'] = dict(task_id=task['id'], depth=((task.get('source') or {}).get('depth') or 0) + 1,
                                 receipt_follow_up=True)
        self.save(created, 'follow_up_reported', actor)
        task.setdefault('follow_up_tasks', {})[marker] = new_id
        self.save(task, 'follow_up_reported', actor)

    def queued_assignments(self, dispatch=False):
        with closing(self.connect()) as db:
            if dispatch:
                # A long busy queue for one agent must not hide free agents
                # beyond the ordinary first-page limit. Consider each queue head.
                query = ("SELECT task_id, position FROM (SELECT a.task_id, a.position, a.rowid AS sequence, "
                         "ROW_NUMBER() OVER (PARTITION BY json_extract(t.data, '$.profile_id') "
                         "ORDER BY a.position, a.rowid) AS rank FROM assignments a JOIN tasks t ON t.id=a.task_id "
                         "WHERE json_extract(t.data, '$.state')='draft') WHERE rank=1 "
                         "ORDER BY position, sequence LIMIT 50")
            else:
                query = ('SELECT a.task_id, a.position FROM assignments a JOIN tasks t ON t.id = a.task_id '
                         "WHERE json_extract(t.data, '$.state')='draft' ORDER BY a.position, a.rowid LIMIT 50")
            return [dict(task_id=row[0], position=row[1]) for row in db.execute(query)]

    def enqueue_assignment(self, task, actor):
        if task.get('run_id') or task.get('state') != 'draft':
            raise ValueError('Only a task that has not started can be queued.')
        previous = task.get('assignment', {})
        task['assignment'] = dict(state='queued', profile_id=task['profile_id'],
                                  queued_at=previous.get('queued_at', stamp()), actor=actor)
        if 'position' in previous:
            task['assignment']['position'] = previous['position']
        return task

    def clear_assignment(self, task_id):
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM assignments WHERE task_id=?', (task_id,))

    def dispatch_assignments(self):
        """Start queued tasks in order whenever their assigned agent is free."""
        assignments = self.queued_assignments(dispatch=True)
        if not assignments:
            return
        # One snapshot per cycle is enough: rank-1 ordering and the launched set
        # keep each agent's head current, and launch_task re-reads state itself.
        jobs = self.store.snapshot(live_status=False).get('jobs', [])
        launched = set()
        for listed in assignments:
            task_id = listed['task_id']
            try:
                with self.operation('task:' + task_id, timeout=0):
                    task = self.get(task_id)
                    if task.get('state') != 'draft' or task.get('assignment', {}).get('state') != 'queued':
                        self.clear_assignment(task_id)
                        continue
                    if task['profile_id'] in launched:
                        continue
                    launched.add(task['profile_id'])  # Keep FIFO even when this task cannot start.
                    blocking = self.active_execution(task['profile_id'], jobs)
                    if blocking and blocking.get('task_id') != task_id:
                        blocking_task = None
                        if blocking.get('task_id'):
                            try:
                                blocking_task = self.get(blocking['task_id'])
                            except ValueError:
                                blocking_task = None
                        if not blocking_task or blocking_task.get('state') not in HANDOFF_TASK_STATES:
                            continue  # The agent is still working; the queue keeps its order silently.
                    self.perform('launch', {}, task_id, 'task_scheduler')
            except Busy:
                continue
            except (ValueError, OSError, sqlite3.Error) as error:
                try:
                    with self.operation('task:' + task_id, timeout=0):
                        task = self.get(task_id)
                        if task.get('state') != 'draft' or task.get('assignment', {}).get('state') != 'queued':
                            continue  # Cancellation or launch won the race; don't restore an obsolete queue error.
                        message = str(error)[:500]
                        if task.get('assignment_error') != message:
                            task['assignment_error'] = message
                            self.save(task, 'assignment_waiting', 'task_scheduler')
                except (ValueError, OSError, sqlite3.Error):
                    continue

    def policy(self, organization_id):
        """Organization follow-up automation policy with defaults applied."""
        merged = dict(AUTOMATION_DEFAULTS)
        with closing(self.connect()) as db:
            row = db.execute('SELECT data FROM policies WHERE id=?', (organization_id,)).fetchone()
        if row:
            merged.update(json.loads(row[0]))
        return merged

    def set_automation_policy(self, body, actor):
        organization_id = body.get('organization_id')
        if not isinstance(organization_id, str) or not 1 <= len(organization_id) <= 64 or '\x00' in organization_id:
            raise ValueError('Select a valid organization.')
        if not any(o.get('id') == organization_id for o in self.store.snapshot(live_status=False).get('organizations', [])):
            raise ValueError('Organization not found.')
        with self.operation('policy:' + organization_id):
            policy = self.policy(organization_id)
            was_enabled = policy['auto_queue_proposals']
            for key in ('auto_queue_proposals', 'paused'):
                if key in body:
                    if not isinstance(body[key], bool):
                        raise ValueError('Invalid automation policy value.')
                    policy[key] = body[key]
            for key, low, high in AUTOMATION_LIMITS:
                if key in body:
                    if type(body[key]) is not int or not low <= body[key] <= high:
                        raise ValueError('Invalid automation limit.')
                    policy[key] = body[key]
            if policy['auto_queue_proposals'] and not was_enabled:
                # Enabling starts a fresh window: discussions created earlier are
                # never queued retroactively.
                policy['auto_queue_since'] = stamp()
            policy.update(updated_at=stamp(), updated_by=actor)
            with closing(self.connect()) as db, db:
                db.execute('INSERT OR REPLACE INTO policies VALUES (?,?)', (organization_id, json.dumps(policy)))
            return dict(policy=policy, organization_id=organization_id)

    def open_work_count(self, profile_id):
        """Queued assignments plus active task executions for one agent."""
        jobs = self.store.snapshot(live_status=False).get('jobs', [])
        active = sum(1 for j in jobs if j.get('kind') == 'launch' and j.get('profile_id') == profile_id
                     and j.get('task_id') and j.get('state') not in TERMINAL_EXECUTIONS)
        with closing(self.connect()) as db:
            queued = db.execute("SELECT COUNT(*) FROM assignments a JOIN tasks t ON t.id = a.task_id "
                                "WHERE json_extract(t.data, '$.profile_id')=? AND json_extract(t.data, '$.state')='draft'",
                                (profile_id,)).fetchone()[0]
        return active + queued

    def auto_created_today(self, organization_id):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with closing(self.connect()) as db:
            return db.execute("SELECT COUNT(*) FROM tasks WHERE json_extract(data, '$.organization_id')=? "
                              "AND json_extract(data, '$.source.auto_queued') IS NOT NULL "
                              "AND json_extract(data, '$.created_at') > ?", (organization_id, cutoff)).fetchone()[0]

    def advance_proposals(self):
        """Materialize qualifying group proposals under the organization policy."""
        with closing(self.connect()) as db:
            saved = {row[0]: json.loads(row[1]) for row in db.execute('SELECT id, data FROM policies')}
        policies = {organization: dict(AUTOMATION_DEFAULTS, **data) for organization, data in saved.items()}
        active = [organization for organization, policy in policies.items()
                  if policy['auto_queue_proposals'] and not policy['paused']]
        if not active:
            return
        # Candidate pairs are unmarked meetings only, newest tasks first, so a
        # long history of evaluated reviews cannot starve new ones.
        placeholders = ','.join('?' for _ in active)
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT t.id, json_extract(t.data, '$.organization_id'), json_extract(m.value, '$.job_id'), "
                "json_extract(m.value, '$.at') FROM tasks t, json_each(t.data, '$.meetings') m "
                "WHERE json_extract(t.data, '$.state') IN ('review_ready','completed','merged') "
                "AND json_type(t.data, '$.meetings') = 'array' "
                "AND json_extract(m.value, '$.job_id') IS NOT NULL "
                "AND json_extract(t.data, '$.organization_id') IN (" + placeholders + ") "
                "AND json_extract(t.data, '$.auto_queue.\"' || json_extract(m.value, '$.job_id') || '\"') IS NULL "
                "ORDER BY t.rowid DESC LIMIT 50", active).fetchall()
        seen = set()
        for task_id, organization_id, job_id, meeting_at in rows:
            policy = policies.get(organization_id)
            if not policy or (task_id, job_id) in seen:
                continue
            seen.add((task_id, job_id))
            since = policy.get('auto_queue_since') or policy.get('updated_at') or ''
            if str(meeting_at or '') < since:
                # Discussions created before the policy was enabled never start
                # retroactively; record the decision once so they are not rescanned.
                try:
                    with self.operation('task:' + task_id, timeout=0):
                        task = self.get(task_id)
                        if (task.get('auto_queue') or {}).get(job_id) is None:
                            task.setdefault('auto_queue', {})[job_id] = dict(
                                at=stamp(), queued=[], skipped={},
                                unavailable='Discussion created before automatic follow-up was enabled.',
                                policy={k: policy[k] for k in AUTOMATION_DEFAULTS})
                            self.save(task, 'auto_queue_checked', 'group_auto_queue')
                except (Busy, ValueError, OSError, sqlite3.Error):
                    pass
                continue
            try:
                with self.operation('task:' + task_id, timeout=0):
                    self.materialize_meeting(task_id, job_id, policy, 'group_auto_queue')
            except Busy:
                continue
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as error:
                self.record_error(task_id, error, 'auto_queue', 'group_auto_queue')

    def materialize_meeting(self, task_id, job_id, policy, actor):
        """Create and queue qualifying proposals from one finalized discussion."""
        task = self.get(task_id)
        if (task.get('auto_queue') or {}).get(job_id) is not None:
            return
        jobs = self.store.snapshot(live_status=False).get('jobs', [])
        job = next((j for j in jobs if j.get('id') == job_id), None)
        if not job:
            task.setdefault('auto_queue', {})[job_id] = dict(
                at=stamp(), queued=[], skipped={}, unavailable='Discussion record unavailable.',
                policy={k: policy[k] for k in AUTOMATION_DEFAULTS})
            self.save(task, 'auto_queue_checked', actor)
            return
        if job.get('state') != 'artifact_ready':
            return  # Evaluate once the discussion finalizes; not marked yet.
        proposals = self.discussion_proposals(job)
        queued, skipped = [], {}
        depth = ((task.get('source') or {}).get('depth') or 0) + 1
        if depth > policy['max_follow_up_depth']:
            skipped = {p['key']: 'follow-up depth limit reached' for p in proposals}
        else:
            profiles = {p['id']: p for p in self.store.snapshot(live_status=False).get('profiles', [])}
            repository = self.path_for(task)
            for proposal in proposals:
                if str(proposal.get('needs_review', '')).lower() in ('true', 'yes'):
                    skipped[proposal['key']] = 'proposal requested operator review'
                    continue
                if len(queued) >= policy['max_per_meeting']:
                    skipped[proposal['key']] = 'meeting limit reached'
                    continue
                profile = profiles.get(proposal['profile_id'])
                if not profile or profile.get('archived') or not profile.get('use_worktree', True):
                    skipped[proposal['key']] = 'assignee is not an eligible worktree agent'
                    continue
                if not profile.get('project') or Path(profile['project']).resolve() != repository:
                    skipped[proposal['key']] = 'assignee is assigned to another repository'
                    continue
                if self.open_work_count(proposal['profile_id']) >= policy['max_open_per_agent']:
                    skipped[proposal['key']] = 'assignee queue limit reached'
                    continue
                if self.auto_created_today(task['organization_id']) >= policy['daily_cap']:
                    skipped[proposal['key']] = 'daily automation limit reached'
                    continue
                try:
                    self.perform('proposal', dict(meeting_id=job_id, proposal_key=proposal['key'], queue=True), task_id, actor)
                except (ValueError, OSError) as error:
                    skipped[proposal['key']] = str(error)[:300]
                    continue
                origin = self.get(task_id)
                created_id = (origin.get('follow_up_tasks') or {}).get(proposal['key'])
                if created_id:
                    created = self.get(created_id)
                    created.setdefault('source', {})['auto_queued'] = dict(
                        at=stamp(), meeting_id=job_id, group_id=job.get('group_id'),
                        policy={k: policy[k] for k in AUTOMATION_DEFAULTS})
                    self.save(created, 'auto_queued', actor)
                    queued.append(proposal['key'])
        task = self.get(task_id)
        task.setdefault('auto_queue', {})[job_id] = dict(
            at=stamp(), queued=queued, skipped=skipped,
            policy={k: policy[k] for k in AUTOMATION_DEFAULTS})
        self.save(task, 'auto_queue_checked', actor)


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
            db.execute('BEGIN IMMEDIATE')
            assignment = task.get('assignment') if task.get('state') == 'draft' else None
            if assignment and assignment.get('state') == 'queued':
                if 'position' not in assignment:
                    assignment['position'] = db.execute('SELECT COALESCE(MAX(position), 0) + 1 FROM assignments').fetchone()[0]
                db.execute('INSERT OR REPLACE INTO assignments VALUES (?,?,?)',
                           (task['id'], assignment['position'], json.dumps(assignment)))
            else:
                db.execute('DELETE FROM assignments WHERE task_id=?', (task['id'],))
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
        executor = ('unavailable' if self.validation is None
                    else 'service' if self.validation.queue is not None else 'gateway')
        with closing(self.connect()) as db:
            saved = {row[0]: json.loads(row[1]) for row in db.execute('SELECT id, data FROM policies')}
            organizations = {row[0] for row in db.execute(
                "SELECT DISTINCT json_extract(data, '$.organization_id') FROM tasks "
                "WHERE json_extract(data, '$.organization_id') IS NOT NULL")}
        automation = {organization: dict(AUTOMATION_DEFAULTS, **saved.get(organization, {}))
                      for organization in organizations}
        if role != 'admin':
            automation = {organization: {k: v for k, v in policy.items() if k in AUTOMATION_DEFAULTS}
                          for organization, policy in automation.items()}
        return dict(tasks=tasks, github=github, executor=executor, automation=automation)

    def detail(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id):
            raise ValueError('Select a valid task.')
        task = self.get(task_id)
        with closing(self.connect()) as db:
            task['audit'] = list(reversed([json.loads(row[0]) for row in db.execute(
                'SELECT data FROM audit WHERE task_id=? ORDER BY rowid DESC LIMIT 500', (task_id,))]))
        snapshot = self.store.snapshot(live_status=False)
        jobs = snapshot.get('jobs', [])
        names = {p.get('id'): p.get('name', '') for p in snapshot.get('profiles', [])}
        sessions = [j for j in jobs if j.get('task_id') == task_id or j.get('id') == task.get('run_id')]
        participants = list(task.get('participants', []))
        known = {p.get('run_id') for p in participants}
        for job in sessions:
            if job.get('kind', 'launch') != 'launch':
                continue
            if job['id'] not in known:
                profile = job.get('profile', {})
                participants.append(dict(profile_id=job.get('profile_id'), run_id=job['id'],
                    name=profile.get('name', 'Agent identity unavailable'), role=profile.get('role', ''),
                    runtime=profile.get('runtime', ''), model=profile.get('model'), provider=profile.get('provider'), assigned_at=job.get('created_at'),
                    provenance='launch_record'))
                known.add(job['id'])
        meetings = []
        for reference in task.get('meetings', []):
            job = next((j for j in jobs if j.get('id') == reference['job_id']), None)
            if job:
                proposals = [dict(p, assignee=names.get(p['profile_id'], 'Agent identity unavailable'))
                             for p in self.discussion_proposals(job)]
                meetings.append(dict(reference, state=job['state'], result=job.get('result', ''),
                    error=job.get('error', ''), proposals=proposals))
            else:
                meetings.append(dict(reference, state='unavailable', proposals=[]))
        task['meeting_results'] = meetings
        task['participants'] = participants
        blocking = self.active_execution(task.get('profile_id'), jobs)
        if blocking and blocking.get('id') != task.get('run_id'):
            blocking_task = None
            if blocking.get('task_id'):
                with closing(self.connect()) as db:
                    row = db.execute('SELECT data FROM tasks WHERE id=?', (blocking['task_id'],)).fetchone()
                blocking_task = json.loads(row[0]) if row else None
            task['blocking_execution'] = dict(
                run_id=blocking['id'], state=blocking['state'], alias=blocking.get('alias'),
                task_id=blocking.get('task_id'), task_title=(blocking_task or {}).get('title', ''),
                task_state=(blocking_task or {}).get('state', ''),
                handoff_ready=bool(blocking.get('state') == 'persona_sent' and blocking_task and (blocking_task.get('state') == 'completed' or (
                    blocking_task.get('state') in HANDOFF_TASK_STATES and blocking_task.get('completion_receipt')))))
        else:
            task['blocking_execution'] = None
        task['sessions'] = [{k: j.get(k) for k in ('id', 'kind', 'state', 'profile_id', 'created_at',
                            'updated_at', 'error', 'pane_id', 'alias', 'session_closed_at')} |
                            dict(history=[{k: h.get(k) for k in ('alias', 'pane_id', 'session_closed_at')}
                                          for h in j.get('session_history', [])]) for j in sessions]
        try:
            task['current_upstream_sha'] = project_git.git(self.path_for(task), 'rev-parse', '--verify', task['base_ref'] + '^{commit}').strip()
            if task.get('head_sha'):
                repository = self.path_for(task)
                task['already_upstream'] = (project_git.git(repository, 'rev-parse', task['head_sha'] + '^{tree}').strip() == project_git.git(repository, 'rev-parse', task['current_upstream_sha'] + '^{tree}').strip())
        except (ValueError, OSError):
            task['current_upstream_sha'] = None
            task['already_upstream'] = None
        # History is a read-only view of immutable commits, never candidate recapture.
        if task.get('head_sha'):
            try:
                repository = self.path_for(task)
                with repository_lock(repository):
                    task['commit_graph'] = self.commit_history(repository, task['base_sha'], task['head_sha'])
            except (ValueError, OSError) as error:
                task['history_error'] = str(error)[:500]
                task['commit_graph'] = []
        return task

    @staticmethod
    def commit_history(repository, base, head):
        if not SHA.fullmatch(str(base)) or not SHA.fullmatch(str(head)):
            raise ValueError('Commit history requires recorded full commit identities.')
        log_format = '%H%x1f%P%x1f%s%x1f%an%x1f%aI%x1f%D'
        rows = project_git.git(repository, 'log', '--topo-order', '--max-count=30',
                               '--format=' + log_format, base + '..' + head, '--').splitlines()
        result = []
        for row in rows:
            sha, parents, subject, author, date, refs = row.split('\x1f', 5)
            result.append(dict(sha=sha, parents=parents.split(), subject=subject[:300],
                               author=author[:120], date=date, refs=refs[:200]))
        if not any(n['sha'] == base for n in result):
            row = project_git.git(repository, 'show', '-s', '--format=' + log_format, base, '--').strip()
            sha, parents, subject, author, date, refs = row.split('\x1f', 5)
            result.append(dict(sha=sha, parents=parents.split(), subject=subject[:300],
                               author=author[:120], date=date, refs=refs[:200]))
        return result

    def commit_detail(self, task_id, sha):
        task = self.detail(task_id)
        if not SHA.fullmatch(str(sha)) or sha not in {n['sha'] for n in task.get('commit_graph', [])}:
            raise ValueError('Select a commit from this task history.')
        repository = self.path_for(task)
        with repository_lock(repository):
            parents = next(n['parents'] for n in task['commit_graph'] if n['sha'] == sha)
            if parents:
                diff = project_git.git(repository, 'diff', '--no-ext-diff', '--no-textconv', parents[0], sha, '--')
            else:
                diff = project_git.git(repository, 'show', '--format=', '--root', '--no-ext-diff', '--no-textconv', sha, '--')
        return dict(sha=sha, diff=diff[:65536], diff_truncated=len(diff) > 65536,
                    comparison='first parent' if parents else 'empty tree', build=task.get('builds', {}).get(sha))

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
        if action not in ('create', 'automation') and (not isinstance(body.get('task_id'), str)
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
                if action not in ('create', 'automation'):
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

    def launch_task(self, task, actor):
        # All task launch reservations share this lock, including manual starts.
        with self.operation('launch-capacity', timeout=0):
            jobs = self.store.snapshot(live_status=False).get('jobs', [])
            if task.get('run_id'):
                return task
            reservations = [j for j in jobs if j.get('kind') == 'launch' and j.get('task_id') == task['id']]
            existing = next((j for j in reservations if j.get('state') not in TERMINAL_EXECUTIONS), None)
            if not existing and reservations:
                raise ValueError('This task already has a finished or released launch reservation; create a new task for further work.')
            blocking = self.active_execution(task['profile_id'], jobs)
            active = sum(1 for j in jobs if j.get('kind') == 'launch' and j.get('task_id')
                         and j.get('state') not in TERMINAL_EXECUTIONS)
            if not existing and active - int(bool(blocking and blocking.get('task_id'))) >= MAX_ACTIVE_EXECUTIONS:
                raise ValueError('Task execution capacity is full. Queue this task until a slot is available.')
            if blocking and not existing:
                if blocking.get('task_id') == task['id']:
                    raise ValueError('This task already has an active session; refresh the task before launching again.')
                blocking_task = None
                if blocking.get('task_id'):
                    try:
                        blocking_task = self.get(blocking['task_id'])
                    except ValueError:
                        blocking_task = None
                if not blocking_task:
                    raise ValueError('Assigned agent has an active session without a task; inspect that session before assigning new work.')
                with self.operation('task:' + blocking_task['id'], timeout=0):
                    blocking_task = self.get(blocking_task['id'])
                    verified, reason = self.execution_finished(blocking_task, blocking)
                    if not verified:
                        raise ValueError("Assigned agent is busy with task '" + blocking_task['title'] + "' (" + blocking_task['id'] + "). " + reason + ' Open that task to inspect its execution.')
                    if blocking_task['state'] != 'completed' and blocking_task.get('completion_receipt', {}).get('commit') != blocking_task['head_sha']:
                        receipt = self.read_receipt(blocking_task, blocking, blocking_task['head_sha'], self.tree_for(blocking_task))
                        blocking_task['completion_receipt'] = dict(commit=blocking_task['head_sha'], tests=receipt['tests'], verified_at=stamp())
                        self.save(blocking_task, 'completion_verified', actor)
                    task['handoff'] = dict(stage='closing', blocking_run=blocking['id'], blocking_task=blocking_task['id'], at=stamp())
                    self.save(task, 'handoff_started', actor)
                    self.finish_execution(blocking_task, blocking, 'handoff-' + task['id'], actor)
                    task = self.get(task['id'])
                    task['handoff'] = dict(stage='closed', blocking_run=blocking['id'], blocking_task=blocking_task['id'], at=stamp())
                    self.save(task, 'handoff_closed', actor)
            prompt = 'Task: ' + task['title'] + '\n' + task['description']
            if task.get('source', {}).get('task_id'):
                try:
                    origin = self.get(task['source']['task_id'])
                except ValueError:
                    origin = None
                if origin:
                    prompt = ('Follow-up of task ' + origin['id'] + ': ' + origin['title'] +
                              '\nRecorded completion: ' + str(origin.get('completion', {}).get('reason', 'unavailable'))[:500] +
                              '\n\n' + prompt)
            if existing:
                run = existing
            else:
                run = self.store.action('launch', dict(request_id='task-' + task['id'],
                    organization_id=task['organization_id'], profile_id=task['profile_id'],
                    task_id=task['id'], worktree_branch=task['branch'], start_sha=task['base_sha'],
                    task_prompt=prompt +
                        '\nWork only in your assigned task branch. Implement, run required checks, and commit your changes. '
                        'Commit using git -c user.name=Herdr-Agent -c user.email=agent@herdr.local commit. Never infer the operator identity or change global Git config. '
                        'Never push, open a pull request, update the shared checkout or deploy. '
                        'When complete, write JSON to {{HERDR_TASK_RECEIPT}} with outcome=complete, commit=the full HEAD SHA, run_id={{HERDR_TASK_RUN}}, token={{HERDR_TASK_TOKEN}}, and tests as an array of actual check results. Write it last after committing. '
                        'If and only if the task genuinely requires no code changes, write the same receipt with outcome=no_changes, commit=the unchanged recorded base SHA and a reason field instead of committing. '
                        'Optionally include "follow_up": {"title": ..., "description": ...} when you found necessary related work; it is recorded as a draft task for operator review, never started automatically. '
                        'Report commands and actual results; missing checks are not passes. Publishing is a dashboard administrator action.\n\n'
                        'Task-local tool acquisition policy:\n' + task_tool_guidance()))
            profile = next((p for p in self.store.snapshot(live_status=False)['profiles'] if p['id'] == task['profile_id']), {})
            task.setdefault('participants', []).append(dict(profile_id=task['profile_id'], run_id=run['id'],
                name=profile.get('name', 'Agent identity unavailable'), role=profile.get('role', ''),
                runtime=profile.get('runtime', ''), model=profile.get('model'), provider=profile.get('provider'), assigned_at=stamp(), provenance='task_launch'))
            task.update(run_id=run['id'], worktree=str((self.projects / '.herdr-worktrees' / run['id']).resolve()),
                        state='implementing')
            task.pop('handoff', None)
            task.pop('assignment', None)
            task.pop('assignment_error', None)
            return self.save(task, 'launch', actor)

    def perform(self, action, body, task_id, actor):
        if action == 'automation':
            return self.set_automation_policy(body, actor)
        if action == 'create':
            try:
                return self.get(task_id)
            except ValueError:
                pass
            for key, limit in [('title', 120), ('description', 8000), ('repository', 2000)]:
                value = body.get(key)
                if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
                    raise ValueError('Invalid task ' + key + '.')
            repository = project_directory(self.projects, str(self.projects / body['repository']))
            try:
                top = Path(project_git.git(repository, 'rev-parse', '--show-toplevel').strip()).resolve()
            except ValueError as error:
                raise ValueError('Selected project is not an accessible Git repository. Clone or initialize it before creating a task.') from error
            if repository != top:
                raise ValueError('Select the repository root.')
            requested = body.get('base_ref', '')
            if not isinstance(requested, str) or len(requested) > 250 or '\x00' in requested:
                raise ValueError('Invalid task base_ref.')
            base, sha = task_base(repository, requested)
            remote = remote_info(repository)
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
                        created_at=stamp(), checks=[], audit=[], auto_validate=False,
                        assigned_agent={k: profile.get(k) for k in ('id', 'name', 'role', 'runtime')}, participants=[])
            return self.save(task, 'create', actor)
        task = self.get(task_id)
        had_error = task.pop('error', None) is not None
        # An unchanged background refresh must not rewrite the task or its audit.
        unchanged = self.comparable(task) if action == 'refresh' else None
        if task['state'] in ('merged', 'closed', 'completed') and action not in ('refresh', 'build', 'discuss', 'review_policy', 'proposal', 'complete', 'assignment'):
            raise ValueError('This task pull request is closed. Create a new task for further changes.')
        if action != 'assignment':
            repository = self.path_for(task)
            if remote_info(repository) != task['github_repository']:
                raise ValueError('Repository remote changed; inspect the task before continuing.')
        if action == 'launch':
            return self.launch_task(task, actor)
        elif action == 'complete':
            if task['state'] == 'completed':
                saved = task.get('completion', {})
                if body.get('head_sha') == saved.get('candidate') and body.get('reason', '').strip() == saved.get('reason') and body.get('outcome') == saved.get('outcome'):
                    return task
                raise ValueError('This task is already completed; its completion record is immutable.')
            head = body.get('head_sha')
            reason = body.get('reason')
            if body.get('inspected') is not True or head != task.get('head_sha') or not SHA.fullmatch(str(head)):
                raise ValueError('Review the exact current candidate before marking the task done.')
            if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 2000:
                raise ValueError('Record how this task was satisfied or superseded.')
            outcome = body.get('outcome')
            if outcome not in ('incorporated_elsewhere', 'superseded'):
                raise ValueError('Choose a completion outcome.')
            if task.get('pull') and task['pull'].get('state') == 'open':
                raise ValueError('Resolve the open pull request before completing this task.')
            path = self.tree_for(task)
            with repository_lock(repository):
                if (project_git.git(path, 'rev-parse', 'HEAD').strip() != head or project_git.merge_state(path)
                        or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip()):
                    raise ValueError('Candidate changed or checkout is not clean; inspect it again.')
                upstream = project_git.git(repository, 'rev-parse', '--verify', task['base_ref'] + '^{commit}').strip()
                if body.get('upstream_sha') != upstream:
                    raise ValueError('Fetched base changed; refresh before confirming completion.')
            evidence = task.get('builds', {}).get(head, {})
            task.update(state='completed', auto_validate=False, auto_review=False, completion=dict(
                outcome=outcome, candidate=head, upstream=upstream,
                validation='verified' if evidence.get('state') == 'complete' and evidence.get('target') == head and evidence.get('required_checks_verified') is True else 'not_verified',
                reason=reason.strip(), actor=actor, at=stamp()))
        elif action == 'assignment':
            mode = body.get('mode')
            if mode not in ('queue', 'cancel', 'move'):
                raise ValueError('Choose queue, cancel or move.')
            if mode == 'queue':
                task = self.enqueue_assignment(task, actor)
            elif mode == 'cancel':
                task.pop('assignment', None)
                task.pop('assignment_error', None)
            else:
                position = body.get('position')
                if type(position) is not int or not 0 <= position <= 10000:
                    raise ValueError('Invalid queue position.')
                if task.get('assignment', {}).get('state') != 'queued':
                    raise ValueError('This task has no queued assignment.')
                task['assignment'] = dict(task.get('assignment', {}), position=position)
        elif action in ('discuss', 'review_policy'):
            groups = self.store.snapshot(live_status=False).get('groups', [])
            group = next((g for g in groups if g['id'] == body.get('group_id') and g['organization_id'] == task['organization_id'] and not g.get('removed_at')), None)
            if not group:
                raise ValueError('Choose an active group in this task organization.')
            if action == 'review_policy':
                if not isinstance(body.get('auto_review'), bool):
                    raise ValueError('Choose whether group reviews are automatic.')
                task.update(review_group_id=group['id'], auto_review=body['auto_review'])
            else:
                with closing(self.connect()) as db:
                    open_tasks = [json.loads(r[0]) for r in db.execute("SELECT data FROM tasks WHERE json_extract(data, '$.organization_id')=? AND json_extract(data, '$.state') NOT IN ('completed','closed','merged') LIMIT 10", (task['organization_id'],))]
                # The instruction comes first: bounded context may be truncated,
                # never the required proposal contract. Automatic follow-up is
                # stated honestly so agents propose sparingly when it is enabled.
                policy = self.policy(task['organization_id'])
                if policy['auto_queue_proposals'] and not policy['paused']:
                    follow_up = ('This organization has automatic follow-up enabled: qualifying proposals are created and queued '
                        'without further operator review, up to ' + str(policy['max_per_meeting']) + ' per meeting and '
                        + str(policy['daily_cap']) + ' per day. Propose only necessary, self-contained work with acceptance criteria '
                        'and required checks. Set needs_review=true on a proposal that changes scope, adds dependencies or touches '
                        'sensitive areas; those stay drafts.')
                else:
                    follow_up = 'These are drafts for operator review, not authorization to launch work.'
                instruction = ('Discuss remaining acceptance criteria, blockers, review evidence and possible duplicate/superseded work. '
                    'Read-only discussion; do not edit, commit, push or deploy. '
                    'Do not treat missing validation as passed. Propose only necessary follow-up work; do not recreate existing tasks. '
                    'In the final action-plan artifact include one fenced json object with task_proposals: an array (at most 10) of {title, description, profile_id}. '
                    'Each description must include acceptance criteria and required checks. Use an existing individual repository worker profile ID from the group roster. '
                    'Also consider one evidence-based improvement that would make similar work easier next time: documentation, tooling or UX. Do not invent work. '
                    'An empty proposal array is valid. ' + follow_up)
                context = ('Review task ' + task['id'] + ': ' + task['title'] + '\n' + task['description'][:2000] +
                    '\nCandidate: ' + str(task.get('head_sha')) + '\nIssue: ' + str(task.get('automation_error', task.get('error', '')))[:500] +
                    '\nOther open work:\n' + '\n'.join(t['id'] + ' ' + t['title'][:120] + ' [' + t['state'] + ']' for t in open_tasks))
                topic = (instruction + '\n\n' + context)[:8000]
                signature = body.get('signature') or hashlib.sha256(str(body.get('request_id', uuid.uuid4().hex)).encode()).hexdigest()[:20]
                if not re.fullmatch(r'[a-f0-9]{20}', signature):
                    raise ValueError('Invalid discussion identity.')
                job = self.store.action('discuss', dict(request_id='task-review-' + task_id + '-' + signature,
                    organization_id=task['organization_id'], group_id=group['id'], prompt=topic))
                self.store.update_job(job['id'], task_id=task_id, review_signature=signature)
                task.setdefault('meetings', []).append(dict(job_id=job['id'], group_id=group['id'], group_name=group['name'], at=stamp(), signature=signature))
                task['meetings'] = task['meetings'][-30:]
                task['review_signature'] = self.review_signature(task)
                task.pop('review_error', None)
        elif action == 'proposal':
            reference = next((m for m in task.get('meetings', []) if m['job_id'] == body.get('meeting_id')), None)
            if not reference:
                raise ValueError('Select a discussion linked to this task.')
            jobs = self.store.snapshot(live_status=False).get('jobs', [])
            job = next((j for j in jobs if j['id'] == reference['job_id'] and j.get('organization_id') == task['organization_id']), None)
            proposal = next((p for p in self.discussion_proposals(job or {}) if p['key'] == body.get('proposal_key')), None)
            if not proposal:
                raise ValueError('Select a finalized discussion proposal.')
            profile = next((p for p in self.store.snapshot(live_status=False)['profiles'] if p['id'] == proposal['profile_id']), None)
            if not profile or profile.get('organization_id') != task['organization_id']:
                raise ValueError('Proposal assignee belongs to a different organization.')
            new_id = uuid.uuid5(uuid.NAMESPACE_URL, 'herdr:' + task_id + ':' + reference['job_id'] + ':' + proposal['key']).hex
            created = self.perform('create', dict(title=proposal['title'], description=proposal['description'],
                repository=task['repository'], base_ref=task['base_ref'], profile_id=proposal['profile_id']), new_id, actor)
            created['source'] = dict(task_id=task_id, meeting_id=reference['job_id'], group_id=reference['group_id'],
                                     proposal_key=proposal['key'], depth=(task.get('source', {}).get('depth') or 0) + 1)
            self.save(created, 'proposal_accepted', actor)
            task.setdefault('follow_up_tasks', {})[proposal['key']] = new_id
            if body.get('queue') is True:
                created = self.enqueue_assignment(created, actor)
                self.save(created, 'assignment_queued', actor)
        elif action == 'policy':
            if not isinstance(body.get('auto_validate'), bool):
                raise ValueError('Choose whether automatic capture and validation is enabled.')
            if body['auto_validate'] and (not self.validation or self.validation.snapshot().get('executor') != 'service'):
                raise ValueError('Install the durable build service before enabling automatic task validation.')
            task['auto_validate'] = body['auto_validate']
        elif action == 'candidate':
            path = self.tree_for(task)
            run = next((j for j in self.store.snapshot(live_status=False)['jobs'] if j['id'] == task.get('run_id')), None)
            if not run or run['state'] != 'persona_sent':
                raise ValueError('Task agent launch is not ready; inspect its job.')
            if run.get('alias'):
                from collaboration import current_run
                current_run(self.store, run)
            # Capturing a candidate is explicit operator review, not a claim the
            # worker passed validation. The immutable SHA remains the evidence.
            with repository_lock(path):
                if project_git.merge_state(path) or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip():
                    raise ValueError('Commit task changes and resolve operations before reviewing.')
                head = project_git.git(path, 'rev-parse', 'HEAD').strip()
                if project_git.git(path, 'symbolic-ref', '--short', 'HEAD').strip() != task['branch']:
                    raise ValueError('Task checkout branch changed; inspect before capturing.')
                if head == task['base_sha']:
                    raise ValueError('The task has no committed changes to review.')
                project_git.git(path, 'merge-base', '--is-ancestor', task['base_sha'], head)
                diff = project_git.git(path, 'diff', '--no-ext-diff', '--no-textconv', task['base_sha'], head, '--')
                graph = []
                for line in project_git.git(path, 'log', '--topo-order', '--max-count=12', '--format=%H %P', task['base_sha'] + '..' + head, '--').splitlines():
                    fields = line.split()
                    graph.append(dict(sha=fields[0], parents=fields[1:]))
                task['commit_graph'] = graph
                try:
                    upstream = project_git.git(repository, 'rev-parse', '--verify', task['base_ref'] + '^{commit}').strip()
                    task['current_upstream_sha'] = upstream
                    task['already_upstream'] = (
                        project_git.git(path, 'rev-parse', head + '^{tree}').strip() ==
                        project_git.git(path, 'rev-parse', upstream + '^{tree}').strip())
                except ValueError:
                    # A pruned or renamed base ref is not a capture failure.
                    task['current_upstream_sha'] = None
                    task['already_upstream'] = None
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
            if previous and previous.get('run_id') == run['id']:
                # The durable queue deduplicated to the existing run; keep its
                # full evidence instead of replacing it with the queue record.
                task.setdefault('builds', {})[target] = dict(previous, state=run['state'])
            else:
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
