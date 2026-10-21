"""Explicit project preparation using durable plans and trusted SDK providers.

Analysis GETs remain read-only. Administrative POSTs plan exact committed
requirements and queue acquisition. The worker never installs as root, edits
global defaults or executes project restore/build scripts during SDK setup.
"""
import os
import logging
from pathlib import Path
import re
import subprocess
import sqlite3
import threading
import uuid
import tempfile
from contextlib import ExitStack

import environment_artifacts
import environment_adapters
import environment_capabilities
import environment_plan
import environment_recipes
import environment_providers
from environment_store import EnvironmentPlanStore
import project_git


class EnvironmentPreparation:
    def __init__(self, analysis, path, root, resolve=None, acquire=None):
        self.analysis = analysis
        self.store = EnvironmentPlanStore(path)
        self.root = Path(root)
        self.resolve = resolve or environment_artifacts.resolve
        self.acquire = acquire or environment_artifacts.acquire
        self.owner = uuid.uuid4().hex
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.admission = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='project-environments', daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=35)

    @staticmethod
    def job_view(job, *, details=False):
        if job is None or details:
            return job
        result = {key: job[key] for key in ('id', 'state', 'revision', 'created_at', 'finished_at') if key in job}
        if job.get('state') == 'failed':
            result['error'] = 'Ask an administrator to inspect the preparation error.'
        return result

    def status(self, project, *, details=False):
        """Saved preparation at the checkout HEAD, without planning or probing SDKs."""
        checkout = self.analysis.project(project)
        project = checkout.relative_to(self.analysis.root.absolute()).as_posix()
        revision = project_git.git(checkout, 'rev-parse', '--verify', 'HEAD^{commit}').strip()
        plan, job, active, prepared_plan, prepared_job = self.store.status_records(project, revision)
        current = bool(prepared_plan and prepared_plan['revision'] == revision)
        for saved in (plan, prepared_plan):
            if saved and environment_plan.digest(saved) != saved.get('digest'):
                raise ValueError('The saved environment plan failed its integrity check.')
        prepared = None
        if prepared_plan:
            prepared = dict(revision=prepared_plan['revision'],
                job_id=prepared_job['id'], finished_at=prepared_job.get('finished_at'), current=current,
                requirements=[{key: item.get(key) for key in ('capability', 'label', 'version', 'components')}
                              for item in prepared_plan['requirements']],
                selected_check_count=len(prepared_plan.get('selected_checks', [])))
        return dict(project=project, revision=revision, plan=plan if details else None,
                    job=self.job_view(job, details=details), prepared=prepared,
                    active_jobs=[self.job_view(item, details=details) for item in active],
                    pending_plan=bool(current and plan and plan['digest'] != prepared_plan['digest']),
                    plan_current=bool(plan and plan['revision'] == revision))

    def plan(self, project, revision=None):
        checkout = self.analysis.project(project)
        revision = revision or project_git.git(checkout, 'rev-parse', '--verify', 'HEAD^{commit}').strip()
        report = self.analysis.effective(project, revision=revision)
        # Release installers select Herdr's own pin, not a managed project SDK.
        plan = environment_plan.build(report, release_entries={}, measure=environment_plan.measurements(report))
        plan['preparation_version'] = 1
        paths = project_git.git(checkout, 'ls-tree', '-r', '-z', '--name-only', revision).split('\0')
        plan['check_candidates'] = environment_recipes.candidates(report, paths)
        plan['selected_checks'] = []
        import project_analysis
        reader = project_analysis.committed_reader(checkout, revision)
        specifications = {}
        for item in plan['requirements']:
            item['adapter_version'] = environment_adapters.ADAPTERS[item['capability']].version if item['capability'] in environment_adapters.ADAPTERS else None
            item.update(activation='unavailable',
                        activation_reason='This capability has no managed activation adapter yet.')
            if item['capability'] not in environment_capabilities.MANAGED:
                continue
            adapter = environment_adapters.get(item['capability'])
            try:
                environment_providers.invoke(adapter.plan_requirement, item, paths=paths, read=reader,
                    revision=revision, resolve=self.resolve, specifications=specifications)
            except (ValueError, OSError) as error:
                item.update(state='unsupported', activation='unavailable', reason=str(error)[:500], activation_reason=str(error)[:500])
        plan['blockers'] = [environment_plan._blocker(item) for item in plan['requirements'] if item['state'] != 'ready']
        plan['blockers'].extend(dict(capability=item['capability'], state='activation_unavailable',
                                    reason=item['activation_reason'], remedy='Add a managed activation adapter.')
                               for item in plan['requirements'] if item['activation'] != 'available')
        plan['digest'] = environment_plan.digest(plan)
        self.store.save(plan)
        return plan

    def assisted_plan(self, plan):
        """Offer supported adoption contracts, never silently replace a managed provider."""
        import copy
        assisted = copy.deepcopy(plan)
        for item in assisted['requirements']:
            try:
                adapter = environment_adapters.get(item['capability'])
            except ValueError:
                continue
            if adapter.assisted and not item.get('conflicts') and (item.get('constraint') or any(
                    entry.get('constraint') or entry.get('selected') for entry in item['evidence'])):
                item.update(preparation_method='agent', activation='awaiting_adoption',
                            activation_reason='A separately authorized preparation agent must submit an SDK for adoption.')
        assisted['digest'] = environment_plan.digest(assisted)
        self.store.save(assisted)
        return assisted

    def select(self, digest, identifiers, actor):
        plan = self.store.get(digest)
        if not plan or plan.get('preparation_version') != 1:
            raise ValueError('Create a committed preparation plan first.')
        plan['selected_checks'] = environment_recipes.select(plan, identifiers, actor)
        plan['digest'] = environment_plan.digest(plan)
        self.store.save(plan)
        return plan

    def authorize_restore(self, digest, allowed, actor):
        """An explicit administrator grant is distinct from choosing checks."""
        plan = self.store.get(digest)
        if not plan or plan.get('preparation_version') != 1 or type(allowed) is not bool:
            raise ValueError('Select a committed preparation plan and a boolean restore permission.')
        plan['restore_permission'] = dict(allowed=allowed, actor=actor)
        plan['digest'] = environment_plan.digest(plan)
        self.store.save(plan)
        return plan

    def prepare(self, digest, retry=False):
        plan = self.store.get(digest)
        if not plan or plan.get('preparation_version') != 1:
            raise ValueError('Create a committed preparation plan first.')
        if any(item.get('activation') != 'available' or not item.get('artifact') for item in plan['requirements']):
            raise ValueError('This plan contains requirements without a managed acquisition/activation adapter.')
        job = self.store.enqueue(digest, retry=retry)
        self.wake.set()
        return job

    def run(self):
        while not self.stop.is_set():
            self.wake.clear()
            try:
                if self.admission is not None:
                    self.admission.tick()
                progressed = self.tick()
            except (OSError, sqlite3.Error):
                logging.exception('Environment preparation storage is unavailable; retrying after backoff.')
                progressed = False
            if not progressed:
                self.wake.wait(5)

    def tick(self):
        job = self.store.claim(self.owner)
        if job is None:
            return False
        receipts = []
        contexts = ExitStack()
        lost = threading.Event()
        finished = threading.Event()

        def renew():
            while not finished.wait(20):
                try:
                    live = self.store.heartbeat(job['id'], self.owner)
                except (OSError, sqlite3.Error):
                    live = False
                if not live:
                    lost.set()
                    return

        heartbeat = threading.Thread(target=renew, name='environment-lease', daemon=True)
        heartbeat.start()

        def cancelled():
            return self.stop.is_set() or lost.is_set() or not self.store.owns(job['id'], self.owner)

        try:
            plan = self.store.get(job['digest'])
            if not plan or plan['digest'] != environment_plan.digest(plan):
                raise ValueError('The stored environment plan failed its integrity check.')
            checkout = self.analysis.project(plan['project'])
            projected = plan.get('verification_scope') == 'isolated_commit'
            expected = {}
            if projected:
                # Only SDK-selection files and directories are needed by version
                # probes. Export blobs directly: checkout hooks/smudge filters
                # must not run with SDK-acquisition permission.
                checkout = Path(contexts.enter_context(tempfile.TemporaryDirectory(prefix='herdr-sdk-context-')))
                import project_analysis
                source = self.analysis.project(plan['project'])
                read = project_analysis.committed_reader(source, plan['revision'])
                for relative in selection_files(plan):
                    blob = read(relative)
                    if blob is not None:
                        if len(blob) > 256 * 1024:
                            raise ValueError('An SDK selection file exceeds the manifest limit.')
                        destination = checkout / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(blob)
                        expected[relative] = blob
                for item in plan['requirements']:
                    for component in item['components']:
                        directory = Path(component)
                        if directory.is_absolute() or '..' in directory.parts:
                            raise ValueError('An SDK component must remain inside its recorded checkout.')
                        (checkout / directory).mkdir(parents=True, exist_ok=True)

            def verify_source():
                if projected:
                    project_git.git(source, 'cat-file', '-e', plan['revision'] + '^{commit}')
                    for relative in selection_files(plan):
                        path = checkout / relative
                        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != expected.get(relative):
                            raise ValueError('The isolated SDK selection changed during preparation.')
                    return
                if project_git.git(checkout, 'rev-parse', '--verify', 'HEAD^{commit}').strip() != plan['revision']:
                    raise ValueError('The project revision changed; create a new preparation plan.')
                if project_git.git(checkout, 'status', '--porcelain=v1', '--untracked-files=no').strip():
                    raise ValueError('Contextual preparation verification requires a clean committed checkout.')
                # Untracked notes cannot alter a probe. New/ignored selection
                # files can shadow committed ancestors, so check those narrowly.
                for relative in selection_files(plan):
                    path = checkout / relative
                    if path.exists() or path.is_symlink():
                        record = project_git.git(checkout, 'ls-tree', plan['revision'], '--', ':(literal)' + relative)
                        if not record.startswith(('100644 blob ', '100755 blob ')):
                            raise ValueError('An uncommitted SDK selection file changes contextual verification: ' + relative)

            verify_source()
            acquired = {}
            for item in plan['requirements']:
                if item.get('adapter_version', 1) != environment_adapters.get(item['capability']).version:
                    raise ValueError('The SDK adapter contract changed; create a new plan.')
                if cancelled():
                    raise ValueError('Environment preparation was cancelled.')
                key = environment_plan.canonical(item['artifact'])
                if key not in acquired:
                    acquired[key] = self.acquire(item['artifact'], self.root, cancelled=cancelled)
                receipt = acquired[key]
                # Downloads can take minutes. Check again before reading cwd-
                # dependent SDK configuration, and before certifying the result.
                verify_source()
                for component in item['components']:
                    cwd = checkout / component
                    executable = receipt['probe']['executable']
                    adapter = environment_adapters.get(item['capability'])
                    arguments = adapter.context_arguments
                    environment = dict(os.environ, **adapter.environment(executable))
                    environment['PATH'] = str(Path(executable).parent) + os.pathsep + environment.get('PATH', '')
                    measured = subprocess.run([executable, *arguments], cwd=cwd, env=environment,
                        capture_output=True, text=True, timeout=30)
                    if measured.returncode or item['version'] not in re.findall(r'[0-9]+\.[0-9]+\.[0-9]+', measured.stdout):
                        raise ValueError(f'The installed SDK was not selected in component {component}.')
                receipts.append(dict(capability=item['capability'], version=item['version'],
                                     components=item['components'], **receipt))
            verify_source()
            if cancelled():
                raise ValueError('Environment preparation was cancelled before completion.')
            self.store.finish(job['id'], self.owner, 'ready', receipts=receipts)
        except Exception as error:
            if self.stop.is_set():
                self.store.requeue(job['id'], self.owner)
            else:
                self.store.finish(job['id'], self.owner, 'failed', receipts=receipts,
                                  error=(str(error) or type(error).__name__)[:1000])
        finally:
            finished.set()
            heartbeat.join(timeout=1)
            contexts.close()
        return True


def selection_files(plan):
    found = set()
    for item in plan['requirements']:
        names = environment_adapters.get(item['capability']).selection_files
        for component in item.get('components', []):
            directory = Path(component)
            if directory.is_absolute() or '..' in directory.parts:
                raise ValueError('Environment components require repository-relative directories.')
            while True:
                found.update((directory / name).as_posix() for name in names)
                if directory == Path('.'):
                    break
                directory = directory.parent
    return sorted(found)
