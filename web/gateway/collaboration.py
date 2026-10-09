"""Persisted agent chat and dedicated Herdr group conversations."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import subprocess
import json
from pathlib import Path
import uuid

from organizations import now, text
from permissions import accessible_paths, permission_mode


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
                   permission_mode=group.get('permission_mode', 'default'),
                   accessible_paths=group.get('accessible_paths', []),
                   use_worktree=group.get('use_worktree', True),
                   **{key: first.get(key, '') for key in ('provider', 'model', 'reasoning')},
                   persona='You are this group conversation. Coordinate its selected members using Herdr agent commands. '
                           'Stay within the user topic, ask follow-ups only when they can change the answer, '
                           'and stop once there is enough information. Do not manufacture disagreements or work. '
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
                j['profile_id'] == profile['id'] and j['state'] not in ('released', 'finished')), None)
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
    if name in ('retry_discussion', 'cancel_discussion'):
        job = store.get(db, 'jobs', text(body, 'job_id', 40), org_id)
        if job['kind'] != 'discussion' or job.get('preparation_version') != 1 or job.get('discussion_submitted_at') or job['state'] not in ('waiting_for_members', 'needs_attention', 'uncertain', 'queued', 'cancelled'):
            raise ValueError('Only a discussion that has not been submitted can be retried or cancelled. Recover submitted artifacts instead.')
        if body.get('inspected') is not True:
            raise ValueError('Inspect the participant sessions first.')
        if name == 'cancel_discussion':
            job.update(state='cancelled', progress='Cancelled before discussion submission', cancelled_at=now())
        else:
            if job['state'] not in ('needs_attention', 'uncertain'):
                raise ValueError('Only a preparation needing attention can be retried.')
            attempts = job.get('preparation_attempt', 0) + 1
            if attempts > 3:
                raise ValueError('Preparation retry limit reached; inspect and create a new meeting.')
            job.update(state='queued', preparation_attempt=attempts, preparation={}, runs=[], waiting_since=None,
                       error='', progress='Retrying inspected preparation; no previous discussion prompt was sent')
        job['updated_at'] = now()
        store.put(db, 'jobs', job)
        return job
    if name == 'transcript':
        job = store.get(db, 'jobs', text(body, 'job_id', 40), org_id)
        if job['kind'] != 'discussion' or job['state'] != 'artifact_ready':
            raise ValueError('Download the transcript after the discussion is finalized.')
        data = discussion_transcript(store, job)
        return dict(content=json.dumps(data, ensure_ascii=False, indent=2) + '\n', filename='discussion.json')
    if name == 'recover':
        job = store.get(db, 'jobs', text(body, 'job_id', 40), org_id)
        if job['kind'] != 'discussion' or job['state'] not in ('needs_attention', 'uncertain', 'artifact_ready'):
            raise ValueError('Only interrupted discussions can recover saved output.')
        if job['state'] == 'artifact_ready':
            return job
        participants = set(job['participants'])
        for row in db.execute('SELECT data FROM jobs WHERE organization_id=?', (org_id,)):
            other = json.loads(row['data'])
            if other['id'] != job['id'] and other['state'] in ('queued', 'running') and participants.intersection(other.get('participants', [other.get('profile_id')])):
                raise ValueError('Wait for other tasks using these agents before recovering output.')
        for saved in [job['group_run'], *job['runs']]:
            run = store.get(db, 'jobs', saved['id'], org_id)
            if run['state'] != 'persona_sent':
                raise ValueError('Run binding was released. Saved output cannot be recovered from this conversation.')
            try:
                store.identity(run)
            except ValueError as error:
                raise ValueError(f"{run['profile']['name']}: {error}") from error
        result, contributions = discussion_documents(store, job)
        job.update(state='artifact_ready', result=result, contributions=contributions,
                   artifact_name='action-plan.md', progress='Saved group response recovered',
                   previous_error=job.get('error', ''), error='', recovered_at=now(), updated_at=now())
        store.put(db, 'jobs', job)
        return job
    if name == 'inspect' and body.get('job_id'):
        return inspect_discussion(store, db, body, org_id)
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
                    description=text(body, 'description', 8000), members=ids, updated_at=now(),
                    read_only=body.get('read_only', True),
                    permission_mode=permission_mode(body, member['runtime']))
        item['accessible_paths'] = accessible_paths(body, member['runtime'])
        item['use_worktree'] = body.get('use_worktree', True)
        if not isinstance(item['use_worktree'], bool):
            raise ValueError('Use worktree must be true or false.')
        if not isinstance(item['read_only'], bool):
            raise ValueError('Read-only must be true or false.')
        if (item['permission_mode'] != 'default' or item['accessible_paths']) and any(store.get(db, 'profiles', i, org_id)['runtime'] != 'opencode' for i in ids):
            raise ValueError('Choose CLI defaults for groups containing other runtimes.')
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
        # Preparation happens outside this transaction, under participant locks.
        # The roster is frozen on the discussion; no member must be turned on manually.
        runs = []
        group_run = launch_group(store, db, group, org)
    if name == 'inspect':
        agent = store.identity(runs[0], ready=False, agent=store.command('agent', 'get', runs[0]['alias'], timeout=2).get('agent'))
        status = agent.get('agent_status', agent.get('state', 'unknown'))
        source = 'recent-unwrapped' if status in ('idle', 'done') else 'visible'
        output = store.command('agent', 'read', runs[0]['alias'], '--source', source, '--lines', '160', timeout=2)
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

    ids = set(group['members']) if name == 'discuss' else {run['profile_id'] for run in runs}
    if name == 'discuss':
        ids.add(group_run['profile_id'])
    for row in db.execute('SELECT data FROM jobs'):
        job = json.loads(row['data'])
        if name == 'discuss' and job['id'] == group_run['id']:
            continue
        if name != 'discuss' and job['state'] in ('queued', 'running') and ids.intersection(job.get('participants', [job.get('profile_id')])):
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
        item.update(profile_id=runs[0]['profile_id'], prompt=text(body, 'prompt', 8000),
                    wait_seconds=wait_seconds(body), integration_event=body.get('integration_event'))
    else:
        item.update(group_id=group['id'], group=group, group_run=group_run,
                    prompt=text(body, 'prompt', 8000, optional=True) or group['description'],
                    contributions=[], progress='Preparing discussion sessions', preparation={}, preparation_version=1)
    store.put(db, 'jobs', item)
    return item


def current_run(store, run, ready=True):
    with store.lock, closing(store.connect()) as db:
        current = store.get(db, 'jobs', run['id'], run['organization_id'])
    if (run.get('alias') != current.get('alias') or
            (run.get('agent_session') and run.get('agent_session') != current.get('agent_session'))):
        raise ValueError('Original conversation was replaced; this reply cannot be recovered into the new session.')
    if current['state'] != 'persona_sent':
        raise ValueError('Run binding was released. Launch and select an agent again.')
    try:
        store.identity(current, ready=ready)
    except ValueError as error:
        raise ValueError(f"{current['profile']['name']}: {error}") from error
    return current


def prompt_and_wait(store, run, prompt, wait=180, job_id=None, reply_name=None):
    run = current_run(store, run)
    if job_id:
        evidence = dict(stage='submitting', started_at=now(), alias=run['alias'], wait_seconds=wait)
        if reply_name:
            # Persist the attempt's exact reply file before any terminal input.
            evidence['reply_name'] = reply_name
        store.update_job(job_id, delivery=evidence)
    # A successful CLI return is evidence of a command response, not proof that
    # the model processed this turn. Only the job-specific reply proves delivery.
    response = store.command('agent', 'prompt', run['alias'], prompt, '--wait', '--timeout', str(wait * 1000), timeout=wait + 10)
    if job_id:
        evidence.update(stage='command_returned', returned_at=now())
        if isinstance(response, dict):
            # The live CLI returns {"agent": {...}}. Retain only bounded scalar
            # protocol fields; never persist echoed prompts or terminal output.
            source = response.get('agent') if isinstance(response.get('agent'), dict) else response
            evidence['response'] = {k: v for k, v in source.items()
                                    if k in ('type', 'status', 'state', 'detected', 'kind', 'completed',
                                             'timed_out', 'timeout')
                                    and isinstance(v, (str, bool, int)) and len(str(v)) <= 160}
        store.update_job(job_id, delivery=evidence)
    # Blocked, replaced, or released bindings never count as completed turns.
    current_run(store, run)
    if job_id:
        evidence.update(stage='session_idle_reply_pending', checked_at=now())
        store.update_job(job_id, delivery=evidence)
        return evidence


def wait_seconds(body):
    """Bound how long a chat request waits for the agent's reply file."""
    value = body.get('wait_seconds', 180)
    if type(value) is not int or not 30 <= value <= 3600:
        raise ValueError('Wait seconds must be an integer between 30 and 3600.')
    return value


def read_contribution(path, limit=40000, label='discussion document'):
    """Read bounded output; preserve the exact reason for operator recovery."""
    prefix = f'Agent did not produce a valid {label}'
    if path.is_symlink():
        raise ValueError(f'{prefix}: symlink rejected at {path}.')
    try:
        if not path.is_file():
            raise ValueError(f'{prefix}: file missing or not a regular file at {path}.')
        size = path.stat().st_size
        if size > limit:
            raise ValueError(f'{prefix}: {size} bytes exceeds maximum {limit} at {path}.')
        # Bound the actual read too, rather than trusting a pre-read stat alone.
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f'{prefix}: exceeds maximum {limit} bytes at {path}.')
        result = data.decode('utf-8').strip()
    except FileNotFoundError:
        raise ValueError(f'{prefix}: file missing at {path}.') from None
    except (OSError, UnicodeError) as error:
        raise ValueError(f'{prefix}: unreadable UTF-8 file at {path} ({type(error).__name__}).') from error
    if not result:
        raise ValueError(f'{prefix}: empty file at {path}.')
    return result


def validate_contributions(contributions, runs):
    ids = {r['profile_id']: r['profile']['name'] for r in runs}
    if not isinstance(contributions, list) or not 1 <= len(contributions) <= 120:
        raise ValueError('Group agent did not provide a valid discussion transcript.')
    for part in contributions:
        if (not isinstance(part, dict) or part.get('profile_id') not in ids or
            type(part.get('round')) is not int or not 1 <= part['round'] <= 20 or
            not isinstance(part.get('content'), str) or not part['content'].strip() or
            len(part['content'].encode('utf-8')) > 6000):
            raise ValueError('Invalid member contribution in group transcript.')
        part['name'] = ids[part['profile_id']]
    return contributions


def discussion_directory(store, job):
    directory = store.path.parent / 'discussion-artifacts' / job['id']
    if directory.is_symlink():
        raise ValueError('Discussion output directory must not be a symlink.')
    return directory


def discussion_transcript(store, job):
    """Normalized export; the original agent-written document stays untouched."""
    data = json.loads(read_contribution(discussion_directory(store, job) / 'discussion.json', 750000))
    if not isinstance(data, dict):
        raise ValueError('Discussion transcript must be a JSON object.')
    validate_contributions(data.get('contributions'), job['runs'])
    return data


def discussion_documents(store, job):
    result = read_contribution(discussion_directory(store, job) / 'action-plan.md')
    return result, discussion_transcript(store, job)['contributions']


def inspect_discussion(store, db, body, org_id):
    job = store.get(db, 'jobs', text(body, 'job_id', 40), org_id)
    if job['kind'] != 'discussion':
        raise ValueError('Select a discussion job.')
    runs = []
    for saved in [job['group_run'], *job['runs']]:
        try:
            run = store.get(db, 'jobs', saved['id'], org_id)
            if run['state'] != 'persona_sent':
                raise ValueError('Agent conversation is not launched or was released.')
            runs.append((saved, run, None))
        except ValueError as error:
            runs.append((saved, None, str(error)))

    def inspect(binding):
        saved, run, error = binding
        stream = dict(profile_id=saved['profile_id'], name=saved['profile']['name'],
                      status='unavailable', output='', error=error or '')
        if run is None:
            return stream
        try:
            response = store.command('agent', 'get', run['alias'], timeout=2)
            agent = store.identity(run, ready=False, agent=response.get('agent'))
            status = agent.get('agent_status', agent.get('state', 'unknown'))
            output = store.command('agent', 'read', run['alias'], '--source',
                                   'visible' if status not in ('idle', 'done') else 'recent-unwrapped',
                                   '--lines', '80', timeout=2)['output']
            # Do not publish output if a binding changed during the CLI read.
            store.identity(run, ready=False, agent=store.command('agent', 'get', run['alias'], timeout=1).get('agent'))
            stream.update(status=status, output=output[:20000])
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            stream['error'] = str(error)
        return stream

    # Seven bindings maximum, two bounded batches; no SQLite handles in workers.
    with ThreadPoolExecutor(max_workers=4) as reader:
        streams = list(reader.map(inspect, runs))
    directory = store.path.parent / 'discussion-artifacts' / job['id']
    draft = ''
    contributions = []
    if not directory.is_symlink():
        try:
            draft = read_contribution(directory / 'action-plan.md')
        except (ValueError, OSError, UnicodeError):
            pass  # Missing or incomplete files are normal during an active turn.
        try:
            contributions = discussion_transcript(store, job)['contributions']
        except (ValueError, OSError, UnicodeError, AttributeError):
            pass
    return dict(job_id=job['id'], streams=streams, artifact_draft=draft, contributions=contributions)


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
        if directory.is_symlink() or directory.parent.is_symlink():
            raise ValueError('Reply directories must not be symlinks.')
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        # One file per execution attempt: a late or repeated invocation can never
        # overwrite, or be mistaken for, another attempt's reply. Legacy jobs
        # keep reading their original reply.md.
        reply_name = 'reply-' + uuid.uuid4().hex[:12] + '.md'
        reply = directory / reply_name
        prompt = (
            f"User message:\n{job['prompt']}\n\n"
            "Returning JSON in the conversation does not replace saving this file. "
            f"Reply contract for job {job['id']}. "
            "Dashboard reply delivery: answer the user message in this conversation. "
            f"Write the exact final reply the task requires as UTF-8 to exactly {reply}, below 40 KB: "
            "Markdown for an ordinary answer, or exactly the JSON the task specifies with no prose around it. "
            "The file must contain only that reply, including relevant code and examples. "
            "Exclude terminal menus, status panels, prior conversation, prompt echoes and tool logs. "
            "Do not change the substance of the user's request. Use your file-writing tools to save "
            "this reply file even for a read-only advisory role; do not modify other files unless "
            "the user's request authorizes it. Finish after saving. If you cannot write the file, "
            "report that limitation rather than claiming delivery."
        )
        evidence = prompt_and_wait(store, run, prompt, job.get('wait_seconds', 180), job_id=job['id'],
                                   reply_name=reply_name)
        result = read_contribution(reply, label='chat reply')
        store.update_job(job['id'], state='answered', result=result, result_format='markdown',
                         reply_name=reply_name, delivery=dict(evidence, stage='reply_verified',
                         verified_at=now(), bytes=len(result.encode('utf-8'))))
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
    def workspace_context(run):
        live = store.identity(run)
        return {'name': run['profile']['name'], 'alias': run['alias'],
                'configured_project': run['profile']['project'],
                'launch_directory': run.get('worktree_path') or run['profile']['project'],
                'herdr_reported_cwd': live.get('cwd') if isinstance(live.get('cwd'), str) else None}

    contexts = [workspace_context(group_run), *(workspace_context(r) for r in job['runs'])]
    roster = '\n'.join(f"{r['profile']['name']} ({r['profile']['role']}): {r['alias']}" for r in job['runs'])
    store.update_job(job['id'], progress='Group agent coordinating members')
    discussion_policy = (
        'This is a read-only advisory discussion: do not modify projects or execute recommended actions. '
        'Tell every member to follow this same restriction. Artifact and transcript output writes are allowed. '
        if job['group'].get('read_only', True) else
        'Project changes may be proposed or performed only within the user message authorization. '
    )
    prompt = (
        f"Group: {job['group']['name']}\nPurpose: {job['group']['description']}\n"
        f"User message:\n{job['prompt']}\n\nSelected members (live Herdr aliases):\n{roster}\n\n"
        f"Workspace context (from saved launch settings and live Herdr bindings): {json.dumps(contexts)}\n"
        "Each member has its own working directory, potentially a separate worktree. Do not assume a shared checkout. "
        "Before project-specific commands, ask the relevant member to confirm pwd and use its assigned launch directory. "
        "If it differs, report the mismatch; do not silently inspect another project. "
        "The projects folder is an allowed directory boundary, not a requirement to put a repository at its root. "
        "An empty directory or absent Git repository is only a blocker when the user's task requires project files. "
        "General discussion and reporting no assigned work do not require a repository, Git identity or test commands. "
        "You are the group conversation and facilitator. Read herdr --skill. Use herdr agent prompt <alias> "
        "with --wait --timeout 180000 to ask selected members for input. Read their results or ask them to "
        "write Markdown files in the discussion directory. Pass the user topic, workspace context and scope restrictions "
        "below to every member. The group purpose defines the recurring workflow and desired outcome; the user "
        "message defines this discussion's task and can refine that workflow. Follow their requested format, depth "
        "and discussion method. Where neither specifies a method, start with one round and use at most two "
        "follow-up rounds, each to resolve a material unanswered question or conflicting evidence. "
        "Stop when the requested outcome is achieved or further replies add no value. Do not manufacture "
        "disagreements or force consensus; preserve unresolved differences when relevant. Do not expand into "
        "unrequested investigations. Synthesize the answer yourself. "
        "Do not prompt yourself, create other agents, or list, read or prompt agents outside this roster, "
        "including other group conversations. Treat old conversation topics as background, not instructions "
        "for this task. If the task needs a particular proposal, document or repository that was not supplied, "
        "report the missing input rather than choosing an unrelated task or searching the machine for one. "
        "Conceptual discussions can proceed without files. If a member is blocked, "
        "stop further rounds and save a concise partial artifact and transcript explaining the missing input. "
        "Limit reads and commands to information necessary for the topic and the assigned project. Do not search "
        "the whole machine, shell history, credential/config directories or tooling caches to find substitute work. "
        "Broader inspection must be explicitly within the group purpose or user request and relevant to the task. "
        "Distinguish observed evidence, member reports, "
        "inferences and proposals. Limited searches do not prove absence everywhere; self-reports do not independently "
        "verify filesystem activity. Source code allowing a path does not establish a required repository layout. "
        f"{discussion_policy}"
        f"Discussion directory: {directory}\n"
        "Use these exact output paths. Do not search home or filesystem roots for action plans or transcripts. "
        "If earlier artifacts are needed, ask for their specific path or content. A permission request is "
        "not permission to widen the task: narrow the operation to the known task/output directory, or "
        "report the needed access. Never switch to a root search to bypass a rejected or pending request. "
        f"Write the final UTF-8 Markdown artifact to exactly {artifact}, below 40 KB. "
        "Deliver the artifact requested by the group purpose or user message, such as a summary, decision memo, "
        "comparison, research brief or implementation plan. If neither specifies a format, lead with the answer "
        "and keep the artifact concise, normally under 800 words. Requested detail overrides this default, "
        "within the file size limit. Include actions, evidence, risks, proposed owners and acceptance "
        "criteria only where useful for this task; do not invent requirements or owners. If nothing needs action, "
        "say so. Keep detailed round history in the transcript rather than repeating it in the artifact. "
        f"Also write {transcript} as JSON with a contributions array. Each entry must have "
        "profile_id (from the roster below), name, round (integer 1 to 20), and content (Markdown under 6 KB). "
        f"Profile IDs: {json.dumps({r['alias']: r['profile_id'] for r in job['runs']})}. "
        "Record actual member responses, never invent them. Update discussion.json after each member reply "
        "using a complete valid JSON document so the dashboard can show rounds as they arrive. "
        "Write and update the artifact as your synthesis develops. Save both files before finishing."
    )
    run = current_run(store, group_run)
    store.update_job(job['id'], discussion_submitted_at=now(), progress='Discussion submitted; awaiting saved artifacts')
    store.command('agent', 'prompt', run['alias'], prompt, '--wait', '--timeout', '900000', timeout=910)
    current_run(store, run)
    for member in job['runs']:
        current_run(store, member)
    result, contributions = discussion_documents(store, job)
    store.update_job(job['id'], state='artifact_ready', result=result, contributions=contributions,
                     artifact_name='action-plan.md', progress='Group response ready')
