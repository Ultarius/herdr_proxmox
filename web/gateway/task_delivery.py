"""Opt-in task verification/publication and a durable worker feedback barrier."""
import json
import time

ACTOR = 'automatic_task_delivery'
MAX_VALIDATION_REPAIRS = 2
ACTIVE_INSPECTIONS = ('queued', 'running', 'waiting_for_input')


def history_entry(flow, **transition):
    """Review facts and a chat audit pointer, never a replayable execution payload."""
    fields = ('sha', 'state', 'error', 'agent_report', 'failure_stage', 'chat_id',
              'superseded_by', 'superseded_at', 'validation', 'superseded_by_validation',
              'previous_inspection_error', 'revalidation_wait')
    record = {key: flow[key] for key in fields if key in flow}
    if 'error' in record:
        record['error'] = str(record['error'])[:2000]
    if isinstance(record.get('agent_report'), dict):
        report = record['agent_report']
        record['agent_report'] = {key: report[key] for key in ('sha', 'outcome', 'summary', 'candidate') if key in report}
    return dict(record, **transition)


def enabled(task):
    return task.get('await_verification') is True or task.get('auto_publish') is True


def pending(task):
    if not enabled(task) or (task.get('completion') or {}).get('outcome') == 'no_changes':
        return False
    flow = task.get('delivery_workflow') or {}
    evidence = task.get('builds', {}).get(task.get('head_sha'), {})
    return (flow.get('state') != 'complete' or flow.get('sha') != task.get('head_sha')
            or evidence.get('target') != task.get('head_sha') or evidence.get('state') != 'complete'
            or evidence.get('required_checks_verified') is not True)


def inspected_validation(flow):
    """Read the frozen inspection identity, including pre-binding gateway jobs."""
    value = flow.get('validation')
    if not isinstance(value, dict):
        # Legacy packets include checks, unlike the new compact binding. Normalize
        # only identity fields; never treat either shape as executable evidence.
        try:
            packet = json.loads((flow.get('feedback_prompt') or '').rsplit('Evidence: ', 1)[1])
            value = packet.get('validation') if packet.get('sha') == flow.get('sha') else None
        except (IndexError, ValueError, AttributeError, TypeError):
            value = None
    return {key: value[key] for key in ('run_id', 'state', 'plan_digest', 'policy_digest', 'required_checks_verified')
            if key in value} if isinstance(value, dict) else {}


def migrate_inspected_validation(service, task, flow):
    """Recover a legacy identity once; this never approves or executes checks."""
    from validation import RUN_ID
    if (isinstance(flow.get('validation'), dict) or not flow.get('sha')
            or flow['sha'] != task.get('head_sha')):
        return
    identity = inspected_validation(flow)
    if (not isinstance(identity.get('run_id'), str) or not RUN_ID.fullmatch(identity['run_id'])
            or identity.get('state') not in ('queued', 'running', 'complete', 'failed', 'error', 'interrupted', 'cancelled')):
        return
    flow['validation'] = identity
    service.save(task, 'delivery_validation_migrated', ACTOR)


def inspection_request_id(task, flow):
    return 'delivery-' + task['id'] + '-' + flow['sha'][:12] + '-' + str(flow.get('attempt', 0))


def inspection_job(service, task, flow):
    if flow.get('chat_id'):
        try:
            return service.store.job_record(flow['chat_id'])
        except ValueError:
            return dict(id=flow['chat_id'], state='unavailable')
    if flow.get('feedback_prompt') and flow.get('sha'):
        return service.store.job_for_request(inspection_request_id(task, flow))
    return None


def check_inspection_retry(service, task):
    """Mutation-time guard: a cached attention banner cannot authorize parallel chats."""
    flow = task.get('delivery_workflow') or {}
    if flow.get('state') != 'attention':
        raise ValueError('Only a delivery needing attention can be retried.')
    reply = inspection_job(service, task, flow) or {}
    if reply.get('state') in ACTIVE_INSPECTIONS:
        raise ValueError('The previous delivery inspection is still active. Wait for it to finish before retrying.')


def refresh_failed_inspection(service, task, flow):
    """New passing evidence supersedes a settled failure, never uncertain input."""
    from contributions import stamp
    head = task.get('head_sha')
    evidence = task.get('builds', {}).get(head, {})
    if (flow.get('sha') != head or flow.get('result_kind') != 'validation_failed'
            or flow.get('state') not in ('attention', 'waiting_agent', 'notifying_agent')):
        return None
    previous = inspected_validation(flow)
    if (previous.get('state') not in ('failed', 'error', 'interrupted', 'cancelled', 'complete')
            or (previous.get('state') == 'complete' and previous.get('required_checks_verified') is True)
            or not previous.get('run_id') or not evidence.get('run_id')
            or previous['run_id'] == evidence['run_id'] or evidence.get('target') != head
            or evidence.get('state') != 'complete' or evidence.get('required_checks_verified') is not True
            or not service.policy_matches(task, evidence)):
        return None
    reply = inspection_job(service, task, flow) or {}
    if not flow.get('chat_id') and reply.get('id'):
        flow['chat_id'] = reply['id']  # Recover the committed enqueue after a crash.
        if (reply.get('kind') == 'chat' and reply.get('organization_id') == task.get('organization_id')
                and reply.get('profile_id') == task.get('profile_id') and reply.get('runs')
                and reply['runs'][0].get('id') == task.get('run_id')):
            service.store.update_job(reply['id'], task_id=task['id'], delivery_inspection=True)
    never_submitted = not reply and not flow.get('chat_id') and bool(flow.get('feedback_prompt'))
    settled = (reply.get('state') == 'answered' and (reply.get('delivery') or {}).get('stage') == 'reply_verified'
               and reply.get('runs') and reply['runs'][0].get('id') == task.get('run_id'))
    try:
        report = json.loads(reply.get('result') or '{}')
        if settled and isinstance(report, dict) and report.get('outcome') == 'repaired':
            return None  # Existing repair acceptance owns any newly committed work.
        settled = (settled and isinstance(report, dict) and report.get('sha') == head
                   and report.get('outcome') in ('reviewed', 'needs_attention')
                   and isinstance(report.get('summary'), str) and 1 <= len(report['summary'].strip()) <= 2000)
    except (ValueError, TypeError):
        settled = False
    if not settled and not never_submitted:
        active = reply.get('state') in ACTIVE_INSPECTIONS
        waiting = dict(previous_run_id=previous['run_id'], run_id=evidence['run_id'],
                       chat_id=flow.get('chat_id'), inspection_pending=active)
        if active:
            remedy = 'Wait for the existing inspection to finish; resolve its console prompt if needed.'
        elif reply.get('state') == 'unavailable' or not reply:
            remedy = 'The previous inspection job is unavailable. Inspect the original session, then choose Retry verification and delivery inspection to request a fresh inspection.'
        else:
            remedy = 'Inspect or recover its saved chat reply, then choose Retry verification and delivery inspection.'
        message = ('Validation run ' + evidence['run_id'] + ' passed. The previous delivery inspection used failed run '
                   + previous['run_id'] + '. ' + remedy)
        state = 'waiting_agent' if active else 'attention'
        if flow.get('revalidation_wait') != waiting or flow.get('error') != message or flow.get('state') != state:
            flow.setdefault('previous_inspection_error', str(flow.get('error') or '')[:2000])
            flow.update(revalidation_wait=waiting, error=message, state=state)
            service.save(task, 'delivery_revalidation_waiting', ACTOR)
        return 'waiting'
    history = [history_entry(entry) for entry in (task.get('delivery_history') or [])[-4:]]
    history.append(history_entry(dict(flow, validation=previous), superseded_by_validation=evidence['run_id'], superseded_at=stamp()))
    task['delivery_history'] = history
    task['delivery_workflow'] = dict(sha=head, state='waiting_verification', started_at=stamp(),
                                    attempt=flow.get('attempt', 0) + 1)
    service.save(task, 'delivery_revalidated', ACTOR)
    return 'refreshed'


def advance(service, task_ids=None):
    from contributions import Busy
    ids = service.select_task_ids("(json_extract(data, '$.await_verification')=1 OR "
                                 "json_extract(data, '$.auto_publish')=1)", task_ids)
    for task_id in ids:
        try:
            with service.operation('task:' + task_id, timeout=0):
                reconcile(service, service.get(task_id))
        except Busy:
            continue
        except (ValueError, OSError) as error:
            # Stop on consequential or ambiguous failures; explicit retry reuses
            # existing push/PR recovery rather than repeatedly writing remotely.
            try:
                with service.operation('task:' + task_id, timeout=0):
                    task = service.get(task_id)
                    flow = task.setdefault('delivery_workflow', {})
                    if flow.get('chat_id') and flow.get('result_kind') == 'success' and flow.get('inspection_verified'):
                        # Late evidence failure needs a fresh failure report, not
                        # the already-answered success inspection.
                        flow['previous_chat_id'] = flow.pop('chat_id')
                        flow.pop('feedback_prompt', None)
                        flow['attempt'] = flow.get('attempt', 0) + 1
                    flow.update(state='attention', error=str(error)[:1000])
                    flow.setdefault('failure_stage', 'delivery')
                    service.save(task, 'delivery_attention', ACTOR)
                    if task.get('head_sha') and not flow.get('chat_id'):
                        flow['result_kind'] = 'delivery_failed'
                        try:
                            notify_agent(service, task, flow)
                        except (ValueError, OSError) as notification_error:
                            flow.update(notification_pending=True, notification_error=str(notification_error)[:500],
                                        notification_retry_after=time.time() + 30)
                            service.save(task, 'delivery_notification_pending', ACTOR)
            except (ValueError, OSError):
                continue


def reconcile(service, task):
    from contributions import stamp, pull_preview, publishing_repository
    from collaboration import current_run
    import pr_repairs
    if task['state'] in ('merged', 'closed', 'completed') and pending(task):
        flow = task.setdefault('delivery_workflow', {})
        if flow.get('state') != 'attention':
            flow.update(state='attention', error='Task became terminal before delivery inspection completed. Inspect it and stop automation to release this barrier.')
            service.save(task, 'delivery_terminal_attention', ACTOR)
        return
    if task['state'] not in ('implementing', 'review_ready', 'published', 'pr_open') or pr_repairs.active(task):
        return
    flow = task.get('delivery_workflow') or {}
    head = task.get('head_sha')
    if (head and task['state'] != 'implementing' and flow.get('sha') and flow['sha'] != head
            and flow.get('state') != 'accepting_repair'):
        # An attention report is evidence about one commit, not a permanent
        # task-wide failure. Preserve it without replaying its frozen prompt or
        # notification retries against the new candidate. accept_repair owns its
        # own crash recovery across capture and must finish that transition first.
        # Compact existing entries too, including snapshots written by older gateways.
        history = [history_entry(entry) for entry in (task.get('delivery_history') or [])[-4:]]
        history.append(history_entry(flow, superseded_by=head, superseded_at=stamp()))
        task['delivery_history'] = history
        flow = dict(sha=head, state='waiting_verification', attempt=0, started_at=stamp())
        task['delivery_workflow'] = flow
        service.save(task, 'delivery_superseded', ACTOR)
    migrate_inspected_validation(service, task, flow)
    revalidation = refresh_failed_inspection(service, task, flow)
    if revalidation == 'waiting':
        return
    if revalidation == 'refreshed':
        flow = task['delivery_workflow']
    if (flow.get('state') == 'attention' and flow.get('result_kind') == 'success'
            and not flow.get('inspection_verified') and flow.get('sha') == head and flow.get('chat_id')):
        reply = inspection_job(service, task, flow) or {}
        recovered_at = reply.get('recovered_at')
        if (reply.get('state') == 'answered' and (reply.get('delivery') or {}).get('stage') == 'reply_verified'
                and (reply.get('delivery') or {}).get('recovered') is True and recovered_at
                and flow.get('recovered_inspection_at') != recovered_at
                and reply.get('runs') and reply['runs'][0].get('id') == task.get('run_id')):
            # Explicit reply recovery is permission to re-read this inspection,
            # not to declare delivery complete. All checks below still apply.
            flow.update(state='waiting_agent', recovered_inspection_at=recovered_at)
            service.save(task, 'delivery_inspection_recovered', ACTOR)
    if flow.get('state') == 'attention':
        if flow.get('notification_pending') and flow.get('notification_retry_after', 0) <= time.time():
            notify_agent(service, task, flow)
        return
    if flow.get('remote_check_after', 0) > time.time():
        return
    if flow.get('state') == 'accepting_repair':
        from collaboration import current_run
        jobs = service.store.snapshot(live_status=False).get('jobs', [])
        run = next((job for job in jobs if job['id'] == task.get('run_id')), None)
        if not run or run.get('state') != 'persona_sent':
            raise ValueError('Original task session is unavailable for repair recovery.')
        current_run(service.store, run)
        accept_repair(service, task, run, flow, flow['repair_report'])
        return
    if not head or task['state'] == 'implementing':
        return
    if flow.get('sha') != head:
        # Initial/legacy flows without a SHA start here. Superseded SHA-bound
        # flows reset above, before their old attention/backoff can block us.
        flow = dict(sha=head, state='waiting_verification', attempt=0, started_at=stamp())
        task['delivery_workflow'] = flow
        service.save(task, 'delivery_started', ACTOR)
    evidence = task.get('builds', {}).get(head, {})
    if evidence.get('state') == 'complete' and not service.policy_matches(task, evidence):
        flow['failure_stage'] = 'verification'
        service.save(task, 'delivery_policy_blocked', ACTOR)
        raise ValueError('Verification no longer satisfies the approved toolchain policy. '
                         'Review policy changes and verify again before delivery.')
    if flow.get('state') == 'complete':
        if pending(task):
            raise ValueError('Durable verification changed after delivery inspection; inspect before releasing the agent.')
        return
    jobs = service.store.snapshot(live_status=False).get('jobs', [])
    run = next((job for job in jobs if job['id'] == task.get('run_id')), None)
    if not run or run.get('state') != 'persona_sent':
        raise ValueError('Original task session is unavailable for delivery inspection. Restore it or disable automation.')
    agent = service.store.identity(run, ready=False)
    if agent.get('agent_status', agent.get('state')) == 'working':
        return
    if flow.get('chat_id'):
        reply = next((job for job in jobs if job['id'] == flow['chat_id']), None)
        if not reply:
            raise ValueError('Delivery inspection record is unavailable; inspect before retrying.')
        if reply['state'] in ('queued', 'running', 'waiting_for_input'):
            return
        if (reply['state'] != 'answered' or (reply.get('delivery') or {}).get('stage') != 'reply_verified'
                or not reply.get('runs') or reply['runs'][0]['id'] != run['id']):
            raise ValueError('Agent delivery inspection needs attention: ' + str(reply.get('error') or reply['state']))
        current_run(service.store, run)
        data = json.loads(reply.get('result') or '{}')
        if (not isinstance(data, dict) or data.get('sha') != head or data.get('outcome') not in ('reviewed', 'needs_attention', 'repaired')
                or not isinstance(data.get('summary'), str) or not 1 <= len(data['summary'].strip()) <= 2000):
            raise ValueError('Agent inspection must report the exact SHA, outcome and a bounded summary.')
        if data['outcome'] == 'repaired':
            accept_repair(service, task, run, flow, data)
            return
        # Re-check the immutable checkout and remote evidence after inspection.
        verify_checkout(service, task, run, head)
        if not flow.get('inspection_verified'):
            flow['inspection_verified'] = True
            service.save(task, 'delivery_inspection_reply_verified', ACTOR)
        evidence = task.get('builds', {}).get(head, {})
        if flow.get('result_kind') == 'success' and (evidence.get('target') != head
                or evidence.get('state') != 'complete' or evidence.get('required_checks_verified') is not True):
            raise ValueError('Durable verification changed during agent inspection.')
        if task.get('auto_publish') and flow.get('result_kind') == 'success':
            verify_authorization(task)
            if not verify_pull(service, task, head):
                flow.update(state='waiting_remote', remote_check_after=time.time() + 30)
                service.save(task, 'delivery_waiting_remote', ACTOR)
                return
        flow.update(agent_report=data, checked_at=stamp())
        if data['outcome'] != 'reviewed' or flow.get('result_kind') != 'success':
            flow.update(state='attention', error=data['summary'])
        else:
            flow.update(state='complete', error='')
        service.save(task, 'delivery_agent_checked', ACTOR)
        return
    evidence = task.get('builds', {}).get(head, {})
    if task.get('delivery_retry_build') and evidence.get('state') in ('failed', 'error', 'interrupted'):
        task = service.perform('build', dict(target=head), task['id'], ACTOR)
        task.pop('delivery_retry_build', None)
        service.save(task, 'delivery_build_retried', ACTOR)
        return
    if task.pop('delivery_retry_build', False):
        service.save(task, 'delivery_retry_consumed', ACTOR)
    if evidence.get('target') != head or evidence.get('state') not in ('complete', 'failed', 'error', 'interrupted'):
        return
    current_run(service.store, run)
    receipt = verify_checkout(service, task, run, head)
    if (task.get('completion_receipt') or {}).get('commit') != head:
        task['completion_receipt'] = dict(commit=head, tests=receipt['tests'], verified_at=stamp())
        service.save(task, 'delivery_completion_verified', ACTOR)
    verified = evidence.get('state') == 'complete' and evidence.get('required_checks_verified') is True
    flow['result_kind'] = 'success' if verified else 'validation_failed'
    if not verified:
        flow['failure_stage'] = 'verification'
    if verified and task.get('auto_publish'):
        verify_authorization(task)
        if (task.get('publish') or {}).get('head_sha') != head or (task.get('pull') or {}).get('head_sha') != head:
            flow['failure_stage'] = 'credentials'
            service.save(task, 'delivery_preflight', ACTOR)
            task['publication_preflight'] = dict(service.github.publication_preflight(
                task['github_repository'], publishing_repository(task)), at=stamp())
        if (task.get('publish') or {}).get('head_sha') != head:
            flow['state'] = 'publishing'
            flow['failure_stage'] = 'branch_push'
            service.save(task, 'delivery_publishing', ACTOR)
            task = service.perform('publish', dict(head_sha=head), task['id'], ACTOR)
            flow = task['delivery_workflow']
        if (task.get('pull') or {}).get('head_sha') != head:
            flow['state'] = 'opening_pr'
            flow['failure_stage'] = 'pull_request'
            service.save(task, 'delivery_opening_pr', ACTOR)
            preview = pull_preview(task)
            task = service.perform('pull', dict(head_sha=head, reviewed=True, title=preview['title'], body=preview['body']),
                                   task['id'], ACTOR)
            flow = task['delivery_workflow']
            if (task.get('manual_pr') or {}).get('state') == 'awaiting_manual_pr':
                flow.update(state='awaiting_manual_pr', failure_stage='manual_pr', remote_check_after=time.time() + 30)
                service.save(task, 'delivery_awaiting_manual_pr', ACTOR)
                return
        if flow.get('failure_stage') != 'github_checks':
            flow['failure_stage'] = 'github_checks'
            service.save(task, 'delivery_remote_inspection', ACTOR)
        if not verify_pull(service, task, head):
            flow.update(state='waiting_remote', remote_check_after=time.time() + 30)
            service.save(task, 'delivery_waiting_remote', ACTOR)
            return
    notify_agent(service, task, flow)


def notify_agent(service, task, flow):
    from contributions import publishing_repository
    from collaboration import current_run
    jobs = service.store.snapshot(live_status=False).get('jobs', [])
    run = next((job for job in jobs if job['id'] == task.get('run_id')), None)
    if not run or run.get('state') != 'persona_sent':
        raise ValueError('Original task session is unavailable for delivery inspection.')
    current_run(service.store, run)
    head = task['head_sha']
    evidence = task.get('builds', {}).get(head, {})
    verified = evidence.get('target') == head and evidence.get('required_checks_verified') is True and evidence.get('state') == 'complete'
    if 'feedback_prompt' not in flow:
        flow['validation'] = {key: evidence.get(key) for key in ('run_id', 'state', 'plan_digest', 'policy_digest', 'required_checks_verified')}
        can_repair = repair_allowed(task, flow)
        flow['repair_allowed'] = can_repair
        packet = dict(sha=head, validation=dict(state=evidence.get('state'),
                      run_id=evidence.get('run_id'), required_checks_verified=verified,
                      checks=[{key: str(check.get(key, ''))[:200 if key == 'summary' else 60] for key in ('id', 'status', 'summary')}
                              for check in evidence.get('checks', [])[:6] if isinstance(check, dict)]),
                      publishing_repository=publishing_repository(task), pull={key: (task.get('pull') or {}).get(key) for key in ('number', 'url', 'head_sha', 'base', 'repository')},
                      delivery_result=flow.get('result_kind'), delivery_error=flow.get('error', ''),
                      failure_stage=flow.get('failure_stage'), branch_published=(task.get('publish') or {}).get('head_sha') == head,
                      next_action='repair_failed_checks' if can_repair else 'inspect_or_request_operator',
                      github_verification=flow.get('github_verification'), upstream=task['github_repository'])
        flow['feedback_prompt'] = (
            'Your implementation has reached the delivery inspection step. The dashboard retained your session. '
            'Review the following gateway evidence for your exact commit, inspect the local diff/status and '
            'the PR target/head/URL when present. Identify failed or missing checks and any inconsistency. '
            + ('If the failing checks are code defects, repair only this task in its assigned worktree, run relevant tests, '
             'commit on the existing task branch, and write a completion receipt for the NEW commit using the original run ID and token, last. '
             'Return outcome=repaired with sha naming the FAILED commit and candidate naming the new commit. '
             'Do not weaken checks or change acceptance criteria. Infrastructure/tooling failures require needs_attention. '
             if can_repair else 'This is a read-only inspection: do not change code or commit. ')
            + 'Never push, merge, approve permissions, deploy, read/change credentials or switch publishing destinations. '
            'For publishing/authentication/fork failures, return needs_attention with the operator action required; do not retry GitHub yourself. '
            'Gateway required-check evidence is independent of your earlier reported tests. '
            'The configured publishing repository may intentionally be a fork of upstream; a different '
            'origin or repository name alone is not a destination error or proof of missing permissions. '
            'Use the recorded gateway publication errors and remote evidence to assess those questions. '
            'Return exactly JSON with sha, outcome=reviewed, repaired or needs_attention, and summary (1-2000 characters). '
            'Use needs_attention if evidence is incomplete or inconsistent; do not claim remote checks you did not perform. '
            'Evidence: ' + json.dumps(packet, ensure_ascii=False))
        flow['state'] = 'notifying_agent'
        service.save(task, 'delivery_feedback_prepared', ACTOR)
    # Stable request identity and frozen prompt recover a crash after enqueueing
    # but before saving the chat ID, without sending the worker another turn.
    job = service.store.action('chat', dict(organization_id=task['organization_id'], profile_id=task['profile_id'],
                               expected_run_id=run['id'],
                               prompt=flow['feedback_prompt'], wait_seconds=600,
                               request_id=inspection_request_id(task, flow)))
    service.store.update_job(job['id'], task_id=task['id'], delivery_inspection=True)
    flow.pop('notification_pending', None)
    flow.pop('notification_error', None)
    flow.update(chat_id=job['id'], state='waiting_agent')
    service.save(task, 'delivery_feedback_sent', ACTOR)


def repair_allowed(task, flow):
    evidence = task.get('builds', {}).get(task.get('head_sha'), {})
    return (flow.get('result_kind') == 'validation_failed' and evidence.get('state') == 'failed'
            and task.get('delivery_repair_count', 0) < MAX_VALIDATION_REPAIRS)


def accept_repair(service, task, run, flow, report):
    import project_git
    from contributions import SHA, stamp
    from repository_lock import repository_lock
    recovering = flow.get('state') == 'accepting_repair'
    if not flow.get('repair_allowed') or (not recovering and not repair_allowed(task, flow)):
        raise ValueError('This delivery failure does not authorize an automatic code repair.')
    candidate = report.get('candidate')
    if not isinstance(candidate, str) or not SHA.fullmatch(candidate) or candidate == flow['sha']:
        raise ValueError('Repair must identify a new full commit SHA.')
    path = service.tree_for(task)
    with repository_lock(path):
        project_git.git(path, 'merge-base', '--is-ancestor', flow['sha'], candidate)
        receipt = verify_checkout(service, task, run, candidate)
        if receipt['outcome'] != 'complete':
            raise ValueError('A validation repair requires a completed changes receipt.')
        if not recovering:
            task['delivery_repair_count'] = task.get('delivery_repair_count', 0) + 1
            flow.update(state='accepting_repair', repair_report=report)
            service.save(task, 'delivery_repair_accepted', ACTOR)
        service.perform('candidate', {}, task['id'], ACTOR)
    task = service.get(task['id'])
    task['delivery_workflow'] = dict(sha=candidate, state='waiting_verification', started_at=stamp(),
        attempt=flow.get('attempt', 0) + 1, previous_sha=flow['sha'], agent_report=report)
    task['completion_receipt'] = dict(commit=candidate, tests=receipt['tests'], verified_at=stamp())
    service.save(task, 'delivery_repair_captured', ACTOR)
    # Fresh SHA means fresh durable evidence; never reuse the failed build.
    service.perform('build', dict(target=candidate), task['id'], ACTOR)


def verify_authorization(task):
    from contributions import publishing_repository
    authorization = task.get('publication_authorization') or {}
    if (authorization.get('repository') != publishing_repository(task)
            or authorization.get('upstream') != task.get('github_repository')
            or authorization.get('branch') != task.get('branch')):
        raise ValueError('Publication destination changed since authorization; disable and re-enable automatic publication.')


def verify_checkout(service, task, run, head):
    import project_git
    from repository_lock import repository_lock
    path = service.tree_for(task)
    with repository_lock(path):
        if (project_git.git(path, 'rev-parse', 'HEAD').strip() != head
                or project_git.git(path, 'symbolic-ref', '--short', 'HEAD').strip() != task['branch']
                or project_git.merge_state(path)
                or project_git.git(path, 'status', '--porcelain=v1', '--untracked-files=all').strip()):
            raise ValueError('Delivery inspection requires the unchanged, clean task candidate.')
        return service.read_receipt(task, run, head, path)


def verify_pull(service, task, head):
    from contributions import matching_pull
    if task.get('publish', {}).get('head_sha') != head or service.remote_head(task) != head:
        raise ValueError('Published branch does not match the validated candidate.')
    pull = task.get('pull') or {}
    current = service.github.request('/repos/' + task['github_repository'] + '/pulls/' + str(pull.get('number')))
    if matching_pull(task, [current]) is None or (current.get('head') or {}).get('sha') != head or current.get('state') != 'open':
        raise ValueError('GitHub PR no longer matches the published candidate and destination.')
    if current.get('mergeable') is None:
        return False
    if current.get('mergeable') is False:
        raise ValueError('GitHub reports merge conflicts; repair and revalidate before completing delivery.')
    from github_checks import optional_evidence
    checks, check_error = optional_evidence(service.github, '/repos/' + task['github_repository'] + '/commits/' + head + '/check-runs', 'check_runs')
    entries = checks.get('check_runs', []) if isinstance(checks, dict) else None
    if not isinstance(entries, list) or any(not isinstance(check, dict) for check in entries):
        raise ValueError('GitHub returned invalid CI evidence.')
    if any(check.get('status') != 'completed' for check in entries):
        return False
    if any(check.get('conclusion') not in ('success', 'neutral', 'skipped') for check in entries):
        raise ValueError('GitHub CI failed for the published candidate.')
    statuses, status_error = optional_evidence(service.github, '/repos/' + task['github_repository'] + '/commits/' + head + '/status', 'statuses')
    task['checks_unavailable'] = {key: value for key, value in (('check_runs', check_error), ('statuses', status_error)) if value}
    if check_error or status_error:
        task['delivery_workflow'].pop('github_verification', None)
        return False
    records = statuses.get('statuses') if isinstance(statuses, dict) else None
    if not isinstance(records, list) or any(not isinstance(status, dict) for status in records):
        raise ValueError('GitHub returned invalid commit status evidence.')
    if any(status.get('state') in ('failure', 'error') for status in records):
        raise ValueError('GitHub commit status checks failed for the published candidate.')
    if any(status.get('state') == 'pending' for status in records):
        return False
    task['delivery_workflow']['github_verification'] = dict(sha=head, mergeable=True,
        checks=[{key: str(check.get(key, ''))[:60] for key in ('name', 'conclusion')} for check in entries[:8]],
        statuses=[{key: str(status.get(key, ''))[:60] for key in ('context', 'state')} for status in records[:8]])
    return True
