"""Durable organization records and explicit, non-retrying Herdr jobs."""
from permissions import accessible_paths, permission_mode, prepare_permissions
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def text(body, key, limit=120, optional=False):
    value = body.get(key, '')
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError(f'Invalid {key} (maximum {limit} characters).')
    value = value.strip()
    if not value and not optional:
        raise ValueError(f'{key} is required.')
    return value


def model_settings(body, runtime):
    values = {key: text(body, key, 160, optional=True) for key in ('provider', 'model', 'reasoning')}
    for key, value in values.items():
        if value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]*', value):
            raise ValueError(f'Invalid {key} identifier.')
    if runtime == 'agy' and any(values.values()):
        raise ValueError('Antigravity model settings are managed in its own CLI.')
    if runtime == 'opencode' and (bool(values['provider']) != bool(values['model'])):
        raise ValueError('OpenCode requires both provider and model, or leave both at CLI defaults.')
    if runtime == 'opencode' and values['reasoning'] and not values['model']:
        raise ValueError('Select an OpenCode provider and model before choosing a reasoning variant.')
    if runtime == 'claude' and values['provider'] not in ('', 'anthropic'):
        raise ValueError('Claude uses its configured account/provider. Configure alternate providers in its CLI.')
    return values


def launch_arguments(profile):
    runtime = profile['runtime']
    settings = model_settings(profile, runtime)
    provider, model, reasoning = (settings[k] for k in ('provider', 'model', 'reasoning'))
    args = []
    if runtime == 'opencode' and model:
        args += ['--model', f'{provider}/{model}' + (f'#{reasoning}' if reasoning else '')]
    elif runtime == 'codex':
        if model:
            args += ['--model', model]
        for key, value in [('model_provider', provider), ('model_reasoning_effort', reasoning)]:
            if value:
                args += ['--config', f'{key}={json.dumps(value)}']
    elif runtime == 'claude':
        if model:
            args += ['--model', model]
        if reasoning:
            args += ['--effort', reasoning]
    return args


class OrganizationStore:
    def __init__(self, path, projects, command, runtime_status=None, model_validator=None):
        self.path = Path(path)
        self.projects = Path(projects).resolve()
        self.command = command
        self.runtime_status = runtime_status
        self.model_validator = model_validator
        self.lock = threading.RLock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='organization')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS organizations (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL);
            ''')
            # A crash may have occurred after terminal input. Never replay automatically.
            for row in db.execute('SELECT id, data FROM jobs').fetchall():
                job = json.loads(row['data'])
                if job['state'] in ('queued', 'running'):
                    job.update(state='uncertain', error='Gateway restarted. Inspect the SSH terminal before creating another run or task.', updated_at=now())
                    self.put(db, 'jobs', job)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def close(self):
        self.worker.shutdown(wait=True)

    def put(self, db, table, item):
        if table == 'organizations':
            db.execute('INSERT INTO organizations VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (item['id'], json.dumps(item)))
        else:
            db.execute(f'INSERT INTO {table} VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (item['id'], item['organization_id'], json.dumps(item)))

    def get(self, db, table, item_id, organization_id=None):
        row = db.execute(f'SELECT data FROM {table} WHERE id=?', (item_id,)).fetchone()
        if not row:
            raise ValueError('Record not found.')
        item = json.loads(row['data'])
        if organization_id and item.get('organization_id') != organization_id:
            raise ValueError('Record belongs to another organization.')
        return item

    def snapshot(self):
        with self.lock, closing(self.connect()) as db:
            data = {table: [json.loads(row['data']) for row in db.execute(f'SELECT data FROM {table} ORDER BY rowid')]
                    for table in ('organizations', 'profiles', 'jobs', 'groups')}
        states = {}
        live = None
        if any(j['kind'] == 'launch' and j['state'] == 'persona_sent' for j in data['jobs']):
            try:
                response = self.command('agent', 'list')
                entries = response if isinstance(response, list) else response.get('agents')
                if isinstance(entries, list):
                    live = {agent['name']: agent for agent in entries if isinstance(agent, dict) and isinstance(agent.get('name'), str)}
            except (ValueError, OSError, subprocess.TimeoutExpired):
                pass
        for profile in data['profiles']:
            run = next((j for j in reversed(data['jobs']) if j['kind'] == 'launch' and j['profile_id'] == profile['id'] and j['state'] != 'released'), None)
            state = dict(active=False, status='off', run_id=run['id'] if run else None)
            if run:
                state['status'] = run['state']
                if run['state'] == 'persona_sent':
                    try:
                        if live is None:
                            state['status'] = 'unknown'
                            states[profile['id']] = state
                            continue
                        if run['alias'] not in live:
                            state['status'] = 'off'
                            states[profile['id']] = state
                            continue
                        agent = self.identity(run, ready=False, agent=live[run['alias']])
                        status = agent.get('agent_status', agent.get('state', 'unknown'))
                        state.update(active=status in ('idle', 'done', 'working', 'blocked'), status=status)
                    except (ValueError, OSError):
                        state['status'] = 'unavailable'
            states[profile['id']] = state
        data['member_states'] = states
        return data

    def project(self, value):
        path = Path(value).expanduser().resolve()
        if not path.is_relative_to(self.projects) or not path.is_dir():
            raise ValueError('Select an existing directory inside /home/herdr/projects.')
        return str(path)

    def label_agents(self, agents):
        """Add dashboard labels only to verified live run bindings; preserve Herdr names."""
        with self.lock, closing(self.connect()) as db:
            profiles = {row['id']: json.loads(row['data']) for row in db.execute('SELECT id, data FROM profiles')}
            jobs = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs')]
        result = []
        for agent in agents:
            item = dict(agent)
            for run in reversed(jobs):
                if run['kind'] != 'launch' or run['state'] not in ('running', 'persona_sent', 'needs_attention') or run.get('alias') != agent.get('name'):
                    continue
                # Label initialization/error states only after the launched conversation
                # has been captured; an alias alone does not prove ownership.
                if not run.get('pane_id') or (run['state'] != 'persona_sent' and not run.get('agent_session')):
                    continue
                profile = profiles.get(run['profile_id'])
                if profile is None:
                    continue
                try:
                    self.identity(run, ready=False, agent=agent)
                except ValueError:
                    continue
                item.update(display_name=profile['name'], entity_type='group' if profile.get('group_id') else 'agent',
                            profile_id=profile['id'], group_id=profile.get('group_id'))
                break
            result.append(item)
        return result

    def action(self, action, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        key = text(body, 'request_id', 80)
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', key):
            raise ValueError('Invalid request ID.')
        fingerprint = hashlib.sha256(json.dumps([action, body], sort_keys=True).encode()).hexdigest()
        submitted = None
        with self.lock, closing(self.connect()) as db, db:
            existing = db.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise ValueError('Request ID already used for different content.')
                return json.loads(existing['response'])
            if action == 'save':
                item_id = text(body, 'id', 40, optional=True) or uuid.uuid4().hex
                if body.get('id'):
                    self.get(db, 'organizations', item_id)
                item = dict(id=item_id, name=text(body, 'name'), purpose=text(body, 'purpose', 2000),
                            instructions=text(body, 'instructions', 8000, optional=True))
                self.put(db, 'organizations', item)
            else:
                org_id = text(body, 'organization_id', 40)
                org = self.get(db, 'organizations', org_id)
                if action in ('group', 'discuss', 'chat', 'inspect', 'input', 'recover'):
                    from collaboration import action as collaboration_action
                    item = collaboration_action(self, db, action, body, org)
                    if action == 'group':
                        submitted = item.get('launch_job_id')
                    if action in ('chat', 'discuss', 'input'):
                        submitted = item['id']
                    if action == 'inspect':
                        return item
                elif action == 'hire':
                    item_id = text(body, 'id', 40, optional=True) or uuid.uuid4().hex
                    previous = self.get(db, 'profiles', item_id, org_id) if body.get('id') else None
                    runtime = text(body, 'runtime')
                    if runtime not in ('codex', 'claude', 'opencode', 'agy'):
                        raise ValueError('Unsupported agent runtime.')
                    manager = text(body, 'manager_id', 40, optional=True)
                    visited = {item_id}
                    current = manager
                    while current:
                        if current in visited:
                            raise ValueError('Manager relationships cannot contain cycles.')
                        visited.add(current)
                        current = self.get(db, 'profiles', current, org_id)['manager_id']
                    item = dict(id=item_id, organization_id=org_id, name=text(body, 'name'), role=text(body, 'role'),
                                persona=text(body, 'persona', 8000), runtime=runtime, manager_id=manager,
                                **model_settings(body, runtime), permission_mode=permission_mode(body, runtime),
                                accessible_paths=accessible_paths(body, runtime),
                                project=self.project(text(body, 'project', 2000)), version=(previous['version'] if previous else 0) + 1)
                    item['use_worktree'] = body.get('use_worktree', True)
                    if not isinstance(item['use_worktree'], bool):
                        raise ValueError('Use worktree must be true or false.')
                    self.put(db, 'profiles', item)
                elif action in ('launch', 'delegate'):
                    profile = self.get(db, 'profiles', text(body, 'profile_id', 40), org_id)
                    jobs = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs WHERE organization_id=?', (org_id,))]
                    if action == 'launch':
                        if any(j['kind'] == 'launch' and j['profile_id'] == profile['id'] and j['state'] != 'released' for j in jobs):
                            raise ValueError('Inspect and release the previous run before launching again.')
                        item = dict(id=uuid.uuid4().hex, organization_id=org_id, kind='launch', profile_id=profile['id'],
                                    profile=profile, organization=org, alias='hire_' + uuid.uuid4().hex[:20])
                        self.project(profile['project'])
                    else:
                        sender = self.get(db, 'profiles', text(body, 'sender_id', 40), org_id)
                        if sender['id'] == profile['id']:
                            raise ValueError('Select two different agents.')
                        participants = {sender['id'], profile['id']}
                        if any(j['state'] in ('queued', 'running') and participants.intersection(j.get('participants', [j.get('profile_id')])) for j in jobs):
                            raise ValueError('An agent already has a queued or running task. Wait for it to finish.')
                        runs = {}
                        for p in (sender, profile):
                            runs[p['id']] = next((j for j in reversed(jobs) if j['kind'] == 'launch' and j['profile_id'] == p['id'] and j['state'] == 'persona_sent'), None)
                            if runs[p['id']] is None:
                                raise ValueError('Launch both agents and deliver their personas first.')
                        item = dict(id=uuid.uuid4().hex, organization_id=org_id, kind='delegate', profile_id=profile['id'],
                                    participants=list(participants),
                                    sender_id=sender['id'], task=text(body, 'task', 8000), sender_name=sender['name'],
                                    sender_run=runs[sender['id']], recipient_run=runs[profile['id']])
                    item.update(state='queued', error='', result='', created_at=now(), updated_at=now())
                    self.put(db, 'jobs', item)
                    submitted = item['id']
                elif action in ('release', 'report'):
                    item = self.get(db, 'jobs', text(body, 'job_id', 40), org_id)
                    if item['state'] in ('queued', 'running'):
                        raise ValueError('Wait for the running job to finish.')
                    if action == 'release':
                        if item['kind'] != 'launch':
                            raise ValueError('Only run bindings can be released.')
                        item['state'] = 'released'
                    else:
                        if item['kind'] != 'delegate' or item['state'] not in ('delivered', 'reported_complete'):
                            raise ValueError('Only delivered tasks can have completion reports.')
                        item.update(state='reported_complete', result=text(body, 'result', 8000))
                    item['updated_at'] = now()
                    self.put(db, 'jobs', item)
                else:
                    raise ValueError('Unsupported organization action.')
            response = {'id': item['id']}
            db.execute('INSERT INTO requests VALUES (?, ?, ?)', (key, fingerprint, json.dumps(response)))
        if submitted:
            self.worker.submit(self.execute, submitted)
        return response

    def update_job(self, job_id, **changes):
        with self.lock, closing(self.connect()) as db, db:
            job = self.get(db, 'jobs', job_id)
            job.update(**changes, updated_at=now())
            self.put(db, 'jobs', job)
        return job

    def identity(self, run, ready=True, agent=None):
        if agent is None:
            response = self.command('agent', 'get', run['alias'])
            agent = response.get('agent')
        if not isinstance(agent, dict) or agent.get('name') != run['alias'] or agent.get('pane_id') != run['pane_id'] or agent.get('agent') != run['profile']['runtime']:
            raise ValueError('Agent binding changed. Inspect the terminal and launch a new run.')
        if run.get('agent_session') and agent.get('agent_session') != run['agent_session']:
            raise ValueError('Agent conversation changed. Launch a new run to deliver its persona.')
        if ready and (agent.get('agent_status', agent.get('state')) not in ('idle', 'done') or agent.get('interactive_ready') is False or agent.get('launch_pending') is True):
            raise ValueError('Agent is not ready for input. Check its terminal over SSH.')
        return agent

    def check_contract(self):
        schema = self.command('api', 'schema', '--json')
        properties = schema.get('schemas', {}).get('success_response', {}).get('$defs', {}).get('AgentInfo', {}).get('properties', {})
        if not {'name', 'pane_id', 'agent', 'agent_status'}.issubset(properties):
            raise ValueError('Unsupported Herdr agent schema. Update Herdr and inspect herdr api schema --json before launching.')

    def wait_for_shell(self, pane):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = self.command('pane', 'process-info', '--pane', pane)
            info = result.get('process_info', {})
            shell_pid = info.get('shell_pid')
            if isinstance(shell_pid, int) and shell_pid > 0 and info.get('foreground_process_group_id') == shell_pid:
                return
            time.sleep(0.25)
        raise ValueError('New pane shell did not become available. Inspect its terminal over SSH.')

    def execute(self, job_id):
        job = self.update_job(job_id, state='running')
        try:
            if job['kind'] in ('chat', 'discussion', 'input'):
                from collaboration import execute as collaboration_execute
                collaboration_execute(self, job)
            elif job['kind'] == 'launch':
                self.check_contract()
                profile = job['profile']
                if self.runtime_status is not None:
                    status = self.runtime_status(profile['runtime'])
                    if status.get('installed') is False or status.get('status') in ('missing', 'not_configured') and profile['runtime'] in ('codex', 'claude'):
                        raise ValueError(f"{profile['name']} uses {profile['runtime']}. Connect this CLI on the CLI accounts page before launching; other CLI accounts do not configure it.")
                if self.model_validator is not None:
                    self.model_validator(profile)
                project = self.project(profile['project'])
                source = Path(project)
                repository = any((parent / '.git').exists() for parent in (source, *source.parents))
                if profile.get('use_worktree', True) and repository:
                    root = (self.projects / '.herdr-worktrees').resolve()
                    if not root.is_relative_to(self.projects):
                        raise ValueError('Worktree directory must remain inside the projects directory.')
                    root.mkdir(exist_ok=True, mode=0o700)
                    checkout = root / job['id']
                    branch = 'codex/herdr-' + job['id']
                    job = self.update_job(job_id, worktree_path=str(checkout), worktree_branch=branch,
                                          source_project=project, workspace_mode='worktree')
                    # Never fall back to the shared checkout after a Git/Herdr failure.
                    created = self.command('worktree', 'create', '--cwd', project, '--branch', branch,
                                           '--path', str(checkout), '--label', profile['name'], '--no-focus', timeout=120)
                else:
                    job = self.update_job(job_id, workspace_mode='workspace', source_project=project,
                                          workspace_note='Not a Git repository; using the selected directory.' if profile.get('use_worktree', True) else '')
                    created = self.command('workspace', 'create', '--cwd', project, '--label', profile['name'], '--no-focus')
                pane = created.get('root_pane', {}).get('pane_id')
                if not isinstance(pane, str) or not re.fullmatch(r'w[0-9]+:p[0-9]+', pane):
                    raise ValueError('Herdr did not return a valid root pane ID.')
                job = self.update_job(job_id, pane_id=pane, workspace_id=created.get('workspace', {}).get('workspace_id'))
                self.wait_for_shell(pane)
                arguments = launch_arguments(profile) + prepare_permissions(profile, self.path.parent)
                self.command('agent', 'start', job['alias'], '--kind', profile['runtime'], '--pane', pane, '--timeout', '60000', *(['--', *arguments] if arguments else []), timeout=70)
                agent = self.identity(job)
                job = self.update_job(job_id, agent_session=agent.get('agent_session'))
                org = job['organization']
                prompt = (f"Organization: {org['name']}\nPurpose: {org['purpose']}\nShared instructions:\n{org['instructions']}\n\n"
                          f"You are {profile['name']}, our {profile['role']}. Persona (version {profile['version']}):\n{profile['persona']}\n\n"
                          'Read and follow the project owner instructions. Adopt this persona for this conversation. '
                          'Acknowledge readiness and wait for an assigned task. Use Herdr agent commands for explicit delegation; '
                          'do not interpret a delivered prompt or idle status as proof of completed work.')
                if profile.get('group_id'):
                    # Do not deliver an imperative group description as the startup task.
                    # Purpose, roster and output paths arrive together in the discussion.
                    prompt = (
                        f"Organization: {org['name']}\nGroup conversation: {profile['name']}\n"
                        f"Shared instructions for future tasks:\n{org['instructions']}\n\n"
                        'INITIALIZATION ONLY: This message configures the group conversation; it does not '
                        'start a discussion or authorize project work. '
                        'Acknowledge readiness briefly and wait for a separate discussion message containing '
                        'the group purpose, selected roster, user task and output paths. Until then, do not list, read or prompt '
                        'other agents, inspect project or home directories, search for past artifacts, '
                        'or create files. You may read herdr --skill for the command reference.'
                    )
                    self.command('agent', 'prompt', job['alias'], prompt, '--wait', '--timeout', '180000', timeout=190)
                    self.identity(job)
                else:
                    self.command('agent', 'prompt', job['alias'], prompt)
                self.update_job(job_id, state='persona_sent')
            else:
                # Resolve persisted runs again: the operator may have released one while this was queued.
                with self.lock, closing(self.connect()) as db:
                    sender = self.get(db, 'jobs', job['sender_run']['id'], job['organization_id'])
                    recipient = self.get(db, 'jobs', job['recipient_run']['id'], job['organization_id'])
                if sender['state'] != 'persona_sent' or recipient['state'] != 'persona_sent':
                    raise ValueError('A run was released before task delivery.')
                self.identity(sender)
                self.identity(recipient)
                prompt = (f"Work delegated by {job['sender_name']} ({sender['alias']}).\nTask ID: {job['id']}\n\n{job['task']}\n\n"
                          f"Report the result to the operator over SSH, or use herdr agent prompt {sender['alias']} to reply when it is ready. "
                          'The dashboard operator will record the completion report separately.')
                self.command('agent', 'prompt', recipient['alias'], prompt)
                self.update_job(job_id, state='delivered')
        except Exception as exc:
            # Timeouts/errors can occur after input was sent. Do not retry the job.
            self.update_job(job_id, state='needs_attention', error=str(exc)[:500] + ' Inspect SSH before retrying; terminal input may have been sent.')
