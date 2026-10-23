"""Explicit candidate-scoped validation approval; never rewrites a live session."""
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

import project_git
import toolchain_policy
from repository_lock import repository_lock

SHA = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
REVIEW_STATES = ('review_ready', 'published', 'pr_open')


def binding(task, target, approved=None):
    approval = task.get('validation_environment') or {}
    if (task.get('state') not in REVIEW_STATES
            or not target or target != approval.get('candidate')
            or approval.get('kind') != 'candidate_validation'
            or approval.get('candidate') != task.get('head_sha')
            or approval.get('run_id') != task.get('run_id')
            or approval.get('candidate_revision') != task.get('candidate_revision')
            or not approval.get('approved_by') or not approval.get('approved_at')):
        return None
    if approved is not None and (approval.get('digest') != toolchain_policy.digest(approved)
                                or approval.get('plan_digest') != approved.get('plan_digest')):
        return None
    return approval


def review(service, task):
    if task.get('state') not in REVIEW_STATES:
        raise ValueError('Review a captured, open task candidate before changing its validation environment.')
    candidate = task.get('head_sha')
    if not SHA.fullmatch(str(candidate)) or not task.get('run_id'):
        raise ValueError('A captured candidate and its recorded task execution are required.')
    repository = service.path_for(task)
    project_git.git(repository, 'cat-file', '-e', candidate + '^{commit}')
    approved_sha = project_git.git(repository, 'rev-parse', '--verify', 'HEAD^{commit}').strip()
    approved = toolchain_policy.resolve_committed(repository)
    blockers = [*(approved.get('blockers') or []), *(approved.get('validation_blockers') or [])]
    if blockers or not approved.get('plan_digest') or not approved.get('recipes'):
        raise ValueError('Prepare the approved project environment and approve validation checks first. '
                         + '; '.join(map(str, blockers))[:350])
    candidate_policy = toolchain_policy.candidate_policy(repository, approved, revision=candidate)
    drift = toolchain_policy.changes(approved, candidate_policy)
    if drift:
        base = task.get('base_sha')
        if SHA.fullmatch(str(base)):
            base_policy = toolchain_policy.candidate_policy(repository, approved, revision=base)
            if not toolchain_policy.changes(base_policy, candidate_policy):
                raise ValueError('Approved upstream validation inputs changed since this task branched. '
                                 'Update the task branch against upstream, then review and validate again: '
                                 + ', '.join(drift)[:350])
        raise ValueError('The candidate differs from approved validation inputs; review those changes first: '
                         + ', '.join(drift)[:350])
    frozen = service.frozen_environment(task) or {}
    result = dict(task_id=task['id'], candidate=candidate, candidate_revision=task.get('candidate_revision'),
        run_id=task['run_id'], approved_commit=approved_sha,
        previous_plan_digest=frozen.get('plan_digest'), previous_policy_digest=frozen.get('digest'),
        prior_approval=(task.get('validation_environment') or {}).get('id'),
        plan_digest=approved['plan_digest'], digest=toolchain_policy.digest(approved),
        toolchains=[dict(name=item['name'], required=item['required'], component=item.get('component', '.'))
                    for item in approved.get('toolchains', [])],
        recipes=[dict(cwd=item['cwd'], arguments=item['arguments'], evidence=item.get('evidence', ''))
                 for item in approved['recipes']], declarations=approved['declarations'],
        scope='Validation of this exact candidate only; the agent session retains its original policy.')
    result['review_digest'] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result


def status(service, task):
    """Live detail-only recovery hint, including failures recorded before hints existed."""
    build = (task.get('builds') or {}).get(task.get('head_sha')) or {}
    if (task.get('state') not in REVIEW_STATES or not task.get('run_id')
            or not (task.get('validation_environment') or build.get('blocker') == 'policy_changed')):
        return None
    try:
        value = review(service, task)
    except (ValueError, OSError) as error:
        return dict(state='unavailable', reason=str(error)[:500])
    approval = binding(task, task.get('head_sha'))
    if approval and all(approval.get(key) == value[key] for key in ('plan_digest', 'digest')):
        return dict(state='approved', candidate=value['candidate'], run_id=value['run_id'],
                    candidate_revision=value['candidate_revision'], approval_id=approval['id'])
    if (build.get('blocker') == 'policy_changed'
            and (value['previous_plan_digest'] != value['plan_digest']
                 or value['previous_policy_digest'] != value['digest'])):
        return dict(state='review_required', recovery='review_validation_environment')
    return None


def action(service, task, body, actor):
    if body.get('mode') not in ('review', 'approve'):
        raise ValueError('Review the validation environment before approving it.')
    with repository_lock(service.path_for(task)):
        value = review(service, task)
        if body['mode'] == 'review':
            return dict(task, validation_environment_review=value)
        if (body.get('candidate') != value['candidate']
                or body.get('review_digest') != value['review_digest'] or body.get('inspected') is not True):
            raise ValueError('The candidate or approved environment changed. Review it again before approving.')
        previous = task.get('validation_environment')
        if previous:
            task['validation_environment_history'] = [*(task.get('validation_environment_history') or []), previous][-20:]
        task['validation_environment'] = dict(value, id=uuid.uuid4().hex, kind='candidate_validation',
            approved_by=actor, approved_at=datetime.now(timezone.utc).isoformat())
        saved = service.save(task, 'validation_environment_approved', actor)
        return dict(saved, action_message='Environment approved for this candidate. Click Validate latest commit to run checks.')
