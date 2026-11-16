from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from contributions import Contributions
import project_git
import venture_feeds
import venture_notebook
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


def candidate(name='Carrier Desk'):
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
        'assumptions': ['Carriers will share invoice data.'],
        'kill_criteria': ['No pilot after 90 days.'],
        'confidence': 70,
        'evidence': ['V1'],
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

    def store_action(self, action, body):
        if action == 'discuss':
            job = dict(id=uuid.uuid4().hex, kind='discussion', state='queued',
                       organization_id=body['organization_id'], group_id=body['group_id'], prompt=body['prompt'])
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
        if body.get('id'):
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
        if action == 'group':
            member = next(profile for profile in self.profiles if profile['id'] == body['members'][0])
            facilitator = dict(id=uuid.uuid4().hex, organization_id=body['organization_id'],
                               name=body['name'] + ' facilitator', project=member['project'],
                               use_worktree=True, runtime='codex', group_id='g' * 32)
            self.profiles.append(facilitator)
            group = dict(id='g' * 32, organization_id=body['organization_id'], name=body['name'],
                         members=body['members'], read_only=True, facilitator_id=facilitator['id'])
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
        self.configure(record, daily_ticks=True, tick_hour_utc=hour)
        saved = self.ventures.get(record['id'], 'org')
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
        record = self.create()
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
        def limited(action, body):
            if action == 'discuss':
                requests.append(body['request_id'])
                if len(requests) == 1:
                    raise ValueError('Meeting limit reached.')
            return original(action, body)
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
        # No sources yet: the studio is created, the scout says what it needs.
        result = self.ventures.action(dict(mode='studio_start', confirmed=True, first_scout=True,
                                           studio_focus=['freight']), 'admin', 'admin')
        self.assertTrue(result['studio']['group_id'])  # The studio exists; only the run did not start.
        self.assertEqual((result['first_scout']['state'], result['first_scout']['category']), ('not_started', 'inputs'))
        self.assertEqual(self.studio_runs(), [])
        self.studio(mode='studio_policy', studio_sources=['https://news.example/feed'])
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
        self.service.patch_policy('org', dict(studio_focus=[]), 'test')
        with self.assertRaisesRegex(ValueError, 'add a scouting source and a focus area'):
            self.studio(mode='studio_run', kind='scout')
        self.assertEqual(self.studio_runs(), [])
        readiness = self.readiness()
        self.assertEqual((readiness['can_run'], readiness['can_scout'], readiness['coverage']),
                         (True, False, dict(sources=0, focus=0)))
        self.assertEqual(self.check('sources')['action'], 'configure')
        self.studio(mode='studio_policy', studio_scout_cadence='daily')
        self.due('scout')
        self.ventures.advance()
        schedule = self.ventures._studio_summary('org')['schedule']['scout']
        self.assertIn('no market evidence', schedule['waiting'])
        self.assertEqual((schedule['failures'], self.studio_runs()), (0, []))
        self.assertEqual(self.studio(mode='studio_run', kind='scout', override=True)['state'], 'meeting')

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

    def test_repair_starts_herdr_and_sends_a_prompt_that_never_arrived(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        original = self.store.action.side_effect
        attempts = []

        def stopped(action, body):
            if action == 'discuss':
                attempts.append(body['request_id'])
                if len(attempts) == 1:
                    raise ValueError('server_not_running: start Herdr over SSH first.')
            return original(action, body)
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
        strong.update(confidence=80, evidence=['N1'])
        weak = candidate('Dock Ledger')
        weak.update(confidence=40, evidence=['N1'],
                    thesis='Warehouse docks double-book trailers because yard schedules live in spreadsheets.')
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
        self.add_venture_group(self.create())
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
