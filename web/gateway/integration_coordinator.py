"""Durable, idle-only integration inbox and coordinator reports."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

import project_git
from session_ownership import reserves as session_reserves
from blockers import TOOLCHAIN, label as blocker_label, normalize as normalize_blocker

# An approval is a point-in-time decision. It expires, and its exact checkout
# revision is re-verified immediately before any approved work is delivered.
APPROVAL_TTL_SECONDS = 24 * 60 * 60


def merge_skill(checkout=None):
    # Deliver the bundled procedure even when the worker's older checkout lacks
    # project-local skills. It grants no extra OpenCode permissions.
    directory = Path(__file__).parent / 'skills/herdr-worktree-integration'
    if checkout is not None:
        from worker_guidance import local_bundle
        directory = local_bundle(checkout)
    entry = (directory / 'SKILL.md').read_text(encoding='utf-8')
    # An existing worker may have an older checkout without these files.
    # Anchor references to the checkout copy for workers; never flatten them into chat.
    for name in ('merge.md', 'tools.md', 'validation.md', 'report.md'):
        resource = directory / 'references' / name
        if resource.is_symlink() or not resource.is_file():
            raise ValueError('Worker skill reference is missing or unsafe: ' + name)
        entry = entry.replace('(references/' + name + ')', '(' + resource.resolve().as_posix() + ')')
    return entry


def merge_mode(checkout, target):
    """Read-only classification; the worker rechecks before any mutation."""
    return 'fast_forward' if int(project_git.git(checkout, 'rev-list', '--count', target + '..HEAD').strip()) == 0 else 'merge'


def stamp():
    return datetime.now(timezone.utc).isoformat()


def identity(*values):
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def report_digest(reports, bounded):
    """Detect changes anywhere while the delivered prompt stays bounded."""
    signature = [{key: event.get(key) for key in
                  ('id', 'state', 'name', 'path', 'target', 'reason', 'checkpoint', 'tests', 'verification', 'recovery', 'blocker', 'validation_run')}
                 for event in reports]
    return identity('summary-only-v5', signature, bounded)


class IntegrationCoordinator:
    def __init__(self, path, store, sdk_request=None):
        self.path, self.store = Path(path), store
        # Fixed-purpose, best-effort hook for an opt-in SDK install request.
        self.sdk_request = sdk_request
        # Set by the gateway once the durable build queue is available.
        self.build_service = False
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self._dispatch_jobs = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript('CREATE TABLE IF NOT EXISTS settings (repository TEXT PRIMARY KEY, data TEXT NOT NULL);'
                             'CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, data TEXT NOT NULL);'
                             'CREATE TABLE IF NOT EXISTS audit (sequence INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);')

    def close(self):
        self.stopped.set()

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, db, event):
        previous = db.execute('SELECT data FROM events WHERE id=?', (event['id'],)).fetchone()
        previous = json.loads(previous[0]) if previous else {}
        if any(previous.get(k) != event.get(k) for k in ('state', 'job_id', 'reason', 'recovery', 'tests', 'verification', 'merge_mode')):
            self.audit(db, dict(event_id=event['id'], repository=event['repository'],
                                profile_id=event['profile_id'], name=event.get('name'), path=event['path'],
                                action='transition', before=previous.get('state'), state=event['state'],
                                reason=event.get('reason'), job_id=event.get('job_id'), recovery=event.get('recovery'),
                                tests=event.get('tests'), verification=event.get('verification'), merge_mode=event.get('merge_mode')))
        event['updated_at'] = stamp()
        db.execute('INSERT OR REPLACE INTO events VALUES (?, ?)', (event['id'], json.dumps(event)))

    def audit(self, db, entry):
        db.execute('INSERT INTO audit(data) VALUES (?)', (json.dumps(dict(entry, at=stamp())),))

    def coordinator_profile(self, org_id, repository):
        """Reuse the repository's existing coordinator profile, or hire one.

        A configuration import carries the coordinator profile but not the
        coordination settings, so enabling coordination afterwards must reuse
        that profile instead of creating a duplicate agent.
        """
        project = str((self.store.projects / repository).resolve())
        existing = next((profile for profile in self.store.profile_records()
                         if profile.get('organization_id') == org_id and not profile.get('removed_at')
                         and profile.get('role') == 'Integration coordinator'
                         and isinstance(profile.get('project'), str) and profile['project']
                         and str(Path(profile['project']).resolve()) == project), None)
        if existing is not None:
            return existing['id']
        # Deterministic request ids make creation safe to retry after a crash.
        key = identity('coordinator', repository, org_id)
        return self.store.action('hire', dict(
            request_id=key, organization_id=org_id, name='Integration coordinator',
            role='Integration coordinator', runtime='opencode',
            project=project, use_worktree=True,
            permission_mode='dashboard_outputs',
            persona='Coordinate integration decisions and summarize queued reports. '
                    'Initialization only: acknowledge readiness and wait for assigned reports. '
                    'Do not change project files or prompt other agents directly. '
                    'The durable gateway inbox delivers requests when workers are idle. '
                    'Preserve deferrals and blockers; never claim a merge or test without evidence.'))['id']

    def configure(self, body, actor=None, role='admin'):
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        if 'auto_sdk' in body and role != 'admin':
            raise ValueError('Only an administrator can change automatic SDK installation.')
        if 'auto_build' in body and role != 'admin':
            raise ValueError('Only an administrator can change automatic builds.')
        repository = body.get('repository')
        if not isinstance(repository, str):
            raise ValueError('Select a repository.')
        info = project_git.inspect(self.store.projects, {'path': repository})
        if not info.get('repository'):
            raise ValueError('Select a Git repository.')
        repository = info['repository_path']
        enabled = body.get('enabled', True)
        if type(enabled) is not bool:
            raise ValueError('Enabled must be a boolean.')
        auto_sdk = body.get('auto_sdk')
        if auto_sdk is not None and type(auto_sdk) is not bool:
            raise ValueError('Automatic SDK installation must be a boolean.')
        auto_build = body.get('auto_build')
        if 'auto_build' in body and type(auto_build) is not bool:
            raise ValueError('Automatic builds must be a boolean.')
        if auto_build is True and not self.build_service:
            raise ValueError('Install the durable build service before enabling automatic builds.')
        org_id = body.get('organization_id')
        profile_id = body.get('profile_id')
        with self.lock, closing(self.connect()) as db, db:
            old = db.execute('SELECT data FROM settings WHERE repository=?', (repository,)).fetchone()
            old = json.loads(old[0]) if old else {}
            if not enabled:
                config = dict(old, repository=repository, enabled=False)
            else:
                if profile_id == 'new':
                    profile_id = self.coordinator_profile(org_id, repository)
                    # Keyed on the resolved profile so a reused imported profile
                    # launches once even if coordination is enabled repeatedly.
                    runs = [job for job in self.store.job_records(launches_only=True)
                            if job.get('profile_id') == profile_id]
                    if not any(job.get('state') != 'released' for job in runs):
                        self.store.action('launch', dict(request_id=identity(profile_id, 'launch', len(runs)),
                                                         organization_id=org_id, profile_id=profile_id))
                profiles = self.store.snapshot(directory=True)['profiles']
                profile = next((p for p in profiles if p['id'] == profile_id), None)
                if profile is None:
                    raise ValueError('Coordinator profile is unavailable.')
                config = dict(old if old.get('profile_id') == profile_id else {}, repository=repository, enabled=True,
                              profile_id=profile_id, organization_id=profile['organization_id'])
            if auto_sdk is not None:
                config['auto_sdk'] = auto_sdk
            if auto_build is not None:
                config['auto_build'] = auto_build
            db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (repository, json.dumps(config)))
            self.audit(db, dict(action='configuration', repository=repository, enabled=enabled,
                                profile_id=config.get('profile_id'), auto_build=config.get('auto_build', False),
                                actor=actor or 'dashboard_operator'))
        return config

    def observe(self, notices):
        with self.lock, closing(self.connect()) as db, db:
            settings = {r[0]: json.loads(r[1]) for r in db.execute('SELECT * FROM settings')}
            for notice in notices:
                config = settings.get(notice.get('repository'), {})
                if (not config.get('enabled') or not notice.get('target') or
                        not notice.get('behind') or notice['profile_id'] == config['profile_id']):
                    continue
                for row in db.execute('SELECT data FROM events').fetchall():
                    previous = json.loads(row[0])
                    if (previous['repository'] == notice['repository'] and previous['profile_id'] == notice['profile_id']
                            and previous['target'] != notice['target'] and previous['state'] in ('waiting', 'deferred', 'ready')):
                        previous.update(state='superseded', reason='A newer main commit is queued.')
                        self.save(db, previous)
                key = identity(notice['repository'], notice['target'], notice['profile_id'], notice['run_id'])
                if db.execute('SELECT 1 FROM events WHERE id=?', (key,)).fetchone():
                    continue
                event = dict(notice, id=key, state='waiting', created_at=stamp(), reason='',
                             coordinator_id=config['profile_id'], organization_id=config['organization_id'])
                self.save(db, event)

    def repair_guidance(self, body, actor=None):
        if not isinstance(body, dict) or body.get('inspected') is not True:
            raise ValueError('Inspect the idle worker before repairing its guidance.')
        with self.lock, closing(self.connect()) as db, db:
            event = self.event(body.get('id'))
            if not event:
                raise ValueError('Integration event not found.')
            path = self.store.projects / event['path']
            bundle = self.store.repair_guidance(event['profile_id'], path)
            self.audit(db, dict(action='guidance_repair', repository=event['repository'],
                                profile_id=event['profile_id'], actor=actor or 'dashboard_operator',
                                reason='Verified checkout guidance restored', path=str(path)))
        return {'bundle': bundle}

    def repair_report(self, body, actor=None):
        if (not isinstance(body, dict) or not isinstance(body.get('repository'), str)
                or body.get('mode') not in ('recover', 'fresh')):
            raise ValueError('Choose saved reply recovery or a fresh summary.')
        if body['mode'] == 'fresh' and body.get('inspected') is not True:
            raise ValueError('Inspect the previous conversation before requesting a fresh summary.')
        with self.lock, closing(self.connect()) as db, db:
            row = db.execute('SELECT data FROM settings WHERE repository=?', (body.get('repository'),)).fetchone()
            if not row:
                raise ValueError('Coordinator is not configured for this repository.')
            config = json.loads(row[0])
            report_id = body.get('job_id')
            if not isinstance(report_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', report_id):
                raise ValueError('Select the interrupted report.')
            wanted = 'recovered' if body['mode'] == 'recover' else 'fresh'
            if config.get('repaired_report_id') == report_id and config.get('repair_resolution') == wanted:
                return {'resolution': config.get('repair_resolution')}
            if not config.get('enabled'):
                raise ValueError('Coordination is paused. Resume it before repairing the report.')
            if config.get('report_job_id') != report_id:
                raise ValueError('The coordinator report changed. Refresh before repairing it.')
            resolution = self.store.resolve_coordinator_report(report_id, config['profile_id'], body['mode'])
            config.update(repaired_report_id=report_id, repair_resolution=resolution)
            if resolution == 'fresh':
                config['report_epoch'] = config.get('report_epoch', 0) + 1
                config.pop('report_job_id', None)
                config.pop('report_digest', None)
                config['report_pending'] = True
            db.execute('UPDATE settings SET data=? WHERE repository=?', (json.dumps(config), config['repository']))
            self.audit(db, dict(action='report_repair', repository=config['repository'],
                                profile_id=config['profile_id'], job_id=report_id, reason=resolution,
                                actor=actor or 'dashboard_operator'))
        return {'resolution': resolution}

    def recover_worker(self, body, actor=None):
        if (not isinstance(body, dict) or body.get('inspected') is not True
                or body.get('mode') not in ('recover', 'validate')):
            raise ValueError('Inspect the worker conversation and choose a recovery action.')
        event = self.event(body.get('id'))
        if not event:
            raise ValueError('Integration event not found.')
        with self.lock, closing(self.connect()) as db, db:
            event = json.loads(db.execute('SELECT data FROM events WHERE id=?', (event['id'],)).fetchone()[0])
            job_id = body.get('job_id')
            if (event.get('repaired_job_id') == job_id and event.get('repair_mode') == body['mode']
                    and event.get('repair_actor') == (actor or 'dashboard_operator')):
                return event
            if event['state'] != 'needs_attention' or not job_id or event.get('job_id') != job_id:
                raise ValueError('The interrupted job changed. Refresh before recovering.')
            jobs = {j['id']: j for j in self.store.job_records()}
            job = jobs.get(job_id)
            if job is None:
                raise ValueError('The original worker job is missing.')
            phase = event.get('interrupted_phase')
            if phase not in ('deciding', 'integrating', 'validating'):
                phase = ('validating' if 'Validation only in your assigned checkout' in job.get('prompt', '')
                         else 'integrating' if event.get('recovery') else 'deciding')
            if body['mode'] == 'validate':
                cwd = self.store.projects / event['path']
                behind = int(project_git.git(cwd, 'rev-list', '--count', 'HEAD..' + event['target']).strip())
                tree = project_git.summary(self.store.projects, cwd)
                if behind or tree['conflicts'] or tree['merging']:
                    raise ValueError('Validation only requires the target incorporated without conflicts or active operations.')
                event['verification'] = dict(target_incorporated=True, conflicts=0, merging=False)
            self.store.resolve_worker_job(job_id, event['profile_id'], event['id'], body['mode'])
            event.update(state=phase if body['mode'] == 'recover' else 'validation_ready',
                         repaired_job_id=job_id, repair_mode=body['mode'],
                         repair_actor=actor or 'dashboard_operator', reason='')
            if body['mode'] == 'validate':
                event.pop('job_id', None)
                event['attempt'] = event.get('attempt', 0) + 1
            self.save(db, event)
            self.audit(db, dict(action='worker_repair', repository=event['repository'],
                                profile_id=event['profile_id'], job_id=job_id, state=event['state'],
                                reason=body['mode'], actor=actor or 'dashboard_operator'))
        return event

    def retry(self, event_id, actor=None):
        if not isinstance(event_id, str) or not re.fullmatch(r'[a-f0-9]{64}', event_id):
            raise ValueError('Invalid integration event ID.')
        with self.lock, closing(self.connect()) as db, db:
            row = db.execute('SELECT data FROM events WHERE id=?', (event_id,)).fetchone()
            if not row:
                raise ValueError('Integration event not found.')
            event = json.loads(row[0])
            config = db.execute('SELECT data FROM settings WHERE repository=?', (event['repository'],)).fetchone()
            config = json.loads(config[0]) if config else {}
            if not config.get('enabled'):
                raise ValueError('Coordination is paused. Resume it before requesting retries.')
            if event['state'] not in ('deferred', 'blocked', 'validation_pending', 'validation_failed'):
                raise ValueError('Only deferred, blocked or pending/failed validation events may be retried. Inspect uncertain jobs first.')
            validate = event['state'] in ('validation_pending', 'validation_failed') or (
                event.get('verification', {}).get('target_incorporated') and not event.get('verification', {}).get('conflicts')
                and not event.get('verification', {}).get('merging'))
            event.update(state='validation_ready' if validate else 'waiting', attempt=event.get('attempt', 0) + 1, reason='')
            event.pop('job_id', None)
            if not validate:
                event.pop('recovery', None)  # A new merge needs a fresh checkpoint.
            self.save(db, event)
            self.audit(db, dict(action='retry', repository=event['repository'], profile_id=event['profile_id'],
                                 state=event['state'], actor=actor or 'dashboard_operator'))
        return event

    def event(self, event_id):
        if not isinstance(event_id, str) or not re.fullmatch(r'[a-f0-9]{64}', event_id):
            return None
        with self.lock, closing(self.connect()) as db:
            row = db.execute('SELECT data FROM events WHERE id=?', (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_validation_run(self, event_id, summary):
        """Attach an exact-commit runner result to its event for the dashboard."""
        with self.lock, closing(self.connect()) as db, db:
            row = db.execute('SELECT data FROM events WHERE id=?', (event_id,)).fetchone()
            if not row:
                return
            event = json.loads(row[0])
            event['validation_run'] = summary
            self.save(db, event)
            self.audit(db, dict(action='validation_run', repository=event['repository'],
                                profile_id=event['profile_id'], **summary))

    def approval_check(self, event):
        """Re-verify an operator approval immediately before delivery.

        Returns (ok, blocker_code, detail, verification). A stale or expired
        approval is never delivered; the event returns to the blocker queue.
        """
        approval = event.get('operator_approval') or {}
        if not approval:
            return True, None, '', None
        expires = approval.get('expires_at')
        if expires:
            try:
                expired = datetime.fromisoformat(expires) <= datetime.now(timezone.utc)
            except (ValueError, TypeError):
                expired = True
            if expired:
                return False, 'approval_expired', 'The operator approval expired before delivery.', None
        else:
            return False, 'approval_expired', 'The operator approval has no expiry and cannot be trusted.', None
        checkout = self.store.projects / event['path']
        approved_head = approval.get('approved_head')
        if approved_head:
            try:
                head = project_git.git(checkout, 'rev-parse', 'HEAD').strip()
            except (ValueError, OSError):
                return False, 'approval_stale', 'The approved checkout can no longer be inspected.', None
            if head != approved_head:
                return False, 'approval_stale', 'The checkout changed after the operator approval.', None
        if approval.get('action') not in ('retry_validation', 'waive_validation'):
            return True, None, '', None
        # Fresh Git verification: incorporation must still hold right now.
        try:
            behind = int(project_git.git(checkout, 'rev-list', '--count', 'HEAD..' + event['target']).strip())
            tree = project_git.summary(self.store.projects, checkout)
            verification = dict(target_incorporated=not behind, conflicts=tree['conflicts'], merging=tree['merging'])
        except (ValueError, OSError):
            return False, 'verification_failed', 'Git verification could not run for this checkout.', None
        if behind or tree['conflicts'] or tree['merging']:
            return False, 'verification_failed', 'Git verification no longer confirms the exact commit.', verification
        return True, None, '', verification

    def missing_toolchain(self, config, event):
        """Queue the scoped SDK install once, when the operator opted in."""
        if not config.get('auto_sdk') or not event.get('blocker') == TOOLCHAIN or self.sdk_request is None:
            return
        if event.get('sdk_requested'):
            return
        try:
            self.sdk_request()
        except (ValueError, OSError):
            return
        event['sdk_requested'] = True
        event['reason'] = (str(event.get('reason', '')) + ' The dashboard queued the pinned SDK installation.').strip()[:2000]

    def review_blockers(self, body, actor='dashboard_operator', role='admin'):
        """Approve exact event revisions atomically; never change CLI permissions.

        Missing tools cannot be made present by an override. A waiver records
        an operator exception while preserving the original test evidence.
        """
        if not isinstance(body, dict):
            raise ValueError('Invalid blocker review.')
        request_id, reason, selections = body.get('request_id'), body.get('reason'), body.get('selections')
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-f0-9]{32}', request_id):
            raise ValueError('Invalid approval request ID.')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError('An approval reason is required (maximum 1000 characters).')
        if not isinstance(selections, list) or not 1 <= len(selections) <= 20:
            raise ValueError('Select 1–20 blocker actions.')
        with self.lock, closing(self.connect()) as db, db:
            # Lost-response retries reuse the durable request and do not dispatch again.
            previous = db.execute("SELECT data FROM audit WHERE json_extract(data, '$.approval_id')=?", (request_id,)).fetchone()
            if previous:
                saved = json.loads(previous[0])
                if saved.get('selections') != selections or saved.get('reason') != reason.strip() or saved.get('actor') != actor:
                    raise ValueError('Approval request ID was reused with different content.')
                return {'approved': len(selections), 'request_id': request_id}
            pending, seen = [], set()
            for choice in selections:
                if not isinstance(choice, dict) or choice.get('action') not in ('retry_validation', 'waive_validation', 'reconsider'):
                    raise ValueError('Unsupported blocker action.')
                event_id = choice.get('id')
                if not isinstance(event_id, str) or not re.fullmatch(r'[a-f0-9]{64}', event_id) or event_id in seen:
                    raise ValueError('Invalid or duplicate event selection.')
                seen.add(event_id)
                row = db.execute('SELECT data FROM events WHERE id=?', (event_id,)).fetchone()
                if not row:
                    raise ValueError('Integration event not found.')
                event = json.loads(row[0])
                if any(choice.get(key) != event.get(key) for key in ('repository', 'target', 'updated_at')):
                    raise ValueError('Blocker changed. Refresh and review it again.')
                if event['state'] not in ('blocked', 'deferred', 'validation_pending', 'validation_failed', 'validation_waived'):
                    raise ValueError('This event requires inspection, not bulk approval.')
                action = choice['action']
                if action == 'waive_validation' and role != 'admin':
                    raise ValueError('Only an administrator can waive validation.')
                verified = event.get('verification') or {}
                incorporated = verified.get('target_incorporated') and not verified.get('conflicts') and not verified.get('merging')
                if action in ('retry_validation', 'waive_validation') and not incorporated:
                    raise ValueError('Validation actions require verified incorporation without conflicts.')
                if action == 'reconsider' and (incorporated or event['state'] not in ('blocked', 'deferred')):
                    raise ValueError('Use a validation action for an incorporated commit.')
                if action != 'waive_validation':
                    row = db.execute('SELECT data FROM settings WHERE repository=?', (event['repository'],)).fetchone()
                    if not row or not json.loads(row[0]).get('enabled'):
                        raise ValueError('Coordination is paused. Resume it before requesting work.')
                pending.append((event, action))
            for event, action in pending:
                approved_head = None
                try:
                    approved_head = project_git.git(self.store.projects / event['path'], 'rev-parse', 'HEAD').strip()
                except (ValueError, OSError):
                    raise ValueError('Inspect the affected checkout before approving.')
                approval = dict(request_id=request_id, action=action, reason=reason.strip(),
                                actor=actor, target=event['target'], path=event['path'],
                                at=datetime.now(timezone.utc).isoformat(),
                                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=APPROVAL_TTL_SECONDS)).isoformat(),
                                approved_head=approved_head)
                event['operator_approval'] = approval
                if action == 'waive_validation':
                    ok, _, detail, fresh = self.approval_check(event)
                    if not ok:
                        raise ValueError(detail)
                    event['verification'] = fresh
                if action == 'waive_validation':
                    event['state'] = 'validation_waived'
                else:
                    event.update(state='validation_ready' if action == 'retry_validation' else 'waiting',
                                 attempt=event.get('attempt', 0) + 1)
                    event.pop('job_id', None)
                    if action == 'reconsider':
                        event.pop('recovery', None)
                self.save(db, event)
                self.audit(db, dict(action='blocker_approval', repository=event['repository'],
                                    profile_id=event['profile_id'], state=event['state'], approval=approval))
            self.audit(db, dict(action='blocker_review', approval_id=request_id,
                                selections=selections, reason=reason.strip(), actor=actor))
        return {'approved': len(selections), 'request_id': request_id}

    def ready(self, profile_id, run_id=None):
        jobs = self._dispatch_jobs if self._dispatch_jobs is not None else self.store.job_records()
        runs = [j for j in jobs if j['kind'] == 'launch' and
                j.get('profile_id') == profile_id and j['state'] == 'persona_sent']
        run = next((j for j in reversed(runs) if not run_id or j['id'] == run_id), None)
        if run is None:
            return None
        if any(session_reserves(j, profile_id) for j in jobs):
            return None
        try:
            self.store.identity(run)
        except ValueError:
            return None
        return run

    def submit(self, event, phase, prompt, profile_id=None):
        if self.stopped.is_set():
            return None
        approval = event.get('operator_approval')
        if approval and approval.get('target') == event.get('target'):
            prompt = ('Operator review for this checkout and exact commit only: ' + json.dumps(approval) +
                      '\nThis does not change runtime permissions or authorize installs, push or deployment.\n' + prompt)
        profile_id = profile_id or event['profile_id']
        run = self.ready(profile_id, event['run_id'] if profile_id == event['profile_id'] else None)
        if not run:
            return None
        # OrganizationStore also checks readiness and occupied participants under
        # its own lock; the pre-check alone never authorizes terminal delivery.
        job_id = self.store.action('chat', dict(
            request_id=identity(event['id'], phase, event.get('attempt', 0), profile_id),
            organization_id=run['organization_id'], profile_id=profile_id,
            prompt=prompt, wait_seconds=1800, integration_event=event['id']))['id']
        if self._dispatch_jobs is not None:
            self._dispatch_jobs.append(dict(id=job_id, kind='chat', profile_id=profile_id, state='queued'))
        return job_id

    def tick(self):
        """Advance durable events once; returns True when another tick can progress."""
        with self.lock, closing(self.connect()) as db, db:
            advance_pending = False
            settings = {r[0]: json.loads(r[1]) for r in db.execute('SELECT * FROM settings')}
            events = [json.loads(r[0]) for r in db.execute("SELECT data FROM events ORDER BY CASE WHEN json_extract(data, '$.state') IN ('waiting','ready','deciding','integrating','deferred','validation_ready','validating') THEN 0 ELSE 1 END, rowid DESC LIMIT 100")]
            self._dispatch_jobs = list(self.store.job_records())
            jobs = {j['id']: j for j in self._dispatch_jobs}
            for event in events:
                if event['state'] not in ('waiting', 'ready', 'deciding', 'integrating', 'deferred', 'validation_ready', 'validating'):
                    continue
                if (not settings.get(event['repository'], {}).get('enabled')
                        and event['state'] not in ('deciding', 'integrating', 'validating')):
                    continue
                try:
                    # Enabled coordination authorizes worker delivery; summary
                    # availability must not freeze unrelated workers.
                    state = event['state']
                    if state in ('waiting', 'ready', 'validation_ready') and event.get('operator_approval'):
                        # An approval is point-in-time: expire it and re-verify the
                        # exact checkout immediately before any approved delivery.
                        ok, code, detail, fresh = self.approval_check(event)
                        if not ok:
                            approval = event['operator_approval']
                            event.update(state='blocked', blocker=code, reason=detail,
                                         verification=fresh or event.get('verification') or {},
                                         operator_approval={**approval, 'invalidated': code, 'invalidated_at': stamp()})
                            self.audit(db, dict(action='approval_invalid', repository=event['repository'],
                                                profile_id=event['profile_id'], target=event['target'],
                                                actor=approval.get('actor'), reason=code))
                            self.save(db, event)
                            continue
                        if fresh is not None:
                            event['verification'] = fresh
                    job = jobs.get(event.get('job_id'))
                    if state in ('deciding', 'integrating', 'validating'):
                        if not job or job['state'] in ('uncertain', 'needs_attention', 'released'):
                            event.update(state='needs_attention', interrupted_phase=state,
                                         reason=((job.get('error') or 'Inspect the existing agent job; delivery may have occurred.')
                                                 if job else 'The original agent job is missing.')[:1000])
                        elif job['state'] == 'answered':
                            if state in ('integrating', 'validating'):
                                # Completion is verified from Git, not accepted on a model claim.
                                cwd = self.store.projects / event['path']
                                behind = int(project_git.git(cwd, 'rev-list', '--count', 'HEAD..' + event['target']).strip())
                                tree = project_git.summary(self.store.projects, cwd)
                                raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', job.get('result', '').strip())
                                try:
                                    result = json.loads(raw)
                                except (ValueError, TypeError):
                                    result = {}
                                if not isinstance(result, dict):
                                    result = {}
                                tests = result.get('tests', {})
                                passed = isinstance(tests, dict) and tests.get('status') == 'passed'
                                merged = not behind and not tree['conflicts'] and not tree['merging'] and result.get('outcome') == 'integrated'
                                outcome = 'completed' if merged and passed else 'blocked'
                                if merged and isinstance(tests, dict) and tests.get('status') in ('failed', 'not_run'):
                                    outcome = 'validation_failed' if tests['status'] == 'failed' else 'validation_pending'
                                event.update(state=outcome,
                                             verification=dict(target_incorporated=not behind, conflicts=tree['conflicts'], merging=tree['merging']),
                                             blocker='verification_failed' if outcome == 'blocked' else normalize_blocker(result.get('blocker')) if outcome in ('validation_pending', 'validation_failed') else '',
                                             reason=str(result.get('reason') or ('Commit incorporated; reported tests passed.' if outcome == 'completed' else 'Integration or reported test results require review.'))[:1000],
                                             tests=dict(status=tests.get('status'), summary=str(tests.get('summary', ''))[:1000]) if isinstance(tests, dict) else {})
                                if event.get('blocker') == TOOLCHAIN:
                                    self.missing_toolchain(settings.get(event['repository'], {}), event)
                            else:
                                raw = job.get('result', '').strip()
                                raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
                                try:
                                    decision = json.loads(raw)
                                except (ValueError, TypeError):
                                    decision = {}
                                if not isinstance(decision, dict):
                                    decision = {}
                                choice = decision.get('decision')
                                decision_state = {'integrate_now': 'ready', 'defer': 'deferred', 'blocked': 'blocked'}.get(choice, 'needs_attention')
                                blocker = normalize_blocker(decision.get('blocker')) if decision_state == 'blocked' else ''
                                event.update(state=decision_state, blocker=blocker,
                                             reason=str(decision.get('reason', 'Reply must contain a valid integration decision.'))[:2000],
                                             checkpoint=str(decision.get('checkpoint', ''))[:1000],
                                             deferred_after=[j['id'] for j in jobs.values() if not j.get('integration_event') and event['profile_id'] in j.get('participants', [j.get('profile_id')])])
                                if blocker == TOOLCHAIN:
                                    self.missing_toolchain(settings.get(event['repository'], {}), event)
                                advance_pending = event['state'] == 'ready' or advance_pending
                    elif state == 'deferred':
                        completed = [j for j in jobs.values() if not j.get('integration_event') and
                                     j['id'] not in event.get('deferred_after', []) and
                                     event['profile_id'] in j.get('participants', [j.get('profile_id')]) and
                                     j['state'] in ('answered', 'delivered', 'artifact_ready')]
                        if completed:
                            event.update(state='waiting', attempt=event.get('attempt', 0) + 1)
                            event.pop('job_id', None)
                            advance_pending = True
                    elif state == 'validation_ready':
                        prompt = (f"Validation only in your assigned checkout {self.store.projects / event['path']} for exact commit {event['target']}. "
                                  "Do not merge again, switch branches, reset, stash or deploy. Confirm the commit is still incorporated and no merge/rebase/conflicts remain. "
                                  'Run required checks and save ONLY JSON in the job reply file specified by Dashboard reply delivery: {"outcome":"integrated|blocked","blocker":"missing_toolchain|missing_permissions|owner_restriction|read_only_role|task_conflict|state_conflict|unspecified","commit":"full HEAD SHA","tests":{"status":"passed|failed|not_run","summary":"commands, results and missing checks"},"reason":"..."}. '
                                  "Missing required checks mean not_run even if other suites pass. Preserve current work; report any blocker. "
                                  "Do not edit tests or work around privileged test failures with sudo, Docker, user namespaces or broad /etc access. "
                                  "Report test isolation defects with the failing command and traceback; tests that ran and failed remain failed.\n\n"
                                  "Follow the validation failure guidance in this worker skill:\n" + merge_skill(self.store.projects / event['path']))
                        job_id = self.submit(event, 'validation', prompt)
                        if job_id:
                            event.update(state='validating', job_id=job_id)
                    elif state == 'waiting':
                        prompt = (f"Integration inbox: main advanced to exact commit {event['target']}. "
                                  f"Your assigned checkout is {self.store.projects / event['path']}. "
                                  "This is a new integration request authorized by the repository owner enabling coordination, separate from any completed read-only review. Assess relevance to your current task. This message requests a decision only; do not execute a merge or change files in this step. A developer assigned implementation work may choose integrate_now; a separate authorized merge request follows. A temporary read-only review task does not itself revoke the developer role. Preserve any explicit owner restriction against integration. "
                                  "For a clean fast-forward, assess readiness and task checkpoints without performing a full code review. The gateway selects the merge path before delivery; the merge request includes the recovery ref and procedure. "
                                  'Respond ONLY with JSON: {"decision":"integrate_now|defer|blocked","blocker":"missing_toolchain|missing_permissions|owner_restriction|read_only_role|task_conflict|state_conflict|unspecified","reason":"...","checkpoint":"..."}. '
                                  "Set blocker only when blocked. Choose blocked for an explicit owner prohibition, read-only assigned role, or unavailable permissions/toolchain. Choose defer for a task checkpoint. Do not treat the decision-only instruction itself as a prohibition on choosing integrate_now.")
                        job_id = self.submit(event, 'decision', prompt)
                        if job_id:
                            event.update(state='deciding', job_id=job_id, reason='')
                        else:
                            event['reason'] = 'Waiting for the original bound agent session to be idle.'
                    elif state == 'ready':
                        if not self.ready(event['profile_id'], event['run_id']):
                            continue
                        if not event.get('recovery'):
                            # Do not hold a SQLite write transaction while
                            # waiting for another repository mutation to finish.
                            db.commit()
                            event['recovery'] = project_git.recovery_snapshot(
                                self.store.projects, event['path'], identity(event['id'], event.get('attempt', 0)))
                            self.save(db, event)
                            db.commit()  # Durable recovery and audit must precede terminal delivery.
                        event['merge_mode'] = merge_mode(self.store.projects / event['path'], event['target'])
                        command = 'git merge --ff-only' if event['merge_mode'] == 'fast_forward' else 'git merge --no-edit'
                        prompt = (f"You chose integrate_now. In your assigned checkout {self.store.projects / event['path']}, "
                                  f"Recovery snapshot is pinned at {event['recovery']['ref']}. Preserve your current work and merge exact commit {event['target']} into your existing branch. "
                                  "Inspect any existing merge/rebase first; do not start another or automatically finish an unrelated operation. "
                                  f"Read-only preflight selected {event['merge_mode']}: use {command} {event['target']} after rechecking the checkout. "
                                  "Resolve conflicts and run relevant tests. Save ONLY JSON in the job reply file specified by Dashboard reply delivery, with "
                                  '{"outcome":"integrated|blocked","blocker":"missing_toolchain|missing_permissions|owner_restriction|read_only_role|task_conflict|state_conflict|unspecified","commit":"full HEAD SHA","tests":{"status":"passed|failed|not_run","summary":"commands and results"},"reason":"..."}. '
                                  "Never claim tests passed if they were not run. "
                                  "Never discard unrelated changes, reset, force-push or deploy. Respect your assigned permissions; report blockers.\n\n"
                                  "Follow this gateway-bundled worker skill for the authorized merge:\n" + merge_skill(self.store.projects / event['path']))
                        job_id = self.submit(event, 'merge', prompt)
                        if job_id:
                            event.update(state='integrating', job_id=job_id)
                    self.save(db, event)
                except (ValueError, OSError) as error:
                    event.update(reason=str(error)[:2000])
                    if event['state'] in ('deciding', 'integrating', 'validating'):
                        event['interrupted_phase'] = event['state']
                        event['state'] = 'needs_attention'
                    self.save(db, event)
            # Coordinator receives the actual collected decisions, never guessed responses.
            for repository, config in settings.items():
                reports = [e for e in events if e['repository'] == repository]
                if not any(e['state'] in ('deferred', 'blocked', 'completed', 'needs_attention', 'validation_pending', 'validation_failed', 'validation_waived') for e in reports):
                    continue
                if not config.get('enabled') or not reports:
                    continue
                # Uncertain delivery needs operator resolution; the old prompt
                # may still be running or waiting for a permission response.
                previous = jobs.get(config.get('report_job_id'))
                if previous and previous.get('state') in ('queued', 'running', 'uncertain', 'needs_attention'):
                    bindings = previous.get('runs', [])
                    released = bool(bindings) and all(jobs.get(run.get('id'), {}).get('state') == 'released'
                                                      for run in bindings)
                    if previous.get('state') in ('queued', 'running') or not released:
                        continue
                bounded = []
                for event in reports:
                    entry = {k: event.get(k) for k in ('name', 'path', 'target', 'state')}
                    entry.update(reason=str(event.get('reason', ''))[:500], checkpoint=str(event.get('checkpoint', ''))[:200])
                    # Supply evidence here so the summarizer need not access another
                    # agent's checkout. Tests remain worker-reported, not gateway-run.
                    entry.update(validation_run=event.get('validation_run'), tests=event.get('tests', {}), verification=event.get('verification', {}), operator_approval=event.get('operator_approval'),
                                 blocker=event.get('blocker') or '',
                                 blocker_label=blocker_label(event.get('blocker')) if event.get('blocker') else '')
                    recovery = event.get('recovery') or {}
                    entry['recovery'] = {k: recovery.get(k) for k in ('ref', 'commit')}
                    if len(json.dumps([*bounded, entry])) > 6000:
                        break
                    bounded.append(entry)
                payload = json.dumps(bounded)
                digest = identity(report_digest(reports, bounded), config.get('report_epoch', 0))
                if config.get('report_digest') == digest:
                    continue
                report = dict(id=digest, profile_id=config['profile_id'], run_id='', attempt=0)
                prompt = ('Integration coordinator report. Summarize these actual worker responses and recommend next steps. '
                          'This is a summary-only task: use only the supplied payload. Do not inspect repositories or other worktrees, run shell commands, or use tools to revalidate evidence. '
                          'Distinguish gateway Git verification from worker-reported tests. Missing evidence is unknown; report it without investigating or requesting directory access. '
                          'Do not prompt workers directly or modify their files; the durable inbox handles delivery. '
                          f'Respect deferrals; identify checkpoints and blockers. Do not claim tests passed without evidence. Showing {len(bounded)} of {len(reports)} worker events; pending entries have no decision yet.\n' + payload)
                try:
                    job_id = self.submit(report, 'report', prompt, config['profile_id'])
                except ValueError:
                    job_id = None
                if job_id:
                    config.update(report_digest=digest, report_job_id=job_id, report_pending=False)
                    db.execute('UPDATE settings SET data=? WHERE repository=?', (json.dumps(config), repository))

            return advance_pending

    def configuration_records(self):
        """Small binding read for organization polls; no jobs, events or audit."""
        with closing(self.connect()) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM settings')]

    def schedule_builds(self, validation):
        """Opt-in service delivery outside the coordinator's transaction/lock.

        Worker claims alone do not authorize a build. Require gateway Git
        incorporation evidence; queue deduplication links multiple worker events.
        Failed attempts remain attached until an operator explicitly retries.
        """
        with closing(self.connect()) as db:
            settings = {row[0]: json.loads(row[1]) for row in db.execute('SELECT * FROM settings')}
            events = [json.loads(row[0]) for row in db.execute('SELECT data FROM events ORDER BY rowid')]
        for event in events:
            config = settings.get(event['repository'], {})
            verified = event.get('verification') or {}
            if (not config.get('enabled') or not config.get('auto_build')
                    or event.get('state') not in ('completed', 'validation_pending', 'validation_failed', 'validation_waived')
                    or verified.get('target_incorporated') is not True
                    or verified.get('conflicts') != 0 or verified.get('merging') is not False
                    or event.get('validation_run') or event.get('automatic_build_id')):
                continue
            try:
                if validation.queue is None:
                    raise ValueError('Automatic builds require the installed build service.')
                run = validation.submit({'id': event['id']}, actor='automatic_build')
                result = dict(automatic_build_id=run['id'], automatic_build_error='')
            except (ValueError, OSError) as error:
                result = dict(automatic_build_error=str(error)[:500])
            with self.lock, closing(self.connect()) as db, db:
                row = db.execute('SELECT data FROM events WHERE id=?', (event['id'],)).fetchone()
                if not row:
                    continue
                current = json.loads(row[0])
                if all(current.get(key) == value for key, value in result.items()):
                    continue
                current.update(result)
                self.save(db, current)
                self.audit(db, dict(action='automatic_build', event_id=event['id'],
                                    repository=event['repository'], **result))

    def snapshot(self):
        # A reader must not wait for dispatch/network work holding the writer lock.
        jobs = self.store.job_records()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN')
            configurations = [json.loads(r[0]) for r in db.execute('SELECT data FROM settings')]
            for config in configurations:
                runs = [j for j in jobs if j['kind'] == 'launch' and j.get('profile_id') == config.get('profile_id')]
                run = max(runs, key=lambda j: j.get('created_at', ''), default={})
                config.update(coordinator_state=run.get('state', 'not_launched'),
                              coordinator_error=run.get('error', ''), coordinator_run_id=run.get('id'),
                              coordinator_path=run.get('worktree_path'))
                report = next((job for job in jobs if job['id'] == config.get('report_job_id')), {})
                config.update(report_state=report.get('state', 'waiting' if config.get('report_pending') else None), report_error=report.get('error', ''))
            # One snapshot can be audited by more than one ready-state save;
            # list each reference once, newest first.
            recoveries, seen = [], set()
            for row in db.execute("SELECT data FROM audit WHERE json_extract(data, '$.recovery') IS NOT NULL AND json_extract(data, '$.state')='ready' ORDER BY sequence DESC"):
                entry = json.loads(row[0])
                reference = (entry.get('recovery') or {}).get('ref')
                # Git refs are scoped to a repository, not globally unique.
                identity = (entry.get('repository'), reference)
                if identity in seen:
                    continue
                seen.add(identity)
                recoveries.append(entry)
            return dict(configurations=configurations,
                        recoveries=recoveries,
                        audit=[dict(json.loads(r[1]), sequence=r[0]) for r in db.execute('SELECT sequence,data FROM audit ORDER BY sequence DESC LIMIT 200')],
                        events=[json.loads(r[0]) for r in db.execute('SELECT data FROM events ORDER BY rowid DESC LIMIT 100')])
