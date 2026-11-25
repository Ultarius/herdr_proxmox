import base64
from datetime import date, datetime, timedelta
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from contributions import Contributions
import project_git
import venture_feeds
import venture_notebook
import venture_studio
import ventures


def engine_roles():
    import venture_engine
    return venture_engine.ROLES


def feed_item(published):
    link = 'https://news.example/item-1'
    return dict(id=hashlib.sha256(link.encode()).hexdigest(), source='https://news.example/feed',
                title='Japanese banks adopt instant settlement', link=link, published=published,
                tags=['payments'], excerpt='A short excerpt about settlement.', fetched_at=published)


def result(citation='N1'):
    return {
        'events': [{'evidence': [citation], 'summary': 'Demand rises for settlement tooling.', 'direction': 'tailwind'}],
        'product_changes': [{'kind': 'add', 'title': 'Japanese onboarding', 'reason': 'A cited bank changed its process.',
                             'evidence': [citation]}],
        'pricing': {'monthly': 48000, 'reason': 'Positioned for the cited customer segment.'},
        'deltas': {'demand': 2, 'competition': -1, 'regulatory': 0, 'delivery_risk': -1},
        'red_team': [{'objection': 'The sales cycle may be longer.', 'severity': 3}],
        'outreach': [{'persona': 'cfo', 'language': 'ja', 'subject': 'A settlement question',
                      'body': 'We saw the cited change and would like a short conversation.', 'evidence': [citation]}],
    }



def strategy_result(citation='S1'):
    return {
        'events': [],
        'product_changes': [],
        'pricing': {},
        'deltas': {'demand': 0, 'competition': 0, 'regulatory': 0, 'delivery_risk': 0},
        'red_team': [],
        'outreach': [],
    }


def candidate(name='Carrier Desk', evidence='V1'):
    return {
        'name': name,
        'thesis': 'Freight forwarders lose margin on invoice exceptions and carrier overcharges.',
        'product': 'Invoice reconciliation for mid-market freight forwarders.',
        'pricing': '$600 per site per month',
        'configuration': {
            'sources': {'feeds': ['https://news.example/feed']},
            'market': {'geography': 'United States', 'currency': 'USD', 'fx_pair': 'USD/USD', 'fx_rate': 1},
            'simulation': {'capital': 250000, 'burn': [{'label': 'hosting', 'amount': 3000, 'currency': 'USD'}],
                           'architecture': 'saas', 'icp_size': '100-1000 employees',
                           'icp_sector': 'freight forwarding'},
            'outreach': {'stage': 'survey', 'tone': 'direct', 'language': 'en', 'persona': 'cfo'},
        },
        'prior_state': {'previous': 'Forwarders reconcile exceptions manually today.',
                        'change': 'The new filing requires a documented exception trail.',
                        'evidence': [evidence]},
        'alternatives': [
            {'name': 'Manual reconciliation', 'kind': 'in_house', 'price': None, 'evidence': [evidence]},
            {'name': 'Safexpert', 'kind': 'software',
             'price': {'amount': 600, 'currency': 'USD'}, 'evidence': [evidence]},
            {'name': 'Consultancy review', 'kind': 'service',
             'price': {'amount': 5000, 'currency': 'USD'}, 'evidence': [evidence]},
        ],
        'buyer': {'role': 'CFO', 'budget': 'finance operations', 'evidence': [evidence]},
        'reachable_accounts': {'count': 300, 'segment': 'US mid-market freight forwarders',
                               'evidence': [evidence]},
        'business_model': {'type': 'saas',
                           'revenue_lines': [{'kind': 'recurring', 'label': 'site subscription',
                                              'amount': 600, 'currency': 'USD', 'period': 'month'}]},
        'delivery_effort': {'expert_days': 2, 'day_rate': {'amount': 900, 'currency': 'USD'}},
        'evidence_against': [{'claim': 'Small forwarders may keep a manual review for low volumes.',
                              'evidence': [evidence]}],
        'dissent': {'verdict': 'advance', 'summary': 'Worth a paid pilot; the workflow may still be too small.',
                    'evidence': [evidence]},
        'confidence_dimensions': {
            'regulatory_driver': {'rating': 'medium', 'evidence': [evidence]},
            'paid_demand': {'rating': 'medium', 'evidence': [evidence]},
            'technical_feasibility': {'rating': 'strong', 'evidence': [evidence]},
            'differentiation': {'rating': 'medium', 'evidence': [evidence]},
            'recurring_potential': {'rating': 'strong', 'evidence': [evidence]},
            'defensibility': {'rating': 'medium', 'evidence': [evidence]},
        },
        'first_offer': 'Fixed-fee invoice reconciliation pilot',
        'time_to_first_payment_days': 30,
        'assumptions': ['Carriers will share invoice data.'],
        'kill_criteria': ['No paid pilot signed within 90 days.'],
        'confidence': 70,
        'evidence': [evidence],
    }
@unittest.skipUnless(shutil.which('git'), 'Git required')
class VentureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.projects = self.root / 'projects'
        self.projects.mkdir()
        self.jobs = []
        self.requests = {}
        self.fail_next = None
        self.profiles = []
        self.groups = []
        self.organizations = [dict(id='org', name='Product organization')]
        self.store = MagicMock()
        self.store.snapshot.side_effect = lambda **kw: dict(
            organizations=self.organizations,
            profiles=self.profiles, groups=self.groups, jobs=self.jobs)
        self.store.action.side_effect = self.store_action
        self.store.update_job.side_effect = self.update_job
        self.service = Contributions(self.root / 'tasks.sqlite3', self.projects, self.store, MagicMock())
        self.addCleanup(self.service.close)
        self.ventures = self.service.ventures
        self.ventures.background = lambda work: work()  # Refresh inline; one test checks the real hand-off.
        self.configuration = dict(
            sources=dict(feeds=['https://news.example/feed'], tags=['payments'], keywords=['settlement'],
                         translation=False, keep_terms=[]),
            market=dict(geography='Japan', currency='JPY', fx_pair='USD/JPY', fx_rate=1.0,
                        sales_cycle=4, legacy_bias=3, regulatory='Banking rules apply.'),
            simulation=dict(capital=100000, burn=[{'label': 'hosting', 'amount': 30000, 'currency': 'JPY'}],
                            architecture='api', icp_size='mid-market', icp_sector='banking', red_team=7,
                            survival_threshold=60, consecutive_days=3, formula_version=1),
            outreach=dict(stage='survey', tone='direct', language='ja', persona='cfo'))

    def store_action(self, action, body, *, studio_run_id=None):
        if action == 'discuss':
            job = dict(id=uuid.uuid4().hex, kind='discussion', state='queued',
                       organization_id=body['organization_id'], group_id=body['group_id'], prompt=body['prompt'],
                       advisors=list(body.get('advisors') or []), studio_run_id=studio_run_id)
            self.jobs.append(job)
            return {'id': job['id']}
        # Mirror the organization store's contract: a request ID is required and
        # replays its saved response, an explicit id means "update an existing
        # record", and projects resolve from the process directory.
        if not isinstance(body.get('request_id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', body['request_id']):
            raise ValueError('request_id is required.')
        replay = self.requests.get(body['request_id'])
        if replay:
            if replay[0] != json.dumps(body, sort_keys=True):
                raise ValueError('Request ID already used for different content.')
            return replay[1]
        if body.get('id') and not (action == 'group' and any(
                group['id'] == body['id'] for group in self.groups)):
            raise ValueError('Record not found.')
        if self.fail_next == action:
            self.fail_next = None
            raise OSError('simulated store failure')
        response = self.apply(action, body)
        self.requests[body['request_id']] = (json.dumps(body, sort_keys=True), response)
        return response

    def apply(self, action, body):
        if action == 'hire':
            project = Path(body['project'])
            if not project.is_absolute() or not project.resolve().is_relative_to(self.projects):
                raise ValueError('Select an existing directory inside the projects folder.')
            profile = dict(id=uuid.uuid4().hex, organization_id=body['organization_id'], name=body['name'],
                           role=body['role'], project=str(project), use_worktree=True,
                           runtime=body['runtime'], provider=body.get('provider', ''),
                           model=body.get('model', ''), permission_mode=body.get('permission_mode', 'default'),
                           accessible_paths=body.get('accessible_paths', []))
            self.profiles.append(profile)
            return profile
        if action == 'group' and body.get('id'):
            group = next(group for group in self.groups if group['id'] == body['id'])
            group.update(name=body['name'], members=body['members'],
                         permission_mode=body.get('permission_mode', 'default'))
            return group
        if action == 'group':
            member = next(profile for profile in self.profiles if profile['id'] == body['members'][0])
            facilitator = dict(id=uuid.uuid4().hex, organization_id=body['organization_id'],
                               name=body['name'] + ' facilitator', project=member['project'],
                               use_worktree=True, runtime='codex', group_id='g' * 32)
            self.profiles.append(facilitator)
            group = dict(id='g' * 32, organization_id=body['organization_id'], name=body['name'],
                         members=body['members'], read_only=True, facilitator_id=facilitator['id'],
                         permission_mode=body.get('permission_mode', 'default'))
            self.groups.append(group)
            return group
        # The session and organization actions the studio's setup and repair use.
        if action == 'save':
            organization = dict(id=uuid.uuid4().hex, name=body['name'])
            self.organizations.append(organization)
            return organization
        if action == 'release':
            next(job for job in self.jobs if job['id'] == body['job_id'])['state'] = 'released'
            return {'id': body['job_id']}
        if action == 'retry_discussion':
            job = next(job for job in self.jobs if job['id'] == body['job_id'])
            if body.get('inspected') is not True or job['state'] not in ('needs_attention', 'uncertain'):
                raise ValueError('Only a preparation needing attention can be retried.')
            job.update(state='queued', error='')
            return {'id': job['id']}
        if action == 'cancel_discussion':
            job = next(job for job in self.jobs if job['id'] == body['job_id'])
            job.update(state='cancelled')
            return {'id': job['id']}
        if action == 'recover':
            return {'id': body['job_id']}
        if action == 'launch':
            job = dict(id=uuid.uuid4().hex, kind='launch', state='queued', profile_id=body['profile_id'],
                       organization_id=body['organization_id'])
            self.jobs.append(job)
            return {'id': job['id']}
        raise AssertionError('Unexpected store action: ' + action)

    def update_job(self, identity, **fields):
        next(job for job in self.jobs if job['id'] == identity).update(fields)

    def create(self, **changes):
        body = dict(mode='create', organization_id='org', name='Settlement Desk',
                    thesis='A settlement API for mid-market Japanese banks.',
                    product='Settlement API', pricing='48000 JPY per month',
                    configuration=self.configuration, request_id='request-1')
        body.update(changes)
        return self.ventures.action(body, 'admin', 'admin')

    def attach_group(self, record):
        notebook = self.projects / record['notebook']
        self.profiles[:] = [
            dict(id='f' * 32, name='Analyst', organization_id='org', project=str(notebook), use_worktree=True),
            dict(id='a' * 32, name='Founder', organization_id='org', project=str(notebook), use_worktree=True),
            dict(id='b' * 32, name='Red Team', organization_id='org', project=str(notebook), use_worktree=True),
        ]
        self.groups[:] = [dict(id='g' * 32, name='Venture team', organization_id='org',
                               facilitator_id='f' * 32, members=['a' * 32, 'b' * 32], read_only=True)]
        return self.ventures.action(dict(mode='configure', organization_id='org', venture_id=record['id'],
                                         group_id='g' * 32), 'admin', 'admin')

    def complete(self, record, value=None):
        tick = self.ventures.tick_for(record['id'], 'org', ventures.now().date().isoformat())
        job = next(job for job in self.jobs if job['id'] == tick['job_id'])
        job.update(state='artifact_ready', result='```json\n' + json.dumps(value or result()) + '\n```')
        self.ventures.advance()
        with closing(self.service.connect()) as db:
            return json.loads(db.execute('SELECT data FROM venture_ticks WHERE id=?', (tick['id'],)).fetchone()[0])

    def add_venture_group(self, record):
        notebook = self.projects / record['notebook']
        self.profiles.extend([
            dict(id='u' * 32, name='Analyst', organization_id='org', project=str(notebook), use_worktree=True),
            dict(id='v' * 32, name='Founder', organization_id='org', project=str(notebook), use_worktree=True),
            dict(id='w' * 32, name='Red Team', organization_id='org', project=str(notebook), use_worktree=True),
        ])
        self.groups.append(dict(id='b1' * 16, name='Venture team', organization_id='org',
                                facilitator_id='u' * 32, members=['v' * 32, 'w' * 32], read_only=True))
        return self.ventures.action(dict(mode='configure', organization_id='org', venture_id=record['id'],
                                         group_id='b1' * 16), 'admin', 'admin')
    def setup_studio_manual(self):
        notebook = self.projects / 'ventures' / 'studio-org'
        venture_notebook.provision_studio(self.projects, 'ventures/studio-org', 'org')
        self.profiles[:] = [
            dict(id='p' * 32, name='Portfolio Manager', organization_id='org', project=str(notebook), use_worktree=True),
            dict(id='q' * 32, name='Venture Scout', organization_id='org', project=str(notebook), use_worktree=True),
            dict(id='r' * 32, name='Skeptic', organization_id='org', project=str(notebook), use_worktree=True),
            dict(id='s' * 32, name='Research Analyst', organization_id='org', project=str(notebook), use_worktree=True),
            dict(id='t' * 32, name='Studio facilitator', organization_id='org', project=str(notebook), use_worktree=True),
        ]
        self.groups[:] = [dict(id='a1' * 16, name='Venture Studio', organization_id='org',
                               facilitator_id='t' * 32, members=['p' * 32, 'q' * 32, 'r' * 32, 's' * 32],
                               read_only=True)]
        # A scout needs a focus area; tests that check the rule clear it again.
        self.service.patch_policy('org', dict(studio_focus=['freight forwarding']), 'test')
        return self.ventures.save_studio('org', dict(notebook='ventures/studio-org', group_id='a1' * 16,
                                                     team={}, created_at=ventures.now().isoformat()))
    def test_create_is_bounded_replayable_and_provisions_a_notebook(self):
        record = self.create()
        self.assertEqual(record['state'], 'draft')
        notebook = self.projects / record['notebook']
        self.assertTrue((notebook / 'venture.md').is_file())
        self.assertTrue((notebook / 'product.md').is_file())
        self.assertTrue((notebook / 'ledger.csv').is_file())
        self.assertTrue((notebook / 'outreach').is_dir())
        replay = self.create()
        self.assertEqual(record['id'], replay['id'])
        self.assertTrue((notebook / '.git').exists())

    def test_configuration_rejects_unbounded_or_unsafe_layers(self):
        for changes in (
            dict(sources=dict(feeds=['http://news.example/feed'])),
            dict(sources=dict(feeds=['https://user:pass@news.example/feed'])),
            dict(sources=dict(feeds=['https://news.example/feed'] * 11)),
            dict(market=dict(sales_cycle=6)),
            dict(simulation=dict(capital=-1)),
            dict(simulation=dict(architecture='desktop')),
            dict(simulation=dict(meeting_cadence='weekly')),
            dict(outreach=dict(persona='ceo')),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    ventures.normalize_config({**self.configuration, **changes})

    def test_manual_tick_dispatches_one_meeting_and_commits_one_ledger_row(self):
        record = self.attach_group(self.create())
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            first = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']), 'admin', 'admin')
        self.assertEqual(first['state'], 'meeting')
        self.assertTrue(next(job for job in self.jobs if job['id'] == first['job_id'])['prompt'].startswith('VENTURE SIMULATION'))
        second = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']), 'admin', 'admin')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.jobs), 1)
        tick = self.complete(record)
        self.assertEqual(tick['state'], 'ready')
        self.assertEqual(tick['ledger']['capital_after'], 99000)
        self.assertEqual(tick['events'][0]['direction'], 'tailwind')
        notebook = self.projects / record['notebook']
        self.assertIn('Japanese onboarding', (notebook / 'product.md').read_text(encoding='utf-8'))
        self.assertEqual(len((notebook / 'ledger.csv').read_text(encoding='utf-8').strip().splitlines()), 2)
        with closing(self.service.connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM venture_outreach').fetchone()[0], 1)

    def test_quiet_day_records_burn_without_starting_a_session(self):
        record = self.create()
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[], etag='', modified='')):
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']), 'admin', 'admin')
        self.assertEqual(tick['state'], 'ready')
        self.assertEqual(tick['ledger']['burn'], 1000)
        self.assertFalse(self.store.action.called)
        self.assertIn(tick['simulated_date'],
                      (self.projects / record['notebook'] / 'ledger.csv').read_text(encoding='utf-8'))

    def test_parse_rejects_unknown_evidence_and_missing_citations(self):
        record = self.attach_group(self.create())
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']), 'admin', 'admin')
        job = next(job for job in self.jobs if job['id'] == tick['job_id'])
        invalid = result('N9')
        job.update(state='artifact_ready', result=json.dumps(invalid))
        self.ventures.advance()
        saved = self.ventures.tick_for(record['id'], 'org', tick['simulated_date'])
        self.assertEqual(saved['state'], 'needs_attention')
        self.assertIn('evidence', saved['error'])

    def test_recovery_reprocesses_existing_artifact_without_resending(self):
        record = self.attach_group(self.create())
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']), 'admin', 'admin')
        job = next(job for job in self.jobs if job['id'] == tick['job_id'])
        job.update(state='artifact_ready', result='not json')
        self.ventures.advance()
        calls = self.store.action.call_count
        job.update(result='```json\n' + json.dumps(result()) + '\n```')
        recovered = self.ventures.action(dict(mode='recover', organization_id='org', venture_id=record['id'],
                                              inspected=True, simulated_date=tick['simulated_date']), 'admin', 'admin')
        self.assertEqual(recovered['state'], 'ready')
        self.assertEqual(self.store.action.call_count, calls)

    def test_http_ventures_route_requires_admin_and_returns_selected_history(self):
        import threading
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        import server as gateway
        record = self.create()
        server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        server.token = 'admin-token'
        server.operators = MagicMock()
        server.operators.identify.side_effect = lambda token: dict(name='viewer', role='operator') if token == 'operator-token' else None
        server.contributions = self.service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def request(path, token=None, data=None):
            headers = {'Authorization': 'Bearer ' + token} if token else {}
            if data is not None:
                headers['Content-Type'] = 'application/json'
            return urlopen(Request(base + path, headers=headers,
                                   data=None if data is None else json.dumps(data).encode()))
        try:
            for token, status in ((None, 401), ('operator-token', 403)):
                with self.assertRaises(HTTPError) as error:
                    request('/api/ventures', token)
                self.assertEqual(error.exception.code, status)
            with self.assertRaises(HTTPError) as error:
                request('/api/ventures', 'operator-token', {'mode': 'create',
                        'organization_id': 'org', 'name': 'Refused'})
            self.assertEqual(error.exception.code, 400)
            with request('/api/ventures', 'admin-token', {'mode': 'create',
                    'organization_id': 'org', 'name': 'Second venture', 'request_id': 'http-create'}) as response:
                self.assertEqual(json.load(response)['name'], 'Second venture')
            with request('/api/ventures?venture=' + record['id'], 'admin-token') as response:
                snapshot = json.load(response)
            self.assertEqual(snapshot['selected']['id'], record['id'])
            self.assertEqual(len(snapshot['ventures']), 2)
            with self.assertRaises(HTTPError) as error:
                request('/api/ventures?venture=' + '0' * 32, 'admin-token')
            self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def run_with_news(self, record):
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            return self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']), 'admin', 'admin')

    def configure(self, record, **simulation):
        configuration = json.loads(json.dumps(self.configuration))
        configuration['simulation'].update(simulation)
        return self.ventures.action(dict(mode='configure', organization_id='org', venture_id=record['id'],
                                         configuration=configuration), 'admin', 'admin')

    def test_tick_settles_score_runway_and_role_aware_prompt(self):
        record = self.attach_group(self.create())
        self.assertEqual(record['roles']['red_team'], ['Red Team'])
        self.assertEqual(record['roles']['founder'], ['Founder'])
        tick = self.run_with_news(record)
        prompt = next(job for job in self.jobs if job['id'] == tick['job_id'])['prompt']
        self.assertIn('Red Team (Red Team): Strictness 7/10', prompt)
        self.assertIn('keigo', prompt)
        self.assertLessEqual(len(prompt), ventures.PROMPT_LIMIT)
        settled = self.complete(record)
        self.assertIsInstance(settled['ledger']['score'], int)
        self.assertEqual(settled['ledger']['runway_days'], 99)
        saved = self.ventures.get(record['id'], 'org')
        self.assertEqual(saved['score'], settled['ledger']['score'])
        self.assertEqual(saved['sim']['monthly_price'], 48000)

    def test_result_must_respect_strictness_persona_and_placeholders(self):
        record = self.attach_group(self.create())
        for name, change in (
            ('no objections', lambda value: value.update(red_team=[])),
            ('fewer objections than changes', lambda value: (
                value['product_changes'].append(dict(value['product_changes'][0], title='Second change')),
                value.update(red_team=[{'objection': 'Only one', 'severity': 2}]))),
            ('too many changes', lambda value: value['product_changes'].extend(value['product_changes'] * 2)),
            ('wrong persona', lambda value: value['outreach'][0].update(persona='cto')),
            ('email address', lambda value: value['outreach'][0].update(body='Write to buyer@bank.example today.')),
        ):
            with self.subTest(name=name):
                value = result()
                change(value)
                with self.assertRaises(ValueError):
                    self.ventures.parse(dict(configuration=ventures.normalize_config(self.configuration),
                                             delivered_evidence_ids=['N1'],
                                             result=json.dumps(value)))

    def test_scheduler_waits_for_the_chosen_hour_then_runs_one_tick(self):
        record = self.attach_group(self.create())
        self.ventures.advance()
        self.assertEqual(self.jobs, [])  # Daily ticks are opt-in.
        hour = (ventures.now().hour + 1) % 24
        self.configure(record, daily_ticks=True, tick_hour_utc=hour)
        saved = self.ventures.get(record['id'], 'org')
        self.assertGreater(ventures.moment(saved['next_at']), ventures.now())
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            self.ventures.advance()
            self.assertEqual(self.jobs, [])  # Not due yet.
            saved = self.ventures.get(record['id'], 'org')
            saved['next_at'] = (ventures.now() - timedelta(minutes=1)).isoformat()
            self.ventures.save(saved)
            self.ventures.advance()
            self.ventures.advance()
        self.assertEqual(len(self.jobs), 1)
        saved = self.ventures.get(record['id'], 'org')
        self.assertEqual(saved['state'], 'running')
        self.assertGreater(ventures.moment(saved['next_at']), ventures.now())

    def test_scheduled_errors_retry_with_backoff_even_though_next_slot_is_future(self):
        record = self.create()
        hour = (ventures.now().hour + 1) % 24
        # A record saved before daily reviews required a team.
        saved = self.ventures.get(record['id'], 'org')
        saved['configuration']['simulation'].update(daily_ticks=True, tick_hour_utc=hour)
        saved['next_at'] = (ventures.now() - timedelta(minutes=1)).isoformat()
        self.ventures.save(saved)
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            self.ventures.advance()
        saved = self.ventures.get(record['id'], 'org')
        self.assertIn('group', saved['schedule_error'])
        self.assertEqual(saved['schedule_failures'], 1)
        self.assertGreater(ventures.moment(saved['retry_at']), ventures.now())
        saved['retry_at'] = (ventures.now() - timedelta(minutes=1)).isoformat()
        self.ventures.save(saved)
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            self.ventures.advance()
        saved = self.ventures.get(record['id'], 'org')
        self.assertEqual(saved['schedule_failures'], 2)

    def test_outreach_unlocks_on_qualification_and_records_real_replies(self):
        record = self.attach_group(self.create())
        self.run_with_news(record)
        self.complete(record)
        with closing(self.service.connect()) as db:
            draft = json.loads(db.execute('SELECT data FROM venture_outreach').fetchone()[0])
        decide = lambda **body: self.ventures.action(dict(mode='outreach', organization_id='org',
                                                         venture_id=record['id'], outreach_id=draft['id'], **body),
                                                    'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'qualifies'):
            decide(decision='approve')
        saved = self.ventures.get(record['id'], 'org')
        saved.update(state='qualified', qualified_at=ventures.now().isoformat())
        self.ventures.save(saved)
        approved = decide(decision='approve')
        self.assertEqual(approved['state'], 'ready')
        written = self.projects / record['notebook'] / approved['notebook_path']
        self.assertIn(draft['body'], written.read_text(encoding='utf-8'))
        sent = decide(decision='outcome', outcome='replied', note='Asked for a call.')
        self.assertEqual(sent['state'], 'sent')
        self.assertEqual(self.ventures.get(record['id'], 'org')['signals']['replied'], 1)

    def test_create_team_clones_an_agent_and_assigns_all_four_roles(self):
        record = self.create()
        notebook = self.projects / record['notebook']
        self.profiles[:] = [dict(id='t' * 32, name='Template', organization_id='org', project=str(notebook),
                                 use_worktree=True, runtime='codex', provider='', model='',
                                 permission_mode='default', accessible_paths=[])]
        saved = self.ventures.action(dict(mode='team', organization_id='org', venture_id=record['id'],
                                          confirmed=True), 'admin', 'admin')
        self.assertEqual(saved['group_id'], 'g' * 32)
        self.assertEqual(saved['roles']['red_team'], ['Red Team'])
        self.assertEqual(len([profile for profile in self.profiles if profile['project'] == str(notebook)]), 6)

    def test_team_retry_after_a_partial_failure_hires_no_duplicates(self):
        record = self.create()
        notebook = self.projects / record['notebook']
        self.profiles[:] = [dict(id='t' * 32, name='Template', organization_id='org', project=str(notebook),
                                 use_worktree=True, runtime='codex', provider='', model='',
                                 permission_mode='default', accessible_paths=[])]
        self.fail_next = 'group'
        with self.assertRaises(OSError):
            self.ventures.action(dict(mode='team', organization_id='org', venture_id=record['id'],
                                      confirmed=True), 'admin', 'admin')
        self.assertEqual(len(self.ventures.get(record['id'], 'org')['team']['members']), 4)
        saved = self.ventures.action(dict(mode='team', organization_id='org', venture_id=record['id'],
                                          confirmed=True), 'admin', 'admin')
        self.assertEqual(saved['group_id'], 'g' * 32)
        self.assertEqual(len([profile for profile in self.profiles if profile.get('role') in engine_roles()]), 4)

    def test_promote_asks_before_replacing_another_discovery_repository(self):
        first = self.attach_group(self.create())
        self.service.discovery.action(dict(mode='configure', organization_id='org', product_brief='Main product',
            discovery_focus=['problem'], do_not_propose='', discovery_group_id='g' * 32,
            discovery_repository=first['notebook']), 'admin', 'admin')
        second = self.create(request_id='second-venture', name='Second Desk')
        self.profiles.append(dict(id='s' * 32, name='Builder', organization_id='org', use_worktree=True,
                                  project=str(self.projects / second['notebook'])))
        saved = self.ventures.get(second['id'], 'org')
        saved.update(state='qualified', qualified_at=ventures.now().isoformat())
        self.ventures.save(saved)
        body = dict(mode='promote', organization_id='org', venture_id=second['id'])
        with self.assertRaisesRegex(ValueError, 'currently uses'):
            self.ventures.action(body, 'admin', 'admin')
        promoted = self.ventures.action(dict(body, replace_discovery=True), 'admin', 'admin')
        self.assertEqual(promoted['promotion']['previous']['discovery_repository'], first['notebook'])
        self.assertEqual(self.service.discovery.policy('org')['discovery_repository'], second['notebook'])

    def test_team_can_choose_a_template_per_role(self):
        record = self.create()
        notebook = self.projects / record['notebook']
        self.profiles[:] = [
            dict(id='t' * 32, name='Alpha agent', organization_id='org', project=str(notebook), use_worktree=True,
                 runtime='claude', provider='anthropic', model='alpha-model', permission_mode='default',
                 accessible_paths=[]),
            dict(id='u' * 32, name='Beta agent', organization_id='org', project=str(notebook), use_worktree=True,
                 runtime='codex', provider='', model='beta-model', permission_mode='default', accessible_paths=[]),
            dict(id='v' * 32, name='Gamma agent', organization_id='org', project=str(notebook), use_worktree=True,
                 runtime='codex', provider='', model='gamma-model', permission_mode='default', accessible_paths=[]),
        ]
        saved = self.ventures.action(dict(mode='team', organization_id='org', venture_id=record['id'],
                                          confirmed=True, templates={'analyst': 't' * 32, 'founder': 'u' * 32,
                                                                    'red_team': 'v' * 32, 'outreach': 'u' * 32}),
                                     'admin', 'admin')
        self.assertEqual(saved['team']['settings_by_role']['analyst']['model'], 'alpha-model')
        self.assertEqual(saved['team']['settings_by_role']['founder']['model'], 'beta-model')
        self.assertEqual(saved['team']['settings_by_role']['red_team']['model'], 'gamma-model')
        self.assertEqual(saved['team']['template_ids']['analyst'], 't' * 32)
        self.assertTrue(any(agent['id'] == 't' * 32 for agent in self.ventures.snapshot()['agents']))

    def test_promote_points_discovery_at_the_notebook_and_seeds_the_brief(self):
        record = self.attach_group(self.create())
        self.service.discovery.action(dict(mode='configure', organization_id='org', product_brief='Existing brief',
            discovery_focus=['problem'], do_not_propose='', discovery_group_id='g' * 32,
            discovery_repository=record['notebook']), 'admin', 'admin')
        saved = self.ventures.get(record['id'], 'org')
        saved.update(state='qualified', qualified_at=ventures.now().isoformat())
        self.ventures.save(saved)
        promoted = self.ventures.action(dict(mode='promote', organization_id='org', venture_id=record['id']),
                                        'admin', 'admin')
        self.assertTrue(promoted['promoted_at'])
        policy = self.service.discovery.policy('org')
        self.assertEqual(policy['discovery_repository'], record['notebook'])
        self.assertIn('Settlement Desk', policy['product_brief'])

    def test_live_fx_prices_the_tick_and_falls_back_to_the_manual_rate(self):
        import venture_fx
        configuration = json.loads(json.dumps(self.configuration))
        configuration['market'].update(fx_source='ecb', fx_rate=140)
        configuration['simulation']['burn'] = [dict(label='cloud', amount=300, currency='USD')]
        quiet = dict(status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                     items=[], etag='', modified='')
        record = self.create(configuration=configuration)
        live = dict(source='ecb', date='2026-11-13', rates={'EUR': 1.0, 'USD': 1.08, 'JPY': 162.0},
                    fetched_at=ventures.now().isoformat())
        with patch.object(venture_feeds, 'fetch', return_value=quiet), \
                patch.object(venture_fx, 'fetch', return_value=live) as fetch:
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']),
                                        'admin', 'admin')
        self.assertEqual(tick['ledger']['fx']['rate'], 150.0)
        self.assertEqual(tick['ledger']['burn'], 1500)  # 300 USD x 150 / 30
        self.assertEqual(fetch.call_count, 1)
        other = self.create(request_id='fx-fallback', name='Fallback Desk', configuration=configuration)
        with closing(self.service.connect()) as db, db:
            db.execute('DELETE FROM venture_fx')
        with patch.object(venture_feeds, 'fetch', return_value=quiet), \
                patch.object(venture_fx, 'fetch', side_effect=ValueError('FX request failed')):
            fallback = self.ventures.action(dict(mode='run', organization_id='org', venture_id=other['id']),
                                            'admin', 'admin')
        self.assertTrue(fallback['ledger']['fx']['fallback'])
        self.assertEqual(fallback['ledger']['burn'], 1400)  # Manual 140

    def test_active_venture_limit_is_editable_per_organization(self):
        with self.assertRaises(ValueError):
            self.ventures.action(dict(mode='limits', organization_id='org', max_active_ventures=0), 'admin', 'admin')
        result = self.ventures.action(dict(mode='limits', organization_id='org', max_active_ventures=1),
                                      'admin', 'admin')
        self.assertEqual(result['max_active_ventures'], 1)
        self.assertEqual(self.ventures.snapshot()['limits']['organizations']['org']['max_active_ventures'], 1)
        first = self.create(request_id='cap-one', name='Cap one')
        second = self.create(request_id='cap-two', name='Cap two')
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[], etag='', modified='')):
            self.ventures.action(dict(mode='run', organization_id='org', venture_id=first['id']), 'admin', 'admin')
            with self.assertRaisesRegex(ValueError, 'limit'):
                self.ventures.action(dict(mode='run', organization_id='org', venture_id=second['id']),
                                     'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.ventures.action(dict(mode='limits', organization_id='org', max_active_ventures=5), 'viewer', 'operator')

    def test_resume_cannot_take_a_venture_past_the_active_limit(self):
        self.ventures.action(dict(mode='limits', organization_id='org', max_active_ventures=1), 'admin', 'admin')
        first = self.create(request_id='cap-one', name='Cap one')
        second = self.create(request_id='cap-two', name='Cap two')
        self.ventures.action(dict(mode='resume', organization_id='org', venture_id=first['id']), 'admin', 'admin')
        # Drafts may still be created; only becoming active is capped.
        for mode in ('resume', 'pause'):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, 'active venture limit of 1'):
                    self.ventures.action(dict(mode=mode, organization_id='org', venture_id=second['id']),
                                         'admin', 'admin')
        self.assertEqual(self.ventures.get(second['id'], 'org')['state'], 'draft')
        self.assertEqual(len(self.ventures._active('org')), 1)
        # Pausing and resuming the active one is not a new place.
        self.ventures.action(dict(mode='pause', organization_id='org', venture_id=first['id']), 'admin', 'admin')
        self.ventures.action(dict(mode='resume', organization_id='org', venture_id=first['id']), 'admin', 'admin')
        self.assertEqual(self.ventures.get(first['id'], 'org')['state'], 'running')
        # Archiving frees the place.
        self.ventures.action(dict(mode='archive', organization_id='org', venture_id=first['id']), 'admin', 'admin')
        self.ventures.action(dict(mode='resume', organization_id='org', venture_id=second['id']), 'admin', 'admin')
        self.assertEqual(self.ventures.get(second['id'], 'org')['state'], 'running')

    def test_an_older_venture_still_opens_and_latest_ticks_load_in_one_query(self):
        old = self.create(request_id='old-venture', name='Old venture')
        with closing(self.service.connect()) as db, db:
            for index in range(100):
                identity = uuid.uuid4().hex
                db.execute('INSERT INTO ventures VALUES (?,?,?)', (identity, 'org', json.dumps(dict(
                    self.ventures.get(old['id'], 'org'), id=identity, name='Filler ' + str(index)))))
        snapshot = self.ventures.snapshot(old['id'])
        self.assertNotIn(old['id'], [item['id'] for item in snapshot['ventures']])
        self.assertEqual(snapshot['selected']['id'], old['id'])
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.ventures.snapshot('f' * 32)
        statements = []
        connect = self.service.connect

        def counting():
            db = connect()
            db.set_trace_callback(statements.append)
            return db
        with patch.object(self.service, 'connect', counting):
            self.ventures.snapshot()
        # One query for every venture's latest tick, not one per venture.
        self.assertEqual(len([sql for sql in statements if 'FROM venture_ticks' in sql and 'venture_id' in sql]), 1)

    def test_a_busy_venture_or_studio_does_not_hold_up_the_poll_thread(self):
        from contributions import Busy
        record = self.create()
        tick = dict(id=uuid.uuid4().hex, venture_id=record['id'], organization_id='org', state='meeting',
                    job_id='missing')
        self.ventures.save_tick(tick)
        calls = []

        @contextmanager
        def operation(key, timeout=30):
            calls.append((key, timeout))
            if key.startswith('venture:'):
                raise Busy('held elsewhere')
            yield
        with patch.object(self.service, 'operation', operation):
            self.ventures.advance()
        self.assertIn(('venture:' + record['id'], 0), calls)
        # Skipped this pass, untouched for the next one.
        with closing(self.service.connect()) as db:
            saved = json.loads(db.execute('SELECT data FROM venture_ticks WHERE id=?', (tick['id'],)).fetchone()[0])
        self.assertEqual(saved['state'], 'meeting')

    def test_restore_promotion_restores_the_previous_discovery_configuration(self):
        record = self.attach_group(self.create())
        existing = self.projects / 'existing'
        existing.mkdir()
        project_git.git(existing, 'init', '-b', 'main')
        (existing / 'README.md').write_text('# Existing project\n', encoding='utf-8')
        project_git.git(existing, 'add', '-A')
        project_git.git(existing, 'commit', '-m', 'base')
        self.profiles.append(dict(id='w' * 32, name='Discovery worker', organization_id='org',
                                  project=str(existing), use_worktree=True, runtime='codex'))
        self.service.discovery.action(dict(mode='configure', organization_id='org', product_brief='Original brief',
            discovery_focus=['problem'], do_not_propose='', discovery_group_id='g' * 32,
            discovery_repository='existing'), 'admin', 'admin')
        saved = self.ventures.get(record['id'], 'org')
        saved.update(state='qualified', qualified_at=ventures.now().isoformat())
        self.ventures.save(saved)
        self.ventures.action(dict(mode='promote', organization_id='org', venture_id=record['id'],
                                  replace_discovery=True), 'admin', 'admin')
        restored = self.ventures.action(dict(mode='restore_promotion', organization_id='org',
                                             venture_id=record['id'], confirmed=True), 'admin', 'admin')
        self.assertNotIn('promoted_at', restored)
        policy = self.service.discovery.policy('org')
        self.assertEqual(policy['discovery_repository'], 'existing')
        self.assertEqual(policy['product_brief'], 'Original brief')

    def test_venture_at_the_active_limit_can_still_run_its_own_tick(self):
        records = [self.create(request_id='limit-' + str(index), name='Venture ' + str(index)) for index in range(3)]
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[], etag='', modified='')):
            for record in records:
                self.ventures.action(dict(mode='resume', organization_id='org', venture_id=record['id']), 'admin', 'admin')
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=records[0]['id']), 'admin', 'admin')
            self.assertTrue(tick['quiet'])
            extra = self.create(request_id='limit-extra', name='Venture extra')
            with self.assertRaisesRegex(ValueError, 'limit'):
                self.ventures.action(dict(mode='run', organization_id='org', venture_id=extra['id']), 'admin', 'admin')

    def news(self, *items):
        return patch.object(venture_feeds, 'fetch', return_value=dict(
            status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
            items=list(items), etag='', modified=''))

    def run_tick(self, record):
        return self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']), 'admin', 'admin')

    def test_module_switch_stops_everything_and_keeps_the_ventures(self):
        record = self.create()
        self.assertTrue(self.ventures.snapshot()['enabled'])
        self.assertIn('api', self.ventures.snapshot()['options']['architectures'])
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.ventures.action(dict(mode='module', enabled=False), 'viewer', 'operator')
        state = self.ventures.action(dict(mode='module', enabled=False), 'admin', 'admin')
        self.assertFalse(state['enabled'])
        self.assertEqual(set(self.ventures.snapshot()), {'enabled', 'module'})
        with self.assertRaisesRegex(ValueError, 'switched off'):
            self.run_tick(record)
        with patch.object(self.ventures, 'advance') as advance:
            self.service.advance_modules()
        advance.assert_not_called()
        self.ventures.action(dict(mode='module', enabled=True), 'admin', 'admin')
        self.assertEqual(self.ventures.snapshot()['ventures'][0]['id'], record['id'])
        with patch.object(self.ventures, 'advance') as advance:
            self.service.advance_modules()
        advance.assert_called_once()

    def test_a_module_fault_never_reaches_the_poll_loop(self):
        with patch.object(self.ventures, 'advance', side_effect=KeyError('configuration')):
            self.service.advance_modules()  # Not ValueError/OSError: this used to end the poll thread.
        self.assertIn('KeyError', self.ventures.snapshot()['module']['fault']['error'])
        self.service.advance_modules()
        self.assertNotIn('fault', self.ventures.snapshot()['module'])

    def test_scheduled_ticks_fetch_on_the_background_thread_only(self):
        record = self.attach_group(self.create())
        self.configure(record, daily_ticks=True, tick_hour_utc=(ventures.now().hour + 1) % 24)
        saved = self.ventures.get(record['id'], 'org')
        saved['next_at'] = (ventures.now() - timedelta(minutes=1)).isoformat()
        self.ventures.save(saved)
        handed_off = []
        self.ventures.background = handed_off.append
        with self.news(feed_item(venture_feeds.now().isoformat())) as fetch:
            self.ventures.advance()
            self.ventures.advance()  # Still refreshing: no second hand-off, no tick.
            self.assertEqual((len(handed_off), fetch.call_count, self.jobs), (1, 0, []))
            handed_off[0]()  # The background thread's work.
            self.assertEqual(fetch.call_count, 1)
            self.ventures.advance()
            self.assertEqual((len(self.jobs), fetch.call_count), (1, 1))

    def test_a_failed_quiet_tick_can_be_recovered(self):
        import venture_notebook
        record = self.create()
        with self.news(), patch.object(venture_notebook, 'commit_tick', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_tick(record)
        tick = self.ventures.tick_for(record['id'], 'org', ventures.now().date().isoformat())
        self.assertEqual((tick['state'], tick['quiet']), ('needs_attention', True))
        recovered = self.ventures.action(dict(mode='recover', organization_id='org', venture_id=record['id'],
                                              inspected=True), 'admin', 'admin')
        self.assertEqual(recovered['state'], 'ready')
        self.assertEqual(recovered['ledger']['capital_after'], 99000)
        self.assertEqual(self.ventures.get(record['id'], 'org')['capital'], 99000)
        self.assertFalse(self.store.action.called)

    def test_quiet_days_do_not_qualify_a_venture(self):
        configuration = json.loads(json.dumps(self.configuration))
        configuration['simulation'].update(consecutive_days=1, survival_threshold=10)
        record = self.create(configuration=configuration)
        with self.news():
            tick = self.run_tick(record)
        self.assertGreaterEqual(tick['ledger']['score'], 10)
        self.assertFalse(tick['ledger']['judged'])
        self.assertEqual(self.ventures.get(record['id'], 'org')['state'], 'running')

    def test_an_item_stored_after_a_tick_is_offered_once_to_the_next(self):
        record = self.attach_group(self.create())
        with self.news():
            self.run_tick(record)  # Quiet tick today; the item below is stored after it.
        self.assertEqual(self.ventures.get(record['id'], 'org')['feed_cursor'], 0)
        tick = self.ventures.tick_for(record['id'], 'org', ventures.now().date().isoformat())
        late = feed_item((ventures.moment(tick['simulated_at']) - timedelta(minutes=10)).isoformat())
        with closing(self.service.connect()) as db, db:
            db.execute('INSERT INTO venture_feed_items VALUES (?,?,?)', ('org', late['id'], json.dumps(late)))
        sources = ventures.normalize_config(self.configuration)['sources']
        # Published before the previous tick, so a publish-time comparison lost it.
        items, cursor = self.ventures._recent_items('org', tick['simulated_at'], sources, 0)
        self.assertEqual(([item['id'] for item in items], cursor > 0), ([late['id']], True))
        self.assertEqual(self.ventures._recent_items('org', tick['simulated_at'], sources, cursor), ([], cursor))

    def test_pruning_never_lets_a_new_item_reuse_a_cursor_row_id(self):
        old = (ventures.now() - timedelta(days=120)).isoformat()
        stale = [dict(feed_item(old), id=str(index) * 64, link='https://news.example/' + str(index))
                 for index in range(1, 4)]
        with closing(self.service.connect()) as db, db:
            for item in stale:
                db.execute('INSERT INTO venture_feed_items VALUES (?,?,?)', ('org', item['id'], json.dumps(item)))
            cursor = db.execute('SELECT MAX(rowid) FROM venture_feed_items').fetchone()[0]
        self.ventures._prune('org')
        with closing(self.service.connect()) as db, db:
            kept = [row[0] for row in db.execute('SELECT id FROM venture_feed_items ORDER BY rowid')]
            fresh = feed_item(ventures.now().isoformat())
            db.execute('INSERT INTO venture_feed_items VALUES (?,?,?)', ('org', fresh['id'], json.dumps(fresh)))
            fresh_row = db.execute('SELECT rowid FROM venture_feed_items WHERE id=?', (fresh['id'],)).fetchone()[0]
        self.assertEqual(kept, [stale[-1]['id']])  # Old rows go, except the highest row id.
        self.assertGreater(fresh_row, cursor)
        sources = ventures.normalize_config(self.configuration)['sources']
        items, _ = self.ventures._recent_items('org', '', sources, cursor)
        self.assertEqual([item['id'] for item in items], [fresh['id']])

    def test_a_qualified_venture_can_move_its_outreach_stage_but_not_its_rules(self):
        record = self.attach_group(self.create())
        saved = self.ventures.get(record['id'], 'org')
        saved.update(state='qualified', qualified_at=ventures.now().isoformat())
        self.ventures.save(saved)
        change = lambda **layers: self.ventures.action(dict(mode='configure', organization_id='org',
                                                           venture_id=record['id'], **layers), 'admin', 'admin')
        staged = change(outreach=dict(self.configuration['outreach'], stage='paid_pilot'),
                        simulation=dict(self.configuration['simulation'], daily_ticks=True))
        self.assertEqual(staged['configuration']['outreach']['stage'], 'paid_pilot')
        self.assertTrue(staged['configuration']['simulation']['daily_ticks'])
        for layers in (dict(simulation=dict(self.configuration['simulation'], survival_threshold=1)),
                       dict(market=dict(self.configuration['market'], sales_cycle=1))):
            with self.assertRaisesRegex(ValueError, 'qualified under'):
                change(**layers)

    def complete_studio(self, run, value):
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        job.update(state='artifact_ready', result='```json\n' + json.dumps(value) + '\n```')
        self.ventures.advance()
        with closing(self.service.connect()) as db:
            return json.loads(db.execute('SELECT data FROM venture_studio_runs WHERE id=?',
                                         (run['id'],)).fetchone()[0])
    def test_every_day_strategy_review_uses_internal_evidence_and_is_not_judged(self):
        record = self.attach_group(self.create())
        self.configure(record, meeting_cadence='every_day')
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[], etag='', modified='')):
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']),
                                        'admin', 'admin')
        self.assertEqual(tick['review_kind'], 'strategy')
        prompt = next(job for job in self.jobs if job['id'] == tick['job_id'])['prompt']
        self.assertIn('VENTURE STRATEGY REVIEW', prompt)
        self.assertIn('S1', prompt)
        review = self.ventures.review_for(record['id'], 'org', ventures.now().date().isoformat())
        self.assertEqual(review['state'], 'meeting')
        completed = self.complete(record, strategy_result())
        self.assertEqual(completed['state'], 'ready')
        self.assertFalse(completed['judged'])
        review = self.ventures.review_for(record['id'], 'org', ventures.now().date().isoformat())
        self.assertEqual(review['state'], 'ready')

    def test_a_skipped_review_can_be_retried_on_the_same_day(self):
        record = self.create()
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            with self.assertRaisesRegex(ValueError, 'group'):
                self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']),
                                     'admin', 'admin')
        skipped = self.ventures.review_for(record['id'], 'org', ventures.now().date().isoformat())
        self.assertEqual(skipped['state'], 'skipped')
        self.assertIn('group', skipped['reason'].lower())
        self.attach_group(record)
        self.configure(record, meeting_cadence='every_day')
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[], etag='', modified='')):
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']),
                                        'admin', 'admin')
        self.assertIn(tick['review_kind'], ('news', 'strategy'))
        review = self.ventures.review_for(record['id'], 'org', ventures.now().date().isoformat())
        self.assertEqual(review['state'], 'meeting')
        self.assertEqual(review['tick_id'], tick['id'])

    def test_studio_setup_provisions_a_notebook_and_group(self):
        self.profiles[:] = [dict(id='z' * 32, name='Template', organization_id='org',
                                 project=str(self.projects), use_worktree=True, runtime='codex', model='m')]
        studio = self.ventures.action(dict(mode='studio_setup', organization_id='org', confirmed=True),
                                      'admin', 'admin')
        self.assertTrue(studio['group_id'])
        self.assertTrue((self.projects / studio['notebook'] / 'studio.md').is_file())
        self.assertEqual(len(self.groups), 1)
        self.assertEqual(len(self.groups[0]['members']), 4)

    def test_scout_candidate_approval_creates_one_linked_venture(self):
        self.setup_studio_manual()
        record = self.add_venture_group(self.create())
        self.ventures.action(dict(mode='studio_policy', organization_id='org',
                                  studio_sources=['https://news.example/feed'],
                                  studio_max_candidates=2), 'admin', 'admin')
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[feed_item(venture_feeds.now().isoformat())], etag='', modified='')):
            run = self.ventures.action(dict(mode='studio_run', organization_id='org', kind='scout'),
                                       'admin', 'admin')
        completed = self.complete_studio(run, {'candidates': [candidate()]})
        self.assertEqual(completed['state'], 'ready', completed.get('error'))
        with closing(self.service.connect()) as db:
            row = db.execute('SELECT data FROM venture_candidates').fetchone()
        stored = json.loads(row[0])
        self.assertEqual(stored['state'], 'proposed')
        approved = self.ventures.action(dict(mode='candidate_decide', organization_id='org',
                                             candidate_id=stored['id'], decision='approve', confirmed=True),
                                        'admin', 'admin')
        self.assertEqual(approved['state'], 'created')
        created = self.ventures.get(approved['venture_id'], 'org')
        self.assertEqual(created['source']['candidate_id'], stored['id'])
        self.assertTrue((self.projects / created['notebook'] / 'venture.md').is_file())

    def test_portfolio_review_requires_every_active_venture(self):
        self.setup_studio_manual()
        record = self.add_venture_group(self.create())
        run = self.ventures.action(dict(mode='studio_run', organization_id='org', kind='portfolio'),
                                   'admin', 'admin')
        completed = self.complete_studio(run, {
            'summary': 'Keep building.',
            'recommendations': [dict(venture_id=record['id'], rank=1, recommendation='continue',
                                     reason='Evidence is thin but the thesis is coherent.', evidence=['V1'])],
        })
        self.assertEqual(completed['state'], 'ready', completed.get('error'))
        self.assertEqual(completed['recommendations'][0]['venture_id'], record['id'])
        with self.assertRaisesRegex(ValueError, 'every active venture'):
            self.ventures._studio_parse(dict(run, active_venture_ids=[record['id']]),
                                        {'result': '```json\n' + json.dumps({'summary': 'x', 'recommendations': []}) + '\n```'})

    def test_studio_policy_is_bounded_and_reports_projection(self):
        with self.assertRaisesRegex(ValueError, 'cadence'):
            self.ventures.action(dict(mode='studio_policy', organization_id='org',
                                      studio_portfolio_cadence='hourly'), 'admin', 'admin')
        with self.assertRaises(ValueError):
            self.ventures.action(dict(mode='studio_policy', organization_id='org',
                                      studio_sources=['http://news.example/feed']), 'admin', 'admin')
        policy = self.ventures.action(dict(mode='studio_policy', organization_id='org',
                                           studio_portfolio_cadence='daily',
                                           studio_max_candidates=3,
                                           studio_candidate_backlog=5), 'admin', 'admin')
        self.assertEqual(policy['studio_portfolio_cadence'], 'daily')
        summary = self.ventures._studio_summary('org')
        self.assertIn('daily_meetings', summary['budget'])
    def live_studio_sessions(self, screens):
        """A persona_sent session for every studio agent; ``screens`` maps a
        profile id to what its terminal shows."""
        for profile in self.profiles:
            self.jobs.append(dict(id=uuid.uuid4().hex, kind='launch', state='persona_sent', organization_id='org',
                                  profile_id=profile['id'], alias='alias-' + profile['id'][:4]))
        aliases = {'alias-' + identity[:4]: screen for identity, screen in screens.items()}

        def command(*args, **kwargs):
            if args[:2] == ('agent', 'read'):
                return dict(output=aliases.get(args[2], 'Waiting for input'))
            return {}
        self.store.command.side_effect = command
        return aliases

    @contextmanager
    def usage_clock(self):
        start = ventures.now()
        elapsed = timedelta()

        def advance(delta):
            nonlocal elapsed
            elapsed += delta

        with patch.object(venture_studio, 'datetime', wraps=datetime) as wall, \
                patch.object(venture_studio, 'time') as clock:
            wall.now.side_effect = lambda tz: (start + elapsed).astimezone(tz)
            clock.monotonic.side_effect = lambda: elapsed.total_seconds()
            yield advance

    def test_a_scheduled_usage_hold_rechecks_and_releases_early(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_portfolio_cadence='daily')
        screens = self.live_studio_sessions({'r' * 32: (
            '5 hour usage limit reached. It will reset in 4 hours 31 minutes. [retrying in 4h 28m attempt #1]')})
        self.due('portfolio')
        with self.usage_clock() as elapse:
            self.ventures.advance()
            self.assertEqual(self.studio_runs(), [])  # Nothing started while the Skeptic cannot answer.
            policy = self.ventures._studio_policy('org')
            self.assertIn('Skeptic hit its usage limit', policy['studio_portfolio_waiting'])
            self.assertIn('Estimated provider reset', policy['studio_portfolio_waiting'])
            self.assertIn('Next CLI retry', policy['studio_portfolio_waiting'])
            self.assertFalse(policy.get('studio_portfolio_failures'))
            hold = self.ventures._studio_summary('org')['usage_holds']['portfolio']
            self.assertEqual(hold['name'], 'Skeptic')
            self.assertEqual(datetime.fromisoformat(hold['reset_at']) - datetime.fromisoformat(hold['retry_at']),
                             timedelta(minutes=3))
            with self.assertRaisesRegex(ValueError, 'Skeptic hit its usage limit'):
                self.studio(mode='studio_run', kind='portfolio')
            elapse(timedelta(minutes=1))
            self.ventures.advance()
            self.assertEqual(self.ventures._studio_summary('org')['usage_holds']['portfolio'], hold)
            # The real scheduler rechecks and starts early when the screen clears.
            screens.clear()
            elapse(timedelta(minutes=1))
            self.ventures.advance()
            self.ventures.advance()
            self.assertEqual([run['state'] for run in self.studio_runs()], ['meeting'])
            self.assertEqual(self.ventures._studio_policy('org')['studio_portfolio_waiting'], '')

    def test_a_static_usage_countdown_does_not_postpone_a_due_run_past_its_reset(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_portfolio_cadence='daily')
        self.live_studio_sessions({'r' * 32: 'Usage limit reached. It will reset in 2 hours.'})
        self.due('portfolio')
        with self.usage_clock() as elapse:
            self.ventures.advance()
            self.assertEqual(self.studio_runs(), [])
            elapse(timedelta(hours=2, seconds=1))
            self.ventures.advance()
            self.assertEqual([run['state'] for run in self.studio_runs()], ['meeting'])

    def test_a_limit_with_no_stated_reset_holds_briefly_and_a_stale_screen_stops_holding(self):
        self.setup_studio_manual()
        self.live_studio_sessions({'q' * 32: "You've hit your usage limit. Try again at 3:05 PM."})
        targets = self.ventures._usage_targets('org', 'portfolio')
        with self.usage_clock() as elapse:
            self.ventures._usage_probe(targets)
            hold = self.ventures._usage_hold(targets)
            self.assertIsNone(hold['reset_at'])
            self.assertIsNone(hold['retry_at'])
            elapse(timedelta(hours=7))
            self.ventures._usage_probe(targets)
            self.assertIsNone(self.ventures._usage_hold(targets))

    def test_due_scout_checks_advisors_even_after_a_portfolio_probe(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        panel = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        self.studio(mode='studio_policy', studio_scout_cadence='daily')
        advisor = panel['members']['investor']
        self.live_studio_sessions({advisor: 'Usage limit reached. It will reset in 2 hours.'})
        self.due('scout')
        portfolio = self.ventures._usage_targets('org', 'portfolio')
        with self.usage_clock():
            self.assertTrue(self.ventures._usage_probe_soon(portfolio))
            self.ventures.advance()
            self.assertEqual(self.studio_runs(), [])
            self.assertIn('Investor (stakeholder) hit its usage limit',
                          self.ventures._studio_policy('org')['studio_scout_waiting'])
            holds = self.ventures._studio_summary('org')['usage_holds']
            self.assertNotIn('portfolio', holds)
            self.assertEqual(holds['scout']['profile_id'], advisor)
            self.assertEqual(self.studio(mode='studio_run', kind='portfolio')['state'], 'meeting')

    def test_a_meeting_waiting_on_a_limited_agent_says_so(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='portfolio')
        self.live_studio_sessions({'r' * 32: 'usage limit reached. It will reset in 2 hours.'})
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        job.update(state='waiting_for_members', progress='Skeptic: waiting for current execution')
        self.ventures.advance()
        waiting = self.studio_runs()[0]['waiting']
        self.assertEqual((waiting['category'], waiting['agent']), ('usage', 'Skeptic'))
        self.assertIn('automatically', waiting['help'])

    def test_a_waiting_scout_uses_its_frozen_advisors_after_the_setting_changes(self):
        studio = self.setup_studio_manual()
        panel = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        advisor = panel['members']['investor']
        run = self.studio(mode='studio_run', kind='scout')
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        studio_group = next(group for group in self.groups if group['id'] == studio['group_id'])
        job.update(state='waiting_for_members', progress='Waiting for current execution',
                   group=dict(studio_group, members=studio_group['members'] + job['advisors']))
        self.setup_panel(scout_consult=False)
        self.live_studio_sessions({advisor: 'Usage limit reached. It will reset in 2 hours.'})
        self.ventures.advance_studio()
        waiting = self.studio_runs()[0]['waiting']
        self.assertEqual((waiting['category'], waiting['profile_id']), ('usage', advisor))
        self.assertNotIn('scout', self.ventures._studio_summary('org')['usage_holds'])

    def test_a_scout_whose_skeptic_never_spoke_is_flagged_and_never_created_unattended(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        studio = self.ventures._studio('org')
        studio['team'] = dict(members=dict(portfolio='p' * 32, scout='q' * 32, critic='r' * 32, analyst='s' * 32))
        self.ventures.save_studio('org', studio)
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=0,
                    studio_sources=['https://news.example/feed'], studio_max_candidates=3)
        with self.news(feed_item(venture_feeds.now().isoformat())):
            run = self.studio(mode='studio_run', kind='scout')
        # The result is judged against the Skeptic who was invited. Saving a
        # different Skeptic now should not change how we read that transcript.
        self.assertEqual(run['skeptic_id'], 'r' * 32)
        studio = self.ventures._studio('org')
        studio['team']['members']['critic'] = 's' * 32
        self.ventures.save_studio('org', studio)
        self.live_studio_sessions({'r' * 32: 'Usage limit reached. It will reset in 2 hours.'})
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        job.update(state='artifact_ready', result='```json\n' + json.dumps(dict(
            candidates=[candidate('Carrier Desk', 'N1')])) + '\n```',
            contributions=[dict(profile_id='s' * 32, name='Research Analyst', round=1, content='Carrier Desk.')])
        self.ventures.advance()
        done = next(item for item in self.studio_runs() if item['id'] == run['id'])
        self.assertTrue(done['skeptic_silent'])
        self.assertEqual(done['skeptic_usage']['profile_id'], 'r' * 32)
        summary = self.ventures._studio_summary('org')['runs'][0]
        self.assertTrue(summary['skeptic_silent'])
        self.assertEqual(summary['skeptic_usage']['profile_id'], 'r' * 32)
        stored, = [item for item in self.stored_candidates() if item['name'] == 'Carrier Desk']
        self.assertTrue(stored['skeptic_silent'])
        self.assertEqual(stored['state'], 'proposed')
        self.assertIn('Skeptic did not speak', json.dumps(done.get('auto') or []))

    def test_studio_schedule_starts_one_due_run_and_advances_the_window(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.ventures.action(dict(mode='studio_policy', organization_id='org',
                                  studio_portfolio_cadence='daily'), 'admin', 'admin')
        saved = self.ventures._studio_policy('org')
        saved['studio_next_portfolio_at'] = (ventures.now() - timedelta(minutes=1)).isoformat()
        self.service.patch_policy('org', {'studio_next_portfolio_at': saved['studio_next_portfolio_at']}, 'test')
        self.ventures.advance()
        with closing(self.service.connect()) as db:
            run = json.loads(db.execute('SELECT data FROM venture_studio_runs').fetchone()[0])
        self.assertEqual(run['kind'], 'portfolio')
        self.assertEqual(run['state'], 'meeting')
        self.assertGreater(ventures.moment(self.ventures._studio_policy('org')['studio_next_portfolio_at']),
                           ventures.now())

    def test_candidate_duplicates_are_excluded(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.ventures.action(dict(mode='studio_policy', organization_id='org',
                                  studio_sources=['https://news.example/feed']), 'admin', 'admin')
        run = self.ventures.action(dict(mode='studio_run', organization_id='org', kind='scout'),
                                   'admin', 'admin')
        completed = self.complete_studio(run, {'candidates': [candidate('Settlement Desk')]})
        self.assertEqual(completed['state'], 'ready')
        with closing(self.service.connect()) as db:
            saved = json.loads(db.execute('SELECT data FROM venture_candidates').fetchone()[0])
        self.assertEqual(saved['state'], 'excluded')
        self.assertTrue(saved['duplicate_of'])
    def test_strategy_result_rejects_news_or_demand(self):
        record = self.attach_group(self.create())
        self.configure(record, meeting_cadence='every_day')
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[], etag='', modified='')):
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']),
                                        'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'invent news'):
            self.ventures.parse(dict(tick, result=json.dumps(dict(
                strategy_result(), events=[{'evidence': ['S1'], 'summary': 'invented', 'direction': 'tailwind'}]))))
        with self.assertRaisesRegex(ValueError, 'demand'):
            self.ventures.parse(dict(tick, result=json.dumps(dict(
                strategy_result(), deltas={'demand': 1, 'competition': 0, 'regulatory': 0, 'delivery_risk': 0}))))

    def test_strategy_outreach_requires_a_recorded_outcome(self):
        record = self.attach_group(self.create())
        self.configure(record, meeting_cadence='every_day')
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[], etag='', modified='')):
            tick = self.ventures.action(dict(mode='run', organization_id='org', venture_id=record['id']),
                                        'admin', 'admin')
        bad = strategy_result()
        bad['outreach'] = [{'persona': 'cfo', 'language': 'ja', 'subject': 'x', 'body': 'y', 'evidence': ['S1']}]
        with self.assertRaisesRegex(ValueError, 'wrong kind'):
            self.ventures.parse(dict(tick, result=json.dumps(bad)))
    def test_operator_can_record_a_disproved_assumption(self):
        record = self.create(assumptions=['Carriers share invoice data.'])
        saved = self.ventures.action(dict(mode='assumptions', organization_id='org', venture_id=record['id'],
                                          failed=['Carriers share invoice data.'], note='Two pilots refused.'),
                                     'admin', 'admin')
        self.assertEqual(saved['assumption_outcomes'][0]['failed'], ['Carriers share invoice data.'])
    def test_scheduler_starts_a_strategy_review_on_a_quiet_every_day_venture(self):
        record = self.attach_group(self.create())
        self.configure(record, daily_ticks=True, meeting_cadence='every_day',
                       tick_hour_utc=(ventures.now().hour + 1) % 24)
        saved = self.ventures.get(record['id'], 'org')
        saved['next_at'] = (ventures.now() - timedelta(minutes=1)).isoformat()
        self.ventures.save(saved)
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[], etag='', modified='')):
            self.ventures.advance()
        tick = self.ventures.tick_for(record['id'], 'org', ventures.now().date().isoformat())
        self.assertEqual(tick['review_kind'], 'strategy')
        review = self.ventures.review_for(record['id'], 'org', ventures.now().date().isoformat())
        self.assertEqual(review['state'], 'meeting')
    def test_create_and_configure_are_admin_only(self):
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.ventures.action(dict(mode='create', organization_id='org', name='x'), 'viewer', 'operator')
        record = self.create()
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.ventures.action(dict(mode='pause', organization_id='org', venture_id=record['id']), 'viewer', 'operator')

    # -- Studio resilience -------------------------------------------------

    def studio(self, **body):
        return self.ventures.action(dict(organization_id='org', **body), 'admin', 'admin')

    def studio_runs(self):
        with closing(self.service.connect()) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM venture_studio_runs ORDER BY rowid')]

    @staticmethod
    def instructions(run):
        """A scout's prompt and its answer rules file, as the chair reads them."""
        rules = Path(run['evidence_file']).with_name(ventures.STUDIO_RULES_FILE)
        return run['prompt'] + '\n' + rules.read_text(encoding='utf-8')

    def due(self, *kinds):
        self.service.patch_policy('org', {'studio_next_' + kind + '_at':
                                          (ventures.now() - timedelta(minutes=1)).isoformat() for kind in kinds}, 'test')

    def scout_with(self, *proposals):
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'], studio_max_candidates=3)
        with self.news(feed_item(venture_feeds.now().isoformat())):
            run = self.studio(mode='studio_run', kind='scout')
        return self.complete_studio(run, {'candidates': list(proposals)})

    def stored_candidates(self):
        with closing(self.service.connect()) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM venture_candidates ORDER BY rowid')]

    def test_a_rejected_studio_result_can_be_dismissed_and_never_blocks_new_runs(self):
        self.setup_studio_manual()
        record = self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='portfolio')
        bad = self.complete_studio(run, {'summary': 'x', 'recommendations': []})
        self.assertEqual(bad['state'], 'needs_attention')
        with self.assertRaisesRegex(ValueError, 'dismiss'):
            self.studio(mode='studio_recover', run_id=run['id'], inspected=True)
        # A run needing attention no longer blocks the studio.
        second = self.studio(mode='studio_run', kind='portfolio')
        self.assertEqual(second['state'], 'meeting')
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.studio(mode='studio_dismiss', run_id=run['id'])
        dismissed = self.studio(mode='studio_dismiss', run_id=run['id'], reason='Agents skipped a venture.')
        self.assertEqual((dismissed['state'], dismissed['dismissed']['reason']),
                         ('dismissed', 'Agents skipped a venture.'))
        self.assertEqual(self.studio(mode='studio_dismiss', run_id=run['id'], reason='again')['state'], 'dismissed')
        good = self.complete_studio(second, {'summary': 'ok', 'recommendations': [dict(
            venture_id=record['id'], rank=1, recommendation='continue', reason='Fine.', evidence=['V1'])]})
        self.assertEqual(good['state'], 'ready')
        self.assertEqual(good['recommendations'][0]['venture_name'], 'Settlement Desk')

    def test_a_failed_dispatch_is_recovered_with_the_same_request_id(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        requests = []
        original = self.store.action.side_effect
        def limited(action, body, **kwargs):
            if action == 'discuss':
                requests.append(body['request_id'])
                if len(requests) == 1:
                    raise ValueError('Meeting limit reached.')
            return original(action, body, **kwargs)
        self.store.action.side_effect = limited
        with self.assertRaisesRegex(ValueError, 'Meeting limit'):
            self.studio(mode='studio_run', kind='portfolio')
        failed = self.studio_runs()[-1]
        self.assertEqual((failed['state'], failed.get('job_id'), failed['dispatch_failed']),
                         ('needs_attention', None, True))
        recovered = self.studio(mode='studio_recover', run_id=failed['id'], inspected=True)
        self.assertEqual(recovered['state'], 'meeting')
        self.assertEqual(requests, ['studio-' + failed['id']] * 2)  # Same request: no second meeting.
        self.assertEqual(recovered['prompt'], failed['prompt'])

    def test_a_recovered_scout_gets_its_whole_pinned_guidance_bundle_back(self):
        # The evidence survived a failed dispatch but the guidance beside it
        # did not; recovery must not send a prompt pointing at missing files.
        studio = self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        checkout = self.ventures._studio_notebook(studio)
        original = self.store.action.side_effect
        attempts = []

        def limited(action, body, **kwargs):
            if action == 'discuss':
                attempts.append(body['request_id'])
                if len(attempts) == 1:
                    raise ValueError('Meeting limit reached.')
            return original(action, body, **kwargs)
        self.store.action.side_effect = limited
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'])
        with self.news(feed_item(venture_feeds.now().isoformat())), \
                patch.object(self.ventures, '_evidence_checkout', return_value=(checkout, True)):
            with self.assertRaisesRegex(ValueError, 'Meeting limit'):
                self.studio(mode='studio_run', kind='scout')
        failed = self.studio_runs()[-1]
        folder = Path(failed['evidence_file']).parent
        names = {ventures.STUDIO_ENTRY_FILE, ventures.STUDIO_RULES_FILE, ventures.STUDIO_REVIEW_FILE,
                 ventures.STUDIO_CONSULT_FILE}
        self.assertEqual(set(failed['bundle']), names)
        pinned = {name: (folder / name).read_text(encoding='utf-8') for name in names}
        self.assertEqual(pinned, failed['bundle'])
        brief = pinned[ventures.STUDIO_ENTRY_FILE]
        for name in names - {ventures.STUDIO_ENTRY_FILE}:
            self.assertIn('`' + name + '`', brief)
        self.assertIn('before any research or delegation', brief)
        self.assertIn('Start with ' + ventures.STUDIO_ENTRY_FILE, failed['prompt'])
        for name in (ventures.STUDIO_RULES_FILE, ventures.STUDIO_ENTRY_FILE):
            (folder / name).unlink()
        (folder / ventures.STUDIO_REVIEW_FILE).write_bytes(b'\xffedited')
        # A gateway upgrade between the two dispatches changes the rules; the
        # frozen prompt still gets the version it was written for.
        with patch.object(ventures, 'scout_answer_rules', return_value='# New rules\n'):
            recovered = self.studio(mode='studio_recover', run_id=failed['id'], inspected=True)
            job = next(item for item in self.jobs if item['id'] == recovered['job_id'])
            prompt = self.ventures.studio_discussion_prompt(job, {'worktree_path': str(checkout)})
        self.assertEqual(recovered['state'], 'meeting')
        self.assertEqual(recovered['prompt'], failed['prompt'])
        self.assertEqual(prompt, failed['prompt'])
        self.assertEqual(attempts, ['studio-' + failed['id']] * 2)
        self.assertEqual({name: (folder / name).read_text(encoding='utf-8') for name in names}, pinned)

    def test_the_answer_rules_separate_refusals_from_optional_fields_and_advice(self):
        rules = ventures.scout_answer_rules()
        sections = [line for line in rules.splitlines() if line.startswith('## ')]
        self.assertEqual(sections, ['## Before research: how to find and rate ideas',
                                    '## Required: an idea without these is refused',
                                    '## Checked: an idea breaking one of these is refused whole',
                                    '## Optional: leave out what you cannot cite',
                                    '## Advice: shown to the operator as notes, never a refusal'])
        optional = rules.split('## Optional')[1].split('## Advice')[0]
        for field in ('analogue', 'expansion', 'market_ceiling', 'incumbent', 'suggested_sources'):
            self.assertIn(field, optional)
        advice = rules.split('## Advice')[1]
        self.assertIn('without the customer\'s current baseline', advice)
        self.assertNotIn('baseline', rules.split('## Checked')[1].split('## Optional')[0])
        self.assertLess(rules.index('FIND IDEAS THREE WAYS'), rules.index('## Required'))

    def test_an_interrupted_pending_run_stops_blocking_after_ten_minutes(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        stale = self.ventures.save_studio_run(dict(
            id='c' * 32, organization_id='org', kind='portfolio', state='pending', created_at='2026-01-01T00:00:00+00:00'))
        with closing(self.service.connect()) as db, db:
            stale['updated_at'] = (ventures.now() - timedelta(minutes=11)).isoformat()
            db.execute('UPDATE venture_studio_runs SET data=? WHERE id=?', (json.dumps(stale), stale['id']))
        self.assertEqual(self.studio(mode='studio_run', kind='portfolio')['state'], 'meeting')
        self.assertEqual(self.studio_runs()[0]['state'], 'needs_attention')

    def test_two_daily_cadences_wait_for_each_other_without_backoff(self):
        self.setup_studio_manual()
        record = self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_portfolio_cadence='daily', studio_scout_cadence='daily',
                    studio_sources=['https://news.example/feed'])
        self.due('portfolio', 'scout')
        feeds = self.news(feed_item(venture_feeds.now().isoformat()))
        feeds.start()
        self.addCleanup(feeds.stop)
        self.ventures._fetch_urls('org', ['https://news.example/feed'])  # Fresh: no refresh pass first.
        for _ in range(3):
            self.ventures.advance()
            policy = self.ventures._studio_policy('org')
            self.assertFalse(policy.get('studio_scout_error'))
            self.assertFalse(policy.get('studio_scout_failures'))
            self.assertLessEqual(ventures.moment(policy['studio_next_scout_at']), ventures.now())
        self.assertEqual([(run['kind'], run['state']) for run in self.studio_runs()], [('portfolio', 'meeting')])
        self.complete_studio(self.studio_runs()[0], {'summary': 'ok', 'recommendations': [dict(
            venture_id=record['id'], rank=1, recommendation='continue', reason='Fine.', evidence=['V1'])]})
        self.ventures.advance()
        self.assertEqual([run['kind'] for run in self.studio_runs()], ['portfolio', 'scout'])
        schedule = self.ventures._studio_summary('org')['schedule']['scout']
        self.assertEqual((schedule['error'], schedule['failures']), ('', 0))
        self.assertTrue(schedule['last_at'])

    def test_scheduler_failures_reset_after_a_success(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_portfolio_cadence='daily')
        self.service.patch_policy('org', dict(studio_portfolio_failures=4, studio_portfolio_error='old'), 'test')
        self.due('portfolio')
        self.ventures.advance()
        schedule = self.ventures._studio_summary('org')['schedule']['portfolio']
        self.assertEqual((schedule['failures'], schedule['error']), (0, ''))

    def test_studio_evidence_keeps_twenty_long_ventures_and_long_history_in_budget(self):
        self.studio(mode='limits', max_active_ventures=20)
        long = 'Thesis ' + 'word ' * 290
        for number in range(20):
            record = self.create(request_id='bulk-' + str(number), name='Venture number ' + str(number), thesis=long,
                                 product='Product ' + 'feature ' * 180)
            saved = self.ventures.get(record['id'], 'org')
            saved['state'] = 'running'
            saved['configuration']['market']['regulatory'] = 'Rule ' * 290
            self.ventures.save(saved)
        for number in range(40):
            self.ventures.save_candidate(dict(id=uuid.uuid4().hex, organization_id='org', state='rejected',
                                              name='Old idea ' + str(number), thesis=long))
        policy = self.ventures._studio_policy('org')
        evidence, active, _ = self.ventures._studio_evidence('org', 'portfolio', policy)
        self.assertEqual(len(evidence['ventures']), 20)
        self.assertEqual(len(active), 20)
        self.assertLessEqual(len(json.dumps(evidence, ensure_ascii=False).encode()), ventures.STUDIO_EVIDENCE_LIMIT)
        self.assertTrue(all(len(item['thesis']) <= 120 for item in evidence['existing']))
        self.assertLessEqual(len(evidence['existing']), 80)
        # Only studio settings reach the agents, never the rest of the organization policy.
        self.assertEqual(set(evidence['policy']), {'focus', 'exclusions', 'max_candidates', 'sources'})

    def test_duplicates_are_found_beyond_the_bounded_evidence_list(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.ventures.save_candidate(dict(id='d' * 32, organization_id='org', state='rejected',
                                          name='Carrier Desk', thesis='An old rejected idea.'))
        for number in range(30):  # Pushes the old idea out of the 20 newest closed entries.
            self.ventures.save_candidate(dict(id=uuid.uuid4().hex, organization_id='org', state='rejected',
                                              name='Filler ' + str(number), thesis='Unrelated filler.'))
        run = self.scout_with(candidate('Carrier Desk'))
        self.assertEqual(run['state'], 'ready')
        fresh = self.stored_candidates()[-1]
        self.assertEqual((fresh['state'], fresh['duplicate_of']), ('excluded', 'd' * 32))

    def test_a_scout_cannot_set_its_own_bar_schedule_or_unlimited_capital(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        proposal = candidate()
        proposal['configuration']['simulation'].update(daily_ticks=True, survival_threshold=0, consecutive_days=1,
                                                       red_team=1, meeting_cadence='every_day',
                                                       capital=1_000_000_000, reference_price=900_000)
        free = candidate('Free Desk')
        free['thesis'] = 'A different idea with no costs at all for municipal libraries.'
        free['configuration']['simulation']['burn'] = []
        run = self.scout_with(proposal, free)
        self.assertEqual(run['state'], 'ready')
        self.assertEqual([item['name'] for item in run['refused_candidates']], ['Free Desk'])
        stored = self.stored_candidates()[0]
        simulation = stored['configuration']['simulation']
        self.assertEqual((simulation['daily_ticks'], simulation['survival_threshold'], simulation['consecutive_days'],
                          simulation['red_team'], simulation['meeting_cadence'], simulation['capital']),
                         (False, 60, 3, 5, 'news_only', 36000))  # 3000 a month x 12 months.
        self.assertEqual(stored['scout_adjusted']['survival_threshold'], dict(proposed=0, used=60))
        self.assertEqual(stored['scout_adjusted']['capital'], dict(proposed=1_000_000_000, used=36000))
        # The price anchor is part of the bar: it goes back to the monthly costs.
        self.assertEqual(simulation['reference_price'], 0)
        self.assertEqual(stored['scout_adjusted']['reference_price'], dict(proposed=900_000, used=0))
        created = self.ventures.get(self.studio(mode='candidate_decide', candidate_id=stored['id'],
                                                decision='approve', confirmed=True)['venture_id'], 'org')
        self.assertEqual((created['configuration']['simulation']['daily_ticks'], created['next_at']), (False, None))

    def test_operator_edits_cannot_schedule_a_candidate(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(candidate())
        stored = self.stored_candidates()[0]
        configuration = json.loads(json.dumps(stored['configuration']))
        configuration['simulation'].update(daily_ticks=True, survival_threshold=40)
        edited = self.studio(mode='candidate_update', candidate_id=stored['id'], configuration=configuration)
        self.assertEqual((edited['configuration']['simulation']['daily_ticks'],
                          edited['configuration']['simulation']['survival_threshold']), (False, 40))

    def test_operator_edits_recompute_economics_and_reject_unpaid_kill_criteria(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(candidate())
        stored = self.stored_candidates()[0]
        configuration = json.loads(json.dumps(stored['configuration']))
        configuration['simulation']['burn'] = [dict(label='hosting', amount=6000, currency='USD')]
        edited = self.studio(mode='candidate_update', candidate_id=stored['id'], configuration=configuration)
        self.assertEqual(edited['economics']['monthly_burn'], 6000)
        with self.assertRaisesRegex(ValueError, 'must use a paid'):
            self.studio(mode='candidate_update', candidate_id=stored['id'],
                        kill_criteria=['No pilot after 90 days.'])
        self.assertEqual(self.stored_candidates()[0]['kill_criteria'],
                         ['No paid pilot signed within 90 days.'])

    def test_a_partial_candidate_edit_keeps_the_fields_it_does_not_name(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(candidate())
        stored = self.stored_candidates()[0]
        before = stored['economics']
        self.assertGreater(before['monthly_burn'], 0)
        # Only the market's sales cycle: burn, capital and the rest of the market stay.
        edited = self.studio(mode='candidate_update', candidate_id=stored['id'],
                             configuration=dict(market=dict(sales_cycle=4)))
        self.assertEqual(edited['configuration']['market']['sales_cycle'], 4)
        self.assertEqual(edited['configuration']['market']['currency'], stored['configuration']['market']['currency'])
        self.assertEqual(edited['configuration']['simulation']['burn'], stored['configuration']['simulation']['burn'])
        self.assertEqual(edited['configuration']['simulation']['capital'],
                         stored['configuration']['simulation']['capital'])
        for key in ('monthly_burn', 'runway_months', 'break_even_customers'):
            self.assertEqual(edited['economics'].get(key), before.get(key), key)
        # A list is replaced as a whole, and the result is still validated.
        edited = self.studio(mode='candidate_update', candidate_id=stored['id'], configuration=dict(
            simulation=dict(burn=[dict(label='hosting', amount=1000, currency='USD')])))
        self.assertEqual(edited['configuration']['simulation']['burn'],
                         [dict(label='hosting', amount=1000, currency='USD')])
        self.assertNotEqual(edited['economics']['monthly_burn'], before['monthly_burn'])
        with self.assertRaises(ValueError):
            self.studio(mode='candidate_update', candidate_id=stored['id'],
                        configuration=dict(market=dict(sales_cycle=9)))

    def test_a_partial_venture_configuration_keeps_the_fields_it_does_not_name(self):
        record = self.create()
        before = self.ventures.get(record['id'], 'org')['configuration']
        updated = self.ventures.action(dict(mode='configure', organization_id='org', venture_id=record['id'],
                                            configuration=dict(simulation=dict(survival_threshold=55))),
                                       'admin', 'admin')
        after = updated['configuration'] if 'configuration' in updated else self.ventures.get(
            record['id'], 'org')['configuration']
        self.assertEqual(after['simulation']['survival_threshold'], 55)
        self.assertEqual(after['simulation']['burn'], before['simulation']['burn'])
        self.assertEqual(after['market'], before['market'])

    def test_daily_reviews_need_a_team_and_nothing_is_created_without_one(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(candidate())
        stored = self.stored_candidates()[0]
        with self.assertRaisesRegex(ValueError, 'need a team'):
            self.studio(mode='candidate_decide', candidate_id=stored['id'], decision='approve', confirmed=True,
                        start_daily=True)
        self.assertEqual(self.stored_candidates()[0]['state'], 'proposed')
        self.assertFalse(self.stored_candidates()[0].get('venture_id'))

    def test_exclusions_are_enforced_by_whole_word_and_never_dropped(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_exclude=['crypto', 'gambling'])
        topical = candidate('Crypto Treasury Desk')
        topical['thesis'] = 'Treasury reporting for online operators holding digital assets.'
        cryptography = candidate('Cryptography Audit Desk')
        cryptography['thesis'] = 'Certificate inventory audits for regional hospitals and clinics.'
        self.scout_with(topical, cryptography)
        stored = {item['name']: item for item in self.stored_candidates()}
        self.assertEqual((stored['Crypto Treasury Desk']['state'], stored['Crypto Treasury Desk']['excluded_by']),
                         ('excluded', 'crypto'))
        self.assertEqual(stored['Cryptography Audit Desk']['state'], 'proposed')  # A word, not a substring.
        self.studio(mode='studio_policy', studio_exclude=['topic ' + str(number) for number in range(40)])
        with self.assertRaisesRegex(ValueError, 'exclusion list is full'):
            self.studio(mode='candidate_decide', candidate_id=stored['Cryptography Audit Desk']['id'],
                        decision='reject', reason='Not for us.', exclude=True)
        self.assertEqual(len(self.ventures._studio_policy('org')['studio_exclude']), 40)
        self.studio(mode='studio_policy', studio_exclude=['cryptography'])
        with self.assertRaisesRegex(ValueError, 'exclusion "cryptography"'):
            self.studio(mode='candidate_decide', candidate_id=stored['Cryptography Audit Desk']['id'],
                        decision='approve', confirmed=True)

    def test_an_empty_portfolio_records_a_skip_instead_of_a_meeting(self):
        self.setup_studio_manual()
        with self.assertRaisesRegex(ValueError, 'no active ventures'):
            self.studio(mode='studio_run', kind='portfolio')
        self.studio(mode='studio_policy', studio_portfolio_cadence='daily')
        self.due('portfolio')
        self.ventures.advance()
        self.assertEqual([(run['state'], run['reason'][:9]) for run in self.studio_runs()], [('skipped', 'No active')])
        self.assertEqual(self.jobs, [])
        self.assertGreater(ventures.moment(self.ventures._studio_policy('org')['studio_next_portfolio_at']),
                           ventures.now())

    def test_an_invalid_stored_policy_value_falls_back_without_breaking_anything(self):
        self.setup_studio_manual()
        self.service.patch_policy('org', dict(studio_max_candidates=9, studio_scout_cadence='hourly'), 'test')
        self.service.advance_modules()
        self.assertNotIn('fault', self.service.module_state('ventures'))
        summary = self.ventures.snapshot()['studios'][0]
        self.assertEqual(sorted(summary['invalid_policy']), ['studio_max_candidates', 'studio_scout_cadence'])
        self.assertEqual((summary['policy']['studio_max_candidates'], summary['policy']['studio_scout_cadence']),
                         (2, 'off'))
        self.assertNotIn('max_active_meetings', summary['policy'])
        repaired = self.studio(mode='studio_policy', studio_focus=['logistics'])
        self.assertEqual(repaired['invalid'], [])
        self.assertEqual(self.ventures._studio_summary('org')['invalid_policy'], [])
        with self.assertRaisesRegex(ValueError, 'candidate cap'):
            self.studio(mode='studio_policy', studio_max_candidates=9)

    def test_budget_counts_real_participants_and_refuses_going_over_the_cap(self):
        self.setup_studio_manual()
        record = self.add_venture_group(self.create())
        self.configure(record, daily_ticks=True, meeting_cadence='news_only')
        self.studio(mode='studio_policy', studio_scout_cadence='weekly')
        budget = self.ventures._studio_summary('org')['budget']
        # news_only: possible, not guaranteed (3 people). Weekly scout: 1/7 of 5 people.
        self.assertEqual((budget['daily_meetings'], budget['max_daily_meetings']), (0.1, 1.1))
        self.assertEqual((budget['projected_agent_sessions'], budget['max_agent_sessions']), (0.7, 3.7))
        self.assertEqual(budget['studio_runs_per_week'], 1)
        self.service.patch_policy('org', dict(daily_session_cap=5), 'test')
        with self.assertRaisesRegex(ValueError, 'above the organization cap of 5'):
            self.studio(mode='studio_policy', studio_portfolio_cadence='daily')
        self.service.patch_policy('org', dict(daily_session_cap=2), 'test')  # Already over: off still works.
        self.assertEqual(self.studio(mode='studio_policy', studio_scout_cadence='off')['studio_scout_cadence'], 'off')
        other = self.create(request_id='second-desk', name='Second Desk')
        saved = self.ventures.get(other['id'], 'org')
        saved['group_id'] = 'b1' * 16
        self.ventures.save(saved)
        with self.assertRaisesRegex(ValueError, 'above the organization cap'):
            self.configure(saved, daily_ticks=True)

    def test_studio_summary_shows_lessons_and_scheduler_errors(self):
        self.setup_studio_manual()
        record = self.create()
        saved = self.ventures.get(record['id'], 'org')
        saved.update(state='failed', outcome_reason='Capital reached zero.')
        self.ventures.save(saved)
        self.service.patch_policy('org', dict(studio_scout_error='The venture candidate backlog is full.',
                                              studio_scout_failures=2), 'test')
        summary = self.ventures._studio_summary('org')
        self.assertEqual(summary['lessons'][0]['title'], 'Settlement Desk')
        self.assertEqual(summary['schedule']['scout']['error'], 'The venture candidate backlog is full.')
        self.assertEqual(summary['schedule']['scout']['failures'], 2)

    # --- The first-run path: what a new operator meets, and each way it used to fail.

    def herdr(self, state='stopped'):
        self.ventures.herdr = FakeHerdr(state)
        self.ventures._herdr_cache = None
        return self.ventures.herdr

    def readiness(self):
        return self.ventures._studio_summary('org')['readiness']

    def check(self, identity):
        return next(item for item in self.readiness()['checks'] if item['id'] == identity)

    def portfolio_answer(self, record):
        return {'summary': 'ok', 'recommendations': [dict(venture_id=record['id'], rank=1, recommendation='continue',
                                                          reason='Fine.', evidence=['V1'])]}

    def test_a_fresh_installation_gets_a_studio_without_an_organization_or_agent(self):
        self.organizations.clear()
        self.store.runtime_ready = lambda runtime: None if runtime == 'codex' else runtime + ' is not installed.'
        self.store.runtime_status = lambda runtime: dict(installed=True, status='configured')
        body = dict(mode='studio_start', confirmed=True, name='My Studio',
                    studio_sources=['https://news.example/feed'], studio_focus=['freight'])
        result = self.ventures.action(dict(body), 'admin', 'admin')
        organization = result['organization_id']
        self.assertEqual([item['name'] for item in self.organizations], ['My Studio'])
        self.assertTrue(result['studio']['group_id'])
        self.assertEqual(result['studio']['team']['settings_source'], 'automatic')
        hired = [profile for profile in self.profiles if profile.get('role') in ventures.STUDIO_ROLES]
        self.assertEqual(len(hired), 4)
        # The first ready CLI on its own default model: nothing was guessed.
        self.assertEqual({(profile['runtime'], profile['model']) for profile in hired}, {('codex', '')})
        self.assertEqual((result['policy']['studio_scout_cadence'], result['policy']['studio_portfolio_cadence']),
                         ('weekly', 'weekly'))
        self.assertTrue(result['readiness']['can_scout'])
        # Asking again is safe: same organization, no second team.
        again = self.ventures.action(dict(body), 'admin', 'admin')
        self.assertEqual(again['organization_id'], organization)
        self.assertEqual((len(self.organizations), len(self.groups)), (1, 1))
        self.assertEqual(len([profile for profile in self.profiles if profile.get('role') in ventures.STUDIO_ROLES]), 4)

    def test_setup_starts_the_first_scout_and_reports_why_it_could_not(self):
        self.store.runtime_ready = lambda runtime: None
        # No focus yet: the studio is created, the scout says what it needs.
        result = self.ventures.action(dict(mode='studio_start', confirmed=True, first_scout=True),
                                      'admin', 'admin')
        self.assertTrue(result['studio']['group_id'])  # The studio exists; only the run did not start.
        self.assertEqual((result['first_scout']['state'], result['first_scout']['category']), ('not_started', 'inputs'))
        self.assertEqual(self.studio_runs(), [])
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'], studio_focus=['freight'])
        with self.news(feed_item(venture_feeds.now().isoformat())):
            result = self.ventures.action(dict(mode='studio_start', confirmed=True, first_scout=True), 'admin', 'admin')
        self.assertEqual(result['first_scout']['state'], 'meeting')

    def test_setup_is_refused_while_herdr_is_stopped_and_leaves_nothing_behind(self):
        # Creating a group starts its facilitator; with Herdr stopped that
        # launch would fail and leave a stuck session.
        self.organizations.clear()
        self.store.runtime_ready = lambda runtime: None
        self.herdr('stopped')
        with self.assertRaisesRegex(ValueError, 'Herdr is stopped. Start Herdr to set up'):
            self.ventures.action(dict(mode='studio_start', confirmed=True), 'admin', 'admin')
        self.assertEqual((self.organizations, self.profiles, self.groups), ([], [], []))
        self.organizations.append(dict(id='org', name='Product organization'))
        with self.assertRaisesRegex(ValueError, 'Herdr is stopped'):
            self.studio(mode='studio_setup', confirmed=True)
        record = self.create()
        with self.assertRaisesRegex(ValueError, 'Herdr is stopped. Start Herdr to create the venture team'):
            self.ventures.action(dict(mode='team', organization_id='org', venture_id=record['id'],
                                      confirmed=True), 'admin', 'admin')
        self.assertEqual((self.profiles, self.groups), ([], []))
        self.assertFalse((self.projects / 'ventures' / 'studio-org').exists())

    def test_one_call_setup_raises_limits_once_and_never_overrides_a_later_choice(self):
        self.store.runtime_ready = lambda runtime: None
        self.service.patch_policy('org', dict(max_active_sessions=4, daily_session_cap=3), 'test')
        result = self.ventures.action(dict(mode='studio_start', confirmed=True), 'admin', 'admin')
        # An existing organization's limits no longer stop the one call.
        self.assertEqual((result['created'], result['limits_raised']),
                         (True, dict(max_active_sessions=5, daily_session_cap=5)))
        self.assertEqual(result['policy']['studio_scout_cadence'], 'weekly')
        # The operator then chooses manual only and a lower limit.
        self.studio(mode='studio_policy', studio_scout_cadence='off', studio_portfolio_cadence='off')
        self.service.patch_policy('org', dict(max_active_sessions=2), 'test')
        again = self.ventures.action(dict(mode='studio_start', confirmed=True), 'admin', 'admin')
        self.assertEqual((again['created'], again['limits_raised']), (False, {}))
        self.assertEqual((again['policy']['studio_scout_cadence'], again['policy']['studio_portfolio_cadence']),
                         ('off', 'off'))
        self.assertEqual(self.service.policy('org')['max_active_sessions'], 2)

    def test_automatic_selection_skips_a_cli_whose_account_offers_no_model(self):
        self.store.runtime_ready = lambda runtime: None if runtime in ('opencode', 'agy') else 'not installed'
        self.store.template_catalog = lambda project: dict(accounts=[
            dict(runtime='opencode', available=False, reason='No models were returned for the configured providers.'),
            dict(runtime='agy', available=True)])
        studio = self.studio(mode='studio_setup', confirmed=True)
        self.assertEqual(studio['team']['settings_by_role']['scout']['runtime'], 'agy')
        self.store.template_catalog = lambda project: dict(accounts=[
            dict(runtime='opencode', available=False, reason='No models were returned for the configured providers.'),
            dict(runtime='agy', available=False, reason='Not signed in.')])
        record = self.create()
        self.profiles.clear()  # No agent to copy and no studio settings to fall back on.
        self.ventures.save_studio('org', {})
        with self.assertRaisesRegex(ValueError, 'No agent CLI is ready.*No models were returned'):
            self.ventures.action(dict(mode='team', organization_id='org', venture_id=record['id'],
                                      confirmed=True), 'admin', 'admin')

    def test_no_ready_cli_refuses_setup_with_the_reason_and_hires_nobody(self):
        self.store.runtime_ready = lambda runtime: runtime + ' has no Herdr integration.'
        with self.assertRaisesRegex(ValueError, 'No agent CLI is ready.*has no Herdr integration'):
            self.studio(mode='studio_setup', confirmed=True)
        self.assertEqual((self.profiles, self.groups), ([], []))
        self.assertFalse(self.check('provider')['ok'])
        self.assertEqual(self.check('provider')['action'], 'cli_accounts')
        # Partial setup continues from where it stopped once a CLI is ready.
        self.ventures._runtime_cache = None
        self.store.runtime_ready = lambda runtime: None
        self.assertTrue(self.studio(mode='studio_setup', confirmed=True)['group_id'])

    def test_a_signed_out_cli_is_skipped_and_a_chosen_model_is_checked_first(self):
        self.store.runtime_ready = lambda runtime: None
        self.store.runtime_status = lambda runtime: dict(status='not_configured' if runtime == 'claude' else 'configured')
        self.store.model_validator = MagicMock(side_effect=ValueError('Selected OpenCode provider/model is unavailable: x/y.'))
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.studio(mode='studio_setup', confirmed=True, settings=dict(runtime='opencode', provider='x', model='y'))
        with self.assertRaisesRegex(ValueError, 'not signed in'):
            self.studio(mode='studio_setup', confirmed=True, settings=dict(runtime='claude'))
        self.assertEqual(self.profiles, [])
        studio = self.studio(mode='studio_setup', confirmed=True)  # Automatic: skips the signed-out Claude.
        self.assertEqual(studio['team']['settings_by_role']['scout']['runtime'], 'codex')

    def test_herdr_stopped_refuses_a_run_and_the_scheduler_waits_without_a_failure(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        fake = self.herdr('stopped')
        with self.assertRaisesRegex(ValueError, 'Herdr is stopped'):
            self.studio(mode='studio_run', kind='portfolio')
        self.assertEqual((self.studio_runs(), self.jobs), ([], []))  # No doomed run, no meeting.
        self.assertEqual((self.readiness()['herdr'], self.check('herdr')['action']), ('stopped', 'start_herdr'))
        self.assertFalse(self.readiness()['can_run'])
        self.studio(mode='studio_policy', studio_portfolio_cadence='daily')
        self.due('portfolio')
        self.ventures.advance()
        schedule = self.ventures._studio_summary('org')['schedule']['portfolio']
        self.assertIn('Herdr is stopped', schedule['waiting'])
        self.assertEqual((schedule['error'], schedule['failures'], self.studio_runs()), ('', 0, []))
        started = self.ventures.action(dict(mode='herdr_start'), 'admin', 'admin')
        self.assertEqual((started['state'], fake.starts), ('running', 1))
        self.ventures.advance()  # The same due window runs as soon as Herdr is back.
        self.assertEqual([run['state'] for run in self.studio_runs()], ['meeting'])
        self.assertEqual(self.ventures._studio_summary('org')['schedule']['portfolio']['waiting'], '')

    def test_a_meeting_tick_is_not_started_while_herdr_is_stopped(self):
        record = self.attach_group(self.create())
        self.herdr('stopped')
        with self.news(feed_item(venture_feeds.now().isoformat())):
            with self.assertRaisesRegex(ValueError, 'Herdr is stopped'):
                self.run_tick(record)
        self.assertEqual(self.jobs, [])
        review = self.ventures.review_for(record['id'], 'org', ventures.now().date().isoformat())
        self.assertEqual(review['state'], 'skipped')
        self.assertIn('Herdr is stopped', review['reason'])
        self.herdr('running')
        with self.news(feed_item(venture_feeds.now().isoformat())):
            self.assertEqual(self.run_tick(record)['state'], 'meeting')

    def test_a_scout_needs_sources_and_focus_unless_the_operator_overrides(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.service.patch_policy('org', dict(studio_focus=[], studio_web_research=False), 'test')
        with self.assertRaisesRegex(ValueError, 'add a scouting source and a focus area'):
            self.studio(mode='studio_run', kind='scout')
        self.assertEqual(self.studio_runs(), [])
        readiness = self.readiness()
        self.assertEqual((readiness['can_run'], readiness['can_scout'], readiness['coverage']['sources'],
                          readiness['coverage']['focus']), (True, False, 0, 0))
        self.assertEqual(self.check('sources')['action'], 'configure')
        self.studio(mode='studio_policy', studio_scout_cadence='daily')
        self.due('scout')
        self.ventures.advance()
        schedule = self.ventures._studio_summary('org')['schedule']['scout']
        self.assertIn('no market evidence', schedule['waiting'])
        self.assertEqual((schedule['failures'], self.studio_runs()), (0, []))
        self.assertEqual(self.studio(mode='studio_run', kind='scout', override=True)['state'], 'meeting')

    def web_candidate(self, name='Carrier Desk', evidence=('W1',)):
        proposal = candidate(name, evidence[0])
        proposal['configuration']['sources']['feeds'] = []
        proposal['evidence'] = list(evidence)
        return proposal

    RESEARCH = [dict(id='W1', url='https://trade.example/report', title='Forwarders dispute one invoice in nine',
                     published='2026-11-02', claim='Mid-market forwarders report frequent carrier overcharges.')]

    def test_without_sources_the_scout_researches_the_web_and_suggests_feeds(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.assertTrue(self.readiness()['can_scout'])  # A focus is enough; sources are suggested later.
        self.assertIn('researches the web instead', self.check('sources')['detail'])
        run = self.studio(mode='studio_run', kind='scout')
        self.assertTrue(run['web_research'])
        self.assertIn('RESEARCH THE WEB YOURSELF', run['prompt'])
        self.assertIn('suggested_sources', run['prompt'])
        self.assertTrue(json.loads(Path(run['evidence_file']).read_text(encoding='utf-8'))['web_research'])
        # The review checklist sits beside the evidence, and the prompt sends the team to it.
        checklist = Path(run['evidence_file']).with_name(ventures.STUDIO_REVIEW_FILE)
        self.assertEqual(checklist.read_text(encoding='utf-8'), ventures.STUDIO_REVIEW_CHECKLIST)
        self.assertIn('review checklist review.md beside that file', run['prompt'])
        self.assertIn('Can the leader copy it within a quarter?', ventures.STUDIO_REVIEW_CHECKLIST)
        self.assertIn('Return held ideas too', ventures.STUDIO_REVIEW_CHECKLIST)
        self.assertIn('auditing them again', ventures.STUDIO_REVIEW_CHECKLIST)
        self.assertIn('Return advanced and held ideas; list rejected ones under "ruled_out".', run['prompt'])
        self.assertIn('"revises":"<its exact name>"', run['prompt'])
        unlisted = self.web_candidate('Dock Ledger', evidence=['W2'])
        unlisted['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        done = self.complete_studio(run, {
            'candidates': [self.web_candidate(), unlisted], 'research': self.RESEARCH,
            'research_note': 'Searched trade press for freight invoice disputes.',
            'suggested_sources': [
                dict(url='https://trade.example/feed.xml', title='Trade press', reason='Weekly freight pricing news.'),
                dict(url='http://insecure.example/feed', title='Dropped', reason='Not https.'),
                dict(url='https://trade.example/feed.xml', title='Repeated', reason='Same feed twice.')]})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        # A page that was not reported cannot be cited; the listed one can.
        self.assertEqual([item['name'] for item in done['refused_candidates']], ['Dock Ledger'])
        stored = self.stored_candidates()[0]
        self.assertEqual((stored['state'], stored['evidence']), ('proposed', ['W1']))
        self.assertEqual(stored['evidence_links'][0]['url'], 'https://trade.example/report')
        shown = self.ventures._studio_summary('org')['runs'][0]
        self.assertEqual([item['url'] for item in shown['suggested_sources']], ['https://trade.example/feed.xml'])
        self.assertEqual((shown['research'][0]['id'], shown['research_note']),
                         ('W1', 'Searched trade press for freight invoice disputes.'))
        # Nothing was added by itself.
        self.assertEqual(self.ventures._studio_policy('org')['studio_sources'], [])

    def test_the_next_scout_skips_what_earlier_scouts_ruled_out_and_searches_wider_first(self):
        # A live scout rejected five crowded segments and came back empty; the
        # next run started from scratch and could spend its budget on the
        # same ones. Each answer now records them, and the next pack has them.
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        rules = ventures.scout_answer_rules()
        self.assertIn('SEARCH WIDER BEFORE GIVING UP', rules)
        self.assertIn('SKIP WHAT EARLIER SCOUTS RULED OUT', rules)
        self.assertIn('second batch in the answer', ventures.STUDIO_REVIEW_CHECKLIST)
        self.assertIn('"market"', rules)
        self.studio(mode='studio_policy', studio_focus=['Dutch padel clubs'])
        first = self.studio(mode='studio_run', kind='scout')
        pack = json.loads(Path(first['evidence_file']).read_text(encoding='utf-8'))
        self.assertNotIn('ruled_out', pack)
        self.assertNotIn('scout_notes', pack)
        # An answer from before ruled_out existed: its note travels as context.
        done = self.complete_studio(first, {'candidates': [], 'research': self.RESEARCH,
                                            'research_note': 'Padel booking is served by Playtomic.'})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        second = self.studio(mode='studio_run', kind='scout')
        pack = json.loads(Path(second['evidence_file']).read_text(encoding='utf-8'))
        self.assertNotIn('ruled_out', pack)
        self.assertEqual([(item['note'], item['focus']) for item in pack['scout_notes']],
                         [('Padel booking is served by Playtomic.', ['Dutch padel clubs'])])
        self.assertIn('"ruled_out"', second['prompt'])
        done = self.complete_studio(second, {'candidates': [], 'research': self.RESEARCH,
                                             'research_note': 'No useful feed items.', 'ruled_out': [
            dict(segment='Padel court yield', market='Dutch padel club owners', evidence=['W1', 'W9'],
                 reason='BookThisCourt already sells peak pricing.'),
            dict(segment='padel court yield', market='dutch padel club owners', reason='The same segment twice.'),
            dict(segment='Padel court yield', market='German padel chains', reason='Playtomic dominates.'),
            dict(segment='Music schools', reason='x' * 900),
            dict(segment='', reason='No segment.'),
            'not an object']})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        # Kept: one entry per segment and market, reasons bounded, page ids as
        # their URLs (an unknown id is dropped, never a reason to refuse the
        # run), and the focus the run was judged under.
        self.assertEqual([(item['segment'], item['market'], item['sources']) for item in done['ruled_out']],
                         [('Padel court yield', 'Dutch padel club owners', ['https://trade.example/report']),
                          ('Padel court yield', 'German padel chains', []), ('Music schools', '', [])])
        self.assertEqual(len(done['ruled_out'][2]['reason']), ventures.SCOUT_POINT_LENGTH)
        self.assertEqual({tuple(item['focus']) for item in done['ruled_out']}, {('Dutch padel clubs',)})
        self.assertEqual(self.ventures._studio_summary('org')['runs'][0]['ruled_out'], done['ruled_out'])
        # Another focus: the rejections still come along, each naming the
        # focus and market it was judged under, so the scout can tell.
        self.studio(mode='studio_policy', studio_focus=['German music schools'])
        third = self.studio(mode='studio_run', kind='scout')
        self.complete_studio(third, {'candidates': [], 'research': self.RESEARCH, 'ruled_out': [
            dict(segment='Padel court yield', market='Dutch padel club owners', reason='Still served.')]})
        fourth = self.studio(mode='studio_run', kind='scout')
        pack = json.loads(Path(fourth['evidence_file']).read_text(encoding='utf-8'))
        # Newest first; a segment rejected again for the same market keeps
        # only its newest reason, so repeats do not use up the memory.
        self.assertEqual([(item['segment'], item['market'], item['reason'][:20], item['focus'])
                          for item in pack['ruled_out']],
                         [('Padel court yield', 'Dutch padel club owners', 'Still served.', ['German music schools']),
                          ('Padel court yield', 'German padel chains', 'Playtomic dominates.', ['Dutch padel clubs']),
                          ('Music schools', '', 'x' * 20, ['Dutch padel clubs'])])
        self.assertTrue(all(item['date'] for item in pack['ruled_out']))
        # Notes are context, never rejections, and an explicit empty list adds none.
        self.assertEqual([item['note'] for item in pack['scout_notes']],
                         ['No useful feed items.', 'Padel booking is served by Playtomic.'])

    def test_a_feed_scout_rejection_keeps_the_feed_item_it_cites(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'], studio_web_research=False)
        with self.news(feed_item(venture_feeds.now().isoformat())):
            run = self.studio(mode='studio_run', kind='scout')
        self.assertFalse(run.get('web_research'))
        done = self.complete_studio(run, {'candidates': [], 'ruled_out': [
            dict(segment='Instant settlement for banks', market='Japanese banks', reason='Not B2B software.',
                 evidence=['N1', 'N7'])]})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        self.assertEqual(done['ruled_out'][0]['sources'], ['https://news.example/item-1'])

    def test_a_web_scout_candidate_keeps_the_schema_and_a_refusal_names_every_problem(self):
        # The shape a real web-researching scout returned: planning notes in
        # the configuration, a list of countries, twelve cited pages, and one
        # kill criterion too many.
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='scout')
        for rule in ('simulation.burn', 'at most 100', '1-8 assumptions', 'cite 1-12', 'refused whole'):
            self.assertIn(rule, self.instructions(run))
        research = [dict(id='W' + str(number), url='https://trade.example/page' + str(number),
                         title='Page ' + str(number), published='', claim='A reported finding.')
                    for number in range(1, 13)]
        rich = self.web_candidate('Ledger Lane', evidence=[item['id'] for item in research])
        rich['configuration']['market'].update(geography=['BE', 'NL', 'DE'], segment='forwarders',
                                               employee_range='10-200')
        rich['configuration']['simulation'].update(horizon_months=12, gross_margin_target=0.65)
        over = self.web_candidate('Dock Ledger', evidence=['W1'])
        over['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        over['kill_criteria'] = ['Criterion ' + str(number) for number in range(9)]
        over['configuration']['simulation']['burn'] = []
        over['configuration']['simulation']['monthly_costs'] = [dict(label='hosting', amount=900, currency='USD')]
        over['evidence'] = ['W99']
        done = self.complete_studio(run, {'candidates': [rich, over], 'research': research})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        stored = self.stored_candidates()[0]
        self.assertEqual((stored['name'], stored['state'], len(stored['evidence'])), ('Ledger Lane', 'proposed', 12))
        self.assertEqual(stored['configuration']['market']['geography'], 'BE, NL, DE')
        self.assertNotIn('segment', stored['configuration']['market'])
        self.assertNotIn('horizon_months', stored['configuration']['simulation'])
        self.assertEqual(len(stored['evidence_links']), 12)
        [refused] = done['refused_candidates']
        self.assertEqual(refused['name'], 'Dock Ledger')
        for problem in ('configuration: A candidate needs at least one monthly cost',
                        'kill criteria: 1 to 8 items needed, got 9', 'evidence:'):
            self.assertIn(problem, refused['error'])

    def test_the_studio_group_name_carries_no_identifier_and_an_old_one_is_renamed_once(self):
        self.setup_studio_manual()
        self.assertEqual(self.ventures._studio_notebook_path('org'), 'ventures/studio-product-organization-org')
        self.groups[0]['name'] = 'Venture Studio org'  # How studios were named before.
        self.ventures._studio_rename_group('org', self.ventures._studio('org'))
        self.assertEqual(self.groups[0]['name'], 'Venture Studio')
        self.assertEqual(self.ventures._studio('org')['team']['group_name'], 'Venture Studio')
        calls = self.store.action.call_count
        self.ventures._studio_rename_group('org', self.ventures._studio('org'))
        self.assertEqual(self.store.action.call_count, calls)  # Nothing left to rename.
        self.groups[0]['name'] = 'Pricing desk'  # An operator's own name is left alone.
        self.ventures._studio_rename_group('org', self.ventures._studio('org'))
        self.assertEqual(self.groups[0]['name'], 'Pricing desk')

    def test_a_scout_without_sources_still_needs_a_focus_to_research_the_web(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.service.patch_policy('org', dict(studio_focus=[]), 'test')
        readiness = self.readiness()
        self.assertEqual((readiness['can_scout'], readiness['can_scout_override']), (False, False))
        # With web research on, missing sources are a suggestion, not a blocker.
        self.assertTrue(self.check('sources')['optional'])
        self.assertFalse(self.check('focus').get('optional'))
        with self.assertRaisesRegex(ValueError, 'Add a focus area'):
            self.studio(mode='studio_run', kind='scout', override=True)
        self.assertEqual(self.studio_runs(), [])
        self.service.patch_policy('org', dict(studio_focus=['freight forwarding']), 'test')
        self.assertEqual(self.studio(mode='studio_run', kind='scout', override=True)['state'], 'meeting')

    def test_a_meeting_stopped_by_a_busy_agent_is_named_and_recover_retries_its_preparation(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='scout')
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        job.update(state='needs_attention', error=(
            'Portfolio Manager: Agent is still working; no new prompt was sent. Discussion preparation failed '
            'before submission; no discussion prompt was sent. Retry inspected preparation after resolving the cause.'))
        self.ventures.advance()
        stored = self.studio_runs()[-1]
        self.assertEqual(stored['state'], 'needs_attention')
        self.assertEqual((stored['failure']['category'], stored['failure']['title'], stored['failure']['agent']),
                         ('busy', 'A studio agent was still busy', 'Portfolio Manager'))
        shown = self.ventures._studio_summary('org')['runs'][0]
        self.assertEqual((shown['recover']['action'], shown['recover']['label']), ('retry', 'Retry preparation'))
        # Recover takes that step: the meeting is prepared again, nothing is finalized or resent.
        recovered = self.studio(mode='studio_recover', run_id=run['id'], inspected=True)
        self.assertEqual(recovered['state'], 'meeting')
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(len([item for item in self.jobs if item.get('kind') == 'discussion']), 1)

    def test_an_unreadable_source_falls_back_to_web_research_and_is_named(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_sources=['https://www.google.com/'])
        with patch.object(venture_feeds, 'fetch', side_effect=ValueError('Feed is not valid XML.')):
            run = self.studio(mode='studio_run', kind='scout')
        self.assertTrue(run['web_research'])  # A page that is not a feed gives no items.
        check = self.check('sources')
        self.assertFalse(check['ok'])
        self.assertIn('Not a readable feed: https://www.google.com/ (Feed is not valid XML.)', check['detail'])
        self.assertTrue(self.readiness()['can_scout'])
        self.assertEqual(self.readiness()['sources'][0]['error'], 'Feed is not valid XML.')

    def test_a_scout_with_source_items_may_not_cite_the_web(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        proposal = candidate()
        proposal['evidence'] = ['W1']
        done = self.scout_with(proposal)
        self.assertFalse(done['web_research'])
        self.assertNotIn('RESEARCH THE WEB', done['prompt'])
        self.assertEqual((done['candidates'], [item['name'] for item in done['refused_candidates']]),
                         ([], ['Carrier Desk']))

    def test_a_suggested_source_is_added_only_when_it_is_a_readable_feed(self):
        self.setup_studio_manual()
        with patch.object(venture_feeds, 'fetch', side_effect=ValueError('Feed is not valid XML.')):
            with self.assertRaisesRegex(ValueError, 'not a readable RSS or Atom feed: Feed is not valid XML'):
                self.studio(mode='studio_source_add', url='https://www.google.com/')
        with self.news():
            with self.assertRaisesRegex(ValueError, 'has no items'):
                self.studio(mode='studio_source_add', url='https://empty.example/feed')
        with self.assertRaisesRegex(ValueError, 'https'):
            self.studio(mode='studio_source_add', url='http://trade.example/feed.xml')
        self.assertEqual(self.ventures._studio_policy('org')['studio_sources'], [])
        with self.news(feed_item(venture_feeds.now().isoformat())):
            added = self.studio(mode='studio_source_add', url='https://trade.example/feed.xml')
            again = self.studio(mode='studio_source_add', url='https://trade.example/feed.xml')
        self.assertEqual((added['added'], again['added']), (True, False))
        self.assertEqual(self.ventures._studio_policy('org')['studio_sources'], ['https://trade.example/feed.xml'])

    def test_automatic_setup_detects_a_concrete_model_from_the_account(self):
        # The account catalog the team presets use: OpenCode offers two models.
        models = [dict(provider='opencode-go', id='glm-5-coder', name='GLM 5 Coder', reasoning=[], tool_call=True,
                       context=200000),
                  dict(provider='opencode-go', id='tiny-nano', name='Tiny Nano', reasoning=[], tool_call=True,
                       context=8000)]
        self.store.runtime_ready = lambda runtime: None if runtime == 'opencode' else 'not installed'
        self.store.template_catalog = lambda project: dict(
            runtimes=dict(opencode=dict(models=models, supports_variants=False)),
            accounts=[dict(runtime='opencode', available=True)])
        checked = []
        self.store.model_validator = checked.append
        studio = self.studio(mode='studio_setup', confirmed=True)
        settings = studio['team']['settings_by_role']['scout']
        # A model that meets the capability floor, not the CLI default and not the tiny one.
        self.assertEqual((settings['runtime'], settings['provider'], settings['model']),
                         ('opencode', 'opencode-go', 'glm-5-coder'))
        self.assertEqual(studio['team']['settings_source'], 'detected')
        self.assertNotIn('detected', settings)  # Only real agent settings are sent to the store.
        self.assertEqual((checked[0]['provider'], checked[0]['model']), ('opencode-go', 'glm-5-coder'))
        hired = [profile for profile in self.profiles if profile.get('role') in ventures.STUDIO_ROLES]
        self.assertEqual({(profile['provider'], profile['model']) for profile in hired}, {('opencode-go', 'glm-5-coder')})

    def test_automatic_setup_falls_back_to_the_cli_default_when_no_model_is_suitable(self):
        self.store.runtime_ready = lambda runtime: None if runtime == 'opencode' else 'not installed'
        self.store.template_catalog = lambda project: dict(
            runtimes=dict(opencode=dict(models=[dict(provider='x', id='tiny-nano', name='Tiny', reasoning=[],
                                                     tool_call=True, context=8000)])),
            accounts=[dict(runtime='opencode', available=True)])
        studio = self.studio(mode='studio_setup', confirmed=True)
        settings = studio['team']['settings_by_role']['scout']
        self.assertEqual((settings['runtime'], settings['model'], studio['team']['settings_source']),
                         ('opencode', '', 'automatic'))

    def test_a_stuck_tick_no_longer_blocks_the_next_day_and_can_be_dismissed(self):
        record = self.attach_group(self.create())
        with self.news(feed_item(venture_feeds.now().isoformat())):
            tick = self.run_tick(record)
        failed = self.complete(record, {'events': 'not a list'})
        self.assertEqual(failed['state'], 'needs_attention')
        # The same day: dismissing closes it so the day can be run again.
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.ventures.action(dict(mode='dismiss_tick', organization_id='org', venture_id=record['id']),
                                 'admin', 'admin')
        closed = self.ventures.action(dict(mode='dismiss_tick', organization_id='org', venture_id=record['id'],
                                           reason='Bad answer.'), 'admin', 'admin')
        self.assertEqual((closed['state'], closed['id']), ('skipped', tick['id']))
        with self.news(feed_item(venture_feeds.now().isoformat())):
            again = self.run_tick(self.ventures.get(record['id'], 'org'))
        self.assertNotEqual(again['id'], tick['id'])
        # A stuck tick from an earlier day is superseded when the next day starts.
        again.update(state='needs_attention', error='Venture result is not valid JSON.',
                     simulated_date='2026-01-01')
        self.ventures.save_tick(again)
        with closing(self.service.connect()) as db, db:  # That day's review record moves with it.
            db.execute('DELETE FROM venture_reviews WHERE venture_id=?', (record['id'],))
        with self.news():
            later = self.run_tick(self.ventures.get(record['id'], 'org'))
        self.assertIn(later['state'], ('ready', 'meeting'))
        with closing(self.service.connect()) as db:
            old = json.loads(db.execute('SELECT data FROM venture_ticks WHERE id=?', (again['id'],)).fetchone()[0])
        self.assertEqual(old['state'], 'superseded')
        self.assertIn('not valid JSON', old['superseded']['reason'])
        with self.assertRaisesRegex(ValueError, 'no tick needing attention'):
            self.ventures.action(dict(mode='dismiss_tick', organization_id='org', venture_id=record['id'],
                                      reason='Nothing left.'), 'admin', 'admin')

    def test_a_scout_candidate_carries_diligence_and_gateway_economics(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        proposal = candidate('Machinery TechFile')
        proposal.pop('confidence', None)  # Dimensions, not a single opinion, drive the summary.
        # The review's case: a pilot credited to an annual subscription.
        proposal['business_model'] = {
            'type': 'hybrid',
            'revenue_lines': [
                dict(kind='one_off', label='technical-file pilot', amount=1900, currency='USD'),
                dict(kind='recurring', label='annual subscription', amount=5400, currency='USD', period='year'),
                dict(kind='credit', label='pilot credited to annual contract', amount=1900, currency='USD'),
            ],
        }
        proposal['delivery_effort'] = dict(expert_days=3, day_rate=dict(amount=500, currency='USD'))
        proposal['reachable_accounts']['count'] = 25
        proposal['time_to_first_payment_days'] = 45
        done = self.scout_with(proposal)
        self.assertEqual(done['state'], 'ready', done.get('error'))
        stored = self.stored_candidates()[0]
        self.assertEqual((stored['state'], stored['confidence']), ('proposed', 62))
        self.assertEqual((stored['prior_state']['change'], stored['buyer']['role']),
                         ('The new filing requires a documented exception trail.', 'CFO'))
        self.assertEqual(stored['confidence_dimensions']['paid_demand']['rating'], 'medium')
        economics = stored['economics']
        # Per month throughout: 5,400 a year is 450 a month, so 3,000 of costs
        # needs 7 customers, not one per 5,400.
        self.assertEqual((economics['basis'], economics['recurring_monthly'], economics['break_even_customers'],
                          economics['break_even_unit'], economics['runway_months']),
                         ('recurring', 450.0, 7, 'paying customers', 12.0))
        # The credit lowers the first year, not the pilot: 5,400, not 7,300.
        self.assertEqual((economics['first_year_revenue'], economics['first_year_contribution'],
                          economics['project_contribution'], economics['pilot_break_even_days']),
                         (5400.0, 3900.0, 400.0, 3.8))
        self.assertEqual((economics['break_even_share'], economics['customers_per_month']), (0.28, 1))
        self.assertEqual(economics['warnings'], [])
        self.assertEqual(stored['kill_criteria_notes'], [])

    def test_economics_flag_a_losing_pilot_and_a_service_that_outgrows_its_market(self):
        configuration = ventures.normalize_config(candidate()['configuration'])
        hybrid = ventures.scout_business_model(dict(type='hybrid', revenue_lines=[
            dict(kind='one_off', label='pilot', amount=1900, currency='USD'),
            dict(kind='recurring', label='annual', amount=5400, currency='USD', period='year')]))
        slow = ventures.scout_delivery_effort(dict(expert_days=6, day_rate=dict(amount=500, currency='USD')))
        losing = ventures.candidate_economics(configuration, hybrid, slow, dict(count=400), 30)
        self.assertIn('The first offer loses money', losing['warnings'][0])
        self.assertIn('break-even at 3.8 days', losing['warnings'][0])
        service = ventures.scout_business_model(dict(type='productised_service', revenue_lines=[
            dict(kind='one_off', label='file build', amount=1000, currency='USD')]))
        fast = ventures.scout_delivery_effort(dict(expert_days=1, day_rate=dict(amount=500, currency='USD')))
        outgrown = ventures.candidate_economics(configuration, service, fast, dict(count=30), 30)
        # 3,000 of costs at 500 contribution: 6 projects a month, about 72 a year.
        self.assertEqual((outgrown['break_even_customers'], outgrown['break_even_unit'], outgrown['accounts_needed']),
                         (6, 'projects a month', 72))
        self.assertIn('only 30 accounts are cited as reachable', outgrown['warnings'][0])
        # A service priced at nothing has no project to fund delivery.
        free = ventures.scout_business_model(dict(type='service', revenue_lines=[
            dict(kind='one_off', label='free audit', amount=0, currency='USD')]))
        unfunded = ventures.candidate_economics(configuration, free, fast, dict(count=30), 30)
        self.assertEqual((unfunded['break_even_customers'], unfunded['project_contribution']), (None, None))
        self.assertEqual(unfunded['warnings'], ['The service model has no one-off revenue to fund delivery.'])
        # A recurring line without a period, or a credit with nothing to credit, is refused.
        with self.assertRaisesRegex(ValueError, 'period'):
            ventures.scout_business_model(dict(type='saas', revenue_lines=[
                dict(kind='recurring', label='plan', amount=600, currency='USD')]))
        with self.assertRaisesRegex(ValueError, 'credit'):
            ventures.scout_business_model(dict(type='productised_service', revenue_lines=[
                dict(kind='one_off', label='pilot', amount=1900, currency='USD'),
                dict(kind='credit', label='credited', amount=1900, currency='USD')]))

    def test_revenue_that_grows_with_the_customer_counts_and_upside_stays_apart(self):
        # The shape of a hotel platform: onboarding, a price per room and a
        # share of the card payments that pass through it.
        configuration = ventures.normalize_config(candidate()['configuration'])
        business = ventures.scout_business_model(dict(type='hybrid', revenue_lines=[
            dict(kind='one_off', label='onboarding', amount=1500, currency='USD'),
            dict(kind='per_unit', label='rooms', amount=12, currency='USD', period='month', unit='room', units=60),
            dict(kind='take_rate', label='card payments', percent=1.0, cost_percent=0.7, volume=100000,
                 currency='USD')]))
        delivery = ventures.scout_delivery_effort(dict(expert_days=2, day_rate=dict(amount=500, currency='USD')))
        ceiling = dict(count=4000, segment='independent hotels in the US', evidence=['V1'])
        expansion = ventures.scout_expansion([dict(name='Revenue management', amount=400, currency='USD',
                                                   period='month', attach_percent=25)])
        economics = ventures.candidate_economics(configuration, business, delivery, dict(count=300), 30,
                                                 ceiling, expansion)
        # 12 x 60 rooms = 720 a month; 0.3% net of 100,000 = 300 a month.
        self.assertEqual(economics['revenue_mix'], dict(recurring=0, per_unit=720.0, take_rate=300.0))
        self.assertEqual((economics['recurring_monthly'], economics['break_even_customers'],
                          economics['break_even_share']), (1020.0, 3, 0.01))
        # Upside is reported but leaves break-even and warnings alone.
        self.assertEqual((economics['expansion_monthly'], economics['ceiling_accounts'],
                          economics['ceiling_annual_revenue'], economics['ceiling_annual_revenue_with_expansion']),
                         (100.0, 4000, 48960000, 53760000))
        self.assertEqual(economics['warnings'], [])
        plain = ventures.candidate_economics(configuration, business, delivery, dict(count=300), 30)
        self.assertEqual((plain['break_even_customers'], plain['ceiling_annual_revenue']), (3, None))
        # A share must earn something, a ceiling cannot be below the reachable
        # accounts, and an expansion needs a share of customers buying it.
        with self.assertRaisesRegex(ValueError, 'cost_percent must be below percent'):
            ventures.scout_business_model(dict(type='saas', revenue_lines=[
                dict(kind='take_rate', label='cards', percent=0.5, cost_percent=0.5, volume=1000, currency='USD')]))
        with self.assertRaisesRegex(ValueError, 'units per customer'):
            ventures.scout_business_model(dict(type='saas', revenue_lines=[
                dict(kind='per_unit', label='seats', amount=10, currency='USD', period='month', unit='seat')]))
        with self.assertRaisesRegex(ValueError, 'cannot be below'):
            ventures.scout_market_ceiling(dict(count=10, segment='x', evidence=['V1']), lambda value, limit: value,
                                          dict(count=300))
        with self.assertRaisesRegex(ValueError, 'attach_percent'):
            ventures.scout_expansion([dict(name='POS', amount=50, currency='USD', period='month', attach_percent=0)])
        # A per-unit or take-rate line is recurring enough for SaaS and a credit.
        self.assertEqual(ventures.scout_business_model(dict(type='saas', revenue_lines=[
            dict(kind='per_unit', label='seats', amount=10, currency='USD', period='year', unit='seat', units=5),
            dict(kind='credit', label='pilot credit', amount=10, currency='USD')]))['type'], 'saas')

    def test_a_scout_idea_carries_its_analogue_incumbent_and_upside(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        proposal = candidate('Marina Desk')
        proposal['prior_state']['driver'] = 'infrastructure'
        proposal['business_model'] = dict(type='saas', revenue_lines=[
            dict(kind='per_unit', label='berths', amount=4, currency='USD', period='month', unit='berth', units=150),
            dict(kind='take_rate', label='berth payments', percent=1.2, cost_percent=0.8, volume=60000,
                 currency='USD', evidence=['V1'])])
        proposal['analogue'] = dict(company='Example Hotel Platform', vertical='hotels', evidence=['V1'],
                                    transfer='A cloud system of record with payments replaced on-premise software.')
        proposal['incumbent'] = dict(name='Legacy Harbour', complaints=[
            dict(claim='Support takes days to answer during the season.', evidence=['V1'])])
        proposal['market_ceiling'] = dict(count=2500, segment='US marinas', evidence=['V1'])
        proposal['expansion'] = [dict(name='Fuel dock POS', amount=90, currency='USD', period='month',
                                      attach_percent=40)]
        # An older answer's key still reads, and an unrated dimension counts as weak.
        dimensions = proposal['confidence_dimensions']
        self.assertIn('regulatory_driver', dimensions)
        dimensions.pop('defensibility')
        uncited = candidate('Dock Payments')
        uncited['thesis'] = 'Boat clubs collect fees by bank transfer and chase late payers by hand.'
        uncited['business_model'] = dict(type='saas', revenue_lines=[
            dict(kind='take_rate', label='fees', percent=1.0, cost_percent=0.5, volume=20000, currency='USD')])
        done = self.scout_with(proposal, uncited)
        self.assertEqual(done['state'], 'ready', done.get('error'))
        # A share of volume must cite where the volume comes from.
        self.assertEqual([item['name'] for item in done['refused_candidates']], ['Dock Payments'])
        self.assertIn('cite', done['refused_candidates'][0]['error'])
        stored = self.stored_candidates()[0]
        self.assertEqual((stored['prior_state']['driver'], stored['analogue']['company'],
                          stored['incumbent']['complaints'][0]['evidence'], stored['market_ceiling']['count']),
                         ('infrastructure', 'Example Hotel Platform', ['V1'], 2500))
        self.assertEqual(sorted(stored['confidence_dimensions']), sorted(
            key for key, _, _ in ventures.SCOUT_CONFIDENCE))
        self.assertEqual(stored['confidence_dimensions']['defensibility'],
                         dict(rating='weak', evidence=[], unrated=True))
        # Without the defensibility rating: 62.5 - 7.5 = 55.
        self.assertEqual(stored['confidence'], 55)
        economics = stored['economics']
        self.assertEqual(economics['revenue_mix'], dict(recurring=0, per_unit=600.0, take_rate=240.0))
        self.assertEqual((economics['expansion_monthly'], economics['ceiling_annual_revenue']), (36.0, 25200000))
        # The prompt teaches the three idea sources and how ideas are rated.
        run = self.studio_runs()[-1]
        for rule in ('FIND IDEAS THREE WAYS', 'paid demand 30%', 'defensibility 15%', 'take_rate', 'market_ceiling',
                     'never inflate a rating'):
            self.assertIn(rule, self.instructions(run))
        self.assertLessEqual(len(run['prompt']), ventures.PROMPT_LIMIT)
        # The brief shows the upside apart from the economics.
        page = self.studio(mode='candidate_brief', candidate_id=stored['id'])['html']
        for shown in ('Example Hotel Platform', 'Legacy Harbour', 'USD 4 per berth a month', '1.2% of USD 60,000 a month',
                      'Upside, not counted in break-even', 'Fuel dock POS'):
            self.assertIn(shown, page)

    def test_review_notes_put_the_checkable_review_questions_in_front_of_the_operator(self):
        configuration = ventures.normalize_config(candidate()['configuration'])
        subscription = ventures.scout_business_model(dict(type='hybrid', revenue_lines=[
            dict(kind='one_off', label='migration', amount=1250, currency='USD'),
            dict(kind='recurring', label='base', amount=299, currency='USD', period='month')]))
        heavy = ventures.scout_delivery_effort(dict(expert_days=15, day_rate=dict(amount=500, currency='USD')))
        economics = ventures.candidate_economics(configuration, subscription, heavy, dict(count=747), 30)
        alternatives = ventures.scout_alternatives([
            dict(name='Cheap Local', kind='software', price=dict(amount=600, currency='USD', period='year'),
                 evidence=['V1']),
            dict(name='Unpriced period', kind='software', price=dict(amount=10, currency='USD'), evidence=['V1']),
            dict(name='Consultant', kind='service', price=dict(amount=20, currency='USD', period='month'),
                 evidence=['V1']),
            dict(name='Spreadsheet', kind='in_house')], lambda value, limit: value)
        self.assertEqual(alternatives[0]['price']['period'], 'year')
        notes = ventures.candidate_review_notes(configuration, alternatives, economics)
        # Only software with a period is compared: 600 a year is 50 a month.
        self.assertIn('above the cheapest cited software alternative (Cheap Local at 50 USD a month)', notes[0])
        # 15 days at 500 is 7,500 against 1,250 + 12 x 299 = 4,838 in the first year.
        self.assertIn('Delivery costs 7500 USD per new customer', notes[1])
        self.assertIn('No revenue grows with the customer', notes[2])
        page = ventures.venture_brief.render(dict(name='Storage Desk', economics=economics, review_notes=notes))
        self.assertEqual(page.count('<b>Review:</b>'), 3)
        # A per-unit price that undercuts the alternative, light delivery: nothing to note.
        units = ventures.scout_business_model(dict(type='saas', revenue_lines=[
            dict(kind='per_unit', label='units', amount=1, currency='USD', period='month', unit='unit', units=40)]))
        light = ventures.scout_delivery_effort(dict(expert_days=1, day_rate=dict(amount=100, currency='USD')))
        fine = ventures.candidate_economics(configuration, units, light, dict(count=747), 30)
        self.assertEqual(ventures.candidate_review_notes(configuration, alternatives, fine), [])
        with self.assertRaisesRegex(ValueError, 'price period'):
            ventures.scout_alternatives([
                dict(name='A', kind='software', price=dict(amount=1, currency='USD', period='week'), evidence=['V1']),
                dict(name='B', kind='service', evidence=['V1']), dict(name='C', kind='in_house')],
                lambda value, limit: value)

    def test_project_review_notes_compare_delivery_with_the_project_price(self):
        configuration = ventures.normalize_config(candidate()['configuration'])
        alternatives = [dict(name='Cheap Local', kind='software',
                             price=dict(amount=600, currency='USD', period='year'), evidence=['V1'])]
        project = dict(kind='one_off', label='project', amount=1000, currency='USD')
        support = dict(kind='recurring', label='support', amount=2400, currency='USD', period='year')
        per_unit = dict(kind='per_unit', label='sites', amount=40, currency='USD', period='month',
                        unit='site', units=1)
        price_note = 'Priced at 200 USD a month per customer, above the cheapest cited software alternative'
        delivery_note = 'Delivery costs 1000 USD per project, more than half of the project price (1000 USD)'
        growth_note = 'No revenue grows with the customer'
        for model in ('service', 'productised_service'):
            for lines, days, expected in (
                    ([project], 2, [delivery_note]),
                    ([project, support], 1, [price_note, growth_note]),
                    ([project, support], 2, [price_note, delivery_note, growth_note]),
                    ([project, per_unit], 2, [delivery_note])):
                with self.subTest(model=model, lines=lines, days=days):
                    business = ventures.scout_business_model(dict(type=model, revenue_lines=lines))
                    effort = ventures.scout_delivery_effort(dict(
                        expert_days=days, day_rate=dict(amount=500, currency='USD')))
                    economics = ventures.candidate_economics(configuration, business, effort, dict(count=747), 30)
                    notes = ventures.candidate_review_notes(configuration, alternatives, economics)
                    self.assertEqual(len(notes), len(expected))
                    for note, fragment in zip(notes, expected):
                        self.assertIn(fragment, note)

    def test_kill_criteria_refuse_only_what_can_be_checked(self):
        economics = dict(runway_months=8.2)
        real = [  # From a real Machinery scout answer.
            'Fewer than 8 of 40 interviewed machine builders rank Annex IV technical-file work a top-3 2027 cost.',
            'Average contract value below EUR 3,000, or customer acquisition cost above 12 months of contract value.',
            'A notified body releases a free Annex IV template covering the core scope.',
        ]
        # Not every item needs money, only one; wording problems are notes.
        self.assertEqual(ventures.scout_kill_criteria(real, economics), real)
        notes = ventures.kill_criteria_notes(real)
        self.assertEqual(len(notes), 2)
        self.assertTrue(notes[0].startswith('Item 1 rests on a proxy'))
        with self.assertRaisesRegex(ValueError, 'at least one must use a paid'):
            ventures.scout_kill_criteria([real[0], real[2]], economics)
        # A payment decides an item even when it mentions a survey or a ranking;
        # a bare amount, such as a competitor's price, does not.
        self.assertEqual(ventures.kill_criteria_notes([
            'Fewer than 3 signed pilots among the survey respondents within 90 days.',
            'Fewer than 2 buyers pay EUR 3,000 after 15 interviews that rank the problem top 3.']), [])
        self.assertEqual(len(ventures.kill_criteria_notes([
            'An incumbent at EUR 99/month or free templates cover 80% of the requested scope.'])), 1)
        # Dates in words count as dates, and a date past the runway is refused.
        late = ventures.now().date() + timedelta(days=200)
        with self.assertRaisesRegex(ValueError, 'beyond the first gate'):
            ventures.scout_kill_criteria(['Fewer than 3 paid pilots before ' + late.strftime('%d %b %Y') + '.'],
                                         economics)
        with self.assertRaisesRegex(ValueError, 'beyond the first gate'):
            ventures.scout_kill_criteria(['No signed contract by ' + late.strftime('%B %Y') + '.'], economics)
        soon = ventures.now().date() + timedelta(days=40)
        self.assertEqual(len(ventures.scout_kill_criteria(
            ['Fewer than 3 paid pilots by ' + soon.strftime('%d %B %Y') + '.'], economics)), 1)
        with self.assertRaisesRegex(ValueError, 'in the past'):
            ventures.scout_kill_criteria(['No paid pilot by 2020-01-01.'], economics)

    def test_a_focus_area_may_be_a_sentence_and_never_overflows_the_scout_prompt(self):
        self.setup_studio_manual()
        focus = ['Fragmented small-business industries with more than 10000 independent operators still running '
                 'on-premise or legacy software', 'Booking and capacity management for independent operators',
                 'Software that takes customer payments for small service businesses',
                 'Independent operators in Belgium the Netherlands and Germany']
        saved = self.studio(mode='studio_policy', studio_focus=focus,
                            studio_exclude=['CRA', 'Machinery Regulation', 'NIS2', 'crypto', 'consumer app'])
        self.assertEqual(saved['studio_focus'], focus)
        with self.assertRaisesRegex(ValueError, 'at most 200 characters; this one has 201'):
            self.studio(mode='studio_policy', studio_focus=['x' * 201])
        with self.assertRaisesRegex(ValueError, 'at most 6 focus areas; 7 were given'):
            self.studio(mode='studio_policy', studio_focus=['area ' + str(index) for index in range(7)])
        # The longest focus list with every exclusion, web research and four
        # advising stakeholders fits inline, with room for the evidence index:
        # the answer rules live in their own file beside the pack.
        longest = ['segment ' + str(index) + ' ' + 'y' * 180 for index in range(6)]
        exclusions = ['excluded term ' + str(index) for index in range(ventures.MAX_STUDIO_EXCLUDE)]
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role=role) for role in ('investor', 'customer', 'operator', 'competitor')])
        self.studio(mode='studio_policy', studio_focus=longest, studio_exclude=exclusions,
                    studio_web_research=True, studio_sources=[])
        run = self.studio(mode='studio_run', kind='scout')
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        self.assertLessEqual(len(job['prompt']) + ventures.PROMPT_INDEX_RESERVE, ventures.PROMPT_LIMIT)
        self.assertIn('Focus: ' + longest[0], job['prompt'])
        self.assertIn('answer-rules.md beside the pack', job['prompt'])
        rules = Path(run['evidence_file']).with_name(ventures.STUDIO_RULES_FILE).read_text(encoding='utf-8')
        self.assertEqual(rules, ventures.scout_answer_rules())
        pack = json.loads(Path(run['evidence_file']).read_text(encoding='utf-8'))
        self.assertEqual((pack['policy']['focus'], pack['policy']['exclusions']), (longest, exclusions))
        # Where the prompt would leave the index no room, focus and exclusions
        # are named by reference; the pack carries them in full.
        frozen = dict(run, prompt='')
        with patch.object(ventures, 'PROMPT_LIMIT', len(job['prompt']) + 100):
            squeezed = self.ventures._studio_prompt('org', frozen, Path(run['evidence_file']))
        self.assertIn('policy.focus and policy.exclusions in the pack', squeezed)
        self.assertNotIn(longest[0], squeezed)

    def test_month_first_kill_dates_are_checked_like_any_other(self):
        # "March 31, 2031" found no date at all, so the window checks were skipped.
        economics = dict(runway_months=8.2)
        soon = ventures.now().date() + timedelta(days=40)
        late = ventures.now().date() + timedelta(days=200)
        for form in ('%B %d, %Y', '%b %d %Y', '%B %d %Y'):
            with self.subTest(form=form):
                self.assertEqual(ventures._kill_dates('by ' + soon.strftime(form)), [soon])
                self.assertEqual(len(ventures.scout_kill_criteria(
                    ['Fewer than 3 paid pilots by ' + soon.strftime(form) + '.'], economics)), 1)
                with self.assertRaisesRegex(ValueError, 'beyond the first gate'):
                    ventures.scout_kill_criteria(['No signed contract by ' + late.strftime(form) + '.'], economics)
        self.assertEqual(ventures._kill_dates('Paid pilot by May 3rd, 2027.'), [date(2027, 5, 3)])
        with self.assertRaisesRegex(ValueError, 'in the past'):
            ventures.scout_kill_criteria(['No paid pilot by March 31, 2020.'], economics)
        with self.assertRaises(ValueError):
            ventures.scout_kill_criteria(['No paid pilot by February 30, 2027.'], economics)
        # A word that only starts like a month is not a date.
        self.assertEqual(ventures._kill_dates('The Mayor 12 2027 budget'), [])

    def test_a_missing_diligence_field_or_unpaid_kill_criterion_refuses_the_idea(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        incomplete = candidate('Incomplete')
        for key in ('prior_state', 'buyer', 'reachable_accounts', 'business_model',
                    'delivery_effort', 'evidence_against', 'dissent', 'confidence_dimensions'):
            incomplete.pop(key)
        incomplete['alternatives'] = [
            dict(name='A', kind='software', evidence=['V1']),
            dict(name='B', kind='software', evidence=['V1']),
            dict(name='C', kind='software', evidence=['V1']),
        ]
        incomplete['kill_criteria'] = ['No pilot after 90 days.']
        done = self.scout_with(incomplete)
        self.assertEqual(done['state'], 'ready', done.get('error'))
        [refused] = done['refused_candidates']
        for field in ('prior_state', 'buyer', 'reachable_accounts', 'business_model', 'delivery_effort',
                      'evidence_against', 'dissent', 'confidence_dimensions', 'kill criteria'):
            self.assertIn(field, refused['error'])

        dated = candidate('Dated Idea')
        dated['kill_criteria'] = ['No paid pilot signed by 2030-01-01.']
        done = self.scout_with(dated)
        self.assertIn('beyond the first gate', done['refused_candidates'][0]['error'])

    def test_a_fractional_confidence_and_a_bad_research_page_do_not_lose_the_ideas(self):
        # The shape a real scout returned: confidence as a fraction, five
        # citations, and one reported page that is not usable.
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='scout')
        self.assertIn('confidence_dimensions', run['prompt'])
        self.assertIn('do not return one confidence', self.instructions(run))
        research = [dict(id='W' + str(number), url='https://eur-lex.example/page-' + str(number),
                         title='Page ' + str(number), published='', claim='Claim ' + str(number) + '. ' + 'x' * 500)
                    for number in range(1, 6)]
        research.append(dict(id='W6', url='http://insecure.example/page', title='Dropped', claim='Not https.'))
        machinery = self.web_candidate('Machinery TechFile', evidence=['W1', 'W2', 'W3', 'W4', 'W5'])
        machinery['confidence'] = 0.42
        machinery['thesis'] = 'Machine builders must keep an Annex IV technical file for every model for ten years.'
        cites_dropped = self.web_candidate('CRA Evidence File', evidence=['W6'])
        cites_dropped['confidence'] = 0.31
        cites_dropped['thesis'] = 'Connected-product makers need a maintained Annex VII documentation file.'
        done = self.complete_studio(run, {'candidates': [machinery, cites_dropped], 'research': research})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        stored = self.stored_candidates()
        self.assertEqual([(item['name'], item['confidence']) for item in stored], [('Machinery TechFile', 62)])
        self.assertEqual(len(done['research']), 5)  # The insecure page was left out, not the run.
        self.assertLessEqual(len(done['research'][0]['claim']), 600)
        refused = done['refused_candidates'][0]
        self.assertEqual(refused['name'], 'CRA Evidence File')
        self.assertIn('W6', refused['error'])  # Names the id that was not usable.

    def test_a_settled_scout_answer_can_be_read_again_without_a_new_meeting(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='scout')
        broken = self.web_candidate('Machinery TechFile')
        broken['confidence'] = 'high'
        kept = self.web_candidate('Dock Ledger')
        kept['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        done = self.complete_studio(run, {'candidates': [kept, broken], 'research': self.RESEARCH})
        self.assertEqual([item['name'] for item in done['refused_candidates']], ['Machinery TechFile'])
        dock = self.stored_candidates()[0]
        self.studio(mode='candidate_decide', candidate_id=dock['id'], decision='reject', reason='Not now.')
        # The rules change (simulated here by a readable saved answer); read it again.
        broken['confidence'] = 42
        job = next(item for item in self.jobs if item['id'] == run['job_id'])
        job['result'] = '```json\n' + json.dumps({'candidates': [kept, broken], 'research': self.RESEARCH}) + '\n```'
        jobs_before = len(self.jobs)
        again = self.studio(mode='studio_recover', run_id=run['id'], inspected=True)
        self.assertEqual((again['state'], again['reread']['added'], again['refused_candidates']),
                         ('ready', ['Machinery TechFile'], []))
        self.assertEqual(len(self.jobs), jobs_before)  # No new meeting.
        states = {item['name']: item['state'] for item in self.stored_candidates()}
        # The rejected one keeps its decision; the re-read one is new.
        self.assertEqual(states, {'Dock Ledger': 'rejected', 'Machinery TechFile': 'proposed'})
        with self.assertRaisesRegex(ValueError, 'Only a studio run needing attention'):
            self.studio(mode='studio_recover', run_id=run['id'], inspected=True)

    def test_a_service_idea_without_its_pilot_fee_is_refused_and_the_scout_is_told_why(self):
        # FysioFront: a productised service with a success fee only; its
        # EUR 1,800 pilot was in the first offer but not in revenue_lines.
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='scout')
        job = next(item for item in self.jobs if item['id'] == run['job_id'])
        prompt = ' '.join(self.instructions(dict(run, prompt=job['prompt'])).split())
        self.assertIn('service and productised_service a one_off one (the first offer\'s fee)', prompt)
        checklist = (Path(run['evidence_file']).parent / ventures.STUDIO_REVIEW_FILE).read_text(encoding='utf-8')
        self.assertIn('a pilot or setup fee is a `one_off` line', ' '.join(checklist.split()))
        idea = self.web_candidate('FysioFront')
        currency = idea['configuration']['market']['currency']
        idea['business_model'] = dict(type='productised_service', revenue_lines=[dict(
            kind='per_unit', label='Verified filled appointment', amount=6, currency=currency, period='month',
            unit='filled appointment', units=30)])
        done = self.complete_studio(run, {'candidates': [idea], 'research': self.RESEARCH})
        self.assertIn('needs a one-off revenue line', done['refused_candidates'][0]['error'])
        # Reading the same answer again under the same rules adds nothing, and says so.
        again = self.studio(mode='studio_recover', run_id=run['id'], inspected=True)
        self.assertEqual((again['reread']['added'], again['reread']['still_refused']), ([], ['FysioFront']))
        # With the pilot fee as a one_off line, the same idea passes.
        idea['business_model']['revenue_lines'].append(dict(kind='one_off', label='90-day pilot', amount=1800,
                                                            currency=currency))
        job['result'] = '```json\n' + json.dumps({'candidates': [idea], 'research': self.RESEARCH}) + '\n```'
        fixed = self.studio(mode='studio_recover', run_id=run['id'], inspected=True)
        self.assertEqual(fixed['reread']['added'], ['FysioFront'])

    def test_approval_can_attach_the_studio_sources_to_an_idea_without_feeds(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='scout')
        self.complete_studio(run, {'candidates': [self.web_candidate()], 'research': self.RESEARCH})
        stored = self.stored_candidates()[0]
        self.assertEqual(stored['configuration']['sources']['feeds'], [])
        # The operator adds a suggested source, then creates the venture with it.
        with self.news(feed_item(venture_feeds.now().isoformat())):
            self.studio(mode='studio_source_add', url='https://trade.example/feed.xml')
        approved = self.studio(mode='candidate_decide', candidate_id=stored['id'], decision='approve',
                               confirmed=True, attach_sources=True)
        venture = self.ventures.get(approved['venture_id'], 'org')
        self.assertEqual(venture['configuration']['sources']['feeds'], ['https://trade.example/feed.xml'])
        self.assertEqual(approved['feeds_attached']['feeds'], ['https://trade.example/feed.xml'])

    def test_an_idea_exports_as_a_printable_brief_that_runs_no_agent_text(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='scout')
        proposal = self.web_candidate('Carrier <Desk>')
        proposal['thesis'] = 'Forwarders overpay carriers.<script>alert(1)</script>'
        proposal['alternatives'][0]['name'] = '<img src=x onerror=alert(2)>'
        done = self.complete_studio(run, {'candidates': [proposal], 'research': self.RESEARCH})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        stored = self.stored_candidates()[0]
        brief = self.studio(mode='candidate_brief', candidate_id=stored['id'])
        page = brief['html']
        self.assertEqual((brief['filename'], brief['content_type']), ('carrier-desk-brief.html', 'text/html'))
        # Agent text is escaped; the only script is the print helper the policy allows by hash.
        self.assertIn('Carrier &lt;Desk&gt;', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertIn('&lt;img src=x onerror=alert(2)&gt;', page)
        self.assertEqual(page.count('<script>'), 1)
        script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)
        policy = html.unescape(re.search(r'Content-Security-Policy" content="([^"]*)"', page).group(1))
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn("script-src 'sha256-" + digest + "'", policy)
        self.assertTrue(policy.startswith("default-src 'none';"))
        # The figures and the cited page travel with the idea onto paper.
        self.assertIn('Business model and unit economics', page)
        self.assertIn('Break-even', page)
        self.assertIn('Kill criteria', page)
        self.assertIn('Forwarders dispute one invoice in nine', page)
        self.assertIn('href="https://trade.example/report"', page)
        self.assertIn('not been verified', page)
        # Malformed saved data still renders: a broken URL, odd shapes, a pruned run.
        odd = dict(stored, confidence_dimensions=['bad'], configuration='bad', economics=None, evidence_links=[])
        page = ventures.venture_brief.render(odd, 'Org', dict(research=[dict(id='W1', url='https://[bad', title='T')]))
        self.assertIn('<b>W1</b> T', page)
        self.assertIn('Business model and unit economics', page)
        # Without the run, the pages saved on the candidate itself still list.
        self.assertIn('trade.example', ventures.venture_brief.render(stored, 'Org', None))
        # Exporting changes nothing, and it stays inside the organization.
        self.assertEqual(self.stored_candidates()[0], stored)
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.ventures.action(dict(mode='candidate_brief', organization_id='org', candidate_id=stored['id']),
                                 'member', 'member')
        with self.assertRaisesRegex(ValueError, 'valid venture candidate'):
            self.studio(mode='candidate_brief', candidate_id='../etc')

    def test_a_field_citing_too_many_real_pages_keeps_three_but_an_unknown_page_refuses(self):
        # A real scout cited five pages for "why now"; that cost the whole idea.
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='scout')
        self.assertIn('every other field cites 1-3', self.instructions(run))
        research = [dict(id='W' + str(number), url='https://trade.example/page-' + str(number),
                         title='Page ' + str(number), published='', claim='Claim ' + str(number) + '.')
                    for number in range(1, 13)]
        wordy = self.web_candidate('Storage Desk', evidence=('W1', 'W2', 'W3', 'W4', 'W5'))
        citations = ['W3', 'W4', 'W5', 'W1', 'W2'] + [page['id'] for page in research[5:]] + ['V1']
        wordy['prior_state']['evidence'] = citations
        invented = self.web_candidate('Dock Ledger', evidence=('W1',))
        invented['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        invented['prior_state']['evidence'] = citations + ['W99']
        done = self.complete_studio(run, {'candidates': [wordy, invented], 'research': research})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        self.assertEqual([(item['name'], 'W99' in item['error']) for item in done['refused_candidates']],
                         [('Dock Ledger', True)])
        stored = self.stored_candidates()[0]
        self.assertEqual((stored['name'], stored['prior_state']['evidence']), ('Storage Desk', ['W3', 'W4', 'W5']))
        self.assertEqual(stored['evidence'], ['W1', 'W2', 'W3', 'W4', 'W5'])

    def setup_panel(self, **body):
        studio = self.ventures._studio('org')
        studio['team'] = dict(settings_by_role=dict(scout=dict(runtime='opencode', provider='opencode-go',
                                                                model='deepseek-v4')))
        self.ventures.save_studio('org', studio)
        self.store.runtime_ready = lambda runtime: None if runtime in ('claude', 'codex', 'opencode') else 'no'
        self.store.template_catalog = lambda project: None
        return self.studio(mode='studio_panel', **body)

    def pitch_runs(self):
        return [run for run in self.studio_runs() if run['kind'] == 'pitch']

    def complete_pitch(self, run, reviews, spoke, summary='They disagree on paid demand.'):
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        members = run['panel']['members']
        job.update(state='artifact_ready',
                   result='```json\n' + json.dumps(dict(reviews=reviews, summary=summary)) + '\n```',
                   contributions=[dict(profile_id=members[role], name=role, round=1, content='My review.')
                                  for role in spoke])
        self.ventures.advance()
        with closing(self.service.connect()) as db:
            return json.loads(db.execute('SELECT data FROM venture_studio_runs WHERE id=?',
                                         (run['id'],)).fetchone()[0])

    @staticmethod
    def review(idea, stakeholder, verdict, objection='The buyer already pays a cheaper tool.'):
        return dict(idea=idea, stakeholder=stakeholder, round=1, verdict=verdict, objections=[objection],
                    questions=['Who signs?'], would_change_mind='Three paid pilots.')

    def test_a_stakeholder_panel_on_other_models_reviews_each_round_and_feeds_the_next(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        # Detected settings prefer CLIs the studio (OpenCode) and the others do not use.
        panel = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer'),
                                               dict(role='competitor')])
        self.assertEqual([panel['settings'][role]['runtime'] for role in panel['roles']],
                         ['claude', 'codex', 'opencode'])
        self.assertEqual((panel['independent'], panel['distinct_models'], panel['warning']), (True, 3, ''))
        hired = [profile for profile in self.profiles if profile.get('role') == 'stakeholder']
        self.assertEqual(sorted(profile['name'] for profile in hired),
                         ['Investor (stakeholder)', 'Market leader (stakeholder)', 'Target customer (stakeholder)'])
        group = next(item for item in self.groups if item['id'] == panel['group_id'])
        self.assertEqual(group['members'], [panel['members'][role] for role in panel['roles']])
        self.assertEqual(self.ventures._studio_summary('org')['panel']['roles'], panel['roles'])

        # Round 1: the scout's new idea is pitched to the panel's own group.
        done = self.scout_with(candidate('Carrier Desk', 'N1'))
        self.assertEqual(done['state'], 'ready', done.get('error'))
        pitch = self.pitch_runs()[0]
        self.assertEqual((pitch['state'], pitch['scout_run_id']), ('meeting', done['id']))
        job = next(job for job in self.jobs if job['id'] == pitch['job_id'])
        self.assertEqual(job['group_id'], panel['group_id'])
        for rule in ('VENTURE STUDIO PITCH TO STAKEHOLDERS', 'one stakeholder at a time',
                     'never write a review for a stakeholder that did not answer', 'Carrier Desk'):
            self.assertIn(rule, job['prompt'])
        pack = json.loads(Path(pitch['evidence_file']).read_text(encoding='utf-8'))
        self.assertEqual(pack['ideas'][0]['name'], 'Carrier Desk')
        self.assertEqual(pack['ideas'][0]['evidence'], ['N1'])
        self.assertEqual([(source['id'], source['link']) for source in pack['sources']],
                         [('N1', 'https://news.example/item-1')])
        self.assertIn('economics', pack['ideas'][0])
        # The next scout waits for the panel: one meeting at a time.
        with self.assertRaisesRegex(ValueError, 'pitch studio run is still in its meeting'):
            self.studio(mode='studio_run', kind='scout')

        # Only a stakeholder that spoke in the transcript can have reviewed.
        settled = self.complete_pitch(pitch, [
            self.review('Carrier Desk', 'investor', 'back'),
            self.review('Carrier Desk', 'customer', 'hold'),
            self.review('Carrier Desk', 'competitor', 'pass'),
            self.review('Carrier Desk', 'operator', 'back'),
            self.review('Unknown Idea', 'investor', 'back')], spoke=['investor', 'customer'])
        self.assertEqual(settled['state'], 'ready', settled.get('error'))
        self.assertEqual((settled['review_count'], settled['unverified'], len(settled['problems'])),
                         (2, ['competitor'], 2))
        self.assertEqual(settled['missing'], [dict(idea='Carrier Desk', stakeholder='competitor')])
        stored = self.stored_candidates()[0]
        self.assertEqual({key: stored['panel'][key] for key in ('back', 'hold', 'pass', 'round', 'reviewers')},
                         dict(back=1, hold=1, **{'pass': 0}, round=1, reviewers=2))
        self.assertEqual(sorted(review['model'] for review in stored['stakeholder_reviews']),
                         ['claude/default', 'codex/default'])
        page = self.studio(mode='candidate_brief', candidate_id=stored['id'])['html']
        for shown in ('What the stakeholders said', 'Round 1: 1 back, 1 hold, 0 pass', 'claude/default',
                      'The buyer already pays a cheaper tool.'):
            self.assertIn(shown, page)

        # Round 2: the feedback is in the next scout's pack, and a revision replaces the idea.
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'], studio_max_candidates=3)
        with self.news(feed_item(venture_feeds.now().isoformat())):
            second = self.studio(mode='studio_run', kind='scout')
        feedback = json.loads(Path(second['evidence_file']).read_text(encoding='utf-8'))['stakeholder_feedback']
        self.assertEqual((feedback[0]['name'], feedback[0]['back'], feedback[0]['hold']), ('Carrier Desk', 1, 1))
        self.assertIn('"revises"', second['prompt'])
        revised = candidate('Carrier Desk Pilot')
        revised.update(revises='carrier desk', revision_note='Priced below the cheaper tool; three paid pilots.')
        unknown = candidate('Dock Ledger')
        unknown.update(revises='Nothing Here',
                       thesis='Warehouse docks double-book trailers because yard schedules live in spreadsheets.')
        done = self.complete_studio(second, {'candidates': [revised, unknown]})
        # Revising an idea that is not open (refused earlier, never recorded)
        # is not a reason to lose the idea: it comes in as a new one.
        self.assertEqual(done['refused_candidates'], [])
        stored = {item['name']: item for item in self.stored_candidates()}
        dock = stored['Dock Ledger']
        self.assertEqual((dock['state'], dock['revises_unmatched'], dock.get('round', 1)),
                         ('proposed', 'Nothing Here', 1))
        self.assertNotIn('revision_of', dock)
        old, new = stored['Carrier Desk'], stored['Carrier Desk Pilot']
        self.assertEqual((old['state'], old['superseded_by']), ('superseded', new['id']))
        # Not excluded as a duplicate of the idea it revises.
        self.assertEqual((new['state'], new['round'], new['revision_of']), ('proposed', 2, old['id']))
        self.assertEqual(new['previous_feedback'][0]['objections'], ['The buyer already pays a cheaper tool.'])
        second_pitch = self.pitch_runs()[-1]
        pack = json.loads(Path(second_pitch['evidence_file']).read_text(encoding='utf-8'))
        self.assertEqual((pack['ideas'][0]['round'], len(pack['ideas'][0]['previous_feedback'])), (2, 2))

    def test_pitch_pack_includes_nested_research_citations_and_can_recover_preparation(self):
        from venture_panel import PANEL_PACK_LIMIT

        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        run = self.studio(mode='studio_run', kind='scout')
        proposal = self.web_candidate()
        proposal['incumbent'] = dict(name='Legacy Carrier', complaints=[
            dict(claim='Reports cannot be exported.', evidence=['W2'])])
        research = self.RESEARCH + [
            dict(self.RESEARCH[0], id='W2', url='https://trade.example/complaints'),
            dict(self.RESEARCH[0], id='W3', url='https://trade.example/unused')]
        with patch('venture_panel.PANEL_PACK_LIMIT', 64):
            done = self.complete_studio(run, {'candidates': [proposal], 'research': research})
        self.assertEqual(done['state'], 'ready', done.get('error'))
        pitch, = self.pitch_runs()
        self.assertEqual((pitch['state'], pitch['dispatch_failed']), ('needs_attention', True))
        self.assertIn('byte budget', pitch['error'])
        self.assertEqual(pitch['candidate_ids'], [self.stored_candidates()[0]['id']])

        recovered = self.studio(mode='studio_recover', run_id=pitch['id'], inspected=True)
        self.assertEqual((recovered['id'], recovered['state']), (pitch['id'], 'meeting'))
        evidence_file = Path(recovered['evidence_file'])
        pack = json.loads(evidence_file.read_text(encoding='utf-8'))
        self.assertLessEqual(evidence_file.stat().st_size, PANEL_PACK_LIMIT)
        self.assertEqual(pack['ideas'][0]['evidence'], ['W1'])
        self.assertEqual([(source['id'], source['url']) for source in pack['sources']],
                         [('W1', self.RESEARCH[0]['url']), ('W2', 'https://trade.example/complaints')])

    def test_an_unexpected_pitch_start_failure_leaves_the_scout_ready(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        with patch.object(self.ventures, '_pitch_prepare', side_effect=RuntimeError('Pitch preparation failed.')):
            done = self.scout_with(candidate())
        self.assertEqual(done['state'], 'ready', done.get('error'))
        pitch, = self.pitch_runs()
        self.assertEqual((pitch['state'], pitch['dispatch_failed'], pitch['error']),
                         ('needs_attention', True, 'Pitch preparation failed.'))
        recovered = self.studio(mode='studio_recover', run_id=pitch['id'], inspected=True)
        self.assertEqual((recovered['id'], recovered['state']), (pitch['id'], 'meeting'))

    def test_a_recovered_pitch_goes_to_the_current_panel_with_the_ideas_still_open(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        first = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        doubted = candidate('Dock Ledger', 'N1')
        doubted['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        with patch('venture_panel.PANEL_PACK_LIMIT', 64):
            self.scout_with(candidate('Carrier Desk'), doubted)
        pitch, = self.pitch_runs()
        self.assertEqual(pitch['state'], 'needs_attention')
        stored = {item['name']: item for item in self.stored_candidates()}
        # Before recovering, the operator decides one idea and swaps the stakeholders' models.
        self.studio(mode='candidate_decide', candidate_id=stored['Dock Ledger']['id'], decision='reject',
                    reason='Not now.')
        second = self.setup_panel(stakeholders=[dict(role='investor', settings=first['settings']['customer']),
                                                dict(role='customer', settings=first['settings']['investor'])])
        self.assertNotEqual(second['members'], first['members'])

        recovered = self.studio(mode='studio_recover', run_id=pitch['id'], inspected=True)
        self.assertEqual(recovered['state'], 'meeting')
        self.assertEqual(recovered['panel']['members'], second['members'])
        self.assertEqual(recovered['candidate_ids'], [stored['Carrier Desk']['id']])
        pack = json.loads(Path(recovered['evidence_file']).read_text(encoding='utf-8'))
        self.assertEqual([idea['name'] for idea in pack['ideas']], ['Carrier Desk'])
        done = self.complete_pitch(recovered, [self.review('Carrier Desk', 'investor', 'back'),
                                               self.review('Carrier Desk', 'customer', 'hold')],
                                   spoke=['investor', 'customer'])
        self.assertEqual((done['state'], done['review_count'], done['unverified']), ('ready', 2, []))

    def test_an_empty_round_still_pitches_open_ideas_the_panel_never_reviewed(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(candidate('Carrier Desk'))  # Proposed before there was a panel.
        policy = dict(self.ventures._studio_policy('org'), studio_scout_cadence='daily',
                      studio_portfolio_cadence='off')
        before = self.ventures._studio_budget('org', policy)
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        after = self.ventures._studio_budget('org', policy)
        # The pitch after a scout round counts toward the maximum, not the guaranteed load.
        self.assertEqual((after['daily_meetings'], after['max_daily_meetings']),
                         (before['daily_meetings'], before['max_daily_meetings'] + 1))
        # Three for the pitch (two stakeholders and the chair), and the two
        # stakeholders again in the scout meeting itself, where they advise.
        self.assertEqual(after['max_agent_sessions'], before['max_agent_sessions'] + 3 + 2)
        self.assertEqual(after['projected_agent_sessions'], before['projected_agent_sessions'] + 2)
        self.setup_panel(scout_consult=False)
        kept = self.ventures._studio_budget('org', policy)
        self.assertEqual(kept['max_agent_sessions'], before['max_agent_sessions'] + 3)
        self.setup_panel(scout_consult=True)

        stored, = self.stored_candidates()
        done = self.scout_with()
        self.assertEqual((done['state'], done['candidates']), ('ready', []))
        pitch, = self.pitch_runs()
        self.assertEqual((pitch['state'], pitch['candidate_ids']), ('meeting', [stored['id']]))
        # While that pitch is open, and once it has settled, the idea is not pitched again.
        with self.assertRaisesRegex(ValueError, 'pitch studio run is still in its meeting'):
            self.scout_with()
        self.complete_pitch(pitch, [self.review('Carrier Desk', 'investor', 'hold'),
                                    self.review('Carrier Desk', 'customer', 'pass')],
                            spoke=['investor', 'customer'])
        self.scout_with()
        self.assertEqual(len(self.pitch_runs()), 1)

    def test_a_mixed_pitch_keeps_sources_from_each_originating_scout_round(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        first = self.studio(mode='studio_run', kind='scout')
        earlier = self.web_candidate('Carrier Desk')
        earlier['buyer']['evidence'] = ['W2']
        old_page = dict(self.RESEARCH[0], id='W2', url='https://trade.example/buyer')
        self.complete_studio(first, {'candidates': [earlier], 'research': self.RESEARCH + [old_page]})

        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        second = self.studio(mode='studio_run', kind='scout')
        fresh = self.web_candidate('Dock Ledger')
        fresh['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        new_page = dict(self.RESEARCH[0], url='https://yard.example/docks', title='A different W1')
        self.complete_studio(second, {'candidates': [fresh], 'research': [new_page]})

        pitch, = self.pitch_runs()
        pack = json.loads(Path(pitch['evidence_file']).read_text(encoding='utf-8'))
        current_idea, backlog_idea = pack['ideas']
        self.assertEqual((current_idea['name'], backlog_idea['name']), ('Dock Ledger', 'Carrier Desk'))
        self.assertNotEqual(current_idea['source_scope'], backlog_idea['source_scope'])
        self.assertEqual(backlog_idea['buyer']['evidence'], ['W2'])
        sources = {(entry['source_scope'], entry['id']): entry['url'] for entry in pack['sources']}
        self.assertEqual(sources, {
            (current_idea['source_scope'], 'W1'): new_page['url'],
            (backlog_idea['source_scope'], 'W1'): self.RESEARCH[0]['url'],
            (backlog_idea['source_scope'], 'W2'): old_page['url'],
        })

    def test_an_oversize_batch_defers_the_backlog_idea_to_the_next_pitch(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        earlier = candidate()
        earlier['product'] += ' Reconcile freight forwarder invoices.' * 30
        first = self.scout_with(earlier)
        old, = self.stored_candidates()
        one_idea = self.ventures._pitch_pack(first, [old])
        limit = len(json.dumps(one_idea, ensure_ascii=False).encode()) + 200
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        fresh = candidate('Dock Ledger')
        fresh['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        fresh['product'] += ' Coordinate warehouse dock scheduling.' * 30
        with patch('venture_panel.PANEL_PACK_LIMIT', limit):
            self.scout_with(fresh)
        pitch, = self.pitch_runs()
        self.assertEqual(pitch['state'], 'meeting', pitch['error'])
        self.assertEqual([idea['name'] for idea in pitch['evidence']['ideas']], ['Dock Ledger'])
        self.complete_pitch(pitch, [self.review('Dock Ledger', 'investor', 'hold')], spoke=['investor'])
        self.scout_with()
        self.assertEqual(self.pitch_runs()[-1]['candidate_ids'], [old['id']])

    def test_a_held_revision_of_an_unreviewed_idea_is_pitched_in_its_place(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(candidate())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=10)
        revision = candidate('Carrier Desk Micro', 'N1')
        revision.update(revises='Carrier Desk', revision_note='Limit the pilot to one site.')
        revision['dissent']['verdict'] = 'hold'
        done = self.scout_with(revision)
        self.assertEqual(done['state'], 'ready', done.get('error'))
        ideas = {item['name']: item for item in self.stored_candidates()}
        self.assertEqual((ideas['Carrier Desk']['state'], ideas['Carrier Desk Micro']['state']),
                         ('superseded', 'proposed'))
        pitch, = self.pitch_runs()
        self.assertEqual(pitch['candidate_ids'], [ideas['Carrier Desk Micro']['id']])
        self.complete_pitch(pitch, [self.review('Carrier Desk Micro', role, 'back')
                                    for role in ('investor', 'customer')], spoke=['investor', 'customer'])
        stored = self.ventures.get_candidate('org', ideas['Carrier Desk Micro']['id'])
        self.assertEqual(stored['state'], 'proposed')
        self.assertIn('The Skeptic would hold', stored['auto']['reason'])

    def test_a_full_backlog_can_be_pitched_without_another_scout_meeting(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(candidate())
        self.studio(mode='studio_policy', studio_candidate_backlog=1, studio_scout_cadence='daily')
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        self.due('scout')
        run = self.studio(mode='studio_run', kind='scout')
        self.assertEqual((run['state'], run.get('job_id')), ('skipped', None))
        pitch, = self.pitch_runs()
        self.assertEqual((pitch['state'], pitch['scout_run_id']), ('meeting', run['id']))
        self.assertGreater(ventures.moment(self.ventures._studio_policy('org')['studio_next_scout_at']),
                           ventures.now())
        self.complete_pitch(pitch, [self.review('Carrier Desk', 'investor', 'hold')], spoke=['investor'])
        with self.assertRaisesRegex(ValueError, 'The venture candidate backlog is full'):
            self.studio(mode='studio_run', kind='scout')

    def test_panel_setup_and_reenable_check_the_combined_session_cap_before_hiring(self):
        self.setup_studio_manual()
        policy = self.studio(mode='studio_policy', studio_scout_cadence='daily', studio_portfolio_cadence='off')
        base = self.ventures._studio_budget('org', policy)['max_agent_sessions']
        stakeholders = [dict(role='investor'), dict(role='customer')]
        self.service.patch_policy('org', dict(daily_session_cap=int(base) + 2), 'test')
        hired = len(self.profiles)
        with self.assertRaisesRegex(ValueError, 'above the organization cap'):
            self.setup_panel(stakeholders=stakeholders)
        self.assertEqual(len(self.profiles), hired)

        self.service.patch_policy('org', dict(daily_session_cap=int(base) + 3), 'test')
        self.setup_panel(stakeholders=stakeholders, scout_consult=False)
        with self.assertRaisesRegex(ValueError, 'above the organization cap'):
            self.setup_panel(scout_consult=True)
        self.assertFalse(self.ventures._panel('org')['scout_consult'])
        self.service.patch_policy('org', dict(daily_session_cap=int(base) + 5), 'test')
        self.setup_panel(scout_consult=True)
        hired = len(self.profiles)
        with self.assertRaisesRegex(ValueError, 'above the organization cap'):
            self.setup_panel(stakeholders=stakeholders + [dict(role='operator')])
        self.assertEqual(len(self.profiles), hired)
        self.service.patch_policy('org', dict(daily_session_cap=int(base) + 2), 'test')
        self.studio(mode='studio_panel', enabled=False)
        self.assertFalse(self.studio(mode='studio_panel', scout_consult=False)['enabled'])
        with self.assertRaisesRegex(ValueError, 'above the organization cap'):
            self.studio(mode='studio_panel')
        self.assertFalse(self.ventures._panel_enabled('org'))

    def test_a_panel_on_the_studio_s_own_model_says_so_and_can_be_switched_off(self):
        self.setup_studio_manual()
        same = dict(runtime='opencode', provider='opencode-go', model='deepseek-v4')
        panel = self.setup_panel(stakeholders=[dict(role='investor', settings=same),
                                               dict(role='customer', settings=same)])
        self.assertFalse(panel['independent'])
        self.assertIn('own model (opencode/deepseek-v4)', panel['warning'])
        with self.assertRaisesRegex(ValueError, '2 to 4'):
            self.studio(mode='studio_panel', stakeholders=[dict(role='investor')])
        with self.assertRaisesRegex(ValueError, 'only once'):
            self.studio(mode='studio_panel', stakeholders=[dict(role='investor'), dict(role='investor')])
        off = self.studio(mode='studio_panel', enabled=False)
        self.assertFalse(off['enabled'])
        self.add_venture_group(self.create())
        self.scout_with(candidate('Carrier Desk'))
        self.assertEqual(self.pitch_runs(), [])  # Switched off: no pitch.
        # Back on keeps the same agents and models; nobody new is hired.
        hired = len(self.profiles)
        on = self.studio(mode='studio_panel')
        self.assertEqual((on['enabled'], on['members'], len(self.profiles)), (True, off['members'], hired))

    def test_panel_changes_codex_approval_mode_without_changing_the_models(self):
        self.setup_studio_manual()
        customer = dict(role='customer', settings=dict(runtime='opencode'))

        def configure(mode):
            return self.setup_panel(stakeholders=[dict(role='investor', settings=dict(
                runtime='codex', permission_mode=mode)), customer])

        first = configure('default')
        updated = configure('codex_no_approval')
        self.assertNotEqual(first['members']['investor'], updated['members']['investor'])
        self.assertEqual(first['members']['customer'], updated['members']['customer'])
        self.assertEqual(updated['settings']['investor']['permission_mode'], 'codex_no_approval')
        group = next(item for item in self.groups if item['id'] == updated['group_id'])
        self.assertEqual(group['permission_mode'], 'codex_no_approval')
        self.assertEqual(configure('codex_no_approval')['members'], updated['members'])
        self.assertEqual(configure('default')['members'], first['members'])
        self.assertEqual(group['members'], list(first['members'].values()))
        self.assertEqual(group['permission_mode'], 'default')

    def test_autonomous_mode_waits_for_the_panel_and_needs_most_stakeholders_to_back(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer'), dict(role='operator')])
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=10)
        backed, doubted = candidate('Carrier Desk'), candidate('Dock Ledger', 'N1')
        doubted['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        incomplete = candidate('Clinic Rota', 'N1')
        incomplete['thesis'] = 'Small clinics lose appointment revenue to unfilled last-minute cancellations.'
        done = self.scout_with(backed, doubted, incomplete)
        self.assertNotIn('auto', done)  # The scout round leaves the decision to the panel.
        self.studio(mode='studio_panel', enabled=False)
        self.complete_pitch(self.pitch_runs()[0], [
            self.review('Carrier Desk', 'investor', 'back'), self.review('Carrier Desk', 'customer', 'back'),
            self.review('Dock Ledger', 'investor', 'back'), self.review('Dock Ledger', 'customer', 'pass'),
            self.review('Dock Ledger', 'operator', 'hold'), self.review('Clinic Rota', 'investor', 'back')],
            spoke=['investor', 'customer', 'operator'])
        stored = {item['name']: item for item in self.stored_candidates()}
        self.assertIn('Most stakeholders did not back it (1 back, 1 hold, 1 pass)', stored['Dock Ledger']['auto']['reason'])
        self.assertIn('Most stakeholders did not back it (1 back, 0 hold, 0 pass)', stored['Clinic Rota']['auto']['reason'])
        self.assertEqual(stored['Clinic Rota']['state'], 'proposed')
        # Backed by the panel, it meets the next cap instead: these sources are internal history.
        self.assertIn('no outside source', stored['Carrier Desk']['auto']['reason'])

    def test_a_pitched_idea_with_no_usable_reviews_has_a_zero_vote_summary(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=10)
        unreviewed = candidate('Dock Ledger', 'N1')
        unreviewed['thesis'] = 'Warehouse docks double-book trailers because yard schedules live in spreadsheets.'
        self.scout_with(candidate(), unreviewed)
        pitch, = self.pitch_runs()

        empty = self.complete_pitch(pitch, [], spoke=[])
        self.assertEqual((empty['state'], empty['review_count']), ('needs_attention', 0))
        self.assertIn('no usable review', empty['error'])
        self.assertEqual(len(empty['missing']), 4)
        self.assertEqual([idea['panel']['reviewers'] for idea in self.stored_candidates()], [0, 0])
        self.scout_with()
        self.assertEqual(len(self.pitch_runs()), 1)  # Still awaiting recovery.

        self.complete_pitch(pitch, [self.review('Carrier Desk', 'investor', 'hold')], spoke=['investor'])
        settled = self.studio(mode='studio_recover', run_id=pitch['id'], inspected=True)
        self.assertEqual((settled['state'], settled['review_count']), ('ready', 1))
        stored = {item['name']: item for item in self.stored_candidates()}
        idea = stored['Dock Ledger']
        self.assertEqual({key: idea['panel'][key] for key in ('back', 'hold', 'pass', 'reviewers', 'stakeholders')},
                         dict(back=0, hold=0, **{'pass': 0}, reviewers=0, stakeholders=2))
        self.assertEqual(idea['state'], 'proposed')
        self.assertIn('Most stakeholders did not back it', idea['auto']['reason'])
        self.assertEqual([item['name'] for item in self.ventures._panel_feedback('org')], ['Carrier Desk'])
        page = self.studio(mode='candidate_brief', candidate_id=idea['id'])['html']
        self.assertIn('Pitched, no reviews.', page)
        self.scout_with()
        retry = self.pitch_runs()[-1]
        self.assertEqual(retry['candidate_ids'], [idea['id']])
        self.complete_pitch(retry, [self.review('Dock Ledger', 'investor', 'hold')], spoke=['investor'])
        self.scout_with()
        self.assertEqual(len(self.pitch_runs()), 2)

    def test_approval_without_attaching_leaves_an_idea_s_feeds_alone(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        done = self.scout_with(candidate())  # Names its own feed from the allowlist.
        stored = self.stored_candidates()[0]
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed', 'https://other.example/feed'])
        approved = self.studio(mode='candidate_decide', candidate_id=stored['id'], decision='approve',
                               confirmed=True, attach_sources=True)
        venture = self.ventures.get(approved['venture_id'], 'org')
        self.assertEqual(done['state'], 'ready')
        self.assertEqual(venture['configuration']['sources']['feeds'], ['https://news.example/feed'])
        self.assertNotIn('feeds_attached', approved)

    def test_autonomous_mode_never_creates_from_web_research_alone(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=0)
        run = self.studio(mode='studio_run', kind='scout')
        self.complete_studio(run, {'candidates': [self.web_candidate()], 'research': self.RESEARCH})
        stored = self.stored_candidates()[0]
        self.assertEqual(stored['state'], 'proposed')
        self.assertIn('not verified by the gateway', stored['auto']['reason'])

    def test_autonomous_mode_holds_a_rejected_or_economically_unsound_candidate(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=0)
        rejected = candidate('Carrier Desk')
        rejected.update(evidence=['N1'], confidence=90)
        rejected['dissent']['verdict'] = 'reject'
        self.scout_with(rejected)
        stored = self.stored_candidates()[0]
        self.assertEqual(stored['state'], 'proposed')
        self.assertIn('Skeptic would reject', stored['auto']['reason'])

        held = candidate('Clinic Rota')
        held.update(evidence=['N1'], thesis='Small clinics lose appointment revenue to unfilled cancellations.')
        held['dissent']['verdict'] = 'hold'
        self.scout_with(held)
        stored = {item['name']: item for item in self.stored_candidates()}['Clinic Rota']
        self.assertEqual(stored['state'], 'proposed')
        self.assertIn('Skeptic would hold', stored['auto']['reason'])

        unsound = candidate('Dock Ledger')
        unsound.update(evidence=['N1'], confidence=90,
                       thesis='Warehouse docks double-book trailers because yard schedules live in spreadsheets.')
        unsound['reachable_accounts']['count'] = 1
        self.scout_with(unsound)
        stored = {item['name']: item for item in self.stored_candidates()}['Dock Ledger']
        self.assertEqual(stored['state'], 'proposed')
        self.assertIn('gateway economics flag', stored['auto']['reason'])

    def test_evidence_is_written_inside_the_facilitator_worktree(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        worktree = self.projects / '.herdr-worktrees' / 'studio-facilitator'
        worktree.mkdir(parents=True)
        project_git.git(worktree, 'init', '-b', 'main')
        self.store.supports_discussion_preparation = True
        import discussion_scheduler
        with patch.object(discussion_scheduler, 'ensure_facilitator',
                          return_value=dict(worktree_path=str(worktree))) as prepared:
            run = self.studio(mode='studio_run', kind='portfolio')
        prepared.assert_called_once_with(self.store, 'a1' * 16, 'org')  # The facilitator is started first.
        target = Path(run['evidence_file']).resolve()
        self.assertTrue(target.is_file())
        self.assertTrue(target.is_relative_to(worktree.resolve()))
        self.assertIn(run['evidence_file'], run['prompt'])
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['kind'], 'portfolio')
        # Nothing is left in the source notebook, which the facilitator cannot read.
        self.assertFalse((self.projects / 'ventures' / 'studio-org' / '.ci-cache' / 'herdr-studio').exists())

    def test_a_stopped_meeting_names_its_failure_and_repair_brings_the_studio_back(self):
        self.setup_studio_manual()
        record = self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='portfolio')
        # One agent failed to launch while Herdr was down; the meeting stopped in preparation.
        self.jobs.append(dict(id='c' * 32, kind='launch', state='needs_attention', profile_id='q' * 32,
                              organization_id='org', error='server_not_running: nothing at herdr.sock'))
        job = next(item for item in self.jobs if item['id'] == run['job_id'])
        job.update(state='needs_attention', error='Venture Scout: session preparation failed; inspect its run')
        self.ventures.advance()
        failed = self.studio_runs()[0]
        failure = failed['failure']
        self.assertEqual((failed['state'], failure['category'], failure['phase'], failure['action'], failure['agent'],
                          failure['job_id']),
                         ('needs_attention', 'team', 'preparation', 'repair', 'Venture Scout', run['job_id']))
        self.assertIn('Venture Scout: session preparation failed', failed['error'])  # Not a generic message.
        self.assertEqual((self.check('sessions')['ok'], self.check('sessions')['action']), (False, 'repair'))
        with self.assertRaisesRegex(ValueError, 'Venture Scout did not start'):
            self.studio(mode='studio_run', kind='portfolio')
        preview = self.studio(mode='studio_repair')
        self.assertTrue(preview['preview'])
        steps = {item['step']: item for item in preview['steps']}
        self.assertEqual((steps['herdr']['needed'], steps['sessions']['needed'], steps['run']['action']),
                         (False, True, 'retry'))
        self.assertEqual(job['state'], 'needs_attention')  # A preview changes nothing.
        result = self.studio(mode='studio_repair', confirmed=True)
        self.assertTrue(result['healthy'], result['steps'])
        launch = next(item for item in self.jobs if item['id'] == 'c' * 32)
        self.assertEqual((launch['state'], job['state'], self.studio_runs()[0]['state']), ('released', 'queued', 'meeting'))
        self.assertTrue(result['readiness']['can_run'])
        self.assertEqual(self.complete_studio(run, self.portfolio_answer(record))['state'], 'ready')

    def test_repair_releases_a_stuck_stakeholder_session_so_the_pitch_can_retry(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        panel = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        self.scout_with(candidate('Carrier Desk'))
        pitch, = self.pitch_runs()
        # The investor's session stopped on a startup question; preparation refused to reuse it.
        # Any unreleased state preparation will not reuse counts as stuck, not only needs_attention.
        self.jobs.append(dict(id='d' * 32, kind='launch', state='cancelled', organization_id='org',
                              profile_id=panel['members']['investor'],
                              error='The agent needs a decision; no prompt input was sent.'))
        # The chair still runs on the model the first stakeholder had before the panel changed.
        group = next(item for item in self.groups if item['id'] == panel['group_id'])
        chair = next(item for item in self.profiles if item['id'] == group['facilitator_id'])
        chair.update(runtime=panel['settings']['investor']['runtime'],
                     model=panel['settings']['investor'].get('model', ''))
        self.jobs.append(dict(id='e' * 32, kind='launch', state='persona_sent', organization_id='org',
                              profile_id=chair['id'], profile=dict(chair, runtime='opencode', model='deepseek-v4.1-flash')))
        job = next(item for item in self.jobs if item['id'] == pitch['job_id'])
        job.update(state='needs_attention',
                   error='Investor (stakeholder): inspect the existing session before meeting preparation')
        self.ventures.advance()
        self.assertEqual(self.pitch_runs()[0]['state'], 'needs_attention')
        self.assertEqual((self.check('panel_sessions')['ok'], self.check('panel_sessions')['action']), (False, 'repair'))
        self.assertIn('Investor (stakeholder) did not start', self.check('panel_sessions')['detail'])
        self.assertTrue(self.check('sessions')['ok'])  # The studio's own runs are not held up.
        steps = {item['step']: item for item in self.studio(mode='studio_repair')['steps']}
        self.assertEqual((steps['sessions']['needed'], steps['run']['action']), (True, 'retry'))
        self.assertIn('Investor (stakeholder)', steps['sessions']['detail'])
        result = self.studio(mode='studio_repair', confirmed=True)
        self.assertTrue(result['healthy'], result['steps'])
        launch = next(item for item in self.jobs if item['id'] == 'd' * 32)
        self.assertEqual((launch['state'], job['state'], self.pitch_runs()[0]['state']), ('released', 'queued', 'meeting'))
        chair_run = next(item for item in self.jobs if item['id'] == 'e' * 32)
        self.assertEqual(chair_run['state'], 'released')
        self.assertTrue(any('panel chair, which ran on opencode/deepseek-v4.1-flash' in item['detail']
                            for item in result['steps']), result['steps'])

    def test_the_scout_explores_and_screens_with_the_stakeholders_before_proposing(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        panel = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        studio = self.ventures._studio('org')
        studio['team']['members'] = {'critic': 'r' * 32}
        self.ventures.save_studio('org', studio)
        self.assertIs(panel['scout_consult'], True)
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'], studio_max_candidates=3)
        with self.news(feed_item(venture_feeds.now().isoformat())):
            run = self.studio(mode='studio_run', kind='scout')
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        # The stakeholders attend the scout meeting as advisors, in panel order.
        self.assertEqual(job['advisors'], [panel['members'][role] for role in panel['roles']])
        self.assertIn('Stakeholder advisors attend: work with them as stakeholders.md', job['prompt'])
        method = (Path(run['evidence_file']).parent / ventures.STUDIO_CONSULT_FILE).read_text(encoding='utf-8')
        method = ' '.join(method.split())
        for rule in ('Explore first', 'one segment the studio may be overlooking', 'Screen a rough shortlist',
                     'one piece of evidence', 'Pivot when a segment looks stronger',
                     'never because a stakeholder liked the idea', 'Never show one stakeholder'):
            self.assertIn(rule, method)
        self.assertIn('investor: ' + panel['members']['investor'], method)
        idea = dict(candidate('Carrier Desk', 'N1'), stakeholder_screen=[
            dict(stakeholder='investor', objection='Buyers already pay a cheaper tool.',
                 would_change_view='Three paid pilots.', response='Found a segment the tool cannot serve.'),
            # The customer never spoke in the meeting: its screen is not counted.
            dict(stakeholder='customer', objection='Too expensive.'),
            dict(stakeholder='competitor', objection='Not on this panel.'),
            dict(stakeholder=['investor'], objection='Malformed.'),
            'not an entry'])
        job.update(state='artifact_ready', result='```json\n' + json.dumps(dict(
            candidates=[idea], stakeholder_exploration=[
                dict(stakeholder='investor', spend='Carrier fees', segments='Small fleets',
                     workarounds='Spreadsheets', overlooked='Freight brokers'),
                dict(stakeholder='customer', spend='Invented.'),
                dict(stakeholder={'role': 'investor'}, spend='Malformed.')])) + '\n```',
            contributions=[dict(profile_id=panel['members']['investor'], name='investor', round=1,
                                content='Small fleets pay carrier fees.')])
        self.ventures.advance()
        done = next(item for item in self.studio_runs() if item['id'] == run['id'])
        self.assertEqual(done['state'], 'ready', done.get('error'))
        self.assertTrue(done['skeptic_silent'])
        self.assertEqual(done['consulted'], ['investor'])
        self.assertEqual([entry['stakeholder'] for entry in done['stakeholder_exploration']], ['investor'])
        self.assertEqual(done['stakeholder_exploration'][0]['overlooked'], 'Freight brokers')
        stored, = [item for item in self.stored_candidates() if item['name'] == 'Carrier Desk']
        self.assertEqual(stored['stakeholder_screen'], [dict(
            stakeholder='investor', objection='Buyers already pay a cheaper tool.',
            would_change_view='Three paid pilots.', response='Found a segment the tool cannot serve.')])
        # The pitch shows the panel what it said on the shortlist and asks whether it is answered.
        pitch, = self.pitch_runs()
        pitch_job = next(item for item in self.jobs if item['id'] == pitch['job_id'])
        self.assertEqual(pitch_job['advisors'], [])
        self.assertIn('say whether each screened objection is answered', pitch_job['prompt'])
        self.assertIn('stays with the chair until all first-round reviews are in', pitch_job['prompt'])
        pack = json.loads(Path(pitch['evidence_file']).read_text(encoding='utf-8'))
        self.assertEqual(pack['ideas'][0]['stakeholder_screen'], stored['stakeholder_screen'])
        self.assertTrue(pack['ideas'][0]['pre_screened'])
        other_panel = dict(members=dict(investor='replacement', customer=panel['members']['customer']))
        self.assertNotIn('pre_screened', self.ventures._pitch_pack(done, [stored], panel=other_panel)['ideas'][0])
        summaries = {item['id']: item for item in self.ventures._studio_summary('org')['runs']}
        self.assertEqual(summaries[run['id']]['consult_roles'], ['investor', 'customer'])
        self.assertEqual(summaries[run['id']]['stakeholder_exploration'], done['stakeholder_exploration'])
        self.assertEqual(summaries[run['id']]['consulted'], ['investor'])
        self.assertEqual(summaries[pitch['id']]['pre_screened_ideas'], ['Carrier Desk'])

    def test_the_stakeholders_can_be_kept_to_the_pitch(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        panel = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')], scout_consult=False)
        self.assertIs(panel['scout_consult'], False)
        with self.assertRaisesRegex(ValueError, 'true or false'):
            self.setup_panel(scout_consult='yes')
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'], studio_max_candidates=3)
        with self.news(feed_item(venture_feeds.now().isoformat())):
            run = self.studio(mode='studio_run', kind='scout')
        job = next(job for job in self.jobs if job['id'] == run['job_id'])
        self.assertEqual(job['advisors'], [])
        self.assertNotIn('advisors', job['prompt'])
        self.assertFalse((Path(run['evidence_file']).parent / ventures.STUDIO_CONSULT_FILE).exists())
        # Switching it back on keeps the stakeholders and models already chosen.
        again = self.setup_panel(scout_consult=True)
        self.assertEqual((again['scout_consult'], again['members']), (True, panel['members']))

    def test_a_stuck_stakeholder_holds_up_a_scout_that_talks_to_it(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        panel = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        self.jobs.append(dict(id='d' * 32, kind='launch', state='needs_attention', organization_id='org',
                              profile_id=panel['members']['investor'], error='Stopped on a question.'))
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'], studio_max_candidates=3)
        with self.news(feed_item(venture_feeds.now().isoformat())):
            with self.assertRaisesRegex(ValueError, 'Investor \\(stakeholder\\) did not start'):
                self.studio(mode='studio_run', kind='scout')
        readiness = self.readiness()
        self.assertTrue(readiness['can_run'])
        self.assertFalse(readiness['can_scout'])
        self.assertIn('panel_sessions', readiness['scout_blocking'])
        self.service.patch_policy('org', dict(max_active_sessions=readiness['participants']), 'test')
        limited = self.readiness()
        self.assertTrue(limited['can_run'])
        self.assertIn('scout_limits', limited['scout_blocking'])
        fixed = self.studio(mode='studio_limits')
        self.assertEqual(fixed['changed']['max_active_sessions'], readiness['participants'] + 2)
        self.assertNotIn('scout_limits', self.readiness()['scout_blocking'])
        self.setup_panel(scout_consult=False)
        with self.news(feed_item(venture_feeds.now().isoformat())):
            self.assertEqual(self.studio(mode='studio_run', kind='scout')['state'], 'meeting')

    def test_a_chair_with_outdated_command_approvals_or_folder_access_is_released(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        panel = self.setup_panel(stakeholders=[dict(role='investor', settings=dict(
            runtime='codex', permission_mode='codex_no_approval')), dict(role='customer')])
        group = next(item for item in self.groups if item['id'] == panel['group_id'])
        chair = next(item for item in self.profiles if item['id'] == group['facilitator_id'])
        first = next(item for item in self.profiles if item['id'] == group['members'][0])
        chair.update({key: first.get(key, '') for key in ('runtime', 'provider', 'model', 'reasoning')})
        outputs = '/home/herdr/.config/herdr-web/discussion-artifacts'
        cases = [
            ('a' * 32, dict(permission_mode='default'), ['--add-dir', outputs], 'other command approvals'),
            ('b' * 32, dict(permission_mode='codex_no_approval'), ['--add-dir', '/x/chat-replies'],
             'could not save meeting files'),
            ('c' * 32, dict(permission_mode='codex_no_approval'), ['--add-dir', outputs], ''),
            ('d' * 32, dict(permission_mode='codex_no_approval'), None, ''),  # Unknown launch: kept.
        ]
        for job_id, started, arguments, expected in cases:
            with self.subTest(expected=expected):
                run = dict(id=job_id, kind='launch', state='persona_sent', organization_id='org',
                           profile_id=chair['id'], profile=dict(chair, **started))
                if arguments is not None:
                    run['launch_arguments'] = arguments
                self.jobs.append(run)
                message = self.ventures._refresh_panel_chair('org')
                self.assertIn(expected, message)
                self.assertEqual(run['state'], 'released' if expected else 'persona_sent')
                if run['state'] == 'persona_sent':
                    run['state'] = 'released'

    def test_a_pitch_out_of_preparation_retries_is_pitched_again_with_its_open_ideas(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        panel = self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')])
        self.scout_with(candidate('Carrier Desk'))
        pitch, = self.pitch_runs()
        job = next(item for item in self.jobs if item['id'] == pitch['job_id'])
        job.update(state='needs_attention', preparation_attempt=3,
                   error='Preparation retry limit reached; inspect and create a new meeting.')
        self.jobs.append(dict(id='d' * 32, kind='launch', state='needs_attention', organization_id='org',
                              profile_id=panel['members']['investor'], error='Needs a decision.'))
        self.ventures.advance()
        hint = self.ventures._recover_hint(self.pitch_runs()[0])
        self.assertEqual((hint['action'], hint['label']), ('restart', 'Pitch again'))
        self.studio(mode='studio_recover', run_id=pitch['id'], inspected=True)
        old, fresh = sorted(self.pitch_runs(), key=lambda item: item['id'] != pitch['id'])
        self.assertEqual((old['state'], job['state']), ('dismissed', 'cancelled'))
        self.assertEqual((fresh['state'], fresh['candidate_ids']), ('meeting', pitch['candidate_ids']))
        self.assertEqual(next(item for item in self.jobs if item['id'] == 'd' * 32)['state'], 'released')

    def test_scout_prices_may_have_cents(self):
        alternatives = ventures.scout_alternatives([
            dict(name='Carsu', kind='software', price=dict(amount=29.9, currency='EUR', period='month'),
                 evidence=['W1']),
            dict(name='Bookkeeper', kind='service', evidence=['W2']), dict(name='Spreadsheet', kind='in_house')],
            lambda value, limit: value)
        self.assertEqual(alternatives[0]['price']['amount'], 29.9)
        business = ventures.scout_business_model(dict(type='saas', revenue_lines=[
            dict(kind='per_unit', label='box', amount=0.89, currency='EUR', period='month', unit='box', units=100),
            dict(kind='recurring', label='base', amount=50.0, currency='EUR', period='month')]))
        self.assertEqual([line['amount'] for line in business['revenue_lines']], [0.89, 50])
        for bad in (-1, float('nan'), '29.90', True):
            with self.subTest(amount=bad), self.assertRaisesRegex(ValueError, 'alternative 1 price amount'):
                ventures.scout_alternatives([dict(name='X', kind='software', evidence=['W1'],
                                                  price=dict(amount=bad, currency='EUR', period='month')),
                                             dict(name='Y', kind='service', evidence=['W2']), dict(name='Z', kind='in_house')],
                                            lambda value, limit: value)
        from venture_brief import _money
        self.assertEqual((_money(0.89, 'EUR'), _money(29.9, 'EUR'), _money(1250, 'EUR')),
                         ('EUR 0.89', 'EUR 29.90', 'EUR 1,250'))

    def test_repair_starts_herdr_and_sends_a_prompt_that_never_arrived(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        original = self.store.action.side_effect
        attempts = []

        def stopped(action, body, **kwargs):
            if action == 'discuss':
                attempts.append(body['request_id'])
                if len(attempts) == 1:
                    raise ValueError('server_not_running: start Herdr over SSH first.')
            return original(action, body, **kwargs)
        self.store.action.side_effect = stopped
        with self.assertRaisesRegex(ValueError, 'server_not_running'):
            self.studio(mode='studio_run', kind='portfolio')
        failure = self.studio_runs()[0]['failure']
        self.assertEqual((failure['category'], failure['phase'], failure['action']), ('herdr', 'dispatch', 'start_herdr'))
        fake = self.herdr('stopped')
        result = self.studio(mode='studio_repair', confirmed=True)
        self.assertTrue(result['healthy'], result['steps'])
        self.assertEqual([item['step'] for item in result['steps']], ['herdr', 'run'])
        self.assertEqual((fake.starts, self.studio_runs()[0]['state'], len(self.jobs)), (1, 'meeting', 1))
        self.assertEqual(len(set(attempts)), 1)  # The same request: never two meetings.

    def test_repair_closes_a_result_that_cannot_validate(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='portfolio')
        failed = self.complete_studio(run, {'summary': 'x', 'recommendations': []})
        self.assertEqual((failed['failure']['category'], failed['failure']['phase'], failed['failure']['action']),
                         ('result', 'validation', 'dismiss'))
        result = self.studio(mode='studio_repair', confirmed=True)
        self.assertTrue(result['healthy'], result['steps'])
        closed = self.studio_runs()[0]
        self.assertEqual(closed['state'], 'dismissed')
        self.assertIn('did not validate', closed['dismissed']['reason'])

    def test_a_blocked_agent_is_shown_on_the_run_while_it_meets(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        run = self.studio(mode='studio_run', kind='portfolio')
        job = next(item for item in self.jobs if item['id'] == run['job_id'])
        job.update(state='waiting_for_input', progress='Studio facilitator asks to read evidence.json outside its checkout')
        self.ventures.advance()
        shown = self.ventures._studio_summary('org')['runs'][0]
        self.assertEqual((shown['state'], shown['waiting']['category'], shown['waiting']['action'],
                          shown['waiting']['job_id']), ('meeting', 'permission', 'open_group', run['job_id']))
        self.assertIn('evidence.json', shown['waiting']['message'])
        job.update(state='running', progress='')
        self.ventures.advance()
        self.assertIsNone(self.studio_runs()[0]['waiting'])

    def test_limits_too_small_for_a_studio_meeting_are_named_and_raised_in_one_action(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.service.patch_policy('org', dict(max_active_sessions=2), 'test')
        with self.assertRaisesRegex(ValueError, 'limit of 2 concurrent sessions is below the 5'):
            self.studio(mode='studio_run', kind='portfolio')
        with self.assertRaisesRegex(ValueError, 'Raise the limits'):
            self.studio(mode='studio_policy', studio_portfolio_cadence='daily')
        self.assertEqual((self.check('limits')['ok'], self.check('limits')['action']), (False, 'limits'))
        self.assertEqual(self.studio(mode='studio_limits')['changed'], dict(max_active_sessions=5))
        self.assertEqual(self.studio(mode='studio_limits')['changed'], {})  # Never lowers, never repeats.
        self.assertEqual(self.studio(mode='studio_run', kind='portfolio')['state'], 'meeting')

    def test_a_venture_team_needs_no_template_agent(self):
        self.store.runtime_ready = lambda runtime: None if runtime == 'opencode' else 'not ready'
        record = self.create()
        teamed = self.ventures.action(dict(mode='team', organization_id='org', venture_id=record['id'],
                                           confirmed=True), 'admin', 'admin')
        self.assertTrue(teamed['group_id'])
        self.assertEqual(teamed['team']['settings_source'], 'automatic')
        self.assertEqual({profile['runtime'] for profile in self.profiles if profile.get('role') in engine_roles()},
                         {'opencode'})

    def test_proposal_mode_is_the_default_and_autonomous_mode_creates_within_its_caps(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.profiles.append(dict(id='z' * 32, name='AAA template', organization_id='org', project=str(self.projects),
                                  use_worktree=True, runtime='codex', model='m'))
        self.assertEqual(self.ventures._studio_summary('org')['mode'], 'proposal')
        with self.assertRaisesRegex(ValueError, 'studio mode'):
            self.studio(mode='studio_policy', studio_mode='unattended')
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=60)
        strong = candidate('Carrier Desk')
        strong.update(evidence=['N1'])
        for factor in strong['confidence_dimensions'].values():
            factor['rating'] = 'strong'
        weak = candidate('Dock Ledger')
        weak.update(evidence=['N1'],
                    thesis='Warehouse docks double-book trailers because yard schedules live in spreadsheets.')
        for factor in weak['confidence_dimensions'].values():
            factor['rating'] = 'weak'
        internal = candidate('Clinic Rota')
        internal.update(confidence=95, evidence=['V1'],
                        thesis='Small clinics lose appointment revenue to unfilled last-minute cancellations.')
        run = self.scout_with(strong, weak, internal)
        self.assertEqual(run['state'], 'ready', run.get('error'))
        stored = {item['name']: item for item in self.stored_candidates()}
        created = stored['Carrier Desk']
        self.assertEqual((created['state'], created['decision']['by'], created['auto']['created']),
                         ('created', 'venture_studio_auto', True))
        venture = self.ventures.get(created['venture_id'], 'org')
        self.assertTrue(venture['group_id'])
        self.assertEqual((venture['configuration']['simulation']['daily_ticks'],
                          venture['configuration']['simulation']['meeting_cadence']), (True, 'news_only'))
        # The others stay proposals, each with the cap that stopped it.
        self.assertEqual((stored['Dock Ledger']['state'], stored['Clinic Rota']['state']), ('proposed', 'proposed'))
        self.assertIn('below the minimum of 60', stored['Dock Ledger']['auto']['reason'])
        self.assertIn('no outside source', stored['Clinic Rota']['auto']['reason'])
        self.assertEqual(sorted((item['name'], item['created']) for item in self.studio_runs()[-1]['auto']),
                         [('Carrier Desk', True), ('Clinic Rota', False), ('Dock Ledger', False)])

    def test_autonomous_mode_respects_the_active_venture_limit(self):
        self.setup_studio_manual()
        record = self.add_venture_group(self.create())
        self.studio(mode='resume', venture_id=record['id'])
        self.studio(mode='limits', max_active_ventures=1)
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=0)
        proposal = candidate('Carrier Desk')
        proposal.update(evidence=['N1'])
        self.scout_with(proposal)
        stored = self.stored_candidates()[0]
        self.assertEqual(stored['state'], 'proposed')
        self.assertIn('active venture limit of 1', stored['auto']['reason'])


class FakeHerdr:
    """Herdr status and start without a server."""

    def __init__(self, state='stopped'):
        self.state = state
        self.starts = 0

    def status(self):
        return {'state': self.state}

    def start(self, body):
        self.starts += 1
        self.state = 'running'
        return {'state': 'running', 'already_running': False}


if __name__ == '__main__':
    unittest.main()
