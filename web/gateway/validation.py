"""Exact-commit validation runs with downloadable logs.

A run materializes an integration event's target commit in a detached Git
worktree beside the managed projects directory, executes one of the fixed
validation scripts found in that worktree, and keeps bounded output for
download. It never touches the checkout the coordinator may deliver: no
branch is switched, no merge is performed, and the worktree is removed when
the run finishes.
"""
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import shutil
import threading
import tarfile
import time

import project_git
from project_files import project_directory
import toolchain_policy
from build_queue import BuildQueueBusy
COMMANDS = ('scripts/validate.sh', 'scripts/build-web.sh')
BASH = 'bash'
EVENT_ID = re.compile(r'[a-f0-9]{64}')
RUN_ID = re.compile(r'[a-f0-9]{32}')
TARGET = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
TIMEOUT_SECONDS = 1800
OUTPUT_LIMIT = 200_000
HISTORY = 20
ARTIFACT_LIMIT = 256 * 1024 * 1024


class ValidationBusy(ValueError):
    """Gateway capacity refused submission before any validation was started."""


def stamp():
    return datetime.now(timezone.utc).isoformat()


def environment_comparison(run, policy):
    """Missing bindings fail closed; legacy sentinels are not policy identities."""
    plan, frozen = run.get('frozen_plan_digest'), run.get('frozen_policy_digest')
    legacy_missing = 'unavailable' in (plan, frozen)
    plan = None if plan == 'unavailable' else plan
    frozen = None if frozen == 'unavailable' else frozen
    missing = legacy_missing or bool(run.get('environment_binding_required') and (not plan or not frozen))
    transition = run.get('policy_transition') or {}
    target_mismatch = (transition.get('kind') == 'candidate_validation'
                       and transition.get('candidate') != run.get('target'))
    current = toolchain_policy.digest(policy)
    matches = not (missing or target_mismatch or (plan and (plan != policy.get('plan_digest') or frozen != current))
                   or (frozen and frozen != current))
    return dict(frozen_plan_digest=plan, frozen_policy_digest=frozen,
                approved_plan_digest=policy.get('plan_digest'), approved_policy_digest=current,
                binding_missing=missing, target_mismatch=target_mismatch, matches=matches)


def toolchain_message(mismatch):
    """The refusal an agent can act on: what was required, and what was found.

    Phrased for the agent that is waiting on the delivery, because the useful
    outcome is a retry under the configured SDK rather than a silent failure.
    """
    first = mismatch[0]
    return (f"{first.get('component', '.')}: required {first['name'].capitalize()} {first['required']}; "
            f"detected {first['actual'] or 'no installed SDK'}. "
            'Verification did not run. Use the configured SDK and retry. '
            + 'Affected components: ' + ', '.join(sorted({item.get('component', '.') for item in mismatch}))[:500])


class ValidationRuns:
    def __init__(self, projects, lookup, record=None, root=None, timeout=TIMEOUT_SECONDS, queue=None, external=False, minimum_free_bytes=0):
        self.projects = Path(projects)
        self.lookup = lookup
        self.record = record
        self.root = Path(root) if root else self.projects.parent / 'herdr-validation'
        self.timeout = timeout
        self.lock = threading.Lock()
        self.install_lock = threading.RLock()
        self.install_guard = None
        # run.json is replaced atomically, but on Windows os.replace fails while
        # a concurrent reader holds the target open. Serialize our own readers and
        # writers, and retry briefly for readers outside this process.
        self.files = threading.Lock()
        self.queue = queue
        self.external = external or queue is not None
        self.minimum_free_bytes = minimum_free_bytes
        # Durable runs cannot remain "running" after the gateway process exits.
        for run in self._history():
            if run.get('state') == 'running' and not self.external:
                folder = self.root / run['id']
                run.update(state='interrupted', finished_at=stamp(), note='Gateway restarted; inspect retained logs before retrying.')
                self._write(folder, run)

    def _write(self, folder, run):
        with self.files:
            temporary = folder / 'run.tmp'
            temporary.write_text(json.dumps(run, indent=2))
            for attempt in range(50):
                try:
                    temporary.replace(folder / 'run.json')
                    return
                except PermissionError:
                    if attempt == 49:
                        temporary.unlink(missing_ok=True)
                        raise
                    time.sleep(0.005)

    def _read(self, folder):
        with self.files:
            try:
                return json.loads((folder / 'run.json').read_text())
            except (OSError, ValueError):
                return None

    def _history(self):
        try:
            folders = [f for f in self.root.iterdir() if f.is_dir() and not f.is_symlink() and RUN_ID.fullmatch(f.name)]
        except OSError:
            return []
        runs = [self._read(folder) for folder in folders]
        runs = [run for run in runs if isinstance(run, dict) and RUN_ID.fullmatch(str(run.get('id', '')))]
        if self.queue is not None:
            for index, run in enumerate(runs):
                queued = self.queue.get(run['id'])
                if queued:
                    # The queue is authoritative if the service claimed a job
                    # before the submitting gateway published its run file.
                    if queued['state'] in ('queued', 'running'):
                        run['state'] = queued['state']
                    else:
                        runs[index] = dict(queued)
                        if run.get('storage_finish'):
                            runs[index]['storage_finish'] = run['storage_finish']
        return sorted(runs, key=lambda run: str(run.get('started_at', '')), reverse=True)

    def running(self):
        return [run for run in self._history() if run.get('state') == 'running']

    def storage_records(self):
        """All durable run identities; no feedback delivery or history truncation."""
        return self._history()

    def update_storage_finish(self, identity, value):
        """Persist retention diagnostics without rewriting build/queue evidence."""
        if not isinstance(identity, str) or not RUN_ID.fullmatch(identity):
            raise ValueError('Invalid validation run ID.')
        folder = self.root / identity
        if folder.is_symlink():
            raise ValueError('Validation storage diagnostics cannot follow a linked run.')
        run = self._read(folder)
        authoritative = self.queue.get(identity) if self.queue is not None else run
        if (not run or run.get('id') != identity or not authoritative or
                authoritative.get('state') not in ('complete', 'failed', 'error', 'cancelled', 'interrupted')):
            raise ValueError('Storage finalization requires a terminal validation run.')
        run['storage_finish'] = value
        self._write(folder, run)

    def snapshot(self):
        runs = self._history()
        if self.queue is not None and self.record is not None:
            # Watcher and dashboard polls can overlap; one lock prevents a
            # result from being audited twice before its marker is written.
            with self.lock:
                for run in runs:
                    if run.get('state') not in ('complete', 'failed', 'error', 'interrupted', 'cancelled'):
                        continue
                    queued = self.queue.get(run['id']) or run
                    for event_id in queued.get('event_ids', [run['event_id']]):
                        folder = self.root / run['id']
                        delivered = folder / ('feedback-' + hashlib.sha256(str(event_id).encode()).hexdigest())
                        if delivered.exists():
                            continue
                        try:
                            self._record_result(event_id, run)
                            delivered.write_text(stamp())
                        except (ValueError, OSError, sqlite3.Error):
                            # Retry delivery after transient storage failures; never
                            # redispatch a build just because its feedback was lost.
                            continue
        return {'runs': runs[:HISTORY], 'running': sum(run['state'] == 'running' for run in runs),
                'executor': 'service' if self.queue is not None else 'gateway',
                'queued': sum(run['state'] == 'queued' for run in runs),
                'commands': list(COMMANDS), 'timeout_seconds': self.timeout}

    def log(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError('Invalid validation run ID.')
        folder = self.root / run_id
        if folder.is_symlink() or (folder / 'log.txt').is_symlink():
            raise ValueError('Invalid validation log path.')
        run = self._read(folder)
        if run is None:
            raise ValueError('Validation run not found.')
        try:
            content = (folder / 'log.txt').read_text(errors='replace')
        except OSError:
            content = 'No log was recorded for this run.\n'
        return run, content

    def artifact(self, run_id, kind='static'):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError('Invalid validation run ID.')
        folder = self.root / run_id
        if kind not in ('static', 'deployment'):
            raise ValueError('Invalid artifact kind.')
        archive = folder / ('artifact.tar.gz' if kind == 'static' else 'deployment.tar.gz')
        if folder.is_symlink() or archive.is_symlink():
            raise ValueError('Invalid artifact path.')
        run = self._read(folder)
        manifest = run.get('artifact' if kind == 'static' else 'deployment_package') if isinstance(run, dict) else None
        if not isinstance(manifest, dict) or run.get('state') != 'complete':
            raise ValueError('No retained artifact is available.')
        if manifest.get('build_id') != run_id or manifest.get('target', manifest.get('source_sha')) != run.get('target'):
            raise ValueError('Artifact identity mismatch.')
        try:
            if archive.stat().st_size != manifest.get('bytes') or archive.stat().st_size > ARTIFACT_LIMIT + 1_000_000:
                raise ValueError('Artifact size mismatch.')
            digest = hashlib.sha256()
            with archive.open('rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    digest.update(chunk)
            if digest.hexdigest() != manifest.get('sha256'):
                raise ValueError('Artifact checksum mismatch.')
        except OSError as error:
            raise ValueError('Retained artifact is unavailable.') from error
        return archive, manifest

    def submit(self, body, actor='dashboard_operator'):
        # Installation restarts the gateway. Serialize its queueing with build
        # submission so neither can start while the other owns the executor.
        with self.install_lock:
            if self.install_guard:
                self.install_guard()
            return self._submit(body, actor)

    def _submit(self, body, actor):
        if not isinstance(body, dict) or not EVENT_ID.fullmatch(str(body.get('id', ''))):
            raise ValueError('Select an integration event to validate.')
        event = self.lookup(body['id'])
        if not isinstance(event, dict):
            raise ValueError('Integration event not found.')
        target = event.get('target')
        if not isinstance(target, str) or not TARGET.fullmatch(target):
            raise ValueError('This event has no exact commit to validate.')
        if type(body.get('retry', False)) is not bool:
            raise ValueError('Retry must be a boolean.')
        with self.lock:
            if self.running() and self.queue is None:
                raise ValidationBusy('Validation is already running. Wait for it to finish.')
            run = dict(id=secrets.token_hex(16), event_id=event['id'], repository=event.get('repository'),
                       target=target, path=event.get('path'), actor=actor, state='running',
                       started_at=stamp(), finished_at=None, exit_code=None, command=None, note='')
            run['task_id'] = event.get('task_id') or event.get('job_id')
            run['frozen_plan_digest'] = event.get('frozen_plan_digest')
            run['frozen_policy_digest'] = event.get('frozen_policy_digest')
            run['environment_binding_required'] = event.get('environment_binding_required', False)
            if event.get('policy_transition'):
                run['policy_transition'] = event['policy_transition']
            # Preserve provenance without turning an event waiver into a pass.
            if event.get('state') == 'validation_waived':
                run['validation_waiver'] = dict(event_id=event['id'], approval=event.get('operator_approval'),
                                               reason=event.get('reason'))
            if self.queue is not None:
                if shutil.disk_usage(self.projects).free < self.minimum_free_bytes:
                    raise ValueError('Insufficient free disk space to queue a build.')
                # The queue's identity must name the approved toolchain, not a
                # default: two runs under different SDKs are different builds.
                approved = toolchain_policy.resolve_committed(self._checkout(event.get('path')))
                toolchain = ','.join(f"{entry['name']}-{entry['required']}"
                                     for entry in approved['toolchains']) or 'none'
                run['policy_digest'] = toolchain_policy.digest(approved)
                run['plan_digest'] = approved.get('plan_digest')
                try:
                    run, created = self.queue.enqueue(run, toolchain=toolchain, retry=body.get('retry', False))
                except BuildQueueBusy as error:
                    raise ValidationBusy(str(error)) from error
                if not created:
                    return run
            folder = self.root / run['id']
            folder.mkdir(parents=True, exist_ok=True)
            self._write(folder, run)
        if self.queue is None:
            threading.Thread(target=self._execute, args=(run, folder), daemon=True).start()
        return dict(run)

    def _execute(self, run, folder):
        from operation_metrics import measured
        worktree = folder / 'tree'
        log_path = folder / 'log.txt'
        lines, state, exit_code = [], 'error', None
        try:
            checkout = self._checkout(run['path'])
            timings = run.setdefault('timings', {})
            with measured('workspace.prepare') as timings['workspace']:
                self._worktree(checkout, worktree, run['target'])
            head = project_git.git(worktree, 'rev-parse', 'HEAD').strip()
            if head != run['target']:
                raise ValueError('The worktree does not match the exact target commit.')
            # The policy is resolved from the approved checkout, never from the
            # candidate: a candidate must not redefine the rules that judge it.
            policy = toolchain_policy.resolve_committed(checkout)
            run['environment_comparison'] = environment_comparison(run, policy)
            if run.get('policy_digest') and run['policy_digest'] != toolchain_policy.digest(policy):
                run['blocker'] = 'policy_changed'
                run['recovery'] = 'retry_validation'
                raise ValueError('Approved policy changed while this build was queued. Submit verification again.')
            run['policy'] = policy
            run['policy_digest'] = toolchain_policy.digest(policy)
            run['plan_digest'] = policy.get('plan_digest')
            try:
                run['policy_changes'] = toolchain_policy.changes(policy, toolchain_policy.candidate_policy(worktree, policy))
            except ValueError:
                run.update(blocker='policy_changed', recovery='review_protected_inputs')
                raise
            if run['policy_changes']:
                run['blocker'] = 'policy_changed'
                run['recovery'] = 'review_protected_inputs'
                raise ValueError('Candidate changes protected toolchain policy files; explicit review is required: '
                                 + ', '.join(run['policy_changes']))
            if not run['environment_comparison']['matches']:
                run.update(blocker='policy_changed', recovery='review_validation_environment')
                lines.append('Environment policy comparison: ' + json.dumps(run['environment_comparison'], sort_keys=True) + '\n')
                reason = ('The recorded session environment binding is missing. '
                          if run['environment_comparison']['binding_missing'] else
                          'The approved environment differs from the session policy. ')
                raise ValueError(reason +
                                 'Open Checks & builds and choose Review validation environment for this candidate.')
            # Probe the real executables before running anything. A declared pin
            # cannot prove which SDK runs, so verification measures it.
            if policy.get('blockers') or policy.get('validation_blockers'):
                run['blocker'] = 'environment_unavailable' if policy.get('blockers') else 'validation_unapproved'
                raise ValueError('; '.join([*policy.get('blockers', []), *policy.get('validation_blockers', [])]))
            from environment_execution import materialize_execution_policy
            with measured('execution.prepare') as timings['environment']:
                execution_policy, restores = materialize_execution_policy(policy, worktree, folder / 'environment',
                    authorized=policy.get('restore_authorized') is True)
            if restores:
                run['environment_restores'] = restores
                self._write(folder, run)
            probed = toolchain_policy.verify(execution_policy, root=worktree)
            run['toolchain_evidence'] = probed
            mismatch = [item for item in probed if not item['matches']]
            if mismatch:
                run['blocker'] = 'missing_toolchain'
                raise ValueError(toolchain_message(mismatch))
            commands = execution_policy.get('recipes') if policy.get('plan_digest') else self._commands(worktree)
            command = ' + '.join(item['id'] if isinstance(item, dict) else item for item in commands)
            if not commands:
                # A plan-based policy selects recipes, so citing Herdr's own
                # script names here would describe a rule that did not apply.
                raise ValueError('No approved validation recipe is selected for this environment.'
                                 if policy.get('plan_digest') else
                                 'No validation script found (looked for ' + ', '.join(COMMANDS) + ').')
            run['command'] = command
            lines.append(f"Validating {run['target']} in an isolated worktree with {command}.\n\n")
            from check_runner import run_scripts, required_passed
            def evidence(checks):
                run['checks'] = checks
                self._write(folder, run)
            with measured('validation.checks') as timings['checks']:
                storage_bindings = []
                checks, exit_code, timed_out = run_scripts(worktree, commands, folder,
                    self.timeout, BASH, OUTPUT_LIMIT, changed=evidence,
                    environment=(None if policy.get('plan_digest') else toolchain_policy.environment(policy)),
                    storage_projects=self.projects, storage_bindings=storage_bindings)
                if exit_code != 0 or timed_out:
                    timings['checks']['status'] = 'failed'
            # Repository scripts can report subchecks, but cannot certify that
            # they left the requested commit and tracked source unchanged.
            checks.extend(self._integrity_checks(worktree, run['target']))
            if not required_passed(checks) and exit_code == 0:
                exit_code = 1
            run.update(checks=checks, required_checks_verified=required_passed(checks))
            state = 'error' if timed_out else 'complete' if exit_code == 0 else 'failed'
            lines.append(log_path.read_text(errors='replace'))
            if exit_code == 0:
                try:
                    run['artifact'] = self._retain_artifact(worktree, folder, run)
                except (ValueError, OSError) as error:
                    # The build passed; only retention failed. Keep that distinct
                    # from a validation failure and without deployable evidence.
                    run['artifact_error'] = str(error)[:500]
                    lines.append(f'Artifact retention failed: {error}\n')
                try:
                    from deployment_package import retain
                    run['deployment_package'] = retain(worktree, folder, dict(run, exit_code=exit_code))
                except (ValueError, OSError) as error:
                    run['deployment_package_error'] = str(error)[:500]
        except (ValueError, OSError) as error:
            run['note'] = str(error)[:2000]
            lines.append(f'Validation could not run: {error}\n')
        finally:
            self._cleanup(checkout=locals().get('checkout'), worktree=worktree)
            try:
                import execution_storage
                for identity in {binding['execution_id'] for binding in locals().get('storage_bindings', [])}:
                    execution_storage.close_execution(identity, projects=self.projects)
            except (OSError, ValueError):
                pass
            run.update(state=state, exit_code=exit_code, finished_at=stamp())
            content = ''.join(lines)[-OUTPUT_LIMIT:]
            try:
                log_path.write_bytes(content.encode('utf-8')[-OUTPUT_LIMIT:])
                self._write(folder, run)
            except OSError:
                pass
            if self.record is not None:
                try:
                    self._record_result(run['event_id'], run)
                except (ValueError, OSError, sqlite3.Error):
                    pass

    def _record_result(self, event_id, run):
        self.record(event_id, dict(run_id=run['id'], state=run['state'], target=run['target'],
                                   command=run.get('command'), exit_code=run.get('exit_code'), checks=run.get('checks', []),
                                   required_checks_verified=run.get('required_checks_verified', False),
                                   policy_digest=run.get('policy_digest'), plan_digest=run.get('plan_digest'),
                                   toolchain_evidence=run.get('toolchain_evidence'),
                                   environment_restores=run.get('environment_restores'),
                                   timings=run.get('timings', {}),
                                   policy_changes=run.get('policy_changes'),
                                   environment_comparison=run.get('environment_comparison'),
                                   policy_transition=run.get('policy_transition'),
                                   blocker=run.get('blocker'), recovery=run.get('recovery'), note=run.get('note'),
                                   finished_at=run.get('finished_at'),
                                   actor=run.get('actor')))

    def _integrity_checks(self, worktree, target):
        results = []
        for identifier, arguments, expected in (
                ('exact-commit', ('rev-parse', 'HEAD'), target),
                ('worktree-clean', ('status', '--porcelain=v1', '--untracked-files=no'), '')):
            started = time.monotonic()
            try:
                actual = project_git.git(worktree, *arguments).strip()
                passed, detail = actual == expected, actual[:500]
            except (ValueError, OSError) as error:
                passed, detail = False, str(error)[:500]
            results.append(dict(id='runner:' + identifier, name=identifier, source='runner', required=True,
                                command='git ' + ' '.join(arguments), duration_ms=int((time.monotonic() - started) * 1000),
                                status='passed' if passed else 'failed', exit_code=0 if passed else 1,
                                summary='Requested source preserved.' if passed else detail))
        return results

    def _checkout(self, value):
        path = Path(str(value))
        if not path.is_absolute():
            path = Path(self.projects) / path
        return project_directory(self.projects, path)

    def _retain_artifact(self, worktree, folder, run):
        """Retain static output only; this is evidence, not deployment authority."""
        source = worktree / 'web/public'
        if not source.is_dir():
            return None
        if source.is_symlink() or source.parent.is_symlink():
            raise ValueError('Build artifact must not use symlinks.')
        files, total = [], 0
        # followlinks=False keeps a linked directory from being traversed or
        # looped before the symlink itself is rejected.
        for current, directories, names in os.walk(source, followlinks=False):
            current = Path(current)
            directories.sort()
            for name in directories:
                if (current / name).is_symlink():
                    raise ValueError('Build artifact must not use symlinks.')
            for name in sorted(names):
                path = current / name
                if path.is_symlink():
                    raise ValueError('Build artifact must not use symlinks.')
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError('Build artifact requires regular files.')
                total += metadata.st_size
                if total > ARTIFACT_LIMIT:
                    raise ValueError('Build artifact exceeds the retention limit.')
                files.append(path)
                if len(files) > 20000:
                    raise ValueError('Build artifact exceeds the file count limit.')
        temporary = folder / 'artifact.tmp'
        with tarfile.open(temporary, 'w:gz') as archive:
            for path in files:
                archive.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
        digest = hashlib.sha256()
        with temporary.open('rb') as artifact:
            for chunk in iter(lambda: artifact.read(65536), b''):
                digest.update(chunk)
        manifest = dict(build_id=run['id'], target=run['target'], command=run['command'],
                        repository=run['repository'], created_at=stamp(),
                        sha256=digest.hexdigest(), bytes=temporary.stat().st_size,
                        file_count=len(files), validation_exit_code=0,
                        checks=run.get('checks', []), required_checks_verified=run.get('required_checks_verified', False),
                        toolchain_pin=run.get('toolchain_pin'),
                        policy_digest=run.get('policy_digest'), plan_digest=run.get('plan_digest'),
                        toolchain_evidence=run.get('toolchain_evidence'),
                        policy_changes=run.get('policy_changes'),
                        task_id=run.get('task_id'), validation_waiver=run.get('validation_waiver'),
                        deployment_authorized=False)
        temporary.replace(folder / 'artifact.tar.gz')
        (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        return manifest

    def _worktree(self, checkout, worktree, target):
        project_git.git(checkout, 'worktree', 'add', '--detach', str(worktree), target, timeout=max(self.timeout, 60))

    def _commands(self, worktree):
        commands = []
        for candidate in COMMANDS:
            if (worktree / candidate).is_file() and not (worktree / candidate).is_symlink() and not (worktree / 'scripts').is_symlink():
                commands.append(candidate)
        return commands

    def _cleanup(self, checkout, worktree):
        if checkout is not None and checkout.exists():
            from repository_lock import repository_lock
            try:
                with repository_lock(checkout):
                    self._cleanup_locked(checkout, worktree)
            except (ValueError, OSError):
                # Leave the recoverable temporary checkout rather than race a
                # mutation after a lock timeout. A later cleanup can prune it.
                pass
        else:
            self._cleanup_locked(checkout, worktree)

    def _cleanup_locked(self, checkout, worktree):
        if worktree.exists() and checkout is not None and checkout.exists():
            try:
                project_git.git(checkout, 'worktree', 'remove', '--force', str(worktree), timeout=60)
            except (ValueError, OSError):
                shutil.rmtree(worktree, ignore_errors=True)
        elif worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
        if checkout is not None and checkout.exists():
            try:
                project_git.git(checkout, 'worktree', 'prune', timeout=30)
            except (ValueError, OSError):
                pass
