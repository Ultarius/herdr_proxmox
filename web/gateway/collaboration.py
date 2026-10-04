"""Persisted agent chat and dedicated Herdr group conversations."""
from contextlib import closing
import json
from pathlib import Path
import uuid

from organizations import now, text


def active_run(store, db, organization_id, profile_id):
    store.get(db, 'profiles', profile_id, organization_id)
    jobs = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs WHERE organization_id=?', (organization_id,))]
    run = next((j for j in reversed(jobs) if j['kind'] == 'launch' and j['profile_id'] == profile_id and j['state'] == 'persona_sent'), None)
    if run is None:
        raise ValueError('Launch this agent and deliver its persona first.')
    return run


def facilitator(store, db, group):
    profile_id = group.get('facilitator_id') or uuid.uuid4().hex
    first = store.get(db, 'profiles', group['members'][0], group['organization_id'])
    previous = store.get(db, 'profiles', profile_id) if group.get('facilitator_id') else None
    profile = dict(id=profile_id, organization_id=group['organization_id'], name=group['name'],
                   role='Group facilitator', runtime=first['runtime'], project=first['project'], manager_id='',
                   **{key: first.get(key, '') for key in ('provider', 'model', 'reasoning')},
                   persona='You are this group conversation. Coordinate its selected members using Herdr agent commands. '
                           'Preserve conversation context, ask follow-up questions, and return evidence-backed recommendations. '
                           'Read herdr --skill for the installed command reference. Group purpose: ' + group['description'],
                   version=(previous['version'] if previous else 0) + 1, group_id=group['id'])
    store.put(db, 'profiles', profile)
    group['facilitator_id'] = profile_id
    store.put(db, 'groups', group)
    return profile


def launch_group(store, db, group, org):
    profile = facilitator(store, db, group)
    existing = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs')]
    run = next((j for j in reversed(existing) if j['kind'] == 'launch' and
                j['profile_id'] == profile['id'] and j['state'] != 'released'), None)
    if run:
        return run
    run = dict(id=uuid.uuid4().hex, organization_id=org['id'], kind='launch', profile_id=profile['id'],
               profile=profile, organization=org, alias='group_' + uuid.uuid4().hex[:20],
               state='queued', error='', result='', created_at=now(), updated_at=now())
    store.put(db, 'jobs', run)
    run['_new'] = True
    return run


def action(store, db, name, body, org):
    org_id = org['id']
    if name == 'group':
        ids = body.get('members')
        if not isinstance(ids, list) or not 2 <= len(ids) <= 6 or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Choose 2–6 distinct agents for the group.')
        for profile_id in ids:
            member = store.get(db, 'profiles', profile_id, org_id)
            if member.get('group_id'):
                raise ValueError('Select member agents, not other group conversations.')
        item_id = text(body, 'id', 40, optional=True) or uuid.uuid4().hex
        if body.get('id'):
            store.get(db, 'groups', item_id, org_id)
        previous = store.get(db, 'groups', item_id, org_id) if body.get('id') else {}
        item = dict(id=item_id, organization_id=org_id, name=text(body, 'name'),
                    description=text(body, 'description', 8000), members=ids, updated_at=now())
        if previous.get('facilitator_id'):
            item['facilitator_id'] = previous['facilitator_id']
        run = launch_group(store, db, item, org)
        if run.get('_new'):
            item['launch_job_id'] = run['id']
        return item
    if name in ('chat', 'inspect', 'input'):
        runs = [active_run(store, db, org_id, text(body, 'profile_id', 40))]
    else:
        group = store.get(db, 'groups', text(body, 'group_id', 40), org_id)
        runs = [active_run(store, db, org_id, profile_id) for profile_id in group['members']]
        group_run = launch_group(store, db, group, org)
        if group_run['state'] not in ('queued', 'running', 'persona_sent'):
            raise ValueError('Inspect and release the group agent run before retrying.')
    if name == 'inspect':
        agent = store.identity(runs[0], ready=False)
        status = agent.get('agent_status', agent.get('state', 'unknown'))
        source = 'recent-unwrapped' if status in ('idle', 'done') else 'visible'
        output = store.command('agent', 'read', runs[0]['alias'], '--source', source, '--lines', '160')
        # A reply file may appear while the agent is still writing it. Never read
        # beyond the reply limit or follow a symlink from agent-controlled output.
        draft = ''
        jobs = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs WHERE organization_id=?', (org_id,))]
        current = next((j for j in reversed(jobs) if j['kind'] == 'chat' and
                        j.get('profile_id') == runs[0]['profile_id'] and j['state'] == 'running'), None)
        if current:
            path = store.path.parent / 'chat-replies' / current['id'] / 'reply.md'
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 40000:
                draft = path.read_text(encoding='utf-8', errors='replace')[:40000]
        return {'output': output['output'], 'status': status, 'reply_draft': draft,
                'reply_job_id': current['id'] if current else None}

    ids = {run['profile_id'] for run in runs}
    if name == 'discuss':
        ids.add(group_run['profile_id'])
    for row in db.execute('SELECT data FROM jobs'):
        job = json.loads(row['data'])
        if name == 'discuss' and job['id'] == group_run['id']:
            continue
        if job['state'] in ('queued', 'running') and ids.intersection(job.get('participants', [job.get('profile_id')])):
            raise ValueError('An agent already has a queued or running task. Wait for it to finish.')
    item = dict(id=uuid.uuid4().hex, organization_id=org_id,
                kind=name if name in ('chat', 'input') else 'discussion', participants=list(ids), runs=runs,
                state='queued', error='', result='', created_at=now(), updated_at=now())
    if name == 'input':
        key = text(body, 'key', 16)
        if key not in ('enter', 'esc', 'up', 'down', 'tab', 'ctrl+c'):
            raise ValueError('Unsupported terminal key.')
        item.update(profile_id=runs[0]['profile_id'], key=key)
    elif name == 'chat':
        item.update(profile_id=runs[0]['profile_id'], prompt=text(body, 'prompt', 8000))
    else:
        item.update(group_id=group['id'], group=group, group_run=group_run,
                    prompt=text(body, 'prompt', 8000, optional=True) or group['description'],
                    contributions=[], progress='Waiting for group agent')
    store.put(db, 'jobs', item)
    return item


def current_run(store, run, ready=True):
    with store.lock, closing(store.connect()) as db:
        current = store.get(db, 'jobs', run['id'], run['organization_id'])
    if current['state'] != 'persona_sent':
        raise ValueError('Run binding was released. Launch and select an agent again.')
    store.identity(current, ready=ready)
    return current


def prompt_and_wait(store, run, prompt):
    run = current_run(store, run)
    store.command('agent', 'prompt', run['alias'], prompt, '--wait', '--timeout', '180000', timeout=190)
    # Blocked, replaced, or released bindings never count as completed turns.
    current_run(store, run)


def read_contribution(path, limit=40000):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f'Agent did not produce a valid discussion document (maximum {limit} bytes).')
    result = path.read_text(encoding='utf-8').strip()
    if not result:
        raise ValueError('Agent produced an empty discussion document.')
    return result


def execute(store, job):
    if job['kind'] == 'input':
        run = current_run(store, job['runs'][0], ready=False)
        store.command('agent', 'send-keys', run['alias'], job['key'])
        current_run(store, run, ready=False)
        store.update_job(job['id'], state='input_sent')
        return
    if job['kind'] == 'chat':
        run = job['runs'][0]
        directory = store.path.parent / 'chat-replies' / job['id']
        directory.mkdir(parents=True, mode=0o700)
        reply = directory / 'reply.md'
        prompt = (
            f"User message:\n{job['prompt']}\n\n"
            "Dashboard reply delivery: answer the user message in this conversation. "
            f"Write your final answer as UTF-8 Markdown to exactly {reply}, below 40 KB. "
            "The file must contain only your answer, including relevant code and examples. "
            "Exclude terminal menus, status panels, prior conversation, prompt echoes and tool logs. "
            "Do not change the substance of the user's request. Use your file-writing tools to save "
            "this reply file even for a read-only advisory role; do not modify other files unless "
            "the user's request authorizes it. Finish after saving. If you cannot write the file, "
            "report that limitation rather than claiming delivery."
        )
        prompt_and_wait(store, run, prompt)
        result = read_contribution(reply)
        store.update_job(job['id'], state='answered', result=result, result_format='markdown',
                         reply_name='reply.md')
        return
    group_run = job['group_run']
    with store.lock, closing(store.connect()) as db:
        group_run = store.get(db, 'jobs', group_run['id'])
    if group_run['state'] == 'queued':
        store.execute(group_run['id'])
    group_run = current_run(store, group_run)
    for run in job['runs']:
        current_run(store, run)
    directory = store.path.parent / 'discussion-artifacts' / job['id']
    directory.mkdir(parents=True, mode=0o700)
    artifact = directory / 'action-plan.md'
    transcript = directory / 'discussion.json'
    roster = '\n'.join(f"{r['profile']['name']} ({r['profile']['role']}): {r['alias']}" for r in job['runs'])
    store.update_job(job['id'], progress='Group agent coordinating members')
    prompt = (
        f"Group: {job['group']['name']}\nPurpose: {job['group']['description']}\n"
        f"User message:\n{job['prompt']}\n\nSelected members (live Herdr aliases):\n{roster}\n\n"
        "You are the group conversation and facilitator. Read herdr --skill. Use herdr agent prompt <alias> "
        "with --wait --timeout 180000 to ask selected members for input. Read their results or ask them to "
        "write Markdown files in the discussion directory. Choose the rounds and follow-ups needed, "
        "include disagreements, and synthesize the answer yourself. Do not prompt yourself, create other "
        "agents, or contact anyone outside this roster. If a member is blocked, stop and report the issue. "
        "This is an advisory discussion: do not modify projects or execute recommended actions. "
        f"Discussion directory: {directory}\n"
        f"Write the final UTF-8 Markdown artifact to exactly {artifact}, below 40 KB. "
        "Include actions, evidence, risks, proposed owners and acceptance criteria. "
        f"Also write {transcript} as JSON with a contributions array. Each entry must have "
        "profile_id (from the roster below), name, round (integer 1 to 20), and content (Markdown under 6 KB). "
        f"Profile IDs: {json.dumps({r['alias']: r['profile_id'] for r in job['runs']})}. "
        "Record actual member responses, never invent them. Save both files before finishing."
    )
    run = current_run(store, group_run)
    store.command('agent', 'prompt', run['alias'], prompt, '--wait', '--timeout', '900000', timeout=910)
    current_run(store, run)
    for member in job['runs']:
        current_run(store, member)
    result = read_contribution(artifact)
    data = json.loads(read_contribution(transcript, 750000))
    contributions = data.get('contributions')
    ids = {r['profile_id']: r['profile']['name'] for r in job['runs']}
    if not isinstance(contributions, list) or not 1 <= len(contributions) <= 120:
        raise ValueError('Group agent did not provide a valid discussion transcript.')
    for part in contributions:
        if (not isinstance(part, dict) or part.get('profile_id') not in ids or
            type(part.get('round')) is not int or not 1 <= part['round'] <= 20 or
            not isinstance(part.get('content'), str) or not part['content'].strip() or
            len(part['content'].encode('utf-8')) > 6000):
            raise ValueError('Invalid member contribution in group transcript.')
        part['name'] = ids[part['profile_id']]
    store.update_job(job['id'], state='artifact_ready', result=result, contributions=contributions,
                     artifact_name='action-plan.md', progress='Group response ready')
