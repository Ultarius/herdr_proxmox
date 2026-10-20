"""Explicit, durable branch updates and conflict repairs on existing tasks."""
from pathlib import Path
import hashlib
import uuid

import project_git
import toolchain_policy
from herdr_errors import HerdrError
from repository_lock import repository_lock
from session_ownership import reserves

ACTIVE = ('queued', 'preparing', 'starting', 'running', 'waiting_for_input', 'monitoring_unavailable', 'validating', 'validation_failed', 'needs_attention')
READ_FAILURE_LIMIT = 3
# Finished attempts that must not trap the task. `validation_failed` stays in
# ACTIVE so publication remains blocked, but the operator must be able to ask
# for a fresh repair instead of being left with only an inspection button.
RETRYABLE = ('validation_failed',)


def active(task):
    return (task.get('pr_repair') or {}).get('state') in ACTIVE


def save(service, task, action):
    # Waiting and working are sampled repeatedly. Record transitions, not every
    # poll, so the activity history stays useful during a long agent turn.
    # The monitoring counters must still persist to accumulate, so they are not
    # excluded here: an outage does add an entry per failed read, which is the
    # price of counting consecutive failures.
    if service.comparable(service.get(task['id'])) == service.comparable(task):
        return task
    return service.save(task, action, 'pr_repair_scheduler')


def refresh_pull(service, task):
    """Refresh and record GitHub facts on task; the caller persists that task."""
    pull = service.github.request('/repos/' + task['github_repository'] + '/pulls/' + str(task['pull']['number']))
    service.record_pull(task, pull)
    if task['state'] in ('merged', 'closed', 'completed') or pull.get('state') != 'open':
        raise ValueError('Only an open pull request can be repaired.')
    return pull


def validation_update(task):
    return (task.get('pr_repair') or {}).get('kind') == 'validation_update'


def protected_changes(repository, before, after, policy):
    """Compare committed files, including new manifests absent from the policy.

    A difference against today's checkout alone cannot attribute an edit to an
    agent. The task's pinned base is the provenance boundary for that decision.
    """
    import project_inventory
    protected = set(policy.get('declarations', {})) | set(policy.get('lockfiles', {}))
    protected.update(toolchain_policy.DECLARATIONS)
    protected.update(toolchain_policy.LOCKFILES)
    names = project_git.git(repository, 'diff', '--name-only', '--no-renames', '-z', before, after, '--').split('\0')
    return sorted(name for name in names if name and
                  (name in protected or project_inventory.is_manifest(Path(name).name)))


def queue_validation_update(service, task, actor):
    """One explicit validation click may authorize one durable branch update.

    No policy waiver: only upstream-owned drift is eligible. Subsequent build
    submissions from this scheduler cannot recursively authorize another repair.
    A click that cannot queue one answers with `action_message` instead of a
    silent fall-through whenever the operator has something to act on.
    """
    from contributions import SHA, stamp
    if (validation_update(task) and task['pr_repair'].get('state') == 'validation_failed'
            and task['pr_repair'].get('candidate') == task.get('head_sha')):
        return None  # Explicitly retry checks, never launch another agent turn.
    if active(task):
        return dict(task, action_message='A branch repair is already in progress. No second execution was started.')
    if task['state'] not in ('review_ready', 'published', 'pr_open'):
        return None
    repository = service.path_for(task)
    candidate, base = task.get('head_sha'), task.get('base_sha')
    if not SHA.fullmatch(str(candidate)) or not SHA.fullmatch(str(base)):
        return None
    upstream = project_git.git(repository, 'rev-parse', '--verify', task['base_ref'] + '^{commit}').strip()
    if upstream == base:
        return None  # The fetched base holds nothing the task does not already have.
    try:
        project_git.git(repository, 'merge-base', '--is-ancestor', upstream, candidate)
        return None  # The candidate already contains the fetched base.
    except ValueError:
        pass
    for descendant in (candidate, upstream):
        project_git.git(repository, 'merge-base', '--is-ancestor', base, descendant)
    policy = toolchain_policy.resolve_committed(repository)
    drift = protected_changes(repository, candidate, upstream, policy)
    if not drift:
        return None  # Upstream-owned protected files did not move; the build proceeds unchanged.
    authored = protected_changes(repository, base, candidate, policy)
    if authored:
        raise ValueError('The task changed protected policy files; explicit review is required: ' + ', '.join(authored))
    approved = project_git.git(repository, 'rev-parse', '--verify', 'HEAD^{commit}').strip()
    # A mismatch may mean lag, divergence or an unrelated checkout branch;
    # inequality alone cannot establish that the approved checkout is behind.
    if upstream != approved:
        return dict(task, action_message='The approved checkout does not match the fetched task base branch '
            + task['base_ref'] + '. Review and update the project checkout, then click Validate latest commit again.')
    previous = task.get('pr_repair') or {}
    if (previous.get('kind') == 'validation_update' and previous.get('head_sha') == candidate
            and previous.get('upstream_sha') == upstream and previous.get('state') != 'cancelled'):
        raise ValueError('This branch update was already attempted. Inspect the saved repair before retrying.')
    profile = next((p for p in service.store.snapshot(live_status=False)['profiles']
                    if p['id'] == task['profile_id'] and not p.get('archived')), None)
    if not profile or profile.get('ephemeral') or profile.get('group_id') or not profile.get('use_worktree', True):
        raise ValueError('Updating this task requires an assigned persistent worktree agent.')
    history = list(task.get('pr_repair_history') or [])
    if previous:
        history.append(previous)
    task['pr_repair_history'] = history[-20:]
    task['pr_repair'] = dict(id=uuid.uuid4().hex, kind='validation_update', state='queued',
        head_sha=candidate, upstream_sha=upstream, previous_base=base, previous_state=task['state'],
        actor=actor, requested_at=stamp(), policy_digest=toolchain_policy.digest(policy),
        policy_files=drift, message='Updating task branch before validation; queued for the assigned agent.')
    saved = service.save(task, 'validation_update_queued', actor)
    return dict(saved, action_message='Task branch update queued for the assigned agent; publication remains separate.')


def check_validation_update(service, task):
    """Recheck the pinned target and approval before dispatch and acceptance."""
    repository, repair = service.path_for(task), task['pr_repair']
    upstream = project_git.git(repository, 'rev-parse', '--verify', task['base_ref'] + '^{commit}').strip()
    if (upstream != repair['upstream_sha'] or
            project_git.git(repository, 'rev-parse', '--verify', 'HEAD^{commit}').strip() != upstream or
            toolchain_policy.digest(toolchain_policy.resolve_committed(repository)) != repair['policy_digest']):
        raise ValueError('The approved upstream changed during the branch update. Inspect before retrying.')


def validation_binding(task):
    """An exact, receipt-backed transition, never a rewrite of session policy."""
    repair = task.get('pr_repair') or {}
    binding = repair.get('validation_policy')
    if (validation_update(task) and isinstance(binding, dict) and
            repair.get('candidate') == task.get('head_sha') == binding.get('candidate') and
            (task.get('completion_receipt') or {}).get('commit') == binding.get('candidate')):
        return binding
    return None


def request(service, task, body, actor):
    from contributions import SHA, stamp
    if body.get('mode') not in (None, 'cancel', 'retry'):
        raise ValueError('Choose conflict repair, cancellation or an inspected recheck.')
    if body.get('mode') == 'retry':
        if (task.get('pr_repair') or {}).get('state') != 'needs_attention' or body.get('inspected') is not True:
            raise ValueError('Inspect the repair session before rechecking it.')
        previous = task['pr_repair']
        run = next((j for j in service.store.snapshot(live_status=False)['jobs'] if j['id'] == task.get('run_id')), {})
        if previous.get('started_at') or run.get('session_request_id') == 'pr-repair-' + previous['id']:
            previous.update(state='queued', message='Rechecking the inspected repair; existing delivery will be adopted.')
            return service.save(task, 'pr_repair_recheck', actor)
        if validation_update(task):
            check_validation_update(service, task)
            previous.update(state='queued', message='Rechecking the inspected branch update against its pinned commits.')
            return service.save(task, 'pr_repair_recheck', actor)
        previous['state'] = 'obsolete'
        body = dict(body, head_sha=task.get('head_sha'))
    if body.get('mode') == 'cancel':
        if (task.get('pr_repair') or {}).get('state') != 'queued':
            raise ValueError('Only a queued, undispatched repair can be cancelled.')
        repair = task['pr_repair']
        runs = service.store.snapshot(live_status=False)['jobs']
        if repair.get('started_at') or any(
                run.get('session_request_id') == 'pr-repair-' + repair['id'] for run in runs):
            raise ValueError('This repair was already submitted; inspect its session instead of cancelling its queue.')
        task['pr_repair'].update(state='cancelled', message='Queued repair cancelled.', at=stamp())
        return service.save(task, 'pr_repair_cancelled', actor)
    if active(task) and (task.get('pr_repair') or {}).get('state') not in RETRYABLE:
        # Response-only feedback: preserve progress and avoid audit noise.
        return dict(task, action_message='A conflict repair is already in progress. No second execution was started.')
    if not task.get('pull') or not task.get('publish'):
        raise ValueError('Publish and open the pull request before requesting conflict repair.')
    pull = refresh_pull(service, task)
    head, base = pull['head']['sha'], pull['base'].get('sha')
    if pull.get('mergeable') is not False:
        raise ValueError('GitHub has not confirmed merge conflicts. Refresh PR status first.')
    if (body.get('head_sha') != head or head != task.get('head_sha')
            or head != task['publish'].get('head_sha') or not SHA.fullmatch(str(base))):
        raise ValueError('The PR candidate or base changed; refresh and review the current conflict first.')
    profile = next((p for p in service.store.snapshot(live_status=False)['profiles']
                    if p['id'] == task['profile_id'] and not p.get('archived')), None)
    if not profile or profile.get('ephemeral') or profile.get('group_id') or not profile.get('use_worktree', True):
        raise ValueError('Conflict repair requires an assigned persistent worktree agent.')
    history = list(task.get('pr_repair_history') or [])
    if task.get('pr_repair'):
        history.append(task['pr_repair'])
    task['pr_repair_history'] = history[-20:]
    task['pr_repair'] = dict(id=uuid.uuid4().hex, state='queued', head_sha=head, upstream_sha=base,
                             previous_base=task['base_sha'], actor=actor, requested_at=stamp(),
                             message='Queued for the assigned agent; other work is not interrupted.')
    return service.save(task, 'pr_repair_queued', actor)


def instructions(task):
    repair = task['pr_repair']
    procedure = (Path(__file__).parent / 'skills/herdr-worktree-integration/references/merge.md').resolve().as_posix()
    assignment = (f"Update this task branch to the approved upstream before validation for {task['github_repository']}. "
                  if validation_update(task) else
                  f"Resolve merge conflicts in existing PR #{task['pull']['number']} for {task['github_repository']}. ")
    return (assignment +
            f"Work only on assigned branch {task['branch']} in this checkout. Starting candidate commit: {repair['head_sha']}. "
            f"Merge EXACT upstream commit {repair['upstream_sha']} using git -c user.name=Herdr-Agent "
            '-c user.email=agent@herdr.local merge --no-edit. '
            f"Pinned recovery: {repair['recovery']['ref']}. Read and follow {procedure} before mutation. "
            'Before starting the merge, verify that HEAD equals the supplied starting commit and the checkout is clean. '
            'Preserve both upstream changes and the original task behavior. Do not rebase, reset, stash, discard unrelated '
            'work, force-push, push, open another PR, merge the PR, update the shared checkout or deploy. '
            'If a product decision is required, stop and report it. Resolve conflicts, commit locally, verify both '
            'supplied commits are ancestors of HEAD, and run the task required checks. '
            'Use git -c user.name=Herdr-Agent -c user.email=agent@herdr.local commit. '
            'Write the completion receipt LAST to {{HERDR_TASK_RECEIPT}} with outcome=complete, commit=full HEAD SHA, '
            'run_id={{HERDR_TASK_RUN}}, token={{HERDR_TASK_TOKEN}}, and tests as an array of actual check results. '
            'Missing or failed checks must be reported honestly. Publication requires dashboard approval.\n\n'
            + ('Protected validation files must match the supplied upstream commit exactly. Do not weaken or '
               'rewrite validation rules. If preserving task behavior requires a policy change, stop and report it.\n\n'
               if validation_update(task) else '')
            + task['title'] + '\nOriginal task and acceptance criteria:\n' + task['description'])


def dispatch(service, task):
    from contributions import MAX_ACTIVE_EXECUTIONS, stamp
    repair = task['pr_repair']
    request_id = 'pr-repair-' + repair['id']
    jobs = service.store.snapshot(live_status=False).get('jobs', [])
    run = next((j for j in jobs if j['id'] == task.get('run_id')), None)
    if not run:
        raise ValueError('The original task execution is missing; inspect its saved checkout before repair.')
    # A restart or lost response after session submission adopts that execution;
    # it must never send the same repair as another turn.
    if run.get('session_request_id') == request_id:
        repair.update(state='running', message='Agent is updating the task branch and resolving conflicts.')
        return save(service, task, 'pr_repair_started')
    if any(reserves(j, task['profile_id']) for j in jobs):
        repair['message'] = 'Queued: another job still owns this agent session.'
        return save(service, task, 'pr_repair_waiting')
    other = service.active_execution(task['profile_id'], [j for j in jobs if j['id'] != run['id']])
    if other:
        if other.get('task_id'):
            with service.operation('task:' + other['task_id'], timeout=0):
                previous = service.get(other['task_id'])
                finished, reason = service.execution_finished(previous, other)
                if finished:
                    service.finish_execution(previous, other, request_id + '-previous', 'pr_repair_scheduler')
                else:
                    repair['message'] = 'Queued: the assigned agent is finishing other work. ' + reason
                    return save(service, task, 'pr_repair_waiting')
        else:
            repair['message'] = 'Queued: inspect or release the agent’s other session before repair.'
            return save(service, task, 'pr_repair_waiting')
    with service.operation('launch-capacity', timeout=0):
        jobs = service.store.snapshot(live_status=False).get('jobs', [])
        occupied = sum(j.get('kind') == 'launch' and bool(j.get('task_id')) and j.get('state') not in ('finished', 'released') for j in jobs)
        held = int(bool(run.get('task_id')) and run.get('state') not in ('finished', 'released'))
        budget = service.session_budget(task['organization_id'], jobs)
        policy = budget['policy']
        if (occupied - held >= MAX_ACTIVE_EXECUTIONS or
                policy['max_active_sessions'] and budget['active'] - held >= policy['max_active_sessions'] or
                policy['daily_session_cap'] and budget['started'] >= policy['daily_session_cap']):
            repair['message'] = 'Queued: waiting for the organization session budget.'
            return save(service, task, 'pr_repair_waiting')
        if validation_update(task):
            check_validation_update(service, task)
        else:
            pull = refresh_pull(service, task)
            if pull['head']['sha'] != repair['head_sha'] or pull['base'].get('sha') != repair['upstream_sha']:
                raise ValueError('The PR head or upstream base changed while queued. Inspect and request a fresh repair.')
            if pull.get('mergeable') is not False:
                if pull.get('mergeable') is None:
                    repair['message'] = 'Queued: GitHub is calculating mergeability.'
                    return save(service, task, 'pr_repair_waiting')
                repair.update(state='obsolete', message='GitHub no longer reports conflicts; no agent prompt was sent.')
                return service.save(task, 'pr_repair_obsolete', 'pr_repair_scheduler')
        if run.get('state') == 'persona_sent':
            agent = service.store.identity(run, ready=False)
            if agent.get('agent_status', agent.get('state')) not in ('idle', 'done'):
                repair['message'] = 'Queued: the original agent is still working or needs your decision. Inspect its session.'
                return save(service, task, 'pr_repair_waiting')
        profile = next((p for p in service.store.snapshot(live_status=False)['profiles'] if p['id'] == task['profile_id']), None)
        if (not profile or profile.get('archived') or profile.get('ephemeral') or profile.get('group_id')
                or not profile.get('use_worktree', True) or Path(profile.get('project', '')).resolve() != service.path_for(task)):
            raise ValueError('The assigned persistent worktree profile changed; inspect it before repair.')
        if service.store.model_validator is not None:
            service.store.model_validator(profile)
        repository, path = service.path_for(task), service.tree_for(task)
        with repository_lock(repository):
            if (project_git.git(path, 'rev-parse', 'HEAD').strip() != repair['head_sha'] or
                    project_git.merge_state(path) or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip()):
                raise ValueError('Original task checkout is changed or has unfinished work; inspect it before repair.')
            project_git.fetch(repository)
            if validation_update(task):
                check_validation_update(service, task)
            project_git.git(path, 'cat-file', '-e', repair['upstream_sha'] + '^{commit}')
            if not repair.get('recovery'):
                repair['recovery'] = project_git.recovery_snapshot(service.projects,
                    path.relative_to(service.projects).as_posix(), hashlib.sha256(request_id.encode()).hexdigest())
            repair.update(state='starting', message='Preparing the saved task session for conflict repair.')
            service.save(task, 'pr_repair_prepared', 'pr_repair_scheduler')
        if run.get('state') == 'persona_sent':
            service.store.manage_session(dict(mode='finish', job_id=run['id'], organization_id=task['organization_id'],
                inspected=True, request_id=request_id + '-archive'), 'pr_repair_scheduler')
            archived = next((j for j in service.store.snapshot(live_status=False)['jobs']
                             if j['id'] == run['id']), None)
            if archived is None:
                raise ValueError('The original execution disappeared while archiving; inspect the agent session.')
            run = archived
        if run.get('state') not in ('finished', 'released') or not run.get('session_archive_id'):
            raise ValueError('The original session must have a verified archive before restarting its checkout.')
        with repository_lock(repository):
            if validation_update(task):
                check_validation_update(service, task)
            if (project_git.git(path, 'rev-parse', 'HEAD').strip() != repair['head_sha'] or
                    project_git.merge_state(path) or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip()):
                raise ValueError('The original checkout changed during archival; inspect it before repair.')
        service.store.manage_session(dict(mode='handover', job_id=run['id'], organization_id=task['organization_id'],
            target_profile_id=task['profile_id'], archive_id=run['session_archive_id'], inspected=True,
            continuation_context='Continue the same task in its preserved checkout. The explicit branch-repair '
                                 'assignment replaces the old implementation prompt.', task_prompt=instructions(task),
            request_id=request_id), 'pr_repair_scheduler')
        repair.update(state='running', started_at=stamp(), message='Agent is updating the task branch and resolving conflicts.')
        return service.save(task, 'pr_repair_started', 'pr_repair_scheduler')


def reconcile(service, task):
    from collaboration import current_run
    from contributions import stamp
    from validation import ValidationBusy
    repair = task['pr_repair']
    if task['state'] in ('merged', 'closed', 'completed'):
        repair.update(state='obsolete' if repair['state'] == 'queued' else 'needs_attention',
                      message='The PR closed while repair was pending. Inspect any active agent session.')
        return save(service, task, 'pr_repair_needs_attention')
    if repair['state'] == 'needs_attention':
        run = next((j for j in service.store.snapshot(live_status=False)['jobs'] if j['id'] == task.get('run_id')), {})
        if (not repair.get('monitoring_retry')
                or run.get('session_request_id') != 'pr-repair-' + repair['id']
                or run.get('state') not in ('queued', 'running', 'persona_sent')):
            return  # Uncertain ownership must never trigger a new launch.
        # Compatibility for interrupted monitoring written by older versions.
        # Recovery is recorded only after a fresh identity read succeeds below.
    if repair['state'] in ('queued', 'preparing', 'starting'):
        return dispatch(service, task)
    if repair['state'] == 'validating':
        build = (task.get('builds') or {}).get(repair['candidate'], {})
        if build.get('target') != repair['candidate']:
            if not service.validation:
                repair['message'] = 'Waiting for validation to be available.'
                return save(service, task, 'pr_repair_waiting')
            if not validation_update(task) and service.validation.snapshot().get('executor') != 'service':
                repair['message'] = 'Waiting for the durable build service. Install it under Builds & deployments.'
                return save(service, task, 'pr_repair_waiting')
            try:
                return service.perform('build', {'target': repair['candidate']}, task['id'], 'pr_repair_scheduler')
            except ValidationBusy:
                # The gateway has one build slot. A busy slot is a transient
                # admission refusal, not an uncertain build or an agent error.
                repair['message'] = 'Waiting for the current validation to finish; submission will retry automatically.'
                return save(service, task, 'pr_repair_waiting')
        if build.get('state') == 'complete' and build.get('required_checks_verified') is True:
            repair.update(state='ready', message=('Updated candidate passed required checks; ready for review.'
                if validation_update(task) else 'Repaired candidate passed required checks. Review and publish to update the existing PR.'))
            return service.save(task, 'pr_repair_validated', 'pr_repair_scheduler')
        if build.get('state') in ('failed', 'error', 'interrupted', 'cancelled', 'complete'):
            repair.update(state='validation_failed', message=(
                'Updated candidate validation needs attention. Inspect Checks & builds.' if validation_update(task)
                else 'Repaired candidate validation needs attention. Inspect Checks & builds.'))
            return service.save(task, 'pr_repair_validation_failed', 'pr_repair_scheduler')
        return
    run = next((j for j in service.store.snapshot(live_status=False)['jobs'] if j['id'] == task.get('run_id')), None)
    if not run or run.get('state') in ('needs_attention', 'released', 'finished'):
        raise ValueError(str((run or {}).get('error') or 'Repair session needs inspection.'))
    if run.get('state') in ('queued', 'running'):
        return
    try:
        agent = current_run(service.store, run, ready=False)
        # current_run returns the launch binding; the verified agent lives in identity.
        agent = service.store.identity(agent, ready=False)
    except (ValueError, OSError) as error:
        recoverable = isinstance(error, OSError) or (
            isinstance(error, HerdrError) and error.code in ('timeout', 'server_not_running', 'herdr_error'))
        if not recoverable:
            repair.pop('monitoring_retry', None)
            save(service, task, 'pr_repair_waiting')
            raise
        failures = int(repair.get('monitoring_failures') or 0) + 1
        repair.update(monitoring_retry=True, monitoring_failures=failures,
                      monitoring_failed_since=repair.get('monitoring_failed_since') or stamp(),
                      monitoring_error=str(error)[:500])
        if failures >= READ_FAILURE_LIMIT:
            repair['state'] = 'monitoring_unavailable'
        repair['message'] = (f'Session status unavailable ({failures} consecutive reads failed). '
                             'Read-only monitoring retries automatically; repair completion is not yet verified. '
                             + str(error)[:500])
        return save(service, task, 'pr_repair_monitoring_unavailable' if failures == READ_FAILURE_LIMIT
                    else 'pr_repair_monitoring_retry')
    recovered = repair.pop('monitoring_retry', None)
    for key in ('monitoring_failures', 'monitoring_failed_since', 'monitoring_error'):
        repair.pop(key, None)
    if recovered:
        repair.update(state='running', message='Session status recovered; monitoring the existing repair.')
        save(service, task, 'pr_repair_recovered')
    status = agent.get('agent_status', agent.get('state'))
    if status == 'blocked':
        repair.update(state='waiting_for_input', message='The agent needs your decision in its session. Monitoring continues.')
        return save(service, task, 'pr_repair_waiting_for_input')
    if status not in ('idle', 'done'):
        repair.update(state='running', message='Agent is resolving conflicts and running the task checks.')
        return save(service, task, 'pr_repair_progress')
    path = service.tree_for(task)
    with repository_lock(path):
        head = project_git.git(path, 'rev-parse', 'HEAD').strip()
        try:
            receipt = service.read_receipt(task, run, head, path)
        except (ValueError, OSError):
            repair['message'] = 'Awaiting the repair session’s completion receipt; idle alone is not completion.'
            return save(service, task, 'pr_repair_waiting')
        if receipt['outcome'] != 'complete':
            raise ValueError('The repair session reported ' + str(receipt['outcome']) + ' rather than a completed repair.')
        if head == repair['head_sha']:
            raise ValueError('Repair must produce a new committed candidate, not reuse the old completion receipt.')
        try:
            for sha in (repair['head_sha'], repair['upstream_sha']):
                project_git.git(path, 'merge-base', '--is-ancestor', sha, head)
        except ValueError:
            raise ValueError('The repaired candidate must preserve the published commit and incorporate the pinned upstream commit.') from None
        if project_git.merge_state(path) or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip():
            raise ValueError('Repair checkout is dirty or has unresolved Git operations.')
        if validation_update(task):
            check_validation_update(service, task)
            policy = toolchain_policy.resolve_committed(service.path_for(task))
            changed = protected_changes(path, repair['upstream_sha'], head, policy)
            if changed:
                raise ValueError('Updated candidate changes protected policy files; explicit review is required: ' + ', '.join(changed))
        task['base_sha'] = repair['upstream_sha']
        task['completion_receipt'] = dict(commit=head, tests=receipt['tests'], verified_at=stamp())
        service.save(task, 'pr_repair_receipt', 'pr_repair_scheduler')
        task = service.perform('candidate', {}, task['id'], 'pr_repair_scheduler')
    repair = task['pr_repair']
    if validation_update(task):
        # The operator authorized this pinned upstream update, and the receipt
        # and protected-file comparison above establish the resulting candidate.
        # Independent validation adopts that policy without claiming the agent
        # originally executed under it.
        # Use the policy already checked while accepting the merge result.
        # A later checkout move must fail validation, never rewrite this grant.
        repair['validation_policy'] = dict(candidate=head, digest=repair['policy_digest'],
            plan_digest=policy.get('plan_digest'), approved_sha=repair['upstream_sha'],
            repair_id=repair['id'], actor=repair['actor'], authorized_at=repair['requested_at'])
    repair.update(state='validating', candidate=head, message=('Task branch updated; validating the new candidate.'
        if validation_update(task) else 'Conflict resolution committed; validating the new candidate.'))
    task['state'] = ('pr_open' if task.get('pull') else 'review_ready') if validation_update(task) else 'pr_open'
    return service.save(task, 'pr_repair_candidate', 'pr_repair_scheduler')


def advance(service, task_ids=None):
    from contributions import Busy
    ids = service.select_task_ids("json_extract(data, '$.pr_repair.state') IN "
                                 "('queued','preparing','starting','running','waiting_for_input','monitoring_unavailable','validating','needs_attention')", task_ids)
    for task_id in ids:
        try:
            with service.operation('task:' + task_id, timeout=0):
                task = service.get(task_id)
                reconcile(service, task)
        except Busy:
            continue
        except (ValueError, OSError) as error:
            try:
                with service.operation('task:' + task_id, timeout=0):
                    task = service.get(task_id)
                    task['pr_repair'].update(state='needs_attention', message=str(error)[:500])
                    save(service, task, 'pr_repair_needs_attention')
            except Busy:
                continue
