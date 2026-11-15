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
        self.store = MagicMock()
        self.store.snapshot.side_effect = lambda **kw: dict(
            organizations=[dict(id='org', name='Product organization')],
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

    def test_create_and_configure_are_admin_only(self):
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.ventures.action(dict(mode='create', organization_id='org', name='x'), 'viewer', 'operator')
        record = self.create()
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.ventures.action(dict(mode='pause', organization_id='org', venture_id=record['id']), 'viewer', 'operator')


if __name__ == '__main__':
    unittest.main()
