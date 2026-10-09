"""Prepare meeting sessions without waiting on busy agents or replaying prompts."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import uuid
import threading

from organizations import now


class Waiting(ValueError):
    pass


def mark_waiting(store, job_id, reason):
    # Keep the compare and state update under one lock: never overwrite a
    # discussion another worker has just begun submitting.
    with store.lock, closing(store.connect()) as db:
        job = store.get(db, 'jobs', job_id)
        if job['state'] not in ('queued', 'waiting_for_members'):
            return
        try:
            since = datetime.fromisoformat(job.get('waiting_since') or now())
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            since = datetime.now(timezone.utc)
        if datetime.now(timezone.utc) - since > timedelta(hours=24):
            store.update_job(job_id, state='needs_attention', error='Meeting waited over 24 hours. Inspect its participants; no prompt was sent.')
        else:
            store.update_job(job_id, state='waiting_for_members', waiting_since=since.isoformat(), progress=reason, error='')


def prepare(store, job_id):
    try:
        with store.lock, closing(store.connect()) as db:
            job = store.get(db, 'jobs', job_id)
            if job['state'] != 'queued':
                return False
            if job.get('discussion_submitted_at'):
                raise ValueError('Discussion was already submitted. Inspect or recover its saved artifacts; no prompt will be resent.')
            store.get(db, 'groups', job['group_id'], job['organization_id'])
            organization = store.get(db, 'organizations', job['organization_id'])
            ids = [*job['group']['members'], job['group_run']['profile_id']]
            profiles = [store.get(db, 'profiles', identity, job['organization_id']) for identity in ids]
            jobs = store.job_records()
        # FIFO among meetings. Active non-meeting work has priority; waiting
        # meetings do not hold participant locks between polling cycles.
        for other in jobs:
            if other['id'] == job_id or not set(ids).intersection(other.get('participants', [other.get('profile_id')])):
                continue
            if other['kind'] == 'discussion':
                if other.get('state') in ('queued', 'running', 'waiting_for_members') and (
                        (other.get('created_at', ''), other['id']) < (job.get('created_at', ''), job['id']) or other['state'] == 'running'):
                    raise Waiting('Waiting for an earlier discussion involving these members')
            elif other['kind'] != 'launch' and other['state'] in ('queued', 'running'):
                raise Waiting('Waiting for active ' + other['kind'] + ' work')
        selected = {}
        controller = store.contributions
        for profile in profiles:
            run = next((j for j in reversed(jobs) if j['kind'] == 'launch' and j.get('profile_id') == profile['id']
                        and j['state'] not in ('finished', 'released')), None)
            if not run:
                continue
            if run['state'] == 'running':
                raise Waiting('Waiting for ' + profile['name'] + ' to finish starting')
            if run['state'] == 'queued' and run.get('task_id'):
                raise Waiting(profile['name'] + ': waiting for a queued task execution')
            if run['state'] == 'queued':
                selected[profile['id']] = run
                continue
            if run['state'] != 'persona_sent':
                raise ValueError(profile['name'] + ': inspect the existing session before meeting preparation')
            # Pending meetings are ordered above, rather than owning one
            # another's facilitator/session and creating a circular wait.
            relevant = [j for j in jobs if j['id'] != job_id and not (j['kind'] == 'discussion'
                        and j['state'] in ('queued', 'waiting_for_members') and not j.get('discussion_submitted_at'))]
            if controller:
                owner = controller.session_owner(run['id'], relevant)
            else:
                from contributions import Contributions
                owner = next((j for j in relevant if Contributions.session_reference(j, run['id']) and (
                    j['kind'] in ('delegate', 'delegation') and j['state'] not in ('completed', 'cancelled') or
                    j['kind'] in ('chat', 'input', 'discussion') and j['state'] not in ('answered', 'input_sent', 'artifact_ready', 'cancelled', 'waiting_for_members'))), None)
            if owner:
                if owner['state'] in ('queued', 'running'):
                    raise Waiting(profile['name'] + ': waiting for active ' + owner['kind'])
                raise ValueError(profile['name'] + ': inspect unresolved ' + owner['kind'] + ' before the meeting')
            if run.get('task_id'):
                if not controller:
                    raise ValueError('Task completion verification is unavailable; inspect the member task')
                task = controller.get(run['task_id'])
                if task['state'] not in ('review_ready', 'publishing', 'published', 'pull_open', 'merged', 'completed', 'closed'):
                    raise Waiting(profile['name'] + ': waiting for implementation to finish')
                verified, reason = controller.execution_finished(task, run)
                if not verified:
                    raise ValueError(profile['name'] + ': ' + reason)
            live = store.identity(run, ready=False)
            status = live.get('agent_status', live.get('state'))
            if status not in ('idle', 'done'):
                if status in ('working', 'running', 'busy', 'thinking'):
                    raise Waiting(profile['name'] + ': waiting for current execution')
                raise ValueError(profile['name'] + ': session requires inspection (' + str(status) + ')')
            store.identity(run)  # Validate interactive readiness too.
            selected[profile['id']] = run
        # Nothing new is launched until all already-active members are eligible.
        # Reserve each absent session durably with a deterministic identity.
        with store.lock, closing(store.connect()) as db, db:
            preparation = dict(job.get('preparation') or {})
            for profile in profiles:
                if profile['id'] in selected:
                    continue
                identity = preparation.get(profile['id']) or uuid.uuid5(uuid.NAMESPACE_URL,
                    'herdr-discussion:' + job_id + ':' + str(job.get('preparation_attempt', 0)) + ':' + profile['id']).hex
                try:
                    run = store.get(db, 'jobs', identity, job['organization_id'])
                except ValueError:
                    run = dict(id=identity, organization_id=job['organization_id'], kind='launch', profile_id=profile['id'],
                               profile=profile, organization=organization, alias='meeting_' + identity[:20], state='queued',
                               error='', result='', created_at=now(), updated_at=now(), session_purpose='discussion',
                               preparation_meeting_id=job_id)
                    store.put(db, 'jobs', run)
                if run['state'] not in ('queued', 'persona_sent'):
                    raise ValueError(profile['name'] + ': prepared session needs inspection; it will not be relaunched')
                preparation[profile['id']] = identity
                selected[profile['id']] = run
            job.update(preparation=preparation)
            store.put(db, 'jobs', job)
        for profile in profiles:
            run = selected[profile['id']]
            if run['state'] == 'queued':
                store.execute(run['id'])  # Reentrant participant lock; no worker waits for another worker.
            with store.lock, closing(store.connect()) as db:
                run = store.get(db, 'jobs', run['id'], job['organization_id'])
            if run['state'] != 'persona_sent':
                raise ValueError(profile['name'] + ': session preparation failed; inspect its run')
            store.identity(run)
            selected[profile['id']] = run
        store.update_job(job_id, runs=[selected[i] for i in job['group']['members']],
                         group_run=selected[job['group_run']['profile_id']], prepared_at=now(),
                         progress='Members ready; preparing the discussion', error='')
        return True
    except Waiting as error:
        mark_waiting(store, job_id, str(error))
        return False
    except (ValueError, OSError) as error:
        store.update_job(job_id, state='needs_attention', error=str(error)[:500], progress='Preparation stopped before discussion submission')
        return False


def ensure_facilitator(store, group_id, organization_id):
    """Prepare only the group's facilitator when discovery needs its evidence checkout."""
    from collaboration import launch_group
    with store.lock, closing(store.connect()) as db, db:
        group = store.get(db, 'groups', group_id, organization_id)
        organization = store.get(db, 'organizations', organization_id)
        run = launch_group(store, db, group, organization)
        lock = store.agent_locks.setdefault(run['profile_id'], threading.RLock())
    if not lock.acquire(blocking=False):
        raise ValueError('Product facilitator is occupied; discovery preparation will wait.')
    try:
        if run['state'] == 'queued':
            store.execute(run['id'])
        with store.lock, closing(store.connect()) as db:
            run = store.get(db, 'jobs', run['id'], organization_id)
        if run['state'] != 'persona_sent':
            raise ValueError('Inspect the product facilitator session before discovery preparation.')
        store.identity(run)
        return run
    finally:
        lock.release()
