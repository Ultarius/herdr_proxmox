"""Durable organization records and explicit, non-retrying Herdr jobs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
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


class OrganizationStore:
    def __init__(self, path, projects, command):
        self.path = Path(path)
        self.projects = Path(projects).resolve()
        self.command = command
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
            return {table: [json.loads(row['data']) for row in db.execute(f'SELECT data FROM {table} ORDER BY rowid')]
                    for table in ('organizations', 'profiles', 'jobs', 'groups')}

    def project(self, value):
        path = Path(value).expanduser().resolve()
        if not path.is_relative_to(self.projects) or not path.is_dir():
            raise ValueError('Select an existing directory inside /home/herdr/projects.')
        return str(path)

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
                if action in ('group', 'discuss', 'chat', 'inspect', 'input'):
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
                                project=self.project(text(body, 'project', 2000)), version=(previous['version'] if previous else 0) + 1)
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

    def identity(self, run, ready=True):
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
                project = self.project(profile['project'])
                created = self.command('workspace', 'create', '--cwd', project, '--label', profile['name'], '--no-focus')
                pane = created.get('root_pane', {}).get('pane_id')
                if not isinstance(pane, str) or not re.fullmatch(r'w[0-9]+:p[0-9]+', pane):
                    raise ValueError('Herdr did not return a valid root pane ID.')
                job = self.update_job(job_id, pane_id=pane)
                self.wait_for_shell(pane)
                self.command('agent', 'start', job['alias'], '--kind', profile['runtime'], '--pane', pane, '--timeout', '60000', timeout=70)
                agent = self.identity(job)
                job = self.update_job(job_id, agent_session=agent.get('agent_session'))
                org = job['organization']
                prompt = (f"Organization: {org['name']}\nPurpose: {org['purpose']}\nShared instructions:\n{org['instructions']}\n\n"
                          f"You are {profile['name']}, our {profile['role']}. Persona (version {profile['version']}):\n{profile['persona']}\n\n"
                          'Read and follow the project owner instructions. Adopt this persona for this conversation. '
                          'Acknowledge readiness and wait for an assigned task. Use Herdr agent commands for explicit delegation; '
                          'do not interpret a delivered prompt or idle status as proof of completed work.')
                if profile.get('group_id'):
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
