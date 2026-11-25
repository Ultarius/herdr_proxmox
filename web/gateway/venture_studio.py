"""Venture Studio operations: what makes the studio self-starting.

Everything here answers "can the studio run, and if not, what is the one thing
to do about it": the readiness checklist, the preflight before a run, where the
evidence file goes, named failure categories, the guided repair, the one-call
setup and the autonomous-creation policy. ``ventures.Ventures`` mixes this in;
the simulation itself stays in ``ventures``.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import time
import uuid

from discovery import redact
import usage_limits
from project_files import project_directory
from repository_lock import repository_lock
from venture_panel import panel_backs, panel_current

# Four role agents and the group facilitator.
STUDIO_PARTICIPANTS = 5
# How long a scout may spend closing its old sessions before its meeting. A
# scheduled scout runs on the shared poller; sessions left when time runs out
# are reused for that round.
SCOUT_RESET_SECONDS = 30
RUNTIME_ORDER = ('claude', 'codex', 'opencode', 'agy')
RUNTIME_LABELS = dict(claude='Claude Code', codex='Codex', opencode='OpenCode', agy='Antigravity')
STUDIO_MODES = dict(proposal='Proposal: the studio suggests, you approve',
                    autonomous='Autonomous: the studio creates ventures within the caps')
# Autonomous mode creates at most this many ventures from one scout run.
AUTO_CREATE_PER_RUN = 1
AUTO_ACTOR = 'venture_studio_auto'
# A launch binding in one of these states never becomes a live session by itself.
FAILED_BINDINGS = ('needs_attention', 'uncertain', 'error')
# Every other unreleased launch state is one meeting preparation refuses to
# reuse ("inspect the existing session"), so repair treats it as failed too.
LIVE_BINDINGS = ('not_started', 'queued', 'running', 'persona_sent', 'waiting_for_input', 'waiting_environment')
HERDR_CACHE_SECONDS = 5
RUNTIME_CACHE_SECONDS = 60
# Recheck a deferred or in-progress meeting even before the estimated reset.
USAGE_PROBE_SECONDS = 60
USAGE_RESULT_PROBE_SECONDS = 10
USAGE_STALE = timedelta(hours=6)

# One primary recovery action per kind of failure. ``action`` is what the
# dashboard offers: start_herdr, repair, open_group, cli_accounts, limits,
# configure or dismiss.
FAILURES = dict(
    herdr=dict(title='Herdr is stopped', action='start_herdr',
               help='No agent can start while Herdr is stopped. Start it, then repair the studio to retry.'),
    permission=dict(title='An agent is waiting for a decision', action='open_group',
                    help='Open the studio group discussion and answer the prompt; the meeting continues by itself.'),
    provider=dict(title='The CLI account is not ready', action='cli_accounts',
                  help='Connect or repair the CLI account on the CLI accounts page, then repair the studio.'),
    model=dict(title='The model is not available', action='cli_accounts',
               help='Choose a model the connected account offers, then repair the studio.'),
    busy=dict(title='A studio agent was still busy', action='repair',
              help='Nothing was sent to the group yet. Repair prepares the meeting again once the agent is idle.'),
    team=dict(title='A studio agent did not start', action='repair',
              help='Repair releases the failed session and starts the team again.'),
    usage=dict(title='An agent hit its usage limit', action='',
               help='The studio rechecks the session automatically. A meeting can start when its members are available.'),
    limits=dict(title='An organization limit blocks the meeting', action='limits',
                help='Raise the session limits, or wait for other work to finish.'),
    inputs=dict(title='The scout has nothing to read', action='configure',
                help='Add scouting sources and a focus area in the studio settings.'),
    evidence=dict(title='The evidence file could not be prepared', action='repair',
                  help='Repair prepares the facilitator checkout and writes the file again.'),
    result=dict(title='The result did not validate', action='dismiss',
                help='The agents answered, but not in the required shape. Dismiss the run and start a new one.'),
    meeting=dict(title='The meeting stopped', action='open_group',
                 help='Open the studio group discussion to see where it stopped, then repair the studio.'),
)
# First match wins, so the specific causes come before the general ones.
_RULES = (
    ('herdr', ('server_not_running', 'herdr is stopped', 'start herdr', 'herdr.sock', 'no herdr server')),
    # Busy is not "did not start": the agent runs, it was answering something.
    ('busy', ('agent is still working', 'agent_working', 'not ready for input', 'agent_not_ready',
              'finish its current reply')),
    ('permission', ('permission', 'waiting for a decision', 'waiting_for_input', 'approval', 'agent_blocked')),
    ('model', ('provider/model', 'model is unavailable', 'unsupported model', 'model not found', 'unknown model',
               'reasoning variant')),
    # Before provider and limits: a plan's usage limit is neither a broken
    # account nor an organization limit, and it clears by itself.
    ('usage', ('usage limit', 'rate limit', 'quota')),
    ('provider', ('not signed in', 'sign in', 'herdr integration', 'not installed', 'cli account', 'api key',
                  'unauthorized', 'authentication')),
    ('limits', ('budget', 'session cap', 'meeting limit', 'limit reached', 'daily session', 'backlog')),
    ('inputs', ('scouting source', 'focus area')),
    ('result', ('json', 'cites evidence', 'recommendation', 'candidate', 'malformed', 'does not validate', 'ranks')),
    ('evidence', ('evidence',)),
    ('team', ('session', 'facilitator', 'launch', 'preparation', 'participant', 'member', 'worktree', 'pane',
              'not ready')),
)


def now():
    return datetime.now(timezone.utc)


# A Herdr error code says what happened; its text is only for people.
_CODE_CATEGORIES = dict(server_not_running='herdr', agent_working='busy', agent_not_ready='busy',
                        agent_blocked='permission', agent_not_found='team', agent_not_running='team',
                        pane_not_found='team', pane_not_running='team')


def classify(message, code=None):
    """The failure category of an error: its Herdr code when it has one, else
    its text; ``meeting`` when nothing matches."""
    if code in _CODE_CATEGORIES:
        return _CODE_CATEGORIES[code]
    folded = str(message or '').lower()
    for category, fragments in _RULES:
        if any(fragment in folded for fragment in fragments):
            return category
    return 'meeting'


class StudioWaiting(ValueError):
    """Something outside the run is not ready (Herdr stopped, no sources, a
    limit). Nothing was started: an operator sees the reason, and the scheduler
    waits for the next pass without counting a failure."""

    def __init__(self, message, category):
        super().__init__(message)
        self.category = category


class StudioOperations:
    """Mixed into ``ventures.Ventures``; uses its storage and policy helpers."""

    # --- The environment -------------------------------------------------

    def _herdr_state(self, fresh=False):
        """``running``, ``stopped`` or ``unknown``. Only ``stopped`` blocks a run."""
        cached = getattr(self, '_herdr_cache', None)
        if cached and not fresh and time.monotonic() - cached[0] < HERDR_CACHE_SECONDS:
            return cached[1]
        try:
            state = self.herdr.status().get('state')
        except Exception:  # A status probe must never break the page or the scheduler.
            state = 'unknown'
        state = state if state in ('running', 'stopped', 'starting') else 'unknown'
        self._herdr_cache = (time.monotonic(), state)
        return state

    def _require_herdr(self, purpose):
        """Creating a group starts its facilitator. With Herdr stopped that
        launch fails and leaves a stuck session, so refuse first."""
        if self._herdr_state(fresh=True) == 'stopped':
            raise StudioWaiting('Herdr is stopped. Start Herdr to ' + purpose + '.', 'herdr')

    def herdr_start(self):
        """Start Herdr through the gateway's allowlisted service start."""
        result = self.herdr.start({})
        self._herdr_cache = None
        return dict(state=result.get('state', 'unknown'), already_running=result.get('already_running') is True)

    def _account_catalog(self, project):
        """The platform's account catalog for ``project``: which CLI accounts
        are usable and which models each offers. The same catalog the team
        presets use. ``None`` when it is unavailable. Building it runs the
        CLIs, so one setup reuses it for a short while."""
        catalog = getattr(self.service.store, 'template_catalog', None)
        if not callable(catalog) or not project:
            return None
        cached = getattr(self, '_catalog_cache', None)
        if cached and cached[0] == project and time.monotonic() - cached[1] < 30:
            return cached[2]
        try:
            available = catalog(project)
        except (ValueError, OSError):
            return None
        available = available if isinstance(available, dict) else None
        self._catalog_cache = (project, time.monotonic(), available)
        return available

    def _account_problems(self, project):
        """Why each CLI account cannot be used, from the platform's account
        catalog: not signed in, or (OpenCode) no model returned for the
        configured providers. Empty when the catalog is unavailable."""
        available = self._account_catalog(project)
        accounts = available.get('accounts') if available else None
        return {item['runtime']: str(item.get('reason') or 'The account is not available.')
                for item in accounts or [] if isinstance(item, dict) and item.get('runtime')
                and item.get('available') is not True}

    def _runtimes(self, deep=False, project=''):
        """Which agent CLIs can run agents now. ``deep`` also asks each CLI
        whether it is signed in and, with a ``project``, whether its account
        offers a model; that starts processes, so only setup asks."""
        cached = getattr(self, '_runtime_cache', None)
        if cached and not deep and time.monotonic() - cached[0] < RUNTIME_CACHE_SECONDS:
            return cached[1]
        store = self.service.store
        ready, status = getattr(store, 'runtime_ready', None), getattr(store, 'runtime_status', None)
        accounts = self._account_problems(project) if deep else {}
        result = []
        for runtime in RUNTIME_ORDER:
            reason = ''
            try:
                problem = ready(runtime) if callable(ready) else None
                if isinstance(problem, str) and problem:
                    reason = problem
                elif deep and runtime in ('codex', 'claude') and callable(status):
                    state = status(runtime)
                    if isinstance(state, dict) and state.get('status') == 'not_configured':
                        reason = RUNTIME_LABELS[runtime] + ' is not signed in. Sign in on the CLI accounts page.'
                if not reason and runtime in accounts:
                    reason = accounts[runtime]
            except (ValueError, OSError) as error:
                reason = str(error) or 'The CLI status could not be read.'
            result.append(dict(runtime=runtime, name=RUNTIME_LABELS[runtime], ready=not reason,
                               reason=redact(reason)[:300]))
        if not deep:
            self._runtime_cache = (time.monotonic(), result)
        return result

    # --- Agent settings without a template agent ---------------------------

    @staticmethod
    def _copied_settings(profile):
        return dict(runtime=profile['runtime'], provider=profile.get('provider', ''),
                    model=profile.get('model', ''), reasoning=profile.get('reasoning', ''),
                    permission_mode=profile.get('permission_mode') or 'default',
                    accessible_paths=profile.get('accessible_paths') or [])

    def _explicit_settings(self, value, project):
        """Operator-chosen CLI, provider and model, checked before anything is hired."""
        if not isinstance(value, dict):
            raise ValueError('Agent settings must name a CLI and, optionally, a provider and model.')
        runtime = value.get('runtime')
        if runtime not in RUNTIME_ORDER:
            raise ValueError('Choose a supported agent CLI.')
        from organizations import model_settings
        from permissions import permission_mode
        # An empty or null mode is the CLI default, as it was before modes were validated.
        mode = permission_mode(dict(permission_mode=value.get('permission_mode') or 'default'), runtime)
        settings = model_settings({key: value.get(key) or '' for key in ('provider', 'model', 'reasoning')}, runtime)
        entry = next(item for item in self._runtimes(deep=True, project=project) if item['runtime'] == runtime)
        if not entry['ready']:
            raise ValueError(entry['reason'])
        validator = getattr(self.service.store, 'model_validator', None)
        if callable(validator):
            validator(dict(settings, runtime=runtime, project=project))
        return dict(runtime=runtime, **settings, permission_mode=mode, accessible_paths=[])

    def _automatic_settings(self, project=''):
        """A usable CLI account and a concrete model on it, detected.

        Uses the routine the team presets use: among the models the ready
        accounts actually offer, pick one that meets the capability floor of
        an independent reviewer (the studio's work is analysis and critique),
        free or cheaper first. The pick is then checked like a hand-made
        choice. When the catalog is unavailable or offers no suitable model,
        fall back to the first ready CLI on its own default model.
        Antigravity is last either way: its sign-in cannot be verified."""
        runtimes = self._runtimes(deep=True, project=project)
        ready = [item['runtime'] for item in runtimes if item['ready']]
        if not ready:
            reasons = '; '.join(item['name'] + ': ' + item['reason'] for item in runtimes if item['reason'])
            raise ValueError('No agent CLI is ready, so the agents cannot be created. Connect one on the CLI '
                             'accounts page first.' + (' ' + reasons if reasons else ''))
        available = self._account_catalog(project)
        offered = {runtime: entry for runtime, entry in ((available or {}).get('runtimes') or {}).items()
                   if runtime in ready and isinstance(entry, dict) and entry.get('models')}
        if offered:
            try:
                import team_templates
                choice = team_templates.selection(team_templates.BY_ID['reviewer'], 'balanced',
                                                  dict(runtimes=offered), tuple(ready))
                settings = dict(runtime=choice['runtime'], provider=choice.get('provider', ''),
                                model=choice.get('model', ''), reasoning=choice.get('reasoning', ''))
                validator = getattr(self.service.store, 'model_validator', None)
                if callable(validator):
                    validator(dict(settings, project=project))
                return dict(settings, permission_mode='default', accessible_paths=[],
                            detected=redact(str(choice.get('selection_note') or ''))[:300])
            except (ValueError, KeyError):
                pass  # No suitable or valid model: the CLI's own default is still a working choice.
        return dict(runtime=ready[0], provider='', model='', reasoning='',
                    permission_mode='default', accessible_paths=[])

    def _agent_settings(self, organization, roles, body, hired=(), fallback=None, project=''):
        """Settings for new role agents: ``(settings_by_role, template_ids, source)``.

        A role uses, in order: the individual agent named for it in
        ``templates``; the operator's explicit ``settings``; the first
        individual worktree agent of the organization; ``fallback`` (the
        studio's own settings); and finally the first ready CLI on its default
        model. No existing agent is needed.
        """
        requested = body.get('templates') or {}
        if not isinstance(requested, dict):
            raise ValueError('Templates must map each role to an individual agent.')
        if any(role not in roles for role in requested):
            raise ValueError('Templates can only name these roles: ' + ', '.join(roles) + '.')
        snapshot = self.service.store.snapshot(live_status=False)
        profiles = {profile['id']: profile for profile in snapshot.get('profiles', [])
                    if profile.get('organization_id') == organization and profile['id'] not in set(hired)
                    and not profile.get('group_id') and not profile.get('removed_at')
                    and not profile.get('archived') and profile.get('use_worktree', True)}
        chosen, templates = {}, {}
        for role in roles:
            identity = requested.get(role)
            if identity in (None, '', 'auto'):
                continue
            if not isinstance(identity, str) or identity not in profiles:
                raise ValueError('Selected template is not an individual worktree agent in this organization.')
            chosen[role] = self._copied_settings(profiles[identity])
            templates[role] = identity
        missing = [role for role in roles if role not in chosen]
        source = 'templates'
        if missing:
            if body.get('settings') is not None:
                base, source = self._explicit_settings(body['settings'], project), 'settings'
            elif profiles:
                default = sorted(profiles.values(), key=lambda profile: (str(profile.get('name', '')), profile['id']))[0]
                base, source = self._copied_settings(default), 'agent'
                templates.update({role: default['id'] for role in missing})
            elif fallback:
                base, source = dict(fallback), 'studio'
            else:
                base = self._automatic_settings(project)
                # 'detected': a concrete model was picked from the account's own
                # list; 'automatic': the CLI runs on its default model.
                source = 'detected' if base.pop('detected', None) is not None else 'automatic'
            for role in missing:
                chosen[role] = dict(base)
        return chosen, templates, source

    # --- The team and its sessions -----------------------------------------

    def _studio_bindings(self, organization, studio=None):
        """Each studio agent with the state of its session binding."""
        studio = studio if studio is not None else self._studio(organization)
        return self._group_bindings(studio.get('group_id'))

    def _group_bindings(self, group_id):
        """Each member and the facilitator of one group, with its session binding."""
        if not group_id:
            return []
        snapshot = self.service.store.snapshot(live_status=False)
        group = next((item for item in snapshot.get('groups', [])
                      if item.get('id') == group_id and not item.get('removed_at')), None)
        if not group:
            return []
        profiles = {profile['id']: profile for profile in snapshot.get('profiles', [])}
        jobs = snapshot.get('jobs', [])
        result = []
        for identity in list(group.get('members') or []) + [group.get('facilitator_id')]:
            person = profiles.get(identity)
            if not person:
                continue
            run = next((job for job in reversed(jobs) if job.get('kind') == 'launch'
                        and job.get('profile_id') == identity
                        and job.get('state') not in ('released', 'finished')), None) or {}
            result.append(dict(profile_id=identity, name=person.get('name', ''),
                               facilitator=identity == group.get('facilitator_id'),
                               state=run.get('state') or 'not_started', job_id=run.get('id', ''),
                               alias=run.get('alias') or '', pane_id=run.get('pane_id') or '',
                               error=redact(str(run.get('error') or ''))[:300],
                               failed=(run.get('state') or 'not_started') not in LIVE_BINDINGS))
        return result

    def _fresh_scout_sessions(self, organization, run, studio):
        """Release the idle sessions a scout meeting would reuse; return their names.

        A meeting reuses any live session of a member, so every scout used to
        meet the same chair, studio agents and stakeholder advisors, each
        still holding every earlier round. They answered from that memory:
        two runs in a row returned the previous answer, with the same three
        pages and no new research. Released, each one starts again with only
        its persona when the meeting prepares it.

        Every step leans towards keeping a session; a session kept only means
        this round reuses it:
        - one Herdr agent listing, bounded in time, shows what is live; without
          it nothing is closed;
        - only a session whose agent is idle, on the pane its run records, and
          that no job still owns (a delivered delegation included) is a
          candidate;
        - when the organization is paused or its daily start budget cannot
          take a new session for every candidate, none is closed, so the
          meeting is never left unable to start its members;
        - each one is closed through the gateway's own session close, which
          takes the agent's lock, re-checks ownership and the agent's identity
          before closing its pane, and archives its transcript first;
        - the whole reset stops at SCOUT_RESET_SECONDS, because a scheduled
          scout runs on the shared poller."""
        store = self.service.store
        deadline = time.monotonic() + SCOUT_RESET_SECONDS
        bindings = self._studio_bindings(organization, studio)
        advisors = set(((run.get('consult') or {}).get('members') or {}).values())
        if advisors:
            bindings += [binding for binding in self._panel_bindings(organization, studio)
                         if binding['profile_id'] in advisors]
        wanted = [binding for binding in bindings if binding.get('state') == 'persona_sent'
                  and binding.get('job_id') and binding.get('alias')]
        if not wanted or not hasattr(store, 'manage_session'):
            return []
        response = store.command('agent', 'list', timeout=5)
        live = response if isinstance(response, list) else (
            response.get('agents') if isinstance(response, dict) else None)
        if not isinstance(live, list):
            return []
        jobs = store.snapshot(live_status=False).get('jobs', [])
        idle = []
        for binding in wanted:
            agents = [item for item in live if isinstance(item, dict) and item.get('name') == binding['alias']]
            if len(agents) != 1:
                continue
            status = agents[0].get('agent_status', agents[0].get('state'))
            if status not in ('idle', 'done'):
                continue  # Working, blocked or unknown: it may still be doing something.
            if binding.get('pane_id') and agents[0].get('pane_id') not in (None, binding['pane_id']):
                continue  # The alias answers from another pane than its run records.
            if self.service.session_owner(binding['job_id'], jobs):
                continue
            idle.append(binding)
        if not idle:
            return []
        if hasattr(self.service, 'session_budget'):
            budget = self.service.session_budget(organization, jobs)
            policy = budget.get('policy') or {}
            daily = policy.get('daily_session_cap') or 0
            if policy.get('paused') or (daily and budget.get('started', 0) + len(idle) > daily):
                logging.info('Scout %s reuses its sessions: no room to start %d new ones today.',
                             run['id'], len(idle))
                return []
        released = []
        for binding in idle:
            if time.monotonic() > deadline:
                break  # The rest are reused this round.
            try:
                store.manage_session(dict(organization_id=organization, job_id=binding['job_id'], mode='close',
                                          inspected=True, request_id='scout-fresh-' + run['id'][:20] + '-'
                                          + binding['job_id'][:20]), AUTO_ACTOR)
            except (ValueError, OSError) as error:
                logging.warning('Scout %s kept the session of %s: %s', run['id'], binding.get('name'),
                                redact(str(error)))
                continue
            released.append(binding.get('name') or binding['profile_id'])
        return released

    def _limit_problems(self, organization, people):
        limits = self._policy(organization)
        problems = []
        for key, label in (('max_active_sessions', 'concurrent sessions'), ('daily_session_cap', 'sessions per day')):
            try:
                value = int(limits.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value and value < people:
                problems.append('the limit of ' + str(value) + ' ' + label + ' is below the ' + str(people)
                                + ' a studio meeting needs')
        return problems

    def _studio_readiness(self, organization, policy=None, studio=None):
        """The setup checklist: what is ready, what is missing and what fixes it."""
        policy = policy or self._studio_policy(organization)
        studio = studio if studio is not None else self._studio(organization)
        checks = []

        def add(identity, label, ok, detail, action, optional=False):
            # Optional: not met, but the studio works without it (shown as a
            # suggestion, not as something that needs the operator).
            checks.append(dict(id=identity, label=label, ok=bool(ok), detail=detail, action='' if ok else action,
                               optional=bool(optional and not ok)))

        herdr = self._herdr_state()
        add('herdr', 'Herdr is running', herdr != 'stopped',
            'Stopped: no agent can start.' if herdr == 'stopped' else
            'Running.' if herdr == 'running' else 'Its status could not be read.', 'start_herdr')
        configured = bool(studio.get('group_id'))
        runtimes = self._runtimes()
        if configured:
            used = sorted({settings.get('runtime') for settings in
                           ((studio.get('team') or {}).get('settings_by_role') or {}).values()
                           if isinstance(settings, dict) and settings.get('runtime')})
            broken = [item for item in runtimes if item['runtime'] in used and not item['ready']]
            add('provider', 'The agent CLI is ready', not broken,
                '; '.join(item['name'] + ': ' + item['reason'] for item in broken) if broken else
                (', '.join(RUNTIME_LABELS.get(item, item) for item in used) or 'CLI') + ' ready.', 'cli_accounts')
        else:
            usable = [item['name'] for item in runtimes if item['ready']]
            add('provider', 'An agent CLI is ready', bool(usable),
                ', '.join(usable) + ' ready.' if usable else 'No agent CLI is ready: '
                + '; '.join(item['name'] + ': ' + item['reason'] for item in runtimes if item['reason']),
                'cli_accounts')
        problem = ''
        bindings = []
        if configured:
            try:
                self._studio_group(organization, studio)
            except ValueError as error:
                problem = str(error)
            bindings = self._studio_bindings(organization, studio)
        add('team', 'The studio team exists', configured and not problem,
            problem or ('Notebook, four agents and one read-only group.' if configured else
                        'Not set up yet: one click creates the notebook, the agents and the group.'),
            'repair' if configured else 'setup')
        failed = [binding for binding in bindings if binding['failed']]
        consult = (self._scout_consult(organization, studio) or {}) if configured else {}
        advisor_ids = set(consult.get('members', {}).values())
        scout_blocking = []
        if configured:
            add('sessions', 'No agent session is stuck', not failed,
                '; '.join(binding['name'] + ' did not start' + (': ' + binding['error'] if binding['error'] else '')
                          for binding in failed) if failed else
                'Agents start by themselves when a meeting needs them.', 'repair')
            stuck = [binding for binding in self._panel_bindings(organization, studio) if binding['failed']]
            if stuck:
                add('panel_sessions', 'No stakeholder session is stuck', False,
                    '; '.join(binding['name'] + ' did not start' + (': ' + binding['error'] if binding['error'] else '')
                              for binding in stuck), 'repair')
                if any(binding['profile_id'] in advisor_ids for binding in stuck):
                    scout_blocking.append('panel_sessions')
        people = len(bindings) or STUDIO_PARTICIPANTS
        advisors = len(advisor_ids)
        limits = self._limit_problems(organization, people)
        add('limits', 'The organization limits allow a studio meeting', not limits,
            ('; '.join(limits).capitalize() + '.') if limits else
            'A studio meeting uses ' + str(people) + ' sessions.', 'limits')
        if advisors:
            scout_limits = self._limit_problems(organization, people + advisors)
            add('scout_limits', 'The organization limits allow a scout with advisors', not scout_limits,
                ('; '.join(scout_limits).capitalize() + '.') if scout_limits else
                'A scout with the stakeholders uses ' + str(people + advisors) + ' sessions.', 'limits')
            if scout_limits:
                scout_blocking.append('scout_limits')
        sources, focus = policy['studio_sources'], policy['studio_focus']
        health = self._source_health(organization, sources)
        broken = [item for item in health if item['error']]
        web = policy.get('studio_web_research') is True
        usable = bool(sources) and len(broken) < len(sources)
        detail = (str(len(sources)) + ' source(s).' if sources else 'None.')
        if broken:
            detail += ' Not a readable feed: ' + '; '.join(
                item['url'] + ' (' + item['error'] + ')' for item in broken[:3]) + '.'
        if not usable:
            detail += (' The scout researches the web instead and suggests feeds you can add.' if web else
                       ' The scout would have no market evidence to cite.')
        add('sources', 'Scouting sources', usable, detail, 'configure', optional=web)
        add('focus', 'Focus areas', bool(focus),
            ', '.join(focus) if focus else 'None: the scout would not know where to look.', 'configure')
        failing = {check['id'] for check in checks if not check['ok']}
        blocking = [identity for identity in ('herdr', 'provider', 'team', 'sessions', 'limits') if identity in failing]
        return dict(herdr=herdr, checks=checks, blocking=blocking, can_run=not blocking,
                    scout_blocking=scout_blocking,
                    can_scout=not blocking and not scout_blocking and 'focus' not in failing and (usable or web),
                    # "Run scout without sources" skips sources, never the focus.
                    can_scout_override=not blocking and not scout_blocking and bool(focus),
                    coverage=dict(sources=len(sources), focus=len(focus), readable=len(sources) - len(broken),
                                  web_research=web and not usable),
                    sources=health,
                    team=bindings, participants=people)

    def _source_health(self, organization, sources):
        """Each scouting source with what its last fetch returned."""
        result = []
        for url in sources:
            state = self._feed_state(organization, url)
            result.append(dict(url=url, last_fetch=state.get('last_fetch'),
                               error=redact(' '.join(str(state.get('last_error') or '').split()))[:200],
                               item_count=state.get('item_count', 0)))
        return result

    def studio_source_add(self, organization, body, actor):
        """Add one scouting source, for example one the scout suggested.

        The URL is fetched first: only a feed that parses and has items is
        added, so a page that is not a feed cannot become a source again."""
        import ventures as core
        import venture_feeds
        url = venture_feeds.validate_url(body.get('url'), resolve=None)
        policy = self._studio_policy(organization)
        sources = list(policy['studio_sources'])
        if url in sources:
            return dict(organization_id=organization, url=url, added=False, sources=sources)
        if len(sources) >= core.MAX_STUDIO_SOURCES:
            raise ValueError('The studio already has ' + str(core.MAX_STUDIO_SOURCES)
                             + ' scouting sources; remove one first.')
        try:
            result = venture_feeds.fetch(url)
        except (ValueError, OSError) as error:
            raise ValueError('This URL is not a readable RSS or Atom feed: ' + redact(str(error))[:200]) from error
        if result.get('status') != 304 and not result.get('items'):
            raise ValueError('This feed has no items, so it would give the scout nothing to cite.')
        self.service.patch_policy(organization, dict(studio_sources=sources + [url]), actor)
        return dict(organization_id=organization, url=url, added=True, sources=sources + [url],
                    items=len(result.get('items') or []))

    def _studio_preflight(self, organization, kind, manual, override, policy, studio):
        """Refuse a run that cannot work, before anything is recorded or started."""
        if self._herdr_state(fresh=manual) == 'stopped':
            raise StudioWaiting('Herdr is stopped. Start Herdr to run the ' + kind + ' review.', 'herdr')
        bindings = self._studio_bindings(organization, studio)
        failed = [binding['name'] for binding in bindings if binding['failed']]
        if failed:
            raise StudioWaiting(', '.join(failed) + ' did not start. Repair the studio team first.', 'team')
        people = len(bindings) or STUDIO_PARTICIPANTS
        if kind == 'scout':
            # The scout talks to the stakeholders, so their sessions must start too.
            advisors = self._scout_advisor_bindings(organization, studio)
            stuck = [binding['name'] for binding in advisors if binding['failed']]
            if stuck:
                raise StudioWaiting(', '.join(stuck) + ' did not start. Repair the studio first: the scout talks '
                                    'to the stakeholders.', 'team')
            people += len((self._scout_consult(organization, studio) or {}).get('roles') or [])
        limits = self._limit_problems(organization, people)
        if limits:
            raise StudioWaiting('A studio meeting cannot start: ' + '; '.join(limits) + '.', 'limits')
        if kind == 'scout' and not override:
            # With web research on, missing or unreadable sources are not a
            # reason to wait: the scout searches and suggests feeds instead.
            missing = [label for label, value in (
                ('a scouting source', policy['studio_sources'] or policy.get('studio_web_research')),
                ('a focus area', policy['studio_focus'])) if not value]
            if missing:
                raise StudioWaiting('The scout has no market evidence to work from: add ' + ' and '.join(missing)
                                    + ' in the studio settings'
                                    + ('' if not manual else ', or run it once without them') + '.', 'inputs')
        # Read only the sessions required by this meeting. Manual runs can
        # check immediately, and the shared poll thread waits for the worker.
        targets = self._usage_targets(organization, kind, studio)
        if manual:
            self._usage_probe(targets)
            checked = True
        else:
            checked = self._usage_probe_soon(targets)
        hold = self._usage_hold(targets)
        if hold:
            raise StudioWaiting(self._usage_message(hold), 'usage')
        if not checked:
            raise StudioWaiting('Checking the studio sessions for provider usage limits.', 'usage')

    # --- Provider usage limits ---------------------------------------------

    def _usage_hold(self, targets, profile_id=None):
        """The active limit with the latest deadline among these sessions."""
        now = datetime.now(timezone.utc)
        with self._usage_lock:
            holds = [(seen, target) for key, target in targets.items()
                     if (profile_id is None or target['profile_id'] == profile_id)
                     and (seen := self._usage_observations.get(key, (0, None))[1]) and seen['until'] > now]
        if not holds:
            return None
        seen, target = max(holds, key=lambda item: item[0]['until'])
        return dict(name=target['name'], profile_id=target['profile_id'], message=seen['message'],
                    until=seen['until'].isoformat(),
                    reset_at=seen['reset_at'].isoformat() if seen['reset_at'] else None,
                    retry_at=(seen['retry_at'].isoformat() if seen['retry_at'] and seen['retry_at'] > now else None))

    @staticmethod
    def _usage_message(hold):
        detail = [] if hold.get('reset_at') else ['Provider reset time unavailable.']
        for key, label in (('reset_at', 'Estimated provider reset'), ('retry_at', 'Next CLI retry')):
            if hold.get(key):
                detail.append(label + ' at ' + datetime.fromisoformat(hold[key]).strftime('%Y-%m-%d %H:%M UTC') + '.')
        return ' '.join([hold['name'] + ' hit its usage limit.', *detail,
                         'Checking the session again automatically.'])

    def _usage_targets(self, organization, kind, studio=None, *, job=None, snapshot=None):
        """Live launch sessions for the prospective or frozen meeting roster."""
        if snapshot is None:
            snapshot = self.service.store.snapshot(live_status=False)
        roster = (job or {}).get('group')
        if roster:
            facilitator = ((job or {}).get('group_run') or {}).get('profile_id') or roster.get('facilitator_id')
            participants = {*roster.get('members', []), facilitator}
        else:
            studio = studio if studio is not None else self._studio(organization)
            panel = studio.get('panel') or {}
            if kind == 'pitch' and not panel.get('enabled'):
                return {}
            group_id = panel.get('group_id') if kind == 'pitch' else studio.get('group_id')
            group = next((item for item in snapshot.get('groups', [])
                          if item['id'] == group_id and not item.get('removed_at')), {})
            participants = {*group.get('members', []), group.get('facilitator_id')}
            if kind == 'scout':
                participants.update((self._scout_consult(organization, studio) or {}).get('members', {}).values())
        participants.discard(None)
        pinned = {}
        if job and job.get('discussion_submitted_at'):
            pinned = {item['profile_id']: item for item in job.get('runs') or []}
            if job.get('group_run'):
                pinned[job['group_run']['profile_id']] = job['group_run']
        names = {profile['id']: profile['name'] for profile in snapshot.get('profiles', []) if 'name' in profile}
        latest = {item['profile_id']: item for item in snapshot.get('jobs', [])
                  if item.get('kind') == 'launch' and item.get('organization_id') == organization
                  and item.get('profile_id') in participants and item['state'] not in ('released', 'finished')}
        targets = {}
        for identity, session in latest.items():
            fixed = pinned.get(identity)
            if (session['state'] == 'persona_sent' and session.get('alias')
                    and (not fixed or (fixed['id'] == session['id'] and fixed.get('pane_id') == session.get('pane_id')))):
                key = (session['id'], session.get('pane_id'), (session.get('session_starts') or [None])[-1])
                targets[key] = dict(alias=session['alias'], profile_id=identity, name=names.get(identity, 'An agent'))
        return targets

    def _usage_probe(self, targets):
        for key, target in targets.items():
            started = time.monotonic()
            output = None
            try:
                result = self.service.store.command(
                    'agent', 'read', target['alias'], '--source', 'visible', '--lines', '60', timeout=2)
                output = result.get('output') if isinstance(result, dict) else None
            except (ValueError, OSError):
                pass  # An unreadable screen does not clear a known limit.
            observed_at = datetime.now(timezone.utc)
            hit = usage_limits.detect(output)
            if hit:
                hit['message'] = redact(hit['message'])[:240]
            with self._usage_lock:
                last, previous = self._usage_observations.get(key, (float('-inf'), None))
                if started >= last:
                    seen = usage_limits.observe(hit, previous, observed_at, USAGE_STALE) if isinstance(output, str) else previous
                    self._usage_observations[key] = (started, seen)

    def _usage_probe_soon(self, targets, every=USAGE_PROBE_SECONDS):
        """Probe stale sessions off the poll thread. True when all were checked recently."""
        def stale():
            cutoff = time.monotonic() - every
            return {key: target for key, target in targets.items()
                    if self._usage_observations.get(key, (float('-inf'), None))[0] <= cutoff}

        with self._usage_lock:
            pending = stale()
            if not pending:
                return True
            fresh_work = {key: target for key, target in pending.items() if key not in self._usage_probing}
            if not fresh_work:
                return False
            self._usage_probing.update(fresh_work)

        def unavailable():
            # A transient read or worker failure should not stall a meeting.
            # Keep any existing deadline while throttling the next attempt.
            with self._usage_lock:
                checked = time.monotonic()
                for key in fresh_work:
                    last, seen = self._usage_observations.get(key, (float('-inf'), None))
                    if checked - last >= every:
                        self._usage_observations[key] = (checked, seen)

        def work():
            try:
                self._usage_probe(fresh_work)
            except Exception as error:  # A probe must never stop the scheduler.
                logging.warning('Usage-limit probe failed: %s', redact(str(error)))
                unavailable()
            finally:
                with self._usage_lock:
                    self._usage_probing.difference_update(fresh_work)
        try:
            self.background(work)
        except RuntimeError:
            unavailable()
            with self._usage_lock:
                self._usage_probing.difference_update(fresh_work)
        with self._usage_lock:
            return not stale()

    def _usage_holds(self, organization, studio):
        now = datetime.now(timezone.utc)
        with self._usage_lock:
            if not any(seen and seen['until'] > now for _, seen in self._usage_observations.values()):
                return {}
        snapshot = self.service.store.snapshot(live_status=False)
        return {kind: hold for kind in ('portfolio', 'scout', 'pitch')
                if (hold := self._usage_hold(self._usage_targets(organization, kind, studio, snapshot=snapshot)))}

    def _usage_waiting(self, hold, job):
        failure = self._failure(self._usage_message(hold), 'preparation' if job.get('state') == 'waiting_for_members'
                                else 'meeting', job, category='usage')
        failure.update(agent=hold['name'], profile_id=hold['profile_id'], until=hold['until'],
                       reset_at=hold['reset_at'], retry_at=hold['retry_at'])
        return failure

    def _failure(self, message, phase, job=None, organization=None, category=None, code=None):
        """A named failure with its phase and its one recovery action."""
        category = category or classify(message, code)
        info = FAILURES[category]
        agent = ''
        if organization:
            agent = next((binding['name'] for binding in self._studio_bindings(organization)
                          if binding['name'] and binding['name'] in str(message)), '')
        job = job if isinstance(job, dict) else {}
        return dict(category=category, phase=phase, title=info['title'], action=info['action'], help=info['help'],
                    message=redact(str(message or info['title']))[:500], job_id=job.get('id', ''),
                    group_id=job.get('group_id', ''), agent=agent)

    # --- Where the evidence file goes ---------------------------------------

    def _evidence_checkout(self, organization, group_id, fallback):
        """The directory the facilitator reads from: ``(path, prepared)``.

        The facilitator runs in its own worktree, so a file in the source
        notebook is outside its checkout and every run would stop on a
        permission prompt. Preparing the facilitator first gives the worktree
        to write into (Product Discovery does the same). Stores that cannot
        prepare a session keep the notebook itself.
        """
        store = self.service.store
        if getattr(store, 'supports_discussion_preparation', False) is not True:
            return Path(fallback), False
        from discussion_scheduler import ensure_facilitator
        run = ensure_facilitator(store, group_id, organization)
        return project_directory(self.service.projects, run.get('worktree_path') or run.get('source_project')
                                 or (run.get('profile') or {}).get('project', '')), True

    def _pack_root(self):
        """Where meeting packs go so every participant may read them.

        Each agent works in its own worktree, and OpenCode asks before reading
        outside it. A pack in the chair's worktree was readable by the chair
        alone, so it copied the files into members' folders and stopped on a
        permission prompt. Every chair and meeting member is granted the
        dashboard's ``discussion-artifacts`` folder at launch, so packs live
        under it. None with a store that has no data folder."""
        store_path = getattr(self.service.store, 'path', None)
        if not isinstance(store_path, (str, Path)):
            return None
        return Path(store_path).parent / 'discussion-artifacts' / 'packs'

    @staticmethod
    def _write_pack(checkout, prepared, folder, identity, content, limit, label, companions=None, shared_root=None):
        """Write one evidence file atomically; refuse a changed copy.

        ``companions`` maps a file name to gateway-written guidance (a review
        checklist) placed beside the evidence. Unlike the evidence it is
        rewritten when it differs, so an upgraded gateway can still retry a
        frozen run. A prepared meeting writes under ``shared_root``, where
        its chair and members may all read."""
        data = json.dumps(content, ensure_ascii=False).encode()
        if len(data) > limit:
            raise ValueError(label + ' evidence exceeds its byte budget.')
        with repository_lock(checkout):
            if prepared and shared_root is not None:
                shared_root = Path(shared_root)
                for part in (shared_root.parent, shared_root):
                    if part.is_symlink() or (part.exists() and not part.is_dir()):
                        raise ValueError(label + ' evidence folder must be a regular directory.')
                shared_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                destination = shared_root / (folder + '-' + identity)
            elif prepared:
                # Inside the facilitator's checkout, next to its guidance
                # bundle: already ignored by Git and readable by the chair.
                from worker_guidance import local_bundle
                destination = local_bundle(checkout).parent / (folder + '-' + identity)
            else:
                cache = Path(checkout) / '.ci-cache'
                if cache.is_symlink():
                    raise ValueError(label + ' evidence cache must be a regular directory.')
                cache.mkdir(exist_ok=True)
                destination = cache / ('herdr-' + folder) / identity
            if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
                raise ValueError(label + ' evidence directory must be a regular directory.')
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / 'evidence.json'
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
                    raise ValueError(label + ' evidence copy changed; inspect its cache before retrying.')
            else:
                descriptor, temporary = tempfile.mkstemp(prefix='.evidence-', dir=destination)
                try:
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            StudioOperations._write_companions(destination, companions, label)
            return target

    @staticmethod
    def _write_companions(destination, companions, label):
        """Write gateway guidance beside an evidence file, then verify every byte."""
        files = {name: text.encode('utf-8') for name, text in (companions or {}).items()}
        for name, data in files.items():
            companion = destination / name
            if companion.is_symlink() or (companion.exists() and not companion.is_file()):
                raise ValueError(label + ' guidance file must be a regular file.')
            if companion.exists() and companion.read_bytes() == data:
                continue
            descriptor, temporary = tempfile.mkstemp(prefix='.guidance-', dir=destination)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(data)
                os.replace(temporary, companion)
            finally:
                Path(temporary).unlink(missing_ok=True)
        for name, data in files.items():
            companion = destination / name
            if companion.is_symlink() or not companion.is_file() or companion.read_bytes() != data:
                raise ValueError(label + ' guidance file ' + name + ' could not be written; nothing was sent.')

    # --- Guided repair -------------------------------------------------------

    def _studio_runs(self, organization, state):
        with closing(self.service.connect()) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT data FROM venture_studio_runs WHERE organization_id=? "
                "AND json_extract(data, '$.state')=? ORDER BY rowid DESC LIMIT 20", (organization, state))]

    def _job(self, identity):
        if not identity:
            return None
        return next((item for item in self.service.store.snapshot(live_status=False).get('jobs', [])
                     if item.get('id') == identity), None)

    RECOVER_LABELS = dict(settle='Read result again', redispatch='Re-send prompt', retry='Retry preparation',
                          restart='Pitch again', finalize='Recover result', dismiss='')
    # Group discussions refuse a fourth inspected preparation retry.
    PREPARATION_RETRIES = 3

    def _recover_hint(self, run):
        """The one step "recover" takes for this run, for the dashboard button."""
        action, label = self._repair_step(run, True)
        return dict(action=action, label=self.RECOVER_LABELS.get(action, ''), detail=label)

    def _repair_step(self, run, newest):
        """What repair does with one run needing attention: ``(action, label)``."""
        job = self._job(run.get('job_id'))
        if job and job.get('state') == 'artifact_ready':
            return 'settle', 'Read the saved ' + run['kind'] + ' result again'
        if not newest:
            return 'dismiss', 'Close the older ' + run['kind'] + ' run; the newest one is retried instead'
        if not run.get('job_id'):
            return 'redispatch', 'Send the ' + run['kind'] + ' prompt to the studio group'
        if not job:
            return 'dismiss', 'Close the ' + run['kind'] + ' run; its discussion record is gone'
        if not job.get('discussion_submitted_at'):
            if int(job.get('preparation_attempt') or 0) >= self.PREPARATION_RETRIES:
                if run['kind'] == 'pitch':
                    return 'restart', ('Start a new pitch meeting for the open ideas; this one used its '
                                       + str(self.PREPARATION_RETRIES) + ' preparation retries')
                return 'dismiss', ('Close the ' + run['kind'] + ' run; it used its preparation retries, and the '
                                   'next scheduled run starts a new meeting')
            return 'retry', 'Retry preparing the ' + run['kind'] + ' meeting; no prompt was sent yet'
        return 'finalize', 'Recover the saved output of the ' + run['kind'] + ' meeting'

    def _repair_plan(self, organization):
        """Every step repair would take now, so the operator sees it first."""
        studio = self._studio(organization)
        if not studio.get('group_id'):
            return [dict(step='setup', label='Set up the Venture Studio first', needed=True, detail='')]
        if self._studio_group_removed(organization, studio):
            return [dict(step='rebuild', label='Rebuild the studio team', needed=True,
                         detail='The studio group was removed. Agents still present are kept; missing ones '
                                'are hired again with the same settings.')]
        bindings = self._studio_bindings(organization, studio)
        failed = [binding for binding in bindings + self._panel_bindings(organization, studio) if binding['failed']]
        herdr = self._herdr_state(fresh=True)
        facilitator = next((binding for binding in bindings if binding['facilitator']), None)
        steps = [
            dict(step='herdr', label='Start Herdr', needed=herdr == 'stopped',
                 detail='Herdr is stopped.' if herdr == 'stopped' else 'Already running.'),
            dict(step='sessions', label='Release failed agent sessions', needed=bool(failed),
                 detail=', '.join(binding['name'] for binding in failed) or 'None failed.'),
            dict(step='facilitator', label='Start the studio facilitator',
                 needed=not facilitator or facilitator['failed'] or facilitator['state'] != 'persona_sent',
                 detail='The other agents start when a meeting needs them.'),
        ]
        busy = self._studio_in_flight(organization)
        newest = not busy
        for run in self._studio_runs(organization, 'needs_attention'):
            action, label = self._repair_step(run, newest)
            newest = newest and action in ('settle', 'dismiss')
            steps.append(dict(step='run', run_id=run['id'], action=action, label=label, needed=True,
                              detail=str(run.get('error') or '')[:200]))
        return steps

    def studio_repair(self, organization, body, actor):
        """One guided action back to a healthy studio.

        Without ``confirmed`` it only returns the plan. With it, each step
        runs in order and reports what happened; a step that cannot finish
        stops the ones that depend on it instead of guessing.
        """
        plan = self._repair_plan(organization)
        if body.get('confirmed') is not True:
            return dict(preview=True, steps=plan)
        if plan and plan[0]['step'] == 'setup':
            raise ValueError('Set up the Venture Studio first.')
        store = self.service.store
        done = []
        rebuilt = None
        if plan and plan[0]['step'] == 'rebuild':
            # Runs before the Herdr step below only needs Herdr to be running.
            if self._herdr_state(fresh=True) == 'stopped':
                self.herdr_start()
            self.setup_studio(organization, dict(confirmed=True), actor)
            rebuilt = dict(step='team', ok=True, detail='Rebuilt the studio team and its group.')

        def record(step, ok, detail, **extra):
            done.append(dict(step=step, ok=bool(ok), detail=redact(str(detail))[:300], **extra))
            return ok

        if rebuilt:
            done.append(rebuilt)

        def finish():
            return dict(preview=False, steps=done, healthy=all(item['ok'] for item in done),
                        readiness=self._studio_readiness(organization))

        if self._herdr_state(fresh=True) == 'stopped':
            try:
                state = self.herdr_start()['state']
            except (ValueError, OSError) as error:
                record('herdr', False, error)
                return finish()
            if not record('herdr', state == 'running', 'Started.' if state == 'running' else
                          'Herdr is still starting; run the repair again in a moment.'):
                return finish()
        studio = self._studio(organization)
        # Stakeholder sessions too: a stuck one makes every pitch retry fail.
        for binding in self._studio_bindings(organization, studio) + self._panel_bindings(organization, studio):
            if not binding['failed']:
                continue
            try:
                store.action('release', dict(request_id='studio-release-' + binding['job_id'],
                                             organization_id=organization, job_id=binding['job_id']))
                record('sessions', True, 'Released the failed session of ' + binding['name'] + '.')
            except (ValueError, OSError) as error:
                record('sessions', False, binding['name'] + ': ' + str(error))
        try:
            chair = self._refresh_panel_chair(organization, studio)
            if chair:
                record('sessions', True, chair)
        except (ValueError, OSError) as error:
            record('sessions', False, 'Stakeholder panel chair: ' + str(error))
        if any(not item['ok'] for item in done):
            return finish()
        if getattr(store, 'supports_discussion_preparation', False) is True:
            try:
                from discussion_scheduler import ensure_facilitator
                ensure_facilitator(store, studio['group_id'], organization)
                record('facilitator', True, 'The facilitator session is live; the other agents start '
                                            'when the next meeting needs them.')
                self._studio_rename_group(organization, studio)
            except (ValueError, OSError) as error:
                failure = self._failure(str(error), 'preparation', organization=organization,
                                        code=getattr(error, 'code', None))
                record('facilitator', False, error, failure=failure)
                return finish()
        newest = not self._studio_in_flight(organization)
        for run in self._studio_runs(organization, 'needs_attention'):
            action, label = self._repair_step(run, newest)
            newest = newest and action in ('settle', 'dismiss')
            try:
                detail = self._repair_run(organization, run, action, actor)
                record('run', True, detail, run_id=run['id'], action=action)
            except (ValueError, OSError) as error:
                record('run', False, error, run_id=run['id'], action=action)
        return finish()

    def _repair_run(self, organization, run, action, actor):
        store = self.service.store

        def dismiss(reason):
            run.update(state='dismissed', dismissed=dict(by=actor, at=now().isoformat(), reason=reason[:300]))
            self.save_studio_run(run)
            return 'Closed the ' + run['kind'] + ' run: ' + reason

        def settle():
            from contributions import Busy
            job = self._job(run.get('job_id'))
            try:
                self._studio_settle(run, self._studio_parse(run, job))
            except Busy:
                raise  # An operator is on one of its ideas: a valid result, for later.
            except ValueError as error:
                return dismiss('the result did not validate (' + str(error)[:160] + ').')
            return 'The ' + run['kind'] + ' result is in.'

        if action == 'dismiss':
            return dismiss('superseded during studio repair.')
        if action == 'settle':
            return settle()
        if action == 'redispatch':
            run.pop('failure', None)
            self._studio_dispatch(organization, run)
            return 'The ' + run['kind'] + ' prompt was sent to the studio group.'
        if action == 'restart':
            return self._pitch_restart(organization, run, actor)
        if action == 'retry':
            store.action('retry_discussion', dict(request_id='studio-retry-' + uuid.uuid4().hex,
                                                  organization_id=organization, job_id=run['job_id'],
                                                  inspected=True))
            run.update(state='meeting', error='', failure=None, waiting=None)
            self.save_studio_run(run)
            return 'The ' + run['kind'] + ' meeting is being prepared again.'
        store.action('recover', dict(request_id='studio-recover-' + uuid.uuid4().hex,
                                     organization_id=organization, job_id=run['job_id']))
        if (self._job(run.get('job_id')) or {}).get('state') == 'artifact_ready':
            return settle()
        raise ValueError('The ' + run['kind'] + ' meeting still needs a decision in the group discussion.')

    def studio_team_start(self, organization, actor):
        """Start the facilitator and every agent that has no session yet."""
        studio = self._studio(organization)
        self._studio_group(organization, studio)
        if self._herdr_state(fresh=True) == 'stopped':
            raise StudioWaiting('Herdr is stopped. Start Herdr to start the studio team.', 'herdr')
        self._studio_rename_group(organization, studio)
        store = self.service.store
        if getattr(store, 'supports_discussion_preparation', False) is True:
            from discussion_scheduler import ensure_facilitator
            ensure_facilitator(store, studio['group_id'], organization)
        started = []
        for binding in self._studio_bindings(organization, studio):
            if binding['facilitator'] or binding['state'] != 'not_started':
                continue
            store.action('launch', dict(request_id='studio-launch-' + uuid.uuid4().hex,
                                        organization_id=organization, profile_id=binding['profile_id']))
            started.append(binding['name'])
        return dict(started=started, team=self._studio_bindings(organization, studio))

    def studio_limits(self, organization, actor):
        """Raise the session limits to what one studio meeting needs; never lower one."""
        people = len(self._studio_bindings(organization)) or STUDIO_PARTICIPANTS
        people += len((self._scout_consult(organization) or {}).get('roles', []))
        policy = self._policy(organization)
        changes = {}
        for key in ('max_active_sessions', 'daily_session_cap'):
            try:
                value = int(policy.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value and value < people:
                changes[key] = people
        if changes:
            self.service.patch_policy(organization, changes, actor)
        return dict(organization_id=organization, changed=changes, participants=people)

    # --- One-call setup -------------------------------------------------------

    def _studio_organization(self, body):
        """The organization that holds the studio: the named one, the only
        one, or a new one. The operator never has to create it first."""
        import ventures as core
        organizations = self._organizations()
        requested = body.get('organization_id')
        if requested:
            if not isinstance(requested, str) or not any(item.get('id') == requested for item in organizations):
                raise ValueError('Organization not found.')
            return requested
        if len(organizations) == 1:
            return organizations[0]['id']
        if organizations:
            raise ValueError('Choose which organization holds the Venture Studio.')
        name = core._value(body.get('name') or 'Venture Studio', 120, 'studio name', required=True)
        response = self.service.store.action('save', dict(
            request_id='studio-org-' + hashlib.sha256(name.encode()).hexdigest()[:32], name=name,
            purpose='Holds the Venture Studio: its agents, limits and audit history.'))
        return response['id']

    def studio_start(self, body, actor):
        """From nothing to a working studio in one call.

        Creates or selects the organization, creates the notebook, agents and
        group, saves the starter policy and can start the first scout. For a
        new studio a cadence that is not given defaults to weekly, so "manual
        only" is a choice the operator makes, and session limits too small
        for one studio meeting are raised and reported. Refused while Herdr
        is stopped.
        """
        import ventures as core
        if body.get('confirmed') is not True:
            raise ValueError('Confirm creating the Venture Studio: one notebook, four agents and one read-only group.')
        if not self._organizations():
            # Checked before an organization is created for nothing.
            self._require_herdr('set up the Venture Studio')
        organization = self._studio_organization(body)
        created = not self._studio(organization).get('group_id')
        with self.service.operation('studio:' + organization):
            studio = self.setup_studio(organization, body, actor)
        changes = {key: body[key] for key in core.STUDIO_VALIDATORS if key in body}
        raised = {}
        with self.service.operation('policy:' + organization):
            if created:
                # Only a new studio gets the weekly default and the limits it
                # needs; asking again never re-enables a cadence the operator
                # turned off or raises a limit they lowered.
                changes.setdefault('studio_scout_cadence', 'weekly')
                changes.setdefault('studio_portfolio_cadence', 'weekly')
                raised = self.studio_limits(organization, actor)['changed']
            policy = self.update_studio_policy(organization, changes, actor)
        result = dict(organization_id=organization, studio=studio, policy=policy, first_scout=None,
                      created=created, limits_raised=raised)
        if body.get('first_scout') is True:
            try:
                with self.service.operation('studio:' + organization):
                    run = self.studio_run(organization, 'scout', actor, override=body.get('override') is True)
                result['first_scout'] = dict(state=run['state'], run_id=run['id'], error='')
            except (ValueError, OSError) as error:
                # The studio exists; only its first run did not start.
                result['first_scout'] = dict(state='not_started', run_id='', error=redact(str(error))[:300],
                                             category=getattr(error, 'category', classify(str(error))))
        result['readiness'] = self._studio_readiness(organization)
        return result

    # --- Autonomous creation ---------------------------------------------------

    def _studio_autocreate(self, run):
        """In autonomous mode, create ventures from a scout run within hard caps.

        A candidate is created only when it reaches the minimum confidence,
        cites at least one outside source item, is not excluded or a
        duplicate, and the organization is below its active-venture limit; at
        most one per run. Every decision, created or not, is recorded on the
        candidate and on the run. Nothing here sends mail, spends or publishes.
        """
        organization = run['organization_id']
        policy = self._studio_policy(organization)
        if run.get('kind') not in ('scout', 'pitch') or policy.get('studio_mode') != 'autonomous':
            return []
        pitch = run['kind'] == 'pitch'
        if not pitch and self._panel_enabled(organization):
            return []  # The stakeholder panel reviews the round first; its pitch decides.
        minimum = policy['studio_auto_min_confidence']
        outcomes, created = [], 0
        if pitch:
            proposed = []
            for identity in run.get('candidate_ids') or []:
                try:
                    stored = self.get_candidate(organization, identity)
                except ValueError:
                    continue
                if stored.get('state') == 'proposed':
                    proposed.append(stored)
        else:
            proposed = [item for item in run.get('candidates') or [] if item.get('state') == 'proposed']
        for candidate in sorted(proposed, key=lambda item: (-int(item.get('confidence') or 0), item['id'])):
            reason = ''
            if int(candidate.get('confidence') or 0) < minimum:
                reason = ('Confidence ' + str(candidate.get('confidence')) + ' is below the minimum of '
                          + str(minimum) + '.')
            elif candidate.get('skeptic_silent'):
                reason = ('The Skeptic did not speak in the scout meeting, so its verdict was not its own; '
                          'approve it yourself if you agree.')
            elif (isinstance(candidate.get('dissent'), dict)
                  and candidate['dissent'].get('verdict') in ('hold', 'reject')):
                # Only an idea the Skeptic would advance is created unattended.
                reason = ('The Skeptic would ' + candidate['dissent']['verdict'] + ' this idea; approve it '
                          'yourself if you disagree.')
            elif pitch and not panel_current(candidate):
                reason = ('It was edited after the panel saw it, so the votes are for an earlier version; '
                          'it goes back to the next pitch.')
            elif pitch and not panel_backs(candidate):
                counts = candidate.get('panel') or {}
                reason = ('Most stakeholders did not back it (' + str(counts.get('back', 0)) + ' back, '
                          + str(counts.get('hold', 0)) + ' hold, ' + str(counts.get('pass', 0)) + ' pass); '
                          'approve it yourself if you disagree.')
            elif (candidate.get('economics') if isinstance(candidate.get('economics'), dict) else {}).get(
                    'warnings'):
                reason = ('The gateway economics flag a problem: '
                          + '; '.join(str(item) for item in candidate['economics']['warnings']))
            elif not any(str(item).startswith('N') for item in candidate.get('evidence') or []):
                # Pages found by web research are what the agents report; the
                # gateway did not fetch them, so they never create a venture alone.
                reason = ('Its sources were found by web research and are not verified by the gateway; '
                          'approve it yourself.' if candidate.get('evidence_links') else
                          'It cites no outside source item, only internal history.')
            elif created >= AUTO_CREATE_PER_RUN:
                reason = 'Only ' + str(AUTO_CREATE_PER_RUN) + ' venture is created per scout run.'
            elif self._places_taken(organization) >= self.limit(organization):
                reason = 'The active venture limit of ' + str(self.limit(organization)) + ' is reached.'
            if not reason:
                try:
                    # Settlement runs on the shared poll thread: never wait for an
                    # operator deciding this idea; it is left to them, with the reason.
                    with self.service.operation('candidate:' + candidate['id'], timeout=0):
                        if pitch and not panel_backs(self.get_candidate(organization, candidate['id'])):
                            raise ValueError('It was edited after the panel saw it, so the votes are for an '
                                             'earlier version; it goes back to the next pitch.')
                        self.candidate_decide(organization, candidate['id'], dict(
                            decision='approve', confirmed=True, create_team=True, start_daily=True,
                            meeting_cadence='news_only'), AUTO_ACTOR)
                    created += 1
                except (ValueError, OSError) as error:
                    reason = str(error) or error.__class__.__name__
            from contributions import Busy
            try:
                with self.service.operation('candidate:' + candidate['id'], timeout=0):
                    stored = self.get_candidate(organization, candidate['id'])
                    stored['auto'] = dict(at=now().isoformat(), created=not reason, reason=redact(reason)[:300],
                                          run_id=run['id'], minimum_confidence=minimum)
                    self.save_candidate(stored)
            except Busy:  # An operator is editing it; the run still records the outcome.
                stored = self.get_candidate(organization, candidate['id'])
            outcomes.append(dict(candidate_id=candidate['id'], name=candidate.get('name', ''),
                                 created=not reason, reason=redact(reason)[:300],
                                 venture_id=stored.get('venture_id', '')))
        return outcomes
