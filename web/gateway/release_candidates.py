"""Pinned release manifests and recoverable integration, separate from task builds.

The repository lock is the cross-process worker claim. Git intent is committed
to SQLite before merging; a restart verifies the resulting parents before
accepting a merge whose result was not yet checkpointed. No task is marked
merged merely because its commit was incorporated into a release.
"""
from contextlib import closing, nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid

import project_git
from project_files import project_directory
from repository_lock import repository_lock
import toolchain_policy

ACTIVE = ('integrating', 'validating', 'repair_queued', 'repairing', 'publishing', 'excluding')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ReleaseChanged(ValueError):
    """A callback or administrator advanced this record during a worker step."""


class ReleaseCandidates:
    def __init__(self, service):
        self.service = service
        self.root = service.projects.parent / 'herdr-releases'
        self.stopped, self.wake = threading.Event(), threading.Event()
        self.thread = None
        with closing(service.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS release_candidates (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS release_requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS release_revisions (id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    def start(self):
        self.thread = threading.Thread(target=self.poll, daemon=True, name='release-integration')
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)

    def poll(self):
        while not self.stopped.is_set():
            self.wake.clear()
            # The UI's recent-history limit must not starve older active runs.
            try:
                with closing(self.service.connect()) as db:
                    identifiers = [row[0] for row in db.execute(
                        "SELECT id FROM release_candidates WHERE json_extract(data, '$.state') IN (" +
                        ','.join('?' for _ in ACTIVE) + ') ORDER BY rowid', ACTIVE)]
            except (OSError, sqlite3.Error):
                self.wake.wait(2)
                continue
            for identifier in identifiers:
                if self.stopped.is_set():
                    break
                try:
                    self.advance(identifier)
                except (ValueError, OSError, sqlite3.Error):
                    # A busy repository or storage outage is retried. Other
                    # refusals are recorded by advance while holding its claim.
                    pass
            self.wake.wait(2)

    def snapshot(self, tasks=None):
        with closing(self.service.connect()) as db:
            releases = [json.loads(row[0]) for row in db.execute(
                'SELECT data FROM release_candidates ORDER BY rowid DESC LIMIT 100')]
            tasks = dict(tasks or {})
            identifiers = {member['task_id'] for release in releases if release['state'] == 'collecting'
                           for member in release.get('members', [])} - tasks.keys()
            tasks.update(self.service.task_records(identifiers, db))
            return [self.present(release, tasks) for release in releases]

    def get(self, identifier, db=None):
        if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
            raise ValueError('Select a release candidate.')
        if db is None:
            with closing(self.service.connect()) as connection:
                return self.get(identifier, connection)
        row = db.execute('SELECT data FROM release_candidates WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Release candidate not found.')
        return json.loads(row[0])

    def save(self, release, db=None):
        release['updated_at'] = stamp()
        if db is None:
            with closing(self.service.connect()) as connection, connection:
                connection.execute('BEGIN IMMEDIATE')
                self.save(release, connection)
        else:
            prior = db.execute('SELECT data FROM release_candidates WHERE id=?', (release['id'],)).fetchone()
            def meaningful(value):
                return {key: item for key, item in value.items() if key not in ('updated_at', 'record_version', 'audit')}
            notify = not prior or meaningful(json.loads(prior[0])) != meaningful(release)
            expected = release.get('record_version', 0)
            release['record_version'] = expected + 1
            if expected:
                changed = db.execute('UPDATE release_candidates SET data=? WHERE id=? AND '
                    "json_extract(data, '$.record_version')=?", (json.dumps(release), release['id'], expected)).rowcount
                if not changed:
                    raise ReleaseChanged('Release advanced during this operation; refresh before retrying.')
            else:
                db.execute('INSERT INTO release_candidates VALUES (?,?)', (release['id'], json.dumps(release)))
            revision = release.get('revision')
            if (isinstance(revision, dict) and isinstance(revision.get('digest'), str)
                    and {'generation', 'release_id', 'base_sha', 'policy_digest', 'members'} <= revision.keys()):
                db.execute('INSERT OR REPLACE INTO release_revisions VALUES (?,?)',
                           (revision['digest'], json.dumps(release)))
            # Incorporation results are meaningful live progress. Audit-only
            # writes and repeated saves do not invalidate every dashboard.
            if notify and hasattr(self.service, 'events'):
                from notifications import record
                record(db, 'release', release['id'])

    def present(self, release, tasks=None, db=None):
        revision = release.get('revision')
        revision = revision if isinstance(revision, dict) else {}
        members = revision.get('members', release.get('members', []))
        results = release.get('results', {})
        release['members'] = [dict(member, **results.get(member['task_id'], {})) for member in members]
        if release['state'] == 'collecting':
            if tasks is None:
                tasks = self.service.task_records([member['task_id'] for member in members], db)
            for member in release['members']:
                task = tasks.get(member['task_id'])
                if task is None:
                    member['eligibility_reason'] = 'Task is no longer available.'
                    continue
                sha = member.get('candidate_sha')
                evidence = task.get('builds', {}).get(sha, {})
                if not sha:
                    reason = 'Capture a task candidate before freezing.'
                elif sha != task.get('head_sha'):
                    reason = 'Candidate changed. Remove and add this task to review its new SHA.'
                elif evidence.get('target') != sha or evidence.get('state') != 'complete' or evidence.get('required_checks_verified') is not True:
                    reason = 'Required checks have not passed for this selected commit.'
                else:
                    reason = 'Selected commit passed its checks; current policy and protected inputs are checked when freezing.'
                member['eligibility_reason'] = reason
        counts = {state: 0 for state in ('pending', 'integrating', 'incorporated', 'blocked', 'excluded')}
        for member in release['members']:
            state = member.get('state', 'pending')
            counts[state] = counts.get(state, 0) + 1
        release['counts'] = dict(counts, selected=len(members))
        return release

    def action(self, action, body, actor, role):
        from request_identity import TASK_REQUEST
        if role != 'admin':
            raise ValueError('Only an administrator can manage release candidates.')
        if not isinstance(body, dict) or not TASK_REQUEST.fullmatch(str(body.get('request_id', ''))):
            raise ValueError('A stable request ID is required.')
        if action not in ('create', 'membership', 'freeze', 'revise', 'retry', 'resume', 'repair', 'exclude', 'publish'):
            raise ValueError('Unknown release action.')
        fingerprint = digest([action, body, actor])
        # Request replay and all manifest edits commit together. Network effects
        # run later, outside this transaction, under the repository claim.
        claim = (repository_lock(self.service.path_for(self.get(body.get('id'))))
                 if action == 'freeze' else nullcontext())
        with claim:
            prepared, inputs = None, None
            if action == 'freeze':
                with closing(self.service.connect()) as db:
                    replay = self.replay(db, body['request_id'], fingerprint)
                    if replay is not None:
                        return replay
                    prepared = self.get(body.get('id'), db)
                    if body.get('edit_version') != prepared['edit_version']:
                        raise ValueError('Release selection changed. Refresh and review it again.')
                    inputs = self.service.task_records([m['task_id'] for m in prepared['members']], db)
                # Git inspection and ref retention can be slow. Hold the
                # repository claim, but never SQLite's global writer slot.
                self.freeze(prepared, inputs)
            return self._action(action, body, actor, fingerprint, prepared, inputs)

    def replay(self, db, identifier, fingerprint):
        old = db.execute('SELECT fingerprint,result FROM release_requests WHERE id=?', (identifier,)).fetchone()
        if old:
            if old[0] != fingerprint:
                raise ValueError('This request ID was used for another release action.')
            return json.loads(old[1])
        return None

    @staticmethod
    def freeze_inputs(tasks):
        """Only admission facts: unrelated audit or worker updates cannot stale a freeze."""
        fields = ('target', 'state', 'required_checks_verified', 'policy_digest')
        return {identifier: dict(repository=task['repository'], head_sha=task.get('head_sha'),
            evidence={key: task.get('builds', {}).get(task.get('head_sha'), {}).get(key) for key in fields})
            for identifier, task in tasks.items()}

    def _action(self, action, body, actor, fingerprint, prepared=None, inputs=None):
        with closing(self.service.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            replay = self.replay(db, body['request_id'], fingerprint)
            if replay is not None:
                return replay
            if action == 'create':
                repository = body.get('repository')
                if not isinstance(repository, str) or not repository:
                    raise ValueError('Select a project repository.')
                path = project_directory(self.service.projects, self.service.projects / repository)
                project_git.git(path, 'rev-parse', '--verify', 'HEAD^{commit}')
                version = body.get('version')
                if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', version):
                    raise ValueError('Enter a release name of up to 64 letters, digits, dots or hyphens.')
                cut = body.get('planned_cut_at') or None
                if cut is not None:
                    if not isinstance(cut, str) or len(cut) > 40:
                        raise ValueError('Enter a valid planned cut date.')
                    try:
                        datetime.fromisoformat(cut)
                    except ValueError:
                        raise ValueError('Enter a valid planned cut date.') from None
                release = dict(id=uuid.uuid4().hex, repository=path.relative_to(self.service.projects).as_posix(),
                               version=version, planned_cut_at=cut, state='collecting', members=[],
                               generation=0, edit_version=0, created_at=stamp(), actor=actor, audit=[])
                if db.execute("SELECT 1 FROM release_candidates WHERE json_extract(data, '$.repository')=? "
                              "AND json_extract(data, '$.version')=? COLLATE NOCASE", (release['repository'], version)).fetchone():
                    raise ValueError('A release with this name already exists in this repository. Revise it or choose another name.')
            else:
                release = self.get(body.get('id'), db)
                if body.get('edit_version') != release['edit_version']:
                    raise ValueError('Release selection changed. Refresh and review it again.')
                if action == 'membership':
                    if release['state'] != 'collecting':
                        raise ValueError('This manifest is frozen. Create another release candidate to change membership.')
                    task = self.service.get(body.get('task_id'))
                    if task['repository'] != release['repository']:
                        raise ValueError('A release can only contain tasks from the same repository.')
                    members = release['members']
                    if type(body.get('included')) is not bool:
                        raise ValueError('Choose whether to include this task.')
                    members[:] = [member for member in members if member['task_id'] != task['id']]
                    if body['included']:
                        if len(members) >= 100:
                            raise ValueError('A release can select at most 100 tasks.')
                        members.append(dict(task_id=task['id'], title=task['title'], candidate_sha=task.get('head_sha')))
                elif action == 'freeze':
                    if release['record_version'] != prepared['record_version']:
                        raise ReleaseChanged('Release changed during freeze; refresh before retrying.')
                    current = self.service.task_records(inputs.keys(), db)
                    if self.freeze_inputs(current) != self.freeze_inputs(inputs):
                        raise ReleaseChanged('Task candidates or validation changed during freeze; review them again.')
                    release = prepared
                elif action == 'revise':
                    if release['state'] not in ('blocked', 'validation_failed', 'ready', 'published'):
                        raise ValueError('Wait for the current release operation to finish before revising.')
                    revision = release.get('revision')
                    if not isinstance(revision, dict) or not isinstance(revision.get('members'), list):
                        raise ValueError('The frozen manifest is unavailable. Inspect this record and create a new release candidate.')
                    members = [dict(m) for m in revision['members']]
                    # Retain the entire previous run in release_revisions. A new
                    # revision never rewrites its evidence, branch or repair.
                    release = {k: release[k] for k in ('id', 'repository', 'version', 'planned_cut_at', 'record_version',
                               'generation', 'edit_version', 'created_at', 'actor', 'audit')}
                    release.update(state='collecting', members=members)
                else:
                    if body.get('revision') != (release.get('revision') or {}).get('digest'):
                        raise ValueError('Select the exact frozen release revision.')
                    if action in ('retry', 'resume'):
                        if release['state'] not in ('blocked', 'validation_failed'):
                            raise ValueError('Only a blocked or failed release can resume.')
                        release.update(state=('publishing' if release.get('failure_phase') == 'publishing' else
                            'integrating' if release.get('intent') or not release.get('resulting_sha') else 'validating'), error='')
                        release['validation_attempt'] = release.get('validation_attempt', 0) + 1
                    elif action == 'repair':
                        if release['state'] != 'blocked' or not release.get('intent'):
                            raise ValueError('Select a release blocked on an integration conflict.')
                        release.update(state='repair_queued', repair_profile_id=body.get('profile_id'),
                                       repair_attempt=release.get('repair_attempt', 0) + 1, error='')
                    elif action == 'exclude':
                        if release['state'] != 'blocked' or not release.get('intent') or body.get('task_id') != release['intent']['task_id']:
                            raise ValueError('Select the blocked member to exclude.')
                        reason = body.get('reason')
                        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
                            raise ValueError('Explain why this selected member is being excluded (up to 1000 characters).')
                        release.update(state='excluding', exclusion_reason=reason.strip())
                    elif action == 'publish':
                        if release['state'] != 'ready' or body.get('head_sha') != release.get('resulting_sha'):
                            raise ValueError('Review the verified combined commit before publishing.')
                        release.update(state='publishing', error='')
            release['edit_version'] += 1
            release['audit'] = [*release['audit'][-49:], dict(action=action, actor=actor, at=stamp())]
            self.save(release, db)
            result = self.present(json.loads(json.dumps(release)), db=db)
            db.execute('INSERT INTO release_requests VALUES (?,?,?)', (body['request_id'], fingerprint, json.dumps(result)))
        self.wake.set()
        return result

    def freeze(self, release, tasks=None):
        from contributions import task_base, SHA, remote_info
        if release['state'] != 'collecting':
            raise ValueError('This release manifest is already frozen.')
        repository = self.service.path_for(release)
        base_ref, base = task_base(repository, '')
        if project_git.git(repository, 'rev-parse', 'HEAD').strip() != base:
            raise ValueError('The approved project checkout must match the configured base before freezing.')
        policy = toolchain_policy.resolve_committed(repository)
        if policy.get('blockers') or policy.get('validation_blockers'):
            raise ValueError('Approve the project environment and required checks before freezing a release.')
        if not release['members']:
            raise ValueError('Select at least one task.')
        if tasks is None:
            tasks = self.service.task_records([member['task_id'] for member in release['members']])
        members = []
        for member in release['members']:
            task = tasks.get(member['task_id'])
            if task is None:
                raise ValueError('A selected task is no longer available.')
            sha = member.get('candidate_sha')
            if task['repository'] != release['repository'] or not SHA.fullmatch(str(sha)) or task.get('head_sha') != sha:
                raise ValueError('A selected task candidate changed. Remove and add it again to review the new SHA.')
            evidence = task.get('builds', {}).get(sha, {})
            if (evidence.get('target') != sha or evidence.get('state') != 'complete'
                    or evidence.get('required_checks_verified') is not True
                    or evidence.get('policy_digest') != toolchain_policy.digest(policy)):
                raise ValueError(task['title'] + ': required checks must pass for the selected SHA under the approved policy.')
            project_git.git(repository, 'cat-file', '-e', sha + '^{commit}')
            # Compare protected inputs to the approved base, not the task's
            # historical base: upstream-only updates must not look authored.
            from pr_repairs import protected_changes
            if protected_changes(repository, base, sha, policy):
                raise ValueError(task['title'] + ': protected policy differs from the approved base; review and update the task.')
            members.append(dict(member))
        generation = release['generation'] + 1
        revision = dict(release_id=release['id'], generation=generation, repository=release['repository'],
                        base_ref=base_ref, base_sha=base, policy_digest=toolchain_policy.digest(policy),
                        plan_digest=policy.get('plan_digest'), members=members)
        revision['digest'] = digest(revision)
        release.update(state='integrating', generation=generation, revision=revision, results={},
                       checkpoint=base, branch='herdr/release-' + release['id'] + '-r' + str(generation))
        try:
            upstream = remote_info(repository)
        except ValueError:
            release['publication_unavailable'] = 'Branch publication requires a configured GitHub origin.'
        else:
            release['publication'] = dict(upstream=upstream, destination=self.service.publishing_default(upstream),
                                          branch=release['branch'])
        revision.update(version=release['version'], planned_cut_at=release.get('planned_cut_at'),
                        publication=release.get('publication'))
        revision['digest'] = digest({k: v for k, v in revision.items() if k != 'digest'})
        # Keep pending candidates reachable even if their task branches advance
        # and Git later expires reflogs. These refs never follow mutable tasks.
        for index, member in enumerate(members):
            # A failed final CAS may leave these refs behind. Digest-qualified
            # names cannot poison a later attempt with a different selection.
            ref = f"refs/herdr/releases/{release['id']}/{revision['digest']}/member-{index}"
            try:
                existing = project_git.git(repository, 'rev-parse', '--verify', ref).strip()
            except ValueError:
                project_git.git(repository, 'update-ref', ref, member['candidate_sha'], '0' * len(base))
            else:
                if existing != member['candidate_sha']:
                    raise ValueError('Retained release member ref differs from the selected candidate.')

    def event(self, identifier):
        with closing(self.service.connect()) as db:
            row = db.execute("SELECT json_extract(data, '$.validation_event') FROM release_candidates "
                             "WHERE json_extract(data, '$.validation_event.id')=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_validation(self, identifier, summary):
        event = self.event(identifier)
        if not event:
            return False
        # Validation callbacks must never wait for the repository lock: submit
        # and result delivery may overlap. Only SQLite is needed for the receipt.
        with closing(self.service.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            release = self.get(event['release_id'], db)
            if release['state'] != 'validating' or (release.get('validation_event') or {}).get('id') != identifier:
                return True  # A late callback cannot reopen a terminal or revised run.
            if summary.get('target') != release.get('resulting_sha'):
                release.update(state='validation_failed', error='Release validation evidence does not match the combined commit.')
                self.save(release, db)
                return True  # Consume foreign evidence; never retry it as a pass.
            release['validation'] = summary
            good = (summary.get('state') == 'complete' and summary.get('required_checks_verified') is True
                    and summary.get('policy_digest') == release['revision']['policy_digest'])
            release.update(state='ready' if good else 'validation_failed',
                           error='' if good else str(summary.get('note') or 'Combined required checks did not pass.')[:2000])
            self.save(release, db)
        return True

    def worktree(self, release):
        return self.root / (release['id'] + '-r' + str(release['generation']))

    def history(self, identifier, *, revision=None, before=None):
        """Bounded, release-scoped audit reads; never a replay or restore action."""
        current = self.get(identifier)
        with closing(self.service.connect()) as db:
            if revision is not None:
                if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{64}', revision):
                    raise ValueError('Select a full retained revision digest.')
                row = db.execute("SELECT data FROM release_revisions WHERE id=? AND json_extract(data, '$.id')=?",
                                 (revision, identifier)).fetchone()
                if not row:
                    raise ValueError('Retained revision not found for this release.')
                return dict(release=self.present(json.loads(row[0])), read_only=True)
            if before is not None and (type(before) is not int or not 1 <= before <= 2**63 - 1):
                raise ValueError('Select a positive revision history cursor.')
            rows = db.execute("SELECT data FROM release_revisions WHERE json_extract(data, '$.id')=? " +
                ("AND json_extract(data, '$.revision.generation')<? " if before is not None else '') +
                "ORDER BY json_extract(data, '$.revision.generation') DESC LIMIT 21",
                (identifier, before) if before is not None else (identifier,)).fetchall()
        records = [json.loads(row[0]) for row in rows]
        summaries = [dict(digest=record['revision']['digest'], generation=record['revision']['generation'],
            state=record['state'], updated_at=record.get('updated_at'), resulting_sha=record.get('resulting_sha'),
            current=record['revision']['digest'] == (current.get('revision') or {}).get('digest'))
            for record in records[:20]]
        return dict(revisions=summaries,
                    next_before=summaries[-1]['generation'] if len(records) > 20 else None)

    def advance(self, identifier):
        release = self.get(identifier)
        repository = self.service.path_for(release)
        with repository_lock(repository):
            release = self.get(identifier)  # The durable record after claiming.
            if release['state'] not in ACTIVE:
                return
            try:
                self._advance(release, repository)
            except ReleaseChanged:
                return
            except (ValueError, OSError) as error:
                release['failure_phase'] = release['state']
                release.update(state='blocked', error=str(error)[:2000])
                intent = release.get('intent')
                if intent:
                    release['results'][intent['task_id']] = dict(state='blocked', reason=release['error'])
                self.save(release)

    def _advance(self, release, repository):
        revision = release.get('revision')
        if (not isinstance(revision, dict)
                or not {'digest', 'policy_digest', 'base_sha', 'members', 'generation', 'release_id'} <= revision.keys()
                or not isinstance(revision.get('members'), list)
                or any(not isinstance(member, dict) or not {'task_id', 'candidate_sha'} <= member.keys()
                       for member in revision['members'])):
            raise ValueError('Frozen release manifest is missing or incomplete; inspect the release record before retrying.')
        if digest({k: v for k, v in revision.items() if k != 'digest'}) != revision['digest']:
            raise ValueError('Frozen release manifest integrity check failed.')
        if toolchain_policy.digest(toolchain_policy.resolve_committed(repository)) != revision['policy_digest']:
            raise ValueError('Approved project policy changed. Review a new release candidate.')
        tree = self.worktree(release)
        ref = 'refs/heads/' + release['branch']
        if release['state'] == 'publishing':
            self.publish(release, repository)
            return
        if release['state'] == 'repair_queued':
            self.repair(release, repository, tree)
            return
        if release['state'] == 'repairing':
            job = self.service.store.job_record(release['repair_chat_id'])
            if job.get('state') in ('queued', 'running', 'waiting_for_input'):
                return
            if job.get('state') != 'answered':
                raise ValueError('Conflict agent needs attention. Inspect its run before resuming.')
            release['state'] = 'integrating'
        if not tree.exists():
            if release.get('intent') or release['checkpoint'] != revision['base_sha']:
                raise ValueError('Integration worktree is missing; retained progress needs inspection.')
            self.root.mkdir(parents=True, exist_ok=True)
            if self.root.is_symlink():
                raise ValueError('Release worktree root cannot be a symbolic link.')
            hooks = self.root / 'empty-hooks'
            hooks.mkdir(exist_ok=True)
            try:
                actual = project_git.git(repository, 'rev-parse', '--verify', ref).strip()
            except ValueError:
                project_git.git(repository, 'update-ref', ref, revision['base_sha'], '0' * len(revision['base_sha']))
            else:
                if actual != revision['base_sha']:
                    raise ValueError('Release branch moved before worktree creation.')
            project_git.git(repository, '-c', 'core.hooksPath=' + str(hooks), 'worktree', 'add', '--detach', str(tree), revision['base_sha'], timeout=60)
        if tree.is_symlink():
            raise ValueError('Integration worktree cannot be a symbolic link.')
        directories = project_git.git(tree, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()
        original = project_git.git(repository, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()
        if Path(directories).resolve() != Path(original).resolve():
            raise ValueError('Integration worktree belongs to a different repository.')
        if project_git.git(tree, 'rev-parse', '--abbrev-ref', 'HEAD').strip() != 'HEAD':
            raise ValueError('Integration worktree must remain detached from task and project branches.')
        head = project_git.git(tree, 'rev-parse', 'HEAD').strip()
        intent = release.get('intent')
        if release['state'] == 'excluding':
            if not intent or head != intent['before']:
                raise ValueError('Worktree advanced during exclusion; inspect its committed result.')
            if project_git.merge_state(tree):
                if project_git.git(tree, 'rev-parse', 'MERGE_HEAD').strip() != intent['candidate_sha']:
                    raise ValueError('Unfinished merge differs from the selected member.')
                project_git.git(tree, 'merge', '--abort')
            if project_git.git(tree, 'status', '--porcelain=v1', '--untracked-files=all').strip():
                raise ValueError('Exclusion left uncommitted work; inspect the integration worktree.')
            release['results'][intent['task_id']] = dict(state='excluded', reason=release.pop('exclusion_reason'))
            release.pop('intent')
            release.update(state='integrating', error='')
            self.save(release)
            return
        if intent:
            if project_git.merge_state(tree):
                conflicts = project_git.git(tree, 'diff', '--name-only', '--diff-filter=U').strip()
                raise ValueError('Resolve and commit the integration conflict before resuming: ' + conflicts[:1200])
            parents = project_git.git(tree, 'rev-list', '--parents', '-n', '1', head).split()[1:]
            if head != intent['before']:
                if parents != [intent['before'], intent['candidate_sha']]:
                    raise ValueError('Unexpected integration HEAD; inspect the retained worktree before resuming.')
                self.checkpoint(release, repository, tree, head, intent)
                return
        elif head != release['checkpoint']:
            raise ValueError('Integration worktree moved outside the recorded merge intent.')
        if project_git.git(tree, 'status', '--porcelain=v1', '--untracked-files=all').strip():
            raise ValueError('Integration worktree has uncommitted changes; inspect before resuming.')
        if project_git.git(repository, 'rev-parse', ref).strip() != release['checkpoint']:
            raise ValueError('Release branch moved outside this run; no ref was overwritten.')
        member = next((m for m in revision['members'] if release['results'].get(m['task_id'], {}).get('state') not in ('incorporated', 'excluded')), None)
        if member:
            intent = dict(task_id=member['task_id'], candidate_sha=member['candidate_sha'], before=head)
            release['intent'] = intent
            release['results'][member['task_id']] = dict(state='integrating')
            self.save(release)
            try:
                project_git.git(tree, 'merge-base', '--is-ancestor', member['candidate_sha'], head)
            except ValueError:
                hooks = self.root / 'empty-hooks'
                hooks.mkdir(exist_ok=True)
                try:
                    project_git.git(tree, '-c', 'core.hooksPath=' + str(hooks), '-c', 'commit.gpgsign=false',
                                    'merge', '--no-ff', '--no-edit', member['candidate_sha'], timeout=120)
                except ValueError:
                    if project_git.merge_state(tree):
                        conflicts = project_git.git(tree, 'diff', '--name-only', '--diff-filter=U').strip()
                        raise ValueError('Integration conflict retained for resolution: ' + conflicts[:1200]) from None
                    raise
                head = project_git.git(tree, 'rev-parse', 'HEAD').strip()
            self.checkpoint(release, repository, tree, head, intent)
            return
        event = dict(id=digest(['release', revision['digest'], head, release.get('validation_attempt', 0)]), release_id=release['id'],
                     repository=release['repository'], path=release['repository'], target=head,
                     frozen_policy_digest=revision['policy_digest'], frozen_plan_digest=revision.get('plan_digest'))
        if release['state'] != 'validating' or release.get('validation_event') != event or release.get('resulting_sha') != head:
            release.update(state='validating', resulting_sha=head, validation_event=event)
            self.save(release)
        validation = self.service.validation
        if validation is None:
            raise ValueError('Validation executor is unavailable; resume when it is configured.')
        # Reconcile a lost submission response before dispatching again, including
        # gateway-mode runs which do not have BuildQueue's identity deduplication.
        snapshot = validation.snapshot()
        runs = [r for r in snapshot['runs'] if event['id'] in r.get('event_ids', [r.get('event_id')])]
        if runs:
            run = runs[0]
            if run['state'] in ('queued', 'running'):
                return
            validation._record_result(event['id'], run)
            return
        from validation import ValidationBusy
        try:
            validation.submit(dict(id=event['id'], retry=True), actor='release_integration')
        except ValidationBusy:
            return  # Capacity is a wait, never a failed integration.
        # Do not save the stale release after submit: a fast callback may already
        # have persisted the terminal receipt.

    def checkpoint(self, release, repository, tree, head, intent):
        if head != intent['before']:
            parents = project_git.git(tree, 'rev-list', '--parents', '-n', '1', head).split()[1:]
            if parents != [intent['before'], intent['candidate_sha']]:
                raise ValueError('Integration commit parents do not match the pinned merge intent.')
        else:
            project_git.git(tree, 'merge-base', '--is-ancestor', intent['candidate_sha'], head)
        if project_git.merge_state(tree) or project_git.git(tree, 'status', '--porcelain=v1', '--untracked-files=all').strip():
            raise ValueError('Integration result is not a clean committed tree.')
        from pr_repairs import protected_changes
        policy = toolchain_policy.resolve_committed(repository)
        if protected_changes(repository, release['revision']['base_sha'], head, policy):
            raise ValueError('Integrated result changes protected policy files; explicit review is required.')
        ref = 'refs/heads/' + release['branch']
        actual = project_git.git(repository, 'rev-parse', ref).strip()
        if actual == release['checkpoint']:
            project_git.git(repository, 'update-ref', ref, head, release['checkpoint'])
        elif actual != head:
            raise ValueError('Release branch moved; no ref was overwritten.')
        release['results'][intent['task_id']] = dict(state='incorporated', integration_sha=head)
        release.update(checkpoint=head, state='integrating', error='')
        release.pop('intent', None)
        self.save(release)

    def repair(self, release, repository, tree):
        profiles = self.service.store.snapshot(live_status=False)['profiles']
        profile = next((p for p in profiles if p['id'] == release.get('repair_profile_id')), None)
        if (not profile or profile.get('read_only') or profile.get('archived') or profile.get('group_id')
                or Path(profile.get('project', '')).resolve() != repository.resolve()):
            raise ValueError('Choose a writable agent assigned to this repository.')
        intent = release['intent']
        if not project_git.merge_state(tree):
            raise ValueError('No unfinished integration merge exists. Inspect the worktree and resume instead.')
        runs = self.service.store.job_records()
        run = next((j for j in reversed(runs) if j.get('kind') == 'launch' and j.get('profile_id') == profile['id']
                    and j.get('state') == 'persona_sent'), None)
        if not run:
            raise ValueError('Launch this agent before assigning the release conflict.')
        run = self.service.store.job_record(run['id'])
        frozen = run.get('toolchain_policy') or {}
        if frozen.get('digest') != release['revision']['policy_digest']:
            raise ValueError('The agent session uses a different environment. Relaunch it under the approved policy.')
        from organizations import policy_guidance
        prompt = (f"Resolve the release integration conflict ONLY in {tree}. Release {release['version']}, "
                  f"manifest {release['revision']['digest']}. First parent must remain {intent['before']}; "
                  f"incoming second parent must be {intent['candidate_sha']}. Finish this existing merge and commit it. "
                  'Do not reset, rebase, install SDKs, change protected policy inputs, push, open PRs or modify task branches. '
                  'Use the approved project environment. Report the full resulting SHA and checks. '
                  'Herdr independently verifies the parents, clean tree, policy and combined required checks.')
        prompt += policy_guidance(frozen)
        # Stable identity survives a crash after dispatch but before saving the
        # chat pointer. OrganizationStore owns idle/session admission.
        job = self.service.store.action('chat', dict(request_id='release-repair-' + digest([release['revision']['digest'], intent, release['repair_attempt']]),
            organization_id=profile['organization_id'], profile_id=profile['id'], expected_run_id=run['id'], prompt=prompt, wait_seconds=1800))
        release.update(state='repairing', repair_chat_id=job['id'])
        self.save(release)

    def publish(self, release, repository):
        from contributions import push
        publication = release.get('publication')
        if not publication:
            raise ValueError('No publication destination was frozen. Configure GitHub and revise this release.')
        if project_git.git(repository, 'rev-parse', 'refs/heads/' + release['branch']).strip() != release['resulting_sha']:
            raise ValueError('Release branch moved after validation; inspect it before publication.')
        self.service.github.allow(publication['upstream'])
        push(repository, publication['branch'], release['resulting_sha'], self.service.github, publication['destination'],
             release_lookup=self.get)
        release.update(state='published', published_at=stamp())
        self.save(release)
