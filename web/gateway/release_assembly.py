"""Continuous private assembly; a validated checkpoint is not release approval.

Each assembly reuses the immutable manifest, merge journal, retained worktree and
exact-commit executor. New inputs create another generation only after the
current operation finishes. Existing generations and their receipts never move.
"""
from contextlib import closing
import copy
from pathlib import Path

import project_git
import toolchain_policy
from release_candidates import ReleaseChanged, stamp

IDLE = ('collecting', 'assembled', 'blocked', 'validation_failed')


def configure(service, release, body):
    if release['state'] in ('ready', 'published', 'publishing'):
        raise ValueError('Revise this frozen release before enabling continuous assembly.')
    enabled = body.get('enabled')
    if type(enabled) is not bool:
        raise ValueError('Choose whether continuous private assembly is enabled.')
    previous = release.get('continuous') or {}
    if not enabled:
        release['continuous'] = dict(previous, enabled=False)
        return
    if release.get('revision') and release['revision'].get('purpose') != 'assembly':
        raise ValueError('Revise the existing frozen manifest before starting continuous assembly.')
    auto = body.get('auto_repair', previous.get('auto_repair', False))
    if type(auto) is not bool:
        raise ValueError('Choose whether automatic conflict repair is authorized.')
    profile_id = body.get('profile_id', previous.get('profile_id'))
    if auto:
        repository = service.service.path_for(release)
        profiles = service.service.store.snapshot(live_status=False)['profiles']
        profile = next((p for p in profiles if p['id'] == profile_id), None)
        if (not profile or profile.get('read_only') or profile.get('archived') or profile.get('group_id')
                or Path(profile.get('project', '')).resolve() != repository.resolve()):
            raise ValueError('Select a writable conflict agent assigned to this repository.')
    limits = {}
    for name, default, maximum in (('max_runs', 20, 100), ('max_repairs', 2, 10)):
        value = body.get(name, previous.get(name, default))
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError('Choose ' + name + ' between 1 and ' + str(maximum) + '.')
        limits[name] = value
    release['continuous'] = dict(previous, enabled=True, auto_repair=auto, profile_id=profile_id,
                                 **limits)
    release.pop('assembly_notice', None)


def selections(release, tasks):
    scope = copy.deepcopy(release['members'])
    for member in scope:
        task = tasks.get(member['task_id'], {})
        sha = task.get('head_sha')
        evidence = task.get('builds', {}).get(sha, {})
        good = (task.get('repository') == release['repository'] and isinstance(sha, str)
                and len(sha) in (40, 64) and all(c in '0123456789abcdef' for c in sha)
                and evidence.get('target') == sha and evidence.get('state') == 'complete'
                and evidence.get('required_checks_verified') is True
                and isinstance(evidence.get('policy_digest'), str) and bool(evidence.get('policy_digest')))
        if release.get('organization_id') and task.get('organization_id') != release['organization_id']:
            good = False
        member['candidate_sha'] = sha if good else None
    return scope, [member for member in scope if member['candidate_sha']]


def identities(members):
    return [(member['task_id'], member.get('candidate_sha')) for member in members]


def commit(service, original, prepared, tasks):
    """Git inspection precedes the writer; source facts are rechecked inside it."""
    with closing(service.service.connect()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if service.get(original['id'], db)['record_version'] != original['record_version']:
            raise ReleaseChanged('Release selection or automation changed during assembly.')
        current = service.service.task_records(tasks.keys(), db)
        if service.freeze_inputs(current) != service.freeze_inputs(tasks):
            raise ReleaseChanged('Task candidates or evidence changed during assembly.')
        service.save(prepared, db)


def notice(service, original, scope, reason, tasks):
    if original.get('assembly_notice') == reason and original['members'] == scope:
        return
    prepared = copy.deepcopy(original)
    if prepared['members'] != scope:
        prepared['edit_version'] += 1
    prepared.update(members=scope, assembly_notice=reason)
    commit(service, original, prepared, tasks)


def coordinate(service, release, repository):
    if release['state'] not in IDLE:
        return False
    tasks = service.service.task_records([m['task_id'] for m in release['members']])
    scope, eligible = selections(release, tasks)
    if not eligible:
        notice(service, release, scope, 'Waiting for selected tasks with passing checks and a recorded policy identity.', tasks)
        return True
    from contributions import task_base
    _, base = task_base(repository, '')
    policy = toolchain_policy.resolve_committed(repository)
    revision = release.get('revision') or {}
    same = (revision.get('base_sha') == base and revision.get('policy_digest') == toolchain_policy.digest(policy)
            and identities(revision.get('members', [])) == identities(eligible))
    if same:
        if release['state'] == 'blocked' and queue_repair(service, release, repository, tasks):
            return True
        reason = ('' if len(eligible) == len(scope) else 'Some selected tasks are still awaiting checks; this checkpoint covers only ready members.')
        notice(service, release, scope, reason, tasks)
        return release['state'] not in ('integrating', 'validating')
    grant = release['continuous']
    if grant.get('runs_used', 0) >= grant['max_runs']:
        notice(service, release, scope, 'Continuous assembly run budget exhausted. Review and increase its limit explicitly.', tasks)
        return True
    prepared = copy.deepcopy(release)
    # Save replaces the retained snapshot for the old revision only while it is
    # current. Removing these execution fields starts an independent generation;
    # its refs, worktree and old validation remain retained, not aborted/reset.
    for name in ('revision', 'intent', 'results', 'validation', 'validation_event', 'resulting_sha',
                 'checkpoint', 'branch', 'error', 'failure_phase', 'repair_chat_id', 'exclusion_reason',
                 'publication', 'publication_unavailable', 'published_at', 'assembly_notice', 'auto_repair_queued'):
        prepared.pop(name, None)
    prepared.update(state='collecting', members=eligible)
    service.freeze(prepared, tasks, assembly=True)
    prepared['members'] = scope
    prepared['continuous']['runs_used'] = grant.get('runs_used', 0) + 1
    prepared['edit_version'] += 1
    prepared['audit'] = [*prepared['audit'][-49:], dict(action='assembly_started', actor='release_assembly', at=stamp())]
    commit(service, release, prepared, tasks)
    return True


def queue_repair(service, release, repository, tasks):
    grant = release['continuous']
    if not grant.get('auto_repair') or not release.get('intent'):
        return False
    tree, intent = service.worktree(release), release['intent']
    if (not project_git.merge_state(tree)
            or project_git.git(tree, 'rev-parse', 'HEAD').strip() != intent['before']
            or project_git.git(tree, 'rev-parse', 'MERGE_HEAD').strip() != intent['candidate_sha']):
        return False
    if grant.get('repairs_used', 0) >= grant['max_repairs']:
        raise ValueError('Automatic conflict repair budget exhausted. Inspect the agent result before granting more attempts.')
    prepared = copy.deepcopy(release)
    prepared['repair_profile_id'] = grant['profile_id']
    service.repair_agent(prepared, repository)
    prepared['continuous']['repairs_used'] = grant.get('repairs_used', 0) + 1
    prepared.update(state='repair_queued', repair_attempt=release.get('repair_attempt', 0) + 1,
                    auto_repair_queued=True, error='')
    prepared.pop('assembly_notice', None)
    commit(service, release, prepared, tasks)
    return True


def freeze_assembled(service, release, tasks):
    if release['state'] != 'assembled':
        raise ValueError('Wait for combined assembly validation before freezing this release candidate.')
    scope, eligible = selections(release, tasks)
    revision = release['revision']
    if (len(eligible) != len(scope) or identities(eligible) != identities(revision['members'])
            or any(member['task_id'] not in release.get('results', {}) for member in eligible)):
        raise ValueError('Selected task candidates or checks changed. Wait for a current complete assembly before freezing.')
    repository = service.service.path_for(release)
    from contributions import task_base
    _, base = task_base(repository, '')
    policy = toolchain_policy.resolve_committed(repository)
    if (base != revision['base_sha'] or project_git.git(repository, 'rev-parse', 'HEAD').strip() != base
            or policy.get('blockers') or policy.get('validation_blockers')
            or toolchain_policy.digest(policy) != revision['policy_digest']):
        raise ValueError('Approved base or policy changed. Wait for a new checked assembly before freezing.')
    validation, sha = release.get('validation') or {}, release.get('resulting_sha')
    if (validation.get('target') != sha or validation.get('state') != 'complete'
            or validation.get('required_checks_verified') is not True
            or validation.get('policy_digest') != revision['policy_digest']
            or project_git.git(repository, 'rev-parse', 'refs/heads/' + release['branch']).strip() != sha):
        raise ValueError('The exact combined assembly must pass current-policy checks before freeze.')
    from pr_repairs import protected_changes
    if protected_changes(repository, base, sha, policy):
        raise ValueError('Assembled result changes protected policy inputs; explicit review is required.')
    release.update(state='ready', frozen_at=stamp(), assembly_notice='')
    release['continuous'] = dict(release.get('continuous') or {}, enabled=False)
