"""Durable organization records and explicit, non-retrying Herdr jobs."""
from concurrent.futures import ThreadPoolExecutor, wait
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

from project_files import project_directory
from permissions import accessible_paths, permission_mode, prepare_permissions
from herdr_ids import is_pane_id, is_workspace_id


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
        self.worker = ThreadPoolExecutor(max_workers=4, thread_name_prefix='organization')
        self.agent_locks = {}
        self.futures = set()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS organizations (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_by_organization ON jobs(organization_id);
                CREATE INDEX IF NOT EXISTS discussion_by_group ON jobs(organization_id, json_extract(data, '$.group_id'), json_extract(data, '$.kind'));
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
        if item.get('removed_at'):
            raise ValueError('Record has been removed.')
        return item

    def snapshot(self, group_id=None, before=None, limit=20, directory=False, live_status=True):
        with self.lock, closing(self.connect()) as db:
            data = {table: [json.loads(row['data']) for row in db.execute(f'SELECT data FROM {table} ORDER BY rowid')]
                    for table in ('organizations', 'profiles', 'groups')}
            data['archived_groups'] = [g for g in data['groups'] if g.get('removed_at')]
            if directory:
                data['jobs'] = []
            elif group_id:
                group = next((g for g in data['groups'] if g['id'] == group_id), None)
                if group is None:
                    raise ValueError('Group not found.')
                org_id = group['organization_id']
                for table in ('profiles', 'groups'):
                    data[table] = [i for i in data[table] if i['organization_id'] == org_id]
                rows = db.execute("SELECT rowid, data FROM jobs WHERE organization_id=? AND json_extract(data, '$.group_id')=? AND json_extract(data, '$.kind')='discussion' AND rowid<? ORDER BY rowid DESC LIMIT ?",
                                  (org_id, group_id, before or 9223372036854775807, limit + 1)).fetchall()
                data['next_before'] = rows[limit - 1]['rowid'] if len(rows) > limit else None
                discussions = [json.loads(r['data']) for r in reversed(rows[:limit])]
                # State summaries omit transcripts and repeated launch snapshots.
                summaries = []
                for row in db.execute("SELECT data FROM jobs WHERE organization_id=? AND (json_extract(data, '$.kind')='launch' OR json_extract(data, '$.state') IN ('queued', 'running')) ORDER BY rowid", (org_id,)):
                    job = json.loads(row['data'])
                    summary = {k: job[k] for k in ('id', 'kind', 'state', 'profile_id', 'participants', 'alias', 'pane_id', 'agent_session') if k in job}
                    if 'profile' in job:
                        summary['profile'] = {k: job['profile'][k] for k in ('id', 'name', 'runtime') if k in job['profile']}
                    summaries.append(summary)
                latest = {j['profile_id']: j for j in summaries if j['kind'] == 'launch'}
                active = [j for j in summaries if j['kind'] != 'launch']
                data['jobs'] = [*latest.values(), *active, *discussions]
                data['group'] = group
                data['organizations'] = [o for o in data['organizations'] if o['id'] == org_id]
                data['archived_groups'] = [g for g in data['archived_groups'] if g['organization_id'] == org_id]
            else:
                data['jobs'] = [json.loads(r['data']) for r in db.execute('SELECT data FROM jobs ORDER BY rowid')]
        for table in ('profiles', 'groups'):
            data[table] = [item for item in data[table] if not item.get('removed_at')]
        if directory:
            data['profiles'] = [{k: p[k] for k in ('id', 'organization_id', 'name', 'runtime', 'group_id') if k in p} for p in data['profiles']]
            return data
        states = {}
        live = None
        if live_status and not directory and any(j['kind'] == 'launch' and j['state'] == 'persona_sent' for j in data['jobs']):
            try:
                response = self.command('agent', 'list')
                entries = response if isinstance(response, list) else response.get('agents')
                if isinstance(entries, list):
                    live = {agent['name']: agent for agent in entries if isinstance(agent, dict) and isinstance(agent.get('name'), str)}
            except (ValueError, OSError, subprocess.TimeoutExpired):
                pass
        latest_runs = {j['profile_id']: j for j in data['jobs'] if j['kind'] == 'launch' and j['state'] != 'released'}
        for profile in data['profiles']:
            run = latest_runs.get(profile['id'])
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

    def job_records(self, launches_only=False):
        """Narrow durable job read with no runtime queries or integration callbacks."""
        with self.lock, closing(self.connect()) as db:
            query = "SELECT data FROM jobs"
            if launches_only:
                query += " WHERE json_extract(data, '$.kind')='launch'"
            return [json.loads(row['data']) for row in db.execute(query + ' ORDER BY rowid')]

    def active_checkouts(self):
        """Where each launched agent works, for the background Git watcher.

        Read-only: used to decide which checkouts to inspect. Worktree launches
        report the isolated checkout, so an agent keeps its own directory.
        """
        with self.lock, closing(self.connect()) as db:
            rows = [json.loads(r['data']) for r in db.execute(
                "SELECT data FROM jobs WHERE json_extract(data, '$.kind')='launch' "
                "AND json_extract(data, '$.state')='persona_sent' ORDER BY rowid DESC")]
        found = {}
        for job in rows:
            profile = job.get('profile') or {}
            path = job.get('worktree_path') or job.get('source_project') or profile.get('project')
            if not path:
                continue
            # Preserve every agent; the watcher reuses measurements for shared checkouts.
            found.setdefault(job.get('profile_id') or profile.get('id'), (profile.get('id') or job.get('profile_id'), job['id'],
                                         profile.get('name', ''), str(path)))
        return list(found.values())

    def state_snapshot(self):
        data = self.snapshot()
        data['jobs'] = [self.job_summary(j) for j in data['jobs']]
        return data

    @staticmethod
    def job_summary(job, document=False):
        omitted = {'runs', 'group_run', 'organization', 'profile'}
        if not document and job['kind'] not in ('launch', 'delegate'):
            omitted.update(('result', 'contributions', 'group', 'prompt'))
        return {k: v for k, v in job.items() if k not in omitted}

    def activity(self, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        org_id = text(body, 'organization_id', 40)
        with self.lock, closing(self.connect()) as db:
            org = self.get(db, 'organizations', org_id)
            data = {'organizations': [org]}
            for table in ('groups', 'profiles'):
                data[table] = [json.loads(r['data']) for r in db.execute(f'SELECT data FROM {table} WHERE organization_id=?', (org_id,))]
                data[table] = [i for i in data[table] if not i.get('removed_at')]
            recent = db.execute("SELECT data FROM jobs WHERE organization_id=? AND json_extract(data, '$.kind')!='launch' ORDER BY rowid DESC LIMIT 20", (org_id,)).fetchall()
            active = db.execute("SELECT data FROM jobs WHERE organization_id=? AND json_extract(data, '$.state') IN ('queued','running')", (org_id,)).fetchall()
            jobs = {j['id']: j for j in (json.loads(r['data']) for r in [*reversed(recent), *active])}
            data['jobs'] = [self.job_summary(j, document=True) for j in jobs.values()]
        return data

    def history(self, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        before = body.get('before')
        if before is not None and (type(before) is not int or before <= 0):
            raise ValueError('Invalid history cursor.')
        limit = body.get('limit', 20)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('History page size must be between 1 and 50.')
        return self.snapshot(group_id=text(body, 'group_id', 40), before=before, limit=limit)

    def project(self, value):
        return str(project_directory(self.projects, value))

    def label_agents(self, agents):
        """Add dashboard labels only to verified live run bindings; preserve Herdr names."""
        with self.lock, closing(self.connect()) as db:
            profiles = {row['id']: json.loads(row['data']) for row in db.execute('SELECT id, data FROM profiles')}
            jobs = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs')]
        by_alias = {}
        for run in reversed(jobs):
            if run['kind'] == 'launch':
                by_alias.setdefault(run.get('alias'), []).append(run)
        result = []
        for agent in agents:
            item = dict(agent)
            for run in by_alias.get(agent.get('name'), []):
                if run['kind'] != 'launch' or run['state'] not in ('running', 'persona_sent', 'needs_attention') or run.get('alias') != agent.get('name'):
                    continue
                # Label initialization/error states only after the launched conversation
                # has been captured; an alias alone does not prove ownership.
                if not run.get('pane_id') or (run['state'] != 'persona_sent' and not run.get('agent_session')):
                    continue
                profile = profiles.get(run['profile_id'])
                if profile is None or profile.get('removed_at'):
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

    def remove(self, db, action, body, org_id):
        table = 'groups' if action == 'remove_group' else 'profiles'
        key = 'group_id' if table == 'groups' else 'profile_id'
        item = self.get(db, table, text(body, key, 40), org_id)
        if table == 'groups':
            profile = self.get(db, 'profiles', item['facilitator_id'], org_id) if item.get('facilitator_id') else None
        else:
            profile = item
            if profile.get('group_id'):
                raise ValueError('Remove this facilitator through its group.')
            memberships = [json.loads(row['data']) for row in db.execute('SELECT data FROM groups WHERE organization_id=?', (org_id,))]
            names = [group['name'] for group in memberships if not group.get('removed_at') and profile['id'] in group['members']]
            if names:
                raise ValueError('Remove this agent from these groups first: ' + ', '.join(names))
        jobs = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs WHERE organization_id=?', (org_id,))]
        profile_id = profile['id'] if profile else None
        if any(job['state'] in ('queued', 'running') and
               ((table == 'groups' and job.get('group_id') == item['id']) or
                (profile_id and profile_id in job.get('participants', [job.get('profile_id')]))) for job in jobs):
            raise ValueError('Wait for active work to finish before removing this entry.')
        runs = [job for job in jobs if profile_id and job['kind'] == 'launch' and
                job.get('profile_id') == profile_id and job['state'] != 'released']
        if runs:
            response = self.command('agent', 'list')
            live = response if isinstance(response, list) else response.get('agents')
            if not isinstance(live, list):
                raise ValueError('Cannot verify live agents before removal.')
            targets = []
            for run in runs:
                agent = next((a for a in live if a.get('name') == run['alias']), None)
                if agent is None:
                    continue
                self.identity(run, ready=False, agent=agent)
                if not run.get('agent_session'):
                    raise ValueError('Agent session was not captured. Inspect and release its run before removal.')
                if agent.get('agent_status', agent.get('state')) not in ('idle', 'done', 'blocked'):
                    raise ValueError('Agent is still working or its status is unknown. Interrupt it before removal.')
                targets.append(run['pane_id'])
            for pane in targets:
                self.command('pane', 'close', pane, timeout=10)
            for run in runs:
                run.update(state='released', updated_at=now())
                self.put(db, 'jobs', run)
        if profile:
            profile['removed_at'] = now()
            self.put(db, 'profiles', profile)
            # Former direct reports remain valid organizational roots.
            for row in db.execute('SELECT data FROM profiles WHERE organization_id=?', (org_id,)).fetchall():
                child = json.loads(row['data'])
                if not child.get('removed_at') and child.get('manager_id') == profile_id:
                    child['manager_id'] = ''
                    self.put(db, 'profiles', child)
        item['removed_at'] = now()
        self.put(db, table, item)
        return item

    def action(self, action, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        key = text(body, 'request_id', 80)
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', key):
            raise ValueError('Invalid request ID.')
        if action in ('inspect', 'transcript'):
            # Freeze records under the lock, then perform external reads without
            # holding the store lock or an on-disk transaction.
            with closing(sqlite3.connect(':memory:')) as snapshot:
                snapshot.row_factory = sqlite3.Row
                with self.lock, closing(self.connect()) as db:
                    db.backup(snapshot)
                org = self.get(snapshot, 'organizations', text(body, 'organization_id', 40))
                from collaboration import action as collaboration_action
                return collaboration_action(self, snapshot, action, body, org)
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
                if action in ('group', 'discuss', 'chat', 'inspect', 'input', 'recover', 'transcript'):
                    from collaboration import action as collaboration_action
                    item = collaboration_action(self, db, action, body, org)
                    if action == 'group':
                        submitted = item.get('launch_job_id')
                    if action in ('chat', 'discuss', 'input'):
                        submitted = item['id']
                    if action in ('inspect', 'transcript'):
                        return item
                elif action in ('remove_agent', 'remove_group'):
                    item = self.remove(db, action, body, org_id)
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
            with self.lock:
                future = self.worker.submit(self.execute, submitted)
                self.futures.add(future)
                future.add_done_callback(self._finished)
        return response

    def _finished(self, future):
        with self.lock:
            self.futures.discard(future)

    def wait_idle(self, timeout=10):
        with self.lock:
            futures = list(self.futures)
        _, pending = wait(futures, timeout=timeout)
        if pending:
            raise TimeoutError('Organization jobs did not finish.')

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

    def created_pane(self, created, project):
        """Resolve only a unique new checkout workspace; never use focused panes."""
        if not isinstance(created, dict):
            raise ValueError('Unexpected Herdr workspace creation response.')
        root_pane = created.get('root_pane')
        pane = root_pane.get('pane_id') if isinstance(root_pane, dict) else None
        if is_pane_id(pane):
            metadata = created.get('workspace')
            workspace = metadata.get('workspace_id') if isinstance(metadata, dict) else None
            # Keep only valid metadata that belongs to the authoritative root pane.
            return pane, workspace if is_workspace_id(workspace) and is_pane_id(pane, workspace) else None
        # Some installed Herdr versions omit a usable root pane in creation
        # responses. Re-read authoritative records for the exact created cwd.
        response = self.command('workspace', 'list')
        workspaces = response if isinstance(response, list) else response.get('workspaces', [])
        matches = [w for w in workspaces if isinstance(w, dict) and
                   (w.get('worktree') or {}).get('checkout_path', w.get('cwd')) and
                   str(Path((w.get('worktree') or {}).get('checkout_path', w.get('cwd'))).resolve()) == str(Path(project).resolve())]
        if len(matches) != 1:
            raise ValueError('Cannot uniquely resolve the created checkout workspace. Inspect its terminal before retrying.')
        workspace = matches[0].get('workspace_id')
        if not is_workspace_id(workspace):
            raise ValueError('Created workspace has an unsupported ID.')
        response = self.command('pane', 'list', '--workspace', workspace)
        panes = response if isinstance(response, list) else response.get('panes', [])
        if len(panes) != 1:
            raise ValueError('Created workspace must have exactly one pane before agent launch.')
        pane = panes[0].get('pane_id')
        if not is_pane_id(pane, workspace):
            raise ValueError('Herdr did not return a valid root pane ID for the created workspace.')
        return pane, workspace

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
        with self.lock, closing(self.connect()) as db:
            job = self.get(db, 'jobs', job_id)
            ids = sorted(set(job.get('participants') or [job.get('profile_id')]) - {None})
            locks = [self.agent_locks.setdefault(profile_id, threading.RLock()) for profile_id in ids]
        # Ordered acquisition also protects discussions/delegations involving
        # several agents, without holding the global store lock during work.
        for lock in locks:
            lock.acquire()
        try:
            self._execute(job_id)
        finally:
            for lock in reversed(locks):
                lock.release()

    def _execute(self, job_id):
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
                pane, workspace = self.created_pane(created, job.get('worktree_path', project))
                job = self.update_job(job_id, pane_id=pane, workspace_id=workspace)
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
