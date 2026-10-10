"""Durable organization records and explicit, non-retrying Herdr jobs."""
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
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
from herdr_errors import HerdrError, delivery_for, parse_completion, parse_resume, preview as preview_text


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
        args += ['--model', f'{provider}/{model}']
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
    supports_discussion_preparation = True
    def __init__(self, path, projects, command, runtime_status=None, model_validator=None, runtime_ready=None):
        self.path = Path(path)
        self.projects = Path(projects).resolve()
        self.command = command
        self.runtime_status = runtime_status
        self.runtime_ready = runtime_ready
        self.model_validator = model_validator
        self.lock = threading.RLock()
        # One-way notification only: never call the watcher while holding a
        # store/profile lock or SQLite transaction.
        self.jobs_changed = threading.Event()
        from notifications import Notifications
        self.events = Notifications()
        self.worker = ThreadPoolExecutor(max_workers=4, thread_name_prefix='organization')
        self.agent_locks = {}
        self.contributions = None
        self.capacity_guard = None
        self.discussion_cursor = 0
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
                CREATE TABLE IF NOT EXISTS session_archives (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, data TEXT NOT NULL);
            ''')
            from notifications import schema
            schema(db)
            # A crash may have occurred after terminal input. Never replay automatically.
            for row in db.execute('SELECT id, data FROM jobs').fetchall():
                job = json.loads(row['data'])
                if job['state'] in ('queued', 'running'):
                    job.update(state='uncertain', error='Gateway restarted. Inspect the SSH terminal before creating another run or task.', updated_at=now())
                    self.put(db, 'jobs', job)
            # Refine restart recovery: annotate liveness of bound sessions without
            # changing their state and without replaying any prompt.
            bound = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs')]
            bound = [job for job in bound if job['kind'] == 'launch' and job['state'] == 'persona_sent']
            if bound:
                try:
                    response = self.command('agent', 'list', timeout=3)
                    agents = response if isinstance(response, list) else response.get('agents') if isinstance(response, dict) else None
                    live = {a.get('name') for a in agents if isinstance(a, dict)} if isinstance(agents, list) else None
                except (ValueError, OSError):
                    live = None
                if live is not None:
                    for job in bound:
                        liveness = 'present' if job.get('alias') in live else 'missing'
                        if job.get('session_liveness') != liveness:
                            job.update(session_liveness=liveness, liveness_checked_at=now(), updated_at=now())
                            self.put(db, 'jobs', job)

    def connect(self):
        from notifications import Connection
        db = sqlite3.connect(self.path, timeout=10, factory=Connection)
        db.notify = self.events.outbox_ready.set
        db.row_factory = sqlite3.Row
        return db

    def close(self):
        self.worker.shutdown(wait=True)

    def put(self, db, table, item):
        from notifications import record
        previous = db.execute(f'SELECT data FROM {table} WHERE id=?', (item['id'],)).fetchone()
        def meaningful(value):
            data = {k: v for k, v in value.items() if k not in ('updated_at',)}
            if data.get('kind') == 'discussion' and data.get('state') in ('queued', 'waiting_for_members'):
                data['state'] = 'pending_members'
            return data
        if table == 'jobs' and not previous and item.get('kind') == 'launch' and self.contributions is not None:
            from contributions import TERMINAL_EXECUTIONS
            policy = self.contributions.policy(item['organization_id'])
            jobs = [json.loads(row[0]) for row in db.execute('SELECT data FROM jobs WHERE organization_id=?', (item['organization_id'],))]
            active = sum(j.get('kind') == 'launch' and j.get('state') not in TERMINAL_EXECUTIONS for j in jobs)
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            started = sum(j.get('kind') == 'launch' and str(j.get('created_at') or '') > cutoff for j in jobs)
            limit = policy.get('max_active_sessions') or 0
            daily = policy.get('daily_session_cap') or 0
            if limit and active >= limit:
                raise ValueError('Shared session budget is full; wait for a session to finish.')
            if daily and started >= daily:
                raise ValueError('Organization daily session budget is reached; wait for the next window.')
        if not previous or meaningful(json.loads(previous[0])) != meaningful(item):
            record(db, 'job' if table == 'jobs' else 'policy', item['id'], item.get('organization_id', item['id']))
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
            data[table] = [item for item in data[table] if not item.get('removed_at') and not item.get('ephemeral')]
        if directory:
            # Task assignment needs the repository and worktree mode; a worktree
            # agent is the only kind that can own a task branch.
            data['profiles'] = [{k: p[k] for k in ('id', 'organization_id', 'name', 'runtime', 'group_id',
                                                    'project', 'use_worktree') if k in p} for p in data['profiles']]
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
        latest_runs = {j['profile_id']: j for j in data['jobs'] if j['kind'] == 'launch' and j['state'] not in ('released', 'finished')}
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
            else:
                query += ' ORDER BY rowid'
            return [json.loads(row['data']) for row in db.execute(query)]

    def knowledge_jobs(self):
        """Identity index for knowledge reconciliation; full records are fetched only on change."""
        with self.lock, closing(self.connect()) as db:
            rows = db.execute(
                "SELECT json_extract(data, '$.id'), json_extract(data, '$.kind'), "
                "json_extract(data, '$.state'), json_extract(data, '$.updated_at') FROM jobs "
                "WHERE (json_extract(data, '$.kind')='discussion' AND json_extract(data, '$.state')='artifact_ready') "
                "OR (json_extract(data, '$.kind')='delegate' AND json_extract(data, '$.state') IN ('reported_complete','completed')) "
                "OR (json_extract(data, '$.kind')='chat' AND json_extract(data, '$.consultation')=1 AND json_extract(data, '$.state')='answered') "
                "ORDER BY json_extract(data, '$.updated_at') DESC LIMIT 200").fetchall()
        return [dict(id=row[0], kind=row[1], state=row[2], updated_at=row[3]) for row in rows]

    def job_record(self, job_id):
        with self.lock, closing(self.connect()) as db:
            return self.get(db, 'jobs', job_id)

    def profile_records(self):
        """Narrow durable profile read with no runtime queries or integration callbacks."""
        with self.lock, closing(self.connect()) as db:
            return [json.loads(row['data']) for row in db.execute('SELECT data FROM profiles ORDER BY rowid')]

    def checkout_in_use(self, path):
        """Fail closed for busy/uncertain managed jobs bound to this checkout.

        Called while the caller holds the repository operation lock, so the
        snapshot must not run agent-list CLI queries; without live status a
        bound launch counts as busy rather than idle.
        """
        wanted = Path(path).resolve()
        data = self.snapshot(live_status=False)
        profiles = {p['id']: p for p in data['profiles']}
        runs = {j['id']: j for j in data['jobs'] if j['kind'] == 'launch'}
        def matches(run):
            value = run.get('worktree_path') or run.get('source_project') or profiles.get(run.get('profile_id'), {}).get('project')
            return value is not None and Path(value).resolve() == wanted
        for job in data['jobs']:
            if job['kind'] == 'launch' and job['state'] not in ('released', 'finished') and matches(job):
                if job['state'] != 'persona_sent' or data['member_states'].get(job['profile_id'], {}).get('status') not in ('idle', 'done', 'off'):
                    return True
            if job['state'] in ('queued', 'running', 'uncertain', 'needs_attention'):
                bindings = job.get('runs') or [r for r in runs.values()
                                              if r.get('profile_id') == job.get('profile_id') and r['state'] not in ('released', 'finished')]
                explicit_path = job['kind'] == 'launch' or job.get('worktree_path') or job.get('source_project')
                if (explicit_path and matches(job)) or any(matches(runs.get(r.get('id'), r)) for r in bindings):
                    return True
        return False

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
            omitted.update(('result', 'contributions', 'group', 'prompt', 'knowledge_pack'))
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
                job.get('profile_id') == profile_id and job['state'] not in ('released', 'finished')]
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
        guard = self.capacity_guard() if self.capacity_guard and action in ('launch', 'group', 'discuss') else nullcontext()
        with guard:
            return self._action(action, body)

    def _action(self, action, body):
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
                if action in ('group', 'discuss', 'chat', 'inspect', 'input', 'recover', 'transcript', 'retry_discussion', 'cancel_discussion'):
                    from collaboration import action as collaboration_action
                    item = collaboration_action(self, db, action, body, org)
                    if action == 'group':
                        submitted = item.get('launch_job_id')
                    if action in ('chat', 'discuss', 'input', 'retry_discussion'):
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
                    # An agent added against a CLI that cannot report state or
                    # resume would fail silently much later.
                    if self.runtime_ready is not None:
                        problem = self.runtime_ready(runtime)
                        if problem:
                            raise ValueError(problem)
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
                        if any(j['kind'] == 'launch' and j['profile_id'] == profile['id'] and j['state'] not in ('released', 'finished') for j in jobs):
                            raise ValueError('Inspect and release the previous run before launching again.')
                        item = dict(id=uuid.uuid4().hex, organization_id=org_id, kind='launch', profile_id=profile['id'],
                                    profile=profile, organization=org, alias='hire_' + uuid.uuid4().hex[:20])
                        if body.get('task_id') is not None:
                            task_id = body.get('task_id')
                            if (not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id)
                                    or body.get('worktree_branch') != 'herdr/task-' + task_id[:12]
                                    or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', str(body.get('start_sha', '')))
                                    or not profile.get('use_worktree', True)):
                                raise ValueError('Invalid task branch or starting commit.')
                            item.update(task_id=task_id, worktree_branch=body['worktree_branch'],
                                        # A task description is allowed 8000 characters and the
                                        # launch adds instructions plus the shared tool policy.
                                        start_sha=body['start_sha'], task_prompt=text(body, 'task_prompt', 16000))
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
                elif action in ('release', 'report', 'resolve_delegation'):
                    item = self.get(db, 'jobs', text(body, 'job_id', 40), org_id)
                    if item['state'] in ('queued', 'running'):
                        raise ValueError('Wait for the running job to finish.')
                    if action == 'resolve_delegation':
                        if item['kind'] != 'delegate' or item['state'] not in ('delivered', 'reported_complete', 'needs_attention', 'uncertain', 'completed', 'cancelled'):
                            raise ValueError('Inspect a delivered or interrupted delegation before resolving it.')
                        decision = body.get('decision')
                        if body.get('inspected') is not True or decision not in ('complete', 'cancel'):
                            raise ValueError('Inspect the delegation and choose complete or cancel.')
                        reason = text(body, 'reason', 2000)
                        if item['state'] not in ('completed', 'cancelled'):
                            if decision == 'complete' and item['state'] != 'reported_complete':
                                raise ValueError('Record a completion report before accepting completion.')
                            item.update(state='completed' if decision == 'complete' else 'cancelled',
                                resolution=dict(decision=decision, reason=reason, actor=body.get('_actor', 'administrator'), at=now()))
                        elif item.get('resolution', {}).get('decision') != decision:
                            raise ValueError('This delegation was already resolved differently.')
                    elif action == 'release':
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
        if changes.get('state') in ('answered', 'persona_sent', 'needs_attention', 'delivered', 'artifact_ready',
                                    'finished', 'released', 'cancelled', 'completed', 'reported_complete',
                                    'uncertain', 'waiting_for_members', 'waiting_for_input'):
            self.jobs_changed.set()
        return job

    def manage_session(self, body, actor):
        if body.get('mode') in ('archives', 'view_archive'):
            from session_archives import listing, read
            org_id = text(body, 'organization_id', 40)
            with closing(self.connect()) as db:
                self.get(db, 'organizations', org_id)
            return ({'archives': listing(self, org_id)} if body['mode'] == 'archives'
                    else read(self, org_id, text(body, 'archive_id', 40)))
        if body.get('mode') == 'cleanup':
            ids = body.get('job_ids')
            if body.get('inspected') is not True or not isinstance(ids, list) or not 1 <= len(ids) <= 100 or not all(isinstance(i, str) for i in ids):
                raise ValueError('Review and select obsolete sessions first.')
            results = []
            for job_id in dict.fromkeys(ids):
                try:
                    with self.lock, closing(self.connect()) as db:
                        old = self.get(db, 'jobs', job_id, text(body, 'organization_id', 40))
                    if old['kind'] != 'launch' or old['state'] != 'released':
                        raise ValueError('Only released sessions are eligible for bulk cleanup.')
                    closed = self.manage_session(dict(body, job_id=job_id, mode='close'), actor)
                    results.append(dict(id=job_id, state=closed.get('status', 'closed'), archive_id=closed.get('archive_id')))
                except (ValueError, OSError) as error:
                    results.append(dict(id=job_id, state='skipped', reason=str(error)))
            return {'results': results}
        mode = body.get('mode')
        if mode not in ('close', 'restart', 'continue', 'finish', 'resume', 'handover') or body.get('inspected') is not True:
            raise ValueError('Inspect the session and choose close or restart.')
        request_id = body.get('request_id')
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 64):
            raise ValueError('Invalid session request ID.')
        with self.lock, closing(self.connect()) as db:
            run = self.get(db, 'jobs', text(body, 'job_id', 40), text(body, 'organization_id', 40))
            lock = self.agent_locks.setdefault(run.get('profile_id'), threading.RLock())
        if not lock.acquire(blocking=False):
            raise ValueError('Agent is executing work. Wait before changing its session.')
        try:
            with self.lock, closing(self.connect()) as db:
                run = self.get(db, 'jobs', run['id'])
                if request_id and run.get('session_request_id') == request_id:
                    if run.get('session_request_mode') != mode:
                        raise ValueError('Session request ID already used for another action.')
                    return {'id': run['id']}
                if run['kind'] != 'launch' or run['state'] not in ('persona_sent', 'released', 'finished'):
                    raise ValueError('Select a completed launch binding.')
                if mode == 'restart' and run['state'] != 'persona_sent':
                    raise ValueError('Restart only the currently bound session.')
                if any(j['state'] in ('queued', 'running') and run['profile_id'] in
                       j.get('participants', [j.get('profile_id')]) for j in self.job_records()):
                    raise ValueError('Wait for queued or running agent work.')
                if run.get('session_closed_at') and mode in ('close', 'finish'):
                    return {'id': run['id'], 'status': 'already_closed', 'archive_id': run.get('session_archive_id')}
            from session_archives import archive, read, context
            saved = None
            status = 'closed'
            target = None
            if mode in ('continue', 'resume', 'handover'):
                if not run.get('session_closed_at'):
                    raise ValueError('Archive and close this session before continuing from saved context.')
                saved = read(self, run['organization_id'], text(body, 'archive_id', 40))
                if saved['run_id'] != run['id']:
                    raise ValueError('Archive does not belong to this run.')
                if mode == 'handover':
                    with closing(self.connect()) as db:
                        target = self.get(db, 'profiles', text(body, 'target_profile_id', 40), run['organization_id'])
                    if target.get('group_id') or target.get('archived') or target.get('ephemeral'):
                        raise ValueError('Choose an individual persistent agent as the handover target.')
                    source_project = run.get('source_project') or (run.get('profile') or {}).get('project')
                    if not source_project or Path(target.get('project', '')).resolve() != Path(source_project).resolve():
                        raise ValueError('The handover target works in another repository.')
                    if any(j['kind'] == 'launch' and j.get('profile_id') == target['id'] and j['state'] not in ('released', 'finished') for j in self.job_records()):
                        raise ValueError('The handover target already has an active execution.')
                    context_value = body.get('continuation_context')
                    if not isinstance(context_value, str) or not context_value.strip() or len(context_value) > 20000:
                        raise ValueError('The handover needs bounded context for the next agent.')
                    task_prompt = body.get('task_prompt', '')
                    if not isinstance(task_prompt, str) or len(task_prompt) > 12000:
                        raise ValueError('Invalid handover task prompt.')
                elif any(j['kind'] == 'launch' and j['id'] != run['id'] and j.get('profile_id') == run['profile_id'] and j['state'] not in ('released', 'finished') for j in self.job_records()):
                    raise ValueError('Release the current agent binding before continuing an archived session.')
                if mode == 'resume':
                    resume = saved.get('resume') if isinstance(saved.get('resume'), dict) else {}
                    if saved.get('native_resume_available') is not True or resume.get('state') != 'verified' or not isinstance(resume.get('args'), list):
                        raise ValueError('This archive has no verified native resume reference; start with saved context instead.')
            else:
                response = self.command('agent', 'list')
                live = response if isinstance(response, list) else response.get('agents')
                if not isinstance(live, list):
                    raise ValueError('Cannot verify live sessions.')
                agent = next((a for a in live if a.get('name') == run['alias']), None)
                if agent is None:
                    # Require a valid inventory; an absent agent alone cannot identify a pane.
                    pane = self.locate_pane(run)
                    if pane and self.orphaned_pane(run, pane, live):
                        # Herdr restores panes as plain shells when native
                        # restore does not apply. Nothing runs in this pane and
                        # it sits in the recorded checkout, so it is ours.
                        saved = archive(self, run, actor, unavailable='The agent process did not survive a Herdr '
                                        'restart and its pane was restored as a shell. Terminal history cannot be '
                                        'recovered from the pane; a recorded native resume reference still applies.')
                        # Archiving probes the agent and reads Git evidence, which
                        # takes time. Re-prove before closing, exactly as the live
                        # path rechecks ownership: a pane that started running or
                        # moved during capture must be left to an operator.
                        if not self.orphaned_pane(run, self.locate_pane(run), self.live_agents()):
                            raise ValueError('The pane changed while its session was archived; inspect it before closure.')
                        self.command('pane', 'close', run['pane_id'], timeout=10)
                        status = 'orphaned'
                    elif pane or not run.get('pane_id'):
                        raise ValueError('Original agent is absent but its pane is present or unidentified; inspect ownership before closure.')
                    else:
                        saved = archive(self, run, actor, unavailable='Agent and recorded pane were already absent. Terminal history cannot be recovered from them.')
                        status = 'already_closed'
                else:
                    self.close_identity(run, live=live)
                    output = self.command('agent', 'read', run['alias'], '--source', 'recent-unwrapped', '--lines', '2000', timeout=10)
                    terminal = output.get('output') if isinstance(output, dict) else None
                    if not isinstance(terminal, str) or not terminal.strip():
                        raise ValueError('Terminal preservation failed or returned empty output; the pane was not closed.')
                    saved = archive(self, run, actor, terminal=terminal[-40000:])
                    self.close_identity(run)  # Recheck ownership after capture and immediately before closure.
                    self.command('pane', 'close', run['pane_id'], timeout=10)
                terminal = 'finished' if mode == 'finish' else 'released'
                run = self.update_job(run['id'], state=terminal, session_closed_at=now(),
                    session_closed_by=actor, session_archive_id=saved['id'], session_close_status=status,
                    session_request_id=request_id, session_request_mode=mode,
                    session_close_identity='agent_session' if run.get('agent_session') else 'pane_checkout',
                    **({'finished_at': now(), 'finished_by': actor} if mode == 'finish' else {}))
            if mode in ('restart', 'continue', 'resume', 'handover'):
                if mode == 'handover':
                    profile = target
                else:
                    with self.lock, closing(self.connect()) as db:
                        profile = self.get(db, 'profiles', run['profile_id'], run['organization_id'])
                profile = dict(profile, project=run.get('source_project') or run['profile']['project'])
                history = list(run.get('session_history', []))[-20:]
                history.append({k: run.get(k) for k in ('alias', 'pane_id', 'agent_session', 'session_closed_at', 'session_archive_id')})
                resume = saved.get('resume') if isinstance(saved.get('resume'), dict) else {}
                self.update_job(run['id'], state='queued', reuse_checkout=True,
                    profile_id=profile['id'], profile=profile, alias='hire_' + uuid.uuid4().hex[:20], agent_session=None,
                    session_closed_at=None, session_history=history, error='', restarted_by=actor,
                    task_prompt=str(body.get('task_prompt', '')) if mode == 'handover' else '',
                    continuation_context=(str(body.get('continuation_context', '')) if mode == 'handover'
                                          else context(saved) if mode == 'continue' else ''),
                    continuation_archive_id=saved['id'] if mode in ('continue', 'resume', 'handover') else None,
                    resume_args=list(resume.get('args'))[:20] if mode == 'resume' and isinstance(resume.get('args'), list) else None,
                    resume_reference=resume.get('reference') if mode == 'resume' else None,
                    handed_from=dict(profile_id=run.get('profile_id'), alias=run.get('alias'), archive_id=saved['id']) if mode == 'handover' else None,
                    session_request_id=request_id, session_request_mode=mode)
                with self.lock:
                    future = self.worker.submit(self.execute, run['id'])
                    self.futures.add(future)
                    future.add_done_callback(self._finished)
            return {'id': run['id'], 'status': status, 'archive_id': saved['id']}
        finally:
            lock.release()

    def recover_chat(self, body, actor):
        if not isinstance(body, dict) or body.get('inspected') is not True:
            raise ValueError('Inspect the idle conversation before recovering a reply.')
        with self.lock, closing(self.connect()) as db:
            job = self.get(db, 'jobs', text(body, 'job_id', 40), text(body, 'organization_id', 40))
        if job['kind'] != 'chat':
            raise ValueError('Select an interrupted chat reply.')
        resolution = self.resolve_coordinator_report(job['id'], job['profile_id'], 'recover')
        if not job.get('recovered_by'):
            self.update_job(job['id'], recovered_by=actor)
        return {'resolution': resolution}

    def repair_guidance(self, profile_id, checkout):
        from collaboration import current_run
        from worker_guidance import local_bundle
        with self.lock:
            lock = self.agent_locks.setdefault(profile_id, threading.RLock())
        if not lock.acquire(blocking=False):
            raise ValueError('Worker is executing a job. Wait before repairing guidance.')
        try:
            jobs = self.job_records()
            runs = [j for j in jobs if j['kind'] == 'launch' and
                    j.get('profile_id') == profile_id and j['state'] == 'persona_sent' and
                    Path(j.get('worktree_path') or j.get('source_project') or '').resolve() == Path(checkout).resolve()]
            if len(runs) != 1:
                raise ValueError('Guidance recovery requires one unchanged worker session.')
            if not runs[0].get('worktree_path'):
                raise ValueError('Guidance recovery requires an isolated worker worktree, not a shared checkout.')
            if any(j['state'] in ('queued', 'running') and profile_id in
                   j.get('participants', [j.get('profile_id')]) for j in jobs):
                raise ValueError('Wait for queued or running worker jobs before repairing guidance.')
            if any(pid != profile_id and Path(path).resolve() == Path(checkout).resolve()
                   for pid, _, _, path in self.active_checkouts()):
                raise ValueError('Guidance recovery requires an exclusive worker checkout.')
            current_run(self, runs[0])
            return str(local_bundle(checkout, recover=True))
        finally:
            lock.release()

    def resolve_worker_job(self, job_id, profile_id, event_id, mode):
        """Resolve uncertain integration delivery without sending terminal input."""
        if mode not in ('recover', 'validate'):
            raise ValueError('Choose saved result recovery or validation only.')
        with self.lock:
            lock = self.agent_locks.setdefault(profile_id, threading.RLock())
        if not lock.acquire(blocking=False):
            raise ValueError('The worker is executing a job. Wait before recovering.')
        try:
            with self.lock, closing(self.connect()) as db:
                job = self.get(db, 'jobs', job_id)
                if (job['kind'] != 'chat' or job.get('profile_id') != profile_id
                        or job.get('integration_event') != event_id):
                    raise ValueError('Job does not belong to this worker integration event.')
                if job.get('repair_resolution') == mode:
                    return mode
                if job['state'] not in ('answered', 'uncertain', 'needs_attention'):
                    raise ValueError('Only answered or interrupted worker jobs can be recovered.')
                jobs = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs')]
                if any(j['state'] in ('queued', 'running') and profile_id in
                       j.get('participants', [j.get('profile_id')]) for j in jobs):
                    raise ValueError('Wait for queued or running worker jobs before recovering.')
            from collaboration import current_run
            if not job.get('runs'):
                raise ValueError('Worker job has no recoverable session binding.')
            for binding in job['runs']:
                current_run(self, binding)  # Original session must be unchanged and idle.
            if mode == 'recover':
                self.resolve_coordinator_report(job_id, profile_id, 'recover')
                self.update_job(job_id, repair_resolution=mode)
            else:
                self.update_job(job_id, state='superseded', previous_state=job['state'],
                                previous_error=job.get('error', ''), repair_resolution=mode, resolved_at=now())
            return mode
        finally:
            lock.release()

    def resolve_coordinator_report(self, job_id, profile_id, mode):
        """Recover output or retire uncertain delivery under the agent's lock.

        Never send terminal input here. The coordinator queues a new, auditable
        summary only after this operator action has resolved the previous job.
        """
        if mode not in ('recover', 'fresh'):
            raise ValueError('Unsupported report repair mode.')
        with self.lock:
            lock = self.agent_locks.setdefault(profile_id, threading.RLock())
        if not lock.acquire(blocking=False):
            raise ValueError('The agent is still executing a job. Wait before repairing its reply.')
        try:
            with self.lock, closing(self.connect()) as db:
                job = self.get(db, 'jobs', job_id)
                if job['kind'] != 'chat' or job.get('profile_id') != profile_id:
                    raise ValueError('Select an interrupted chat reply for the matching agent.')
                if job['state'] == 'answered' and mode == 'recover':
                    return 'recovered'
                if job['state'] == 'superseded' and mode == 'fresh':
                    return 'fresh'
                if job['state'] not in ('uncertain', 'needs_attention'):
                    raise ValueError('Only interrupted chat replies can be repaired.')
                jobs = [json.loads(row['data']) for row in db.execute('SELECT data FROM jobs ORDER BY rowid')]
                if any(j['state'] in ('queued', 'running') and profile_id in
                       j.get('participants', [j.get('profile_id')]) for j in jobs):
                    raise ValueError('Wait for this agent\'s queued or running work before repairing.')
                runs = [j for j in jobs if j['kind'] == 'launch' and j.get('profile_id') == profile_id and j['state'] not in ('released', 'finished')]
            if mode == 'recover':
                from collaboration import current_run, read_contribution
                for binding in job.get('runs', []):
                    current_run(self, binding)  # Checks the original session is idle and unchanged.
                if not job.get('runs'):
                    raise ValueError('Report has no recoverable session binding.')
                directory = self.path.parent / 'chat-replies' / job_id
                if directory.is_symlink() or directory.parent.is_symlink():
                    raise ValueError('Reply directories must not be symlinks.')
                try:
                    # Read the attempt's own reply file. Legacy jobs without
                    # per-attempt evidence keep their original reply.md. Recovery
                    # only reads: it never quarantines or rewrites the reply.
                    name = (job.get('delivery') or {}).get('reply_name') or 'reply.md'
                    if not re.fullmatch(r'reply-[a-f0-9]{12}\.md|reply\.md', str(name)):
                        raise ValueError('unsupported reply file name in job delivery evidence')
                    reply = read_contribution(directory / name, label='chat reply')
                except (ValueError, OSError) as error:
                    raise ValueError(f'Reply recovery failed for job {job_id}: {error}') from error
                self.update_job(job_id, state='answered', result=reply, result_format='markdown',
                                previous_error=job.get('error', ''), error='', recovered_at=now(),
                                delivery=dict(job.get('delivery') or {}, stage='reply_verified',
                                              recovered=True, bytes=len(reply.encode('utf-8'))))
                return 'recovered'
            if runs:
                if runs[-1]['state'] != 'persona_sent':
                    raise ValueError('Inspect and release the unsuccessful coordinator run before relaunching.')
                self.identity(runs[-1])  # Never inject a summary into a busy/blocked session.
            else:
                self.action('launch', dict(request_id=hashlib.sha256(('repair:' + job_id).encode()).hexdigest(),
                                          organization_id=job['organization_id'], profile_id=profile_id))
            self.update_job(job_id, state='superseded', previous_state=job['state'], resolved_at=now())
            return 'fresh'
        finally:
            lock.release()

    def identity(self, run, ready=True, agent=None):
        if agent is None:
            response = self.command('agent', 'get', run['alias'])
            agent = response.get('agent')
        if not isinstance(agent, dict) or agent.get('name') != run['alias'] or agent.get('pane_id') != run['pane_id'] or agent.get('agent') != run['profile']['runtime']:
            raise ValueError('Agent binding changed. Inspect the terminal and launch a new run.')
        if run.get('agent_session') and agent.get('agent_session') != run['agent_session']:
            raise ValueError('Agent conversation changed. Launch a new run to deliver its persona.')
        if ready:
            status = agent.get('agent_status', agent.get('state'))
            if status not in ('idle', 'done') or agent.get('interactive_ready') is False or agent.get('launch_pending') is True:
                if status == 'blocked':
                    raise HerdrError('Agent needs a decision before new input; no prompt was sent.',
                                     'agent_blocked', 'agent get', 'none')
                if status in ('working', 'running', 'busy', 'thinking'):
                    raise HerdrError('Agent is still working; no new prompt was sent.',
                                     'agent_working', 'agent get', 'none')
                raise HerdrError('Agent is not ready for input; no new prompt was sent.',
                                 'agent_not_ready', 'agent get', 'none')
        return agent

    @staticmethod
    def completion_baseline(response):
        """Record the completion sequence observed at prompt delivery, if any."""
        agent = response.get('agent') if isinstance(response, dict) and isinstance(response.get('agent'), dict) else response
        return parse_completion(agent)

    def agent_state(self, run, preview=False):
        """Live status for a bound run, with an optional bounded visible preview."""
        response = self.command('agent', 'get', run['alias'], timeout=2)
        agent = response.get('agent') if isinstance(response, dict) else None
        if not isinstance(agent, dict):
            raise ValueError('Agent state is unavailable.')
        result = dict(status=agent.get('agent_status', agent.get('state', 'unknown')),
                      interactive_ready=agent.get('interactive_ready'))
        if preview and result['status'] == 'blocked':
            try:
                output = self.command('agent', 'read', run['alias'], '--source', 'visible', '--lines', '30', timeout=2)
                text = output.get('output') if isinstance(output, dict) else ''
            except (ValueError, OSError):
                text = ''
            result['preview'] = preview_text(text)
        return result

    def probe_resume(self, run):
        """Read resume facts from the live agent without executing command text.

        Best-effort: a probe failure must never block archival or closure.
        """
        agent = None
        try:
            response = self.command('agent', 'get', run['alias'], timeout=5)
            agent = response.get('agent') if isinstance(response, dict) else None
            if not isinstance(agent, dict):
                agent = response if isinstance(response, dict) else None
        except Exception:
            agent = None
        if not isinstance(agent, dict) or not agent.get('resume'):
            try:
                explained = self.command('agent', 'explain', run['alias'], timeout=5)
                if isinstance(explained, dict):
                    inner = explained.get('agent') if isinstance(explained.get('agent'), dict) else explained
                    if isinstance(inner, dict):
                        agent = dict(agent or {}, **inner)
            except Exception:
                pass
        return parse_resume(agent)

    def live_agents(self):
        response = self.command('agent', 'list')
        live = response if isinstance(response, list) else response.get('agents')
        if not isinstance(live, list) or not all(isinstance(item, dict) for item in live):
            raise ValueError('Cannot verify live sessions.')
        return live

    def locate_pane(self, run):
        """Find this run's recorded pane in a fresh inventory, or None."""
        if not run.get('pane_id'):
            return None
        inventory = self.command('workspace', 'list')
        workspaces = inventory if isinstance(inventory, list) else inventory.get('workspaces')
        if not isinstance(workspaces, list):
            raise ValueError('Cannot verify workspace inventory.')
        for workspace in workspaces:
            workspace_id = workspace.get('workspace_id') or workspace.get('id')
            if not isinstance(workspace_id, str):
                raise ValueError('Cannot identify a workspace in the live inventory.')
            response = self.command('pane', 'list', '--workspace', workspace_id)
            panes = response if isinstance(response, list) else response.get('panes')
            if not isinstance(panes, list):
                raise ValueError('Cannot verify pane inventory.')
            found = next((p for p in panes if p.get('pane_id') == run['pane_id']), None)
            if found:
                return found
        return None

    @staticmethod
    def orphaned_pane(run, pane, live):
        """Prove a pane left behind by a dead agent is ours and idle.

        Four independent conditions must hold. Anything ambiguous leaves the
        pane for an operator: closing the wrong pane destroys real work, so
        absence of evidence is never treated as evidence.
        """
        if not run.get('pane_id') or not isinstance(pane, dict) or pane.get('pane_id') != run['pane_id']:
            return False
        # 1. No live agent claims this pane, so nothing runs in it.
        if any(item.get('pane_id') == run['pane_id'] for item in live if isinstance(item, dict)):
            return False
        # 2. No live agent still answers to this run's alias.
        if any(item.get('name') == run.get('alias') for item in live if isinstance(item, dict)):
            return False
        # 3. The pane reports no agent state, so it is a shell rather than a
        #    session whose reporting integration failed.
        if pane.get('agent_status') not in (None, '', 'unknown'):
            return False
        # 4. It sits in the checkout this run was assigned, which ties the pane
        #    to the recorded work rather than to unrelated activity.
        recorded = run.get('worktree_path') or run.get('source_project') or (run.get('profile') or {}).get('project')
        current = pane.get('cwd') or pane.get('foreground_cwd')
        if not isinstance(recorded, str) or not isinstance(current, str):
            return False
        try:
            return Path(recorded).resolve() == Path(current).resolve()
        except OSError:
            return False

    def close_identity(self, run, live=None):
        """Verify pane ownership for closure, including runtimes without session IDs.

        The legacy fallback identifies the managed pane and checkout, not a
        provider conversation. Never adopt a newly observed conversation ID.
        """
        if live is None:
            response = self.command('agent', 'list')
            live = response if isinstance(response, list) else response.get('agents') if isinstance(response, dict) else None
        if not isinstance(live, list) or any(not isinstance(item, dict) for item in live):
            raise ValueError('Cannot verify live sessions before closure.')
        matches = [item for item in live if item.get('name') == run.get('alias')]
        if len(matches) != 1:
            raise ValueError('Original session is absent or ambiguous; refresh its run before closure.')
        agent = self.identity(run, agent=matches[0])
        if run.get('agent_session'):
            return agent
        pane = run.get('pane_id')
        workspace_id = run.get('workspace_id') or (pane.split(':')[0] if isinstance(pane, str) else None)
        if not is_workspace_id(workspace_id) or not is_pane_id(pane, workspace_id):
            raise ValueError('Legacy session has no verifiable pane identity; inspect its run.')
        if len([item for item in live if item.get('pane_id') == pane]) != 1:
            raise ValueError('Legacy session pane has ambiguous agent ownership; inspect its run.')
        response = self.command('pane', 'list', '--workspace', workspace_id)
        panes = response if isinstance(response, list) else response.get('panes') if isinstance(response, dict) else None
        if not isinstance(panes, list) or any(not isinstance(item, dict) for item in panes):
            raise ValueError('Cannot verify legacy session pane inventory.')
        matches = [item for item in panes if item.get('pane_id') == pane]
        if len(matches) != 1:
            raise ValueError('Legacy session pane is absent or ambiguous; inspect its run.')
        # Herdr reports no cwd or worktree on a workspace, so the pane's own
        # directory is the authoritative checkout. A workspace-level path is
        # still preferred if a future version adds one.
        response = self.command('workspace', 'list')
        workspaces = response if isinstance(response, list) else response.get('workspaces') if isinstance(response, dict) else None
        if not isinstance(workspaces, list) or any(not isinstance(item, dict) for item in workspaces):
            raise ValueError('Cannot verify legacy session workspace inventory.')
        owners = [item for item in workspaces if (item.get('workspace_id') or item.get('id')) == workspace_id]
        if len(owners) != 1:
            raise ValueError('Legacy session workspace is absent or ambiguous; inspect its run.')
        worktree = owners[0].get('worktree')
        checkout = worktree.get('checkout_path') if isinstance(worktree, dict) else None
        checkout = (checkout or owners[0].get('cwd')
                    or matches[0].get('cwd') or matches[0].get('foreground_cwd'))
        expected = run.get('worktree_path') or run.get('source_project') or run['profile'].get('project')
        if not isinstance(checkout, str) or not checkout or not isinstance(expected, str) or not expected:
            raise ValueError('Legacy session checkout identity is unavailable; inspect its run.')
        if project_directory(self.projects, checkout) != project_directory(self.projects, expected):
            raise ValueError('Legacy session checkout changed; the pane was not closed.')
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
        # responses. Herdr reports no cwd or worktree on a workspace, so the
        # pane directory is the only checkout evidence; re-read it for the
        # exact created cwd. Rare path: 0.9.3 always returns a root pane.
        response = self.command('workspace', 'list')
        workspaces = response if isinstance(response, list) else response.get('workspaces', [])
        expected = Path(project).resolve()
        matches = []
        for workspace in workspaces:
            if not isinstance(workspace, dict):
                continue
            workspace_id = workspace.get('workspace_id') or workspace.get('id')
            if not is_workspace_id(workspace_id):
                continue
            response = self.command('pane', 'list', '--workspace', workspace_id)
            panes = response if isinstance(response, list) else response.get('panes', [])
            for candidate in panes:
                if not isinstance(candidate, dict):
                    continue
                directory = candidate.get('cwd') or candidate.get('foreground_cwd')
                if isinstance(directory, str) and Path(directory).resolve() == expected:
                    matches.append((workspace_id, candidate.get('pane_id')))
        if len(matches) != 1:
            raise ValueError('Cannot uniquely resolve the created checkout workspace. Inspect its terminal before retrying.')
        workspace, pane = matches[0]
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

    def advance_discussions(self):
        # Waiting meetings reserve no busy workers. Recheck on the normal poll loop.
        with self.lock, closing(self.connect()) as db:
            rows = db.execute(
                "SELECT rowid,data FROM jobs WHERE json_extract(data, '$.kind')='discussion' "
                "AND json_extract(data, '$.state')='waiting_for_members' AND rowid>? ORDER BY rowid LIMIT 20",
                (self.discussion_cursor,)).fetchall()
            if not rows and self.discussion_cursor:
                self.discussion_cursor = 0
                rows = db.execute(
                    "SELECT rowid,data FROM jobs WHERE json_extract(data, '$.kind')='discussion' "
                    "AND json_extract(data, '$.state')='waiting_for_members' ORDER BY rowid LIMIT 20").fetchall()
            if rows:
                self.discussion_cursor = rows[-1]['rowid']
            waiting = [json.loads(row['data']) for row in rows]
            for job in waiting:
                job.update(state='queued', updated_at=now())
                self.put(db, 'jobs', job)
            db.commit()
        for job in waiting:
            with self.lock:
                future = self.worker.submit(self.execute, job['id'])
                self.futures.add(future)
                future.add_done_callback(self._finished)

    def execute(self, job_id):
        with self.lock, closing(self.connect()) as db:
            job = self.get(db, 'jobs', job_id)
            if job.get('state') != 'queued':
                return  # A second worker/retry must never replay an accepted job.
            ids = sorted(set(job.get('participants') or [job.get('profile_id')]) - {None})
            locks = [self.agent_locks.setdefault(profile_id, threading.RLock()) for profile_id in ids]
        acquired = []
        try:
            for lock in locks:
                if job['kind'] == 'discussion':
                    if not lock.acquire(blocking=False):
                        from discussion_scheduler import mark_waiting
                        mark_waiting(self, job_id, 'Waiting for active member work; no discussion prompt sent')
                        return
                else:
                    lock.acquire()
                acquired.append(lock)
            if job['kind'] == 'discussion':
                from discussion_scheduler import prepare
                try:
                    prepared = prepare(self, job_id)
                except Exception as error:  # A preparation fault must stay visible, never silently queued.
                    self.update_job(job_id, state='needs_attention',
                                    error=(str(error) or error.__class__.__name__)[:500],
                                    progress='Preparation stopped before discussion submission')
                    return
                if not prepared:
                    return
            self._execute(job_id)
        finally:
            for lock in reversed(acquired):
                lock.release()

    def _execute(self, job_id):
        with self.lock, closing(self.connect()) as db, db:
            job = self.get(db, 'jobs', job_id)
            if job.get('state') != 'queued':
                return
            job.update(state='running', updated_at=now())
            self.put(db, 'jobs', job)
        try:
            if job['kind'] in ('chat', 'discussion', 'input'):
                from collaboration import execute as collaboration_execute
                collaboration_execute(self, job)
            elif job['kind'] == 'launch':
                job = self.update_job(job_id, launch_stage='preflight')
                self.check_contract()
                profile = job['profile']
                if self.runtime_status is not None:
                    status = self.runtime_status(profile['runtime'])
                    if status.get('installed') is False or status.get('status') in ('missing', 'not_configured') and profile['runtime'] in ('codex', 'claude'):
                        raise ValueError(f"{profile['name']} uses {profile['runtime']}. Connect this CLI on the CLI accounts page before launching; other CLI accounts do not configure it.")
                    if self.runtime_ready is not None:
                        problem = self.runtime_ready(profile['runtime'])
                        if problem:
                            raise ValueError(f"{profile['name']} cannot run yet: {problem}")
                if self.model_validator is not None:
                    self.model_validator(profile)
                job = self.update_job(job_id, launch_stage='workspace_preparing')
                project = self.project(profile['project'])
                source = Path(project)
                repository = any((parent / '.git').exists() for parent in (source, *source.parents))
                if job.get('reuse_checkout'):
                    checkout = Path(job.get('worktree_path') or job.get('source_project', project)).resolve()
                    if not checkout.is_relative_to(self.projects) or not checkout.is_dir():
                        raise ValueError('Assigned checkout is missing or outside managed projects.')
                    if job.get('worktree_branch'):
                        import project_git
                        if project_git.git(checkout, 'symbolic-ref', '--short', 'HEAD').strip() != job['worktree_branch']:
                            raise ValueError('Assigned checkout branch changed; inspect before restarting.')
                    created = self.command('workspace', 'create', '--cwd', str(checkout), '--label', profile['name'], '--no-focus')
                elif profile.get('use_worktree', True) and repository:
                    root = (self.projects / '.herdr-worktrees').resolve()
                    if not root.is_relative_to(self.projects):
                        raise ValueError('Worktree directory must remain inside the projects directory.')
                    root.mkdir(exist_ok=True, mode=0o700)
                    checkout = root / job['id']
                    branch = job.get('worktree_branch') or 'codex/herdr-' + job['id']
                    import project_git
                    configured_base = project_git.configured_base(project)
                    if configured_base and not job.get('start_sha'):
                        job = self.update_job(job_id, start_sha=project_git.git(
                            project, 'rev-parse', '--verify', configured_base + '^{commit}').strip())
                    job = self.update_job(job_id, worktree_path=str(checkout), worktree_branch=branch,
                                          source_project=project, workspace_mode='worktree')
                    # Never fall back to the shared checkout after a Git/Herdr failure.
                    from repository_lock import repository_lock
                    with repository_lock(project):
                        created = self.command('worktree', 'create', '--cwd', project, '--branch', branch,
                                               '--path', str(checkout), '--label', profile['name'], '--no-focus', timeout=120)
                        if job.get('start_sha'):
                            # Pin only this new, clean checkout before starting any
                            # agent. Never reset an existing or live task checkout.
                            if (project_git.git(checkout, 'symbolic-ref', '--short', 'HEAD').strip() != branch
                                    or project_git.git(checkout, 'status', '--porcelain=v1', '--untracked-files=all').strip()
                                    or project_git.merge_state(checkout)):
                                raise ValueError('New task checkout is not clean on its assigned branch.')
                            project_git.git(checkout, 'cat-file', '-e', job['start_sha'] + '^{commit}')
                            project_git.git(checkout, 'reset', '--hard', job['start_sha'])
                            if project_git.git(checkout, 'rev-parse', 'HEAD').strip() != job['start_sha']:
                                raise ValueError('Task checkout did not reach its recorded starting commit.')
                else:
                    job = self.update_job(job_id, workspace_mode='workspace', source_project=project,
                                          workspace_note='Not a Git repository; using the selected directory.' if profile.get('use_worktree', True) else '')
                    created = self.command('workspace', 'create', '--cwd', project, '--label', profile['name'], '--no-focus')
                pane, workspace = self.created_pane(created, job.get('worktree_path', project))
                job = self.update_job(job_id, pane_id=pane, workspace_id=workspace)
                self.wait_for_shell(pane)
                if job.get('task_prompt') or job.get('task_id'):
                    from worker_guidance import local_bundle
                    guidance = local_bundle(job.get('worktree_path', project))
                # Any agent can be asked to save a chat reply: an operator
                # message, a consultation, or a delegation reply all write under
                # chat-replies. The launch happens before the chat exists, so
                # the grant cannot be decided from the job kind here. Only a
                # group facilitator or a discussion member also writes an
                # artifact and transcript.
                output_directories = ['chat-replies']
                if profile.get('group_id') or job.get('session_purpose') == 'discussion':
                    output_directories.append('discussion-artifacts')
                arguments = launch_arguments(profile) + prepare_permissions(
                    profile, self.path.parent, output_directories=tuple(output_directories))
                # Native resume uses adapter arguments validated before they were
                # recorded; free-form command text is never executed here.
                resume_args = job.get('resume_args')
                if isinstance(resume_args, list) and resume_args and all(isinstance(a, str) and 0 < len(a) <= 200 for a in resume_args):
                    arguments = arguments + [str(a) for a in resume_args][:20]
                # Record the exact executable arguments sent to Herdr, separately
                # from the editable profile and any later in-TUI model changes.
                job = self.update_job(job_id, launch_arguments=arguments)
                job = self.update_job(job_id, launch_stage='agent_starting')
                self.command('agent', 'start', job['alias'], '--kind', profile['runtime'], '--pane', pane, '--timeout', '60000', *(['--', *arguments] if arguments else []), timeout=70)
                agent = self.identity(job)
                job = self.update_job(job_id, agent_session=agent.get('agent_session'))
                if job.get('task_id'):
                    job = self.update_job(job_id, completion_token=uuid.uuid4().hex)
                org = job['organization']
                prompt = (f"Organization: {org['name']}\nPurpose: {org['purpose']}\nShared instructions:\n{org['instructions']}\n\n"
                          f"You are {profile['name']}, our {profile['role']}. Persona (version {profile['version']}):\n{profile['persona']}\n\n"
                          'Read and follow the project owner instructions. Adopt this persona for this conversation. '
                          'Acknowledge readiness and wait for an assigned task. Use Herdr agent commands for explicit delegation; '
                          'do not interpret a delivered prompt or idle status as proof of completed work.')
                prompt += '\nNever push branches or create pull requests without explicit publication authorization. Dashboard task publishing is administrator-controlled.'
                if job.get('task_id'):
                    receipt_directory = guidance.parent / ('task-' + job['task_id'])
                    if receipt_directory.is_symlink():
                        raise ValueError('Task receipt directory must not be a symlink.')
                    receipt_directory.mkdir(exist_ok=True)
                    prompt += ('\nTask completion protocol for this session: after authorized work is committed, write JSON to ' +
                               (receipt_directory / 'receipt.json').as_posix() +
                               ' with outcome=complete, commit=full HEAD SHA, run_id=' + job['id'] +
                               ', token=' + job['completion_token'] + ', and tests as an array of actual check results. '
                               'This protocol does not authorize replaying an old task.')
                if job.get('task_prompt'):
                    deployed = (Path(__file__).parent / 'skills/herdr-worktree-integration').resolve().as_posix()
                    task_prompt = job['task_prompt'].replace(deployed, guidance.as_posix())
                    if job.get('task_id'):
                        task_prompt = task_prompt.replace('{{HERDR_TASK_RECEIPT}}', (receipt_directory / 'receipt.json').as_posix()).replace('{{HERDR_TASK_RUN}}', job['id']).replace('{{HERDR_TASK_TOKEN}}', job['completion_token'])
                    prompt += '\n\nAssigned task (start now in this checkout):\n' + task_prompt
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
                    # Group launches replace the prompt above, so restate the
                    # publication boundary instead of losing it here.
                    prompt += ('\nNever push branches or create pull requests without explicit '
                               'publication authorization. Dashboard task publishing is administrator-controlled.')
                    if job.get('continuation_context'):
                        prompt += '\n\n' + job['continuation_context']
                    self.command('agent', 'prompt', job['alias'], prompt, '--wait', '--timeout', '180000', timeout=190)
                    self.identity(job)
                else:
                    if job.get('continuation_context'):
                        prompt += '\n\n' + job['continuation_context']
                    response = self.command('agent', 'prompt', job['alias'], prompt)
                    self.update_job(job_id, delivery=dict(stage='sent', at=now(),
                                                          completion=self.completion_baseline(response)))
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
            code = getattr(exc, 'code', '')
            delivery = delivery_for(exc)
            detail = str(exc)[:500]
            if job['kind'] in ('launch', 'chat', 'delegate') and delivery in ('blocked', 'unknown') and code:
                self.update_job(job_id, delivery=dict(stage='blocked' if delivery == 'blocked' else 'unknown',
                                                      code=code, at=now()))
            if job['kind'] == 'chat':
                # Diagnostics must never replace the original failure with their own.
                try:
                    with self.lock, closing(self.connect()) as db:
                        current = self.get(db, 'jobs', job_id)
                    stage = (current.get('delivery') or {}).get('stage', 'not_submitted')
                except Exception:
                    stage = 'unknown'
                detail += f' Delivery stage: {stage}. Inspect Org chart → Chat before retrying; terminal input may have been sent.'
            elif job['kind'] == 'launch' and job.get('launch_stage') == 'preflight':
                detail += ' Launch failed during preflight; no workspace or pane was created and no terminal input was sent.'
            elif job['kind'] == 'launch' and job.get('launch_stage') == 'workspace_preparing':
                detail += ' Launch failed while preparing the workspace; no agent was started and no terminal input was sent.'
            elif delivery == 'blocked':
                detail += ' The agent needs a decision; no prompt input was sent. Inspect the session and choose an action.'
            elif delivery == 'unknown' and code:
                detail += ' Prompt delivery is unknown; inspect the terminal before retrying.'
            else:
                detail += ' Inspect SSH before retrying; terminal input may have been sent.'
            self.update_job(job_id, state='needs_attention', error=detail)
