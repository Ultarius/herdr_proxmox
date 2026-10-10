"""Background integration notices for checkouts where agents are working.

Git inspection is read-only. When coordination is enabled by the operator,
the durable coordinator queues worker decisions and merges. Busy workers wait.

The gateway keeps running when no dashboard is connected, so drift is detected
even while nobody is watching. Fetches stay operator-initiated; each notice
carries when the remote-tracking refs were last updated so a stale comparison
is visible.
"""
from datetime import datetime, timezone
from pathlib import Path
import threading
import time

import project_git

INTERVAL = 60
LIMIT = 20
BASE_TTL = 300


class IntegrationWatcher:
    def __init__(self, root, checkouts, interval=INTERVAL, coordinator=None, wake_event=None):
        # wake_event may be a threading.Event or a notification Mailbox, which
        # implements the same set/clear/wait surface. The loop below relies on
        # wait() draining what it consumed, so Mailbox.clear() stays a no-op.
        self.root = Path(root).resolve()
        self.checkouts = checkouts
        self.coordinator = coordinator
        self.interval = interval
        self.lock = threading.Lock()
        self.alerts = []
        self.checked = None
        self.bases = {}
        self.build_results = None
        self.schedule_builds = None
        self.build_active = False
        self.stopped = threading.Event()
        self.wake_event = wake_event if wake_event is not None else threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.wake_event.set()

    def wake(self):
        self.wake_event.set()

    def base_for(self, repository):
        """Cached base ref for a repository, re-resolved occasionally so a later
        fetch that creates origin/main is noticed."""
        configured = project_git.configured_base(repository)
        if configured:
            try:
                project_git.git(repository, 'rev-parse', '--verify', configured)
            except ValueError:
                # A configured base that no longer exists locally cannot be
                # compared or updated. Fall back to detection instead of
                # reporting an error for every checkout in this repository.
                configured = None
            else:
                return configured
        now = time.monotonic()
        with self.lock:
            cached = self.bases.get(repository)
            if cached and now - cached[1] < BASE_TTL:
                return cached[0]
        base = None
        for candidate in ('refs/remotes/origin/main', 'refs/remotes/origin/master'):
            try:
                project_git.git(repository, 'rev-parse', '--verify', candidate)
                base = candidate
                break
            except ValueError:
                continue
        with self.lock:
            self.bases[repository] = (base, now)
        return base

    def _loop(self):
        while not self.stopped.is_set():
            self.wake_event.wait(min(self.interval, 5) if self.build_active else self.interval)
            self.wake_event.clear()
            if self.stopped.is_set():
                break
            try:
                self.refresh()
            except Exception:
                # A broken repository must never stop the watcher thread.
                continue

    def refresh(self):
        if self.build_results is not None:
            builds = self.build_results()
            self.build_active = bool(builds.get('running') or builds.get('queued'))
        found = []
        measured = {}
        configs = self.coordinator.snapshot()['configurations'] if self.coordinator else []
        coordinator_ids = {c.get('profile_id') for c in configs}
        for profile_id, run_id, name, path in self.checkouts()[:LIMIT]:
            try:
                key = str(Path(path).resolve())
                if key not in measured:
                    measured[key] = self.notice(profile_id, run_id, name, path)
                found.append(dict(measured[key], profile_id=profile_id, run_id=run_id, name=name))
            except (ValueError, OSError) as error:
                found.append(dict(profile_id=profile_id, run_id=run_id, name=name,
                                  path=str(path), error=str(error)))
        # Only actionable drift is kept; clean checkouts produce no notice.
        for notice in found:
            notice['checkout_role'] = 'coordinator' if notice['profile_id'] in coordinator_ids else 'worker'
        notices = [n for n in found
                   if n['checkout_role'] != 'coordinator'
                   if n.get('behind') or n.get('conflicts') or n.get('merging') or n.get('error')]
        with self.lock:
            now = time.monotonic()
            self.bases = {key: value for key, value in self.bases.items() if now - value[1] < BASE_TTL}
            self.alerts = notices
            self.checked = datetime.now(timezone.utc).isoformat()
        if self.coordinator is not None and not self.stopped.is_set():
            self.coordinator.observe(notices)
            if self.coordinator.tick():
                self.wake()
            if self.schedule_builds is not None:
                self.schedule_builds()
                # Poll promptly after submission, including without a browser.
                builds = self.build_results() if self.build_results else {}
                self.build_active = bool(builds.get('running') or builds.get('queued'))
        return notices

    def annotate_worktrees(self, data):
        """Checkout age is status, not an integration request for a summary agent."""
        configs = self.coordinator.snapshot()['configurations'] if self.coordinator else []
        paths = {str(Path(c['coordinator_path']).resolve()) for c in configs if c.get('coordinator_path')}
        for tree in data.get('worktrees', []):
            cwd = tree.get('cwd')
            tree['checkout_role'] = ('coordinator' if cwd and str(Path(cwd).resolve()) in paths
                                     else 'shared' if tree.get('path') == data.get('repository_path')
                                     else 'worker')
        return data

    def annotate_profiles(self, data):
        """Expose binding by profile ID; duplicate display names are harmless."""
        configs = self.coordinator.configuration_records() if self.coordinator else []
        bindings = {}
        for config in configs:
            bindings.setdefault(config.get('profile_id'), []).append(config.get('repository'))
        for profile in data.get('profiles', []):
            repositories = bindings.get(profile.get('id'), [])
            profile['coordination_repositories'] = repositories
            if repositories:
                profile['coordination_binding'] = 'used'
            elif str(profile.get('role') or '').lower() == 'integration coordinator':
                profile['coordination_binding'] = 'inactive'
        return data

    def notice(self, profile_id, run_id, name, path):
        directory = Path(path).resolve()
        if not directory.is_relative_to(self.root) or not directory.is_dir():
            raise ValueError('Agent checkout is outside the projects directory.')
        repository = Path(project_git.git(directory, 'rev-parse', '--show-toplevel').strip()).resolve()
        if not repository.is_relative_to(self.root):
            raise ValueError('Repository is outside the projects directory.')
        common = Path(project_git.git(repository, 'rev-parse', '--git-common-dir').strip())
        if not common.is_absolute():
            common = repository / common
        common = common.resolve()
        if not common.is_relative_to(self.root):
            raise ValueError('Git metadata is outside the projects directory.')
        fetched = common / 'FETCH_HEAD'
        last_fetch = datetime.fromtimestamp(fetched.stat().st_mtime, timezone.utc).isoformat() if fetched.is_file() else None
        tree = project_git.summary(self.root, directory, self.base_for(repository))
        base = self.base_for(repository)
        target = project_git.git(repository, 'rev-parse', base).strip() if base else None
        return dict(repository=(common.parent if common.name == '.git' else repository).relative_to(self.root).as_posix(), target=target, profile_id=profile_id, run_id=run_id, name=name, path=tree['path'], last_fetch=last_fetch,
                    branch=tree['branch'], base=tree['base'], behind=tree['base_behind'],
                    ahead=tree['base_ahead'], conflicts=tree['conflicts'],
                    dirty=tree['dirty'], merging=tree['merging'])

    def snapshot(self):
        with self.lock:
            return {'alerts': list(self.alerts), 'checked': self.checked, 'interval': self.interval,
                    'coordination': self.coordinator.snapshot() if self.coordinator else {'configurations': [], 'events': []}}
