"""Release-bound group advice and explicit, recoverable draft PR creation.

Advice never supplies a check receipt or authorizes capture, freeze or a remote
write. The release worker owns assembly; agents own their assigned tasks and
explicit conflict repairs. Durable requests precede discussion and GitHub effects.
"""
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote

import project_git
import toolchain_policy
from release_candidates import digest, stamp


def packet(manager, release, tasks):
    import task_delivery
    revision = release.get('revision') or {}
    return dict(release_id=release['id'], repository=release['repository'],
        manifest=revision.get('digest'), combined_sha=release.get('resulting_sha'),
        policy_digest=revision.get('policy_digest'),
        members=[dict(task_id=m['task_id'], selected_sha=m.get('candidate_sha'),
                      delivery_pending=task_delivery.pending(tasks.get(m['task_id'], {})),
                      integration={k: (release.get('results') or {}).get(m['task_id'], {}).get(k) for k in ('state', 'reason')},
                      current_sha=tasks.get(m['task_id'], {}).get('head_sha'),
                      selected_evidence={k: tasks.get(m['task_id'], {}).get('builds', {}).get(m.get('candidate_sha'), {}).get(k)
                          for k in ('target', 'state', 'required_checks_verified', 'policy_digest')},
                      evidence={k: tasks.get(m['task_id'], {}).get('builds', {}).get(
                          tasks.get(m['task_id'], {}).get('head_sha'), {}).get(k)
                          for k in ('target', 'state', 'required_checks_verified', 'policy_digest')})
                 for m in release['members']],
        combined={k: (release.get('validation') or {}).get(k)
                  for k in ('target', 'state', 'required_checks_verified', 'policy_digest')})


def group_for(manager, release, identifier):
    directory = manager.service.store.snapshot(live_status=False)
    group = next((g for g in directory['groups'] if g['id'] == identifier), None)
    profiles = {p['id']: p for p in directory['profiles']}
    repository = manager.service.path_for(release).resolve()
    if not group or group.get('archived') or not group.get('members'):
        raise ValueError('Choose an available release review group.')
    for identifier in group['members']:
        profile = profiles.get(identifier) or {}
        if (profile.get('archived') or profile.get('organization_id') != group['organization_id']
                or not profile.get('project') or Path(profile['project']).resolve() != repository):
            raise ValueError('Every release review member must be assigned to this repository and organization.')
    if release.get('organization_id') and release['organization_id'] != group['organization_id']:
        raise ValueError('Choose a group from the release organization.')
    return group


def configure_reviews(manager, release, body, tasks):
    """A release-scoped discussion grant, separate from assembly and publication."""
    enabled = body.get('enabled')
    if type(enabled) is not bool:
        raise ValueError('Choose whether automatic release review is enabled.')
    prior = release.get('review_policy') or {}
    if not enabled:
        release['review_policy'] = dict(prior, enabled=False, notice='Automatic release review is disabled.')
        return
    if release['state'] in ('publishing', 'published'):
        raise ValueError('Automatic review runs before publication. Request a manual review or revise the release first.')
    group = group_for(manager, release, body.get('group_id', prior.get('group_id')))
    if any(t.get('organization_id') != group['organization_id'] for t in tasks.values()):
        raise ValueError('Release tasks and review group must share an organization.')
    limit = body.get('max_reviews', prior.get('max_reviews', 3))
    if type(limit) is not int or not 1 <= limit <= 20 or limit < prior.get('reviews_used', 0):
        raise ValueError('Choose a review limit between 1 and 20, at least the number already reserved.')
    grant = prior.get('grant_id') if prior.get('enabled') and prior.get('group_id') == group['id'] else body['request_id']
    release['review_policy'] = dict(prior, enabled=True, group_id=group['id'], group_name=group['name'],
        grant_id=grant, max_reviews=limit, notice='Waiting for the full selection and combined required checks.')


def automatic_review(manager, release, repository):
    """Reserve at most one discussion per checked full-scope evidence snapshot.

    The repository claim serializes workers. Save's record CAS prevents an
    operator change from being overwritten; discussion effects happen only on
    a later worker step, after the reservation and budget spend are durable.
    """
    policy = release.get('review_policy') or {}
    if policy.get('enabled') is not True:
        return
    def notice(reason):
        if policy.get('notice') != reason:
            policy['notice'] = reason
            manager.save(release)
    if release['state'] not in ('assembled', 'ready'):
        notice('Waiting for the full selection and combined required checks.')
        return
    revision = release.get('revision') or {}
    if not revision or digest({k: v for k, v in revision.items() if k != 'digest'}) != revision.get('digest'):
        notice('The frozen manifest integrity check failed. Inspect the release before automatic review.')
        return
    selected = [(m['task_id'], m.get('candidate_sha')) for m in release['members']]
    pinned = [(m['task_id'], m.get('candidate_sha')) for m in revision.get('members', [])]
    if not selected or selected != pinned or any((release.get('results') or {}).get(identifier, {}).get('state')
            not in ('incorporated', 'excluded') for identifier, _ in selected):
        notice('Waiting for every selected task to be incorporated or explicitly excluded.')
        return
    evidence = release.get('validation') or {}
    sha = release.get('resulting_sha')
    if (not sha or evidence.get('target') != sha or evidence.get('state') != 'complete'
            or evidence.get('required_checks_verified') is not True
            or evidence.get('policy_digest') != revision.get('policy_digest')):
        notice('Waiting for verified combined checks under the frozen policy.')
        return
    tasks = manager.service.task_records([identifier for identifier, _ in selected])
    import task_delivery
    def finished(task, candidate):
        evidence = task.get('builds', {}).get(candidate, {})
        return (task.get('repository') == release['repository'] and task.get('head_sha') == candidate
            and task.get('state') in ('review_ready', 'published', 'pr_open', 'merged', 'closed', 'completed')
            and evidence.get('target') == candidate and evidence.get('state') == 'complete'
            and evidence.get('required_checks_verified') is True and bool(evidence.get('policy_digest'))
            and not task_delivery.pending(task))
    if any(not finished(tasks.get(identifier, {}), candidate) for identifier, candidate in selected
            if (release.get('results') or {}).get(identifier, {}).get('state') != 'excluded'):
        notice('Waiting for unchanged task candidates and their required delivery inspections.')
        return
    identity = digest(packet(manager, release, tasks))
    previous = release.get('review') or {}
    if identity in policy.get('reserved', []) or previous.get('digest') == identity:
        notice('This evidence snapshot already has a review request. Inspect its outcome and recommendations.')
        return
    if previous.get('state') in ('queued', 'running', 'needs_attention'):
        notice('Inspect or finish the existing release discussion before another automatic review.')
        return
    if policy.get('reviews_used', 0) >= policy['max_reviews']:
        notice('Automatic release review budget exhausted. Review and increase its limit explicitly.')
        return
    from contributions import task_base
    _, base = task_base(repository, '')
    if base != revision.get('base_sha') or toolchain_policy.digest(toolchain_policy.resolve_committed(repository)) != revision.get('policy_digest'):
        notice('Approved base or policy changed. Wait for a new checked assembly before automatic review.')
        return
    try:
        queue_review(manager, release, dict(group_id=policy['group_id'], request_id='auto-review-' + identity), tasks)
    except ValueError as error:
        from discovery import redact
        notice(redact(error)[:1000])
        return
    release['review']['automatic_grant'] = policy['grant_id']
    policy.update(reviews_used=policy.get('reviews_used', 0) + 1,
                  reserved=[*policy.get('reserved', []), identity],
                  notice='Automatic group review reserved for the complete checked selection.')
    manager.save(release)
    manager.wake.set()  # Dispatch the durable reservation on the next worker step, without an idle delay.


def queue_review(manager, release, body, tasks):
    previous = release.get('review') or {}
    if body.get('retry') is True:
        if previous.get('state') != 'needs_attention':
            raise ValueError('Only an interrupted release review can retry.')
        previous.update(state='running' if previous.get('job_id') else 'queued', error='')
        return  # Retain the authorized packet, prompt and dispatch identity.
    if not release['members']:
        raise ValueError('Select at least one task before requesting a release review.')
    if previous.get('state') in ('queued', 'running') or (previous.get('dispatch_started_at') and not previous.get('job_id')):
        raise ValueError('A release discussion is already queued or running; inspect it before requesting another.')
    group = group_for(manager, release, body.get('group_id'))
    if any(t.get('organization_id') != group['organization_id'] for t in tasks.values()):
        raise ValueError('Release tasks and review group must share an organization.')
    evidence = packet(manager, release, tasks)
    review = dict(state='queued', group_id=group['id'], group_name=group['name'],
        organization_id=group['organization_id'], request_id=body['request_id'],
        packet=evidence, digest=digest(evidence), requested_at=stamp())
    review_prompt(release, review)  # Validate before changing a durable dispatch reservation.
    release['review'] = review


def review_prompt(release, review):
    evidence = review['packet']
    # Common 40-character Git SHAs fit at the 100-member bound; longer input
    # is explicitly size checked rather than truncating selected commits.
    # Prefixes are display labels only; the retained packet has full task IDs.
    policies = list(dict.fromkeys((m['selected_evidence'] if m['selected_sha'] else m['evidence'])['policy_digest']
                                 for m in evidence['members']))
    def row(member):
        sha = member['selected_sha'] or member['current_sha']
        check = member['selected_evidence'] if member['selected_sha'] else member['evidence']
        changed = member['selected_sha'] and member['selected_sha'] != member['current_sha']
        good = sha and check['target'] == sha and check['state'] == 'complete' and check['required_checks_verified'] is True
        state = 'head-changed' if changed else 'checked' if good else 'needs-evidence'
        if member['delivery_pending']:
            state = 'delivery-wait'
        if member['integration']['state'] == 'excluded':
            state = 'excluded'
        return f"{member['task_id'][:8]} {sha or '-'} {state} p{policies.index(check['policy_digest'])}"
    rows = '\n'.join(row(member) for member in evidence['members'])
    prompt = (f"Review release {release['version']} in {release['repository']}. Evidence packet {review['digest']}.\n"
        f"Manifest {evidence['manifest']}; combined SHA {evidence['combined_sha']}; policy {evidence['policy_digest']}.\n"
        f"Recorded combined checks: {json.dumps(evidence['combined'], separators=(',', ':'))}.\n"
        'Recorded source policies: ' + '; '.join(f"p{i}={value or 'unavailable'}" for i, value in enumerate(policies)) + '.\n' +
        'Exclusions (not incorporated): ' + json.dumps({m['task_id'][:8]: m['integration']['reason']
            for m in evidence['members'] if m['integration']['state'] == 'excluded'}, separators=(',', ':')) + '.\n' +
        "Task prefixes, selected SHAs (current captured SHA when none selected) and recorded indicators (not admission):\n" + rows + '\n'
        "Discuss missing evidence, source capture/validation, integration risks and readiness for a draft release PR. "
        "Inspect only these exact commits in your assigned repository. Report unavailable evidence explicitly. "
        "Return a concise assessment with blockers, evidence citations and recommended next actions. "
        "Do not edit, capture, commit, run restore scripts, publish, open PRs or emit task proposals/release_plan. "
        "This is advice only: Herdr independently captures and validates through existing authorized task workflows; "
        "the release worker assembles checked commits. Freeze, publication and PR creation require separate administrator actions.")
    if len(prompt) > 8000:
        raise ValueError('Release review packet exceeds the supported discussion size.')
    return prompt


def advance_review(manager, release):
    review = release.get('review') or {}
    if review.get('state') not in ('queued', 'running'):
        return
    if review['state'] == 'queued':
        grant = review.get('automatic_grant')
        policy = release.get('review_policy') or {}
        if grant and not review.get('dispatch_started_at') and (
                policy.get('enabled') is not True or policy.get('grant_id') != grant):
            review.update(state='cancelled', error='Automatic review authorization changed before discussion dispatch.')
            manager.save(release)
            return
        tasks = manager.service.task_records([m['task_id'] for m in release['members']])
        if not review.get('dispatch_started_at') and digest(packet(manager, release, tasks)) != review['digest']:
            review.update(state='stale', error='Release evidence changed before discussion dispatch. Request a fresh review.')
        else:
            group_for(manager, release, review['group_id'])
            if not review.get('dispatch_started_at'):
                review.update(dispatch_started_at=stamp(), prompt=review_prompt(release, review))
                manager.save(release)  # Journal before dispatch; retries replay this exact prompt.
            job = manager.service.store.action('discuss', dict(
                request_id='release-review-' + digest([release['id'], review['request_id']]),
                organization_id=review['organization_id'], group_id=review['group_id'],
                purpose='release_review', review_repository=str(manager.service.path_for(release).resolve()),
                prompt=review['prompt']))
            review.update(state='running', job_id=job['id'])
    else:
        job = manager.service.store.job_record(review['job_id'])
        if (job.get('id') != review['job_id'] or job.get('kind') != 'discussion' or job.get('group_id') != review['group_id']
                or job.get('organization_id') != review['organization_id'] or job.get('purpose') != 'release_review'):
            raise ValueError('The recorded discussion does not match this release review.')
        if job.get('prompt') != review.get('prompt'):
            raise ValueError('The discussion input differs from the retained release evidence request.')
        if job.get('state') == 'artifact_ready':
            result = str(job.get('result') or '')
            from discovery import redact
            review.update(state='complete', completed_at=stamp(), report=redact(result)[:6000],
                          result_sha256=hashlib.sha256(result.encode()).hexdigest())
        elif job.get('state') in ('failed', 'error', 'cancelled', 'needs_attention', 'uncertain'):
            review.update(state='needs_attention', error='Inspect the group discussion before requesting another review.')
        else:
            return
    manager.save(release)


def pull_preview(release):
    revision = release.get('revision') or {}
    return dict(title='Release ' + release['version'], body=(
        f"Release candidate `{release['version']}`\n\n"
        f"Combined commit: `{release.get('resulting_sha')}`\n"
        f"Manifest: `{revision.get('digest')}`\n"
        f"Approved policy: `{revision.get('policy_digest')}`\n\n"
        "Included task commits:\n" + '\n'.join(
            f"- {m['task_id']}: `{m['candidate_sha']}`" for m in revision.get('members', [])
            if (release.get('results') or {}).get(m['task_id'], {}).get('state') != 'excluded') +
        '\n\nCombined required checks passed for this commit. Deployment acceptance is separate.'))


def queue_pull(release, body):
    if (release['state'] != 'published' or body.get('head_sha') != release.get('resulting_sha')
            or body.get('reviewed') is not True):
        raise ValueError('Publish and review the exact verified release commit before creating its draft PR.')
    if (release.get('pull_request') or {}).get('state') in ('queued', 'complete'):
        raise ValueError('The release PR is already queued or recorded.')
    title, description = body.get('title'), body.get('body')
    if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 256 or '\n' in title or '\r' in title
            or not isinstance(description, str) or not 1 <= len(description.strip()) <= 30000):
        raise ValueError('Review a PR title (1–256 characters) and description (1–30000 characters).')
    release['pull_request'] = dict(state='queued', title=title.strip(), body=description,
        head_sha=release['resulting_sha'], manifest=release['revision']['digest'], requested_at=stamp())


def advance_pull(manager, release, repository):
    from contributions import matching_pull
    from github_api import GitHubError
    request = release['pull_request']
    revision, publication = release['revision'], release['publication']
    sha = request['head_sha']
    validation = release.get('validation') or {}
    if (release['state'] != 'published' or request['manifest'] != revision['digest']
            or digest({k: v for k, v in revision.items() if k != 'digest'}) != revision['digest']
            or publication != revision.get('publication') or release['branch'] != publication['branch']
            or sha != release.get('resulting_sha') or validation.get('target') != sha
            or validation.get('state') != 'complete' or validation.get('required_checks_verified') is not True
            or validation.get('policy_digest') != revision['policy_digest']
            or toolchain_policy.digest(toolchain_policy.resolve_committed(repository)) != revision['policy_digest']
            or project_git.git(repository, 'rev-parse', 'refs/heads/' + release['branch']).strip() != sha):
        raise ValueError('Release commit, policy or required-check evidence changed; inspect before creating its PR.')
    github = manager.service.github
    upstream, destination, branch = publication['upstream'], publication['destination'], publication['branch']
    github.allow_publishing(upstream, destination)
    remote = github.request('/repos/' + destination + '/git/ref/heads/' + quote(branch, safe='/'))
    if not isinstance(remote, dict) or not isinstance(remote.get('object'), dict) or remote['object'].get('sha') != sha:
        raise ValueError('Published release branch does not match the reviewed commit.')
    base = revision['base_ref'].removeprefix('refs/remotes/origin/')
    identity = dict(github_repository=upstream, publishing_repository=destination, branch=branch, base_ref=base)
    owner = destination.split('/')[0]
    def existing():
        found = github.find_pull(upstream, branch, owner, base)
        if not isinstance(found, list):
            raise ValueError('GitHub returned an invalid pull request list.')
        return matching_pull(identity, found)
    pull = existing()
    reused = pull is not None
    if pull is None:
        payload = dict(title=request['title'], body=request['body'], base=base, draft=True,
                       head=branch if destination.lower() == upstream.lower() else owner + ':' + branch)
        if destination.lower() != upstream.lower() and owner.lower() == upstream.split('/')[0].lower():
            payload['head_repo'] = destination.split('/')[1]
        try:
            pull = github.request('/repos/' + upstream + '/pulls', 'POST', payload, token=github.pr_credential().token)
        except GitHubError as error:
            if error.status != 422:
                raise
            pull = existing()
            if pull is None:
                raise
            reused = True
    if (matching_pull(identity, [pull]) is None or pull['head'].get('sha') != sha
            or type(pull.get('number')) is not int or pull['number'] < 1
            or pull.get('state') != 'open'):
        raise ValueError('GitHub PR does not match the reviewed release head, repositories and base.')
    release['pull_request'] = dict(state='complete', number=pull['number'],
        url=f"https://github.com/{upstream}/pull/{pull['number']}", head_sha=sha,
        manifest=revision['digest'], reused=reused, draft=pull.get('draft') is True, recorded_at=stamp(),
        remote_state='pr_open', next_refresh_at=time.time() + 60)
    manager.save(release)


def refresh_pull(manager, release):
    """Observe the frozen release PR; logical delivery never asserts task ancestry."""
    from contributions import matching_pull, SHA
    request, publication, revision = release['pull_request'], release['publication'], release['revision']
    if (publication != revision['publication'] or request['manifest'] != revision['digest']
            or digest({k: v for k, v in revision.items() if k != 'digest'}) != revision['digest']):
        raise ValueError('Release publication no longer matches its frozen manifest.')
    upstream = publication['upstream']
    manager.service.github.allow(upstream)
    pull = manager.service.github.request('/repos/' + upstream + '/pulls/' + str(request['number']))
    identity = dict(github_repository=upstream, publishing_repository=publication['destination'],
                    branch=publication['branch'], base_ref=revision['base_ref'])
    if (matching_pull(identity, [pull]) is None or type(pull.get('number')) is not int
            or pull['number'] != request['number'] or pull['head'].get('sha') != request['head_sha']
            or request['head_sha'] != release['resulting_sha'] or request['manifest'] != revision['digest']
            or pull.get('state') not in ('open', 'closed')):
        raise ValueError('GitHub release PR identity or reviewed head changed; inspect it before accepting merge evidence.')
    if pull.get('merged') is True:
        if pull['state'] != 'closed' or not SHA.fullmatch(str(pull.get('merge_commit_sha'))):
            raise ValueError('GitHub release merge evidence is incomplete.')
        if request.get('merge_sha') and request['merge_sha'] != pull['merge_commit_sha']:
            raise ValueError('GitHub release merge commit contradicts the recorded receipt.')
        request.update(remote_state='merged', merge_sha=pull['merge_commit_sha'], merged_at=pull.get('merged_at'))
    elif request.get('remote_state') == 'merged':
        raise ValueError('GitHub returned an observation that contradicts the recorded release merge.')
    else:
        request['remote_state'] = 'pr_open' if pull['state'] == 'open' else 'closed'
    request.update(checked_at=stamp(), next_refresh_at=time.time() + 60)
    release.pop('pull_refresh_error', None)
    release['pull_refresh_requested'] = False
    manager.save(release)
