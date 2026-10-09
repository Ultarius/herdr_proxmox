import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from contextlib import closing
from datetime import timedelta
import uuid

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from contributions import Contributions
import discovery


@unittest.skipUnless(shutil.which('git'), 'Git required')
class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.test')
        (self.repo / 'README.md').write_text('# Product\nA useful application.\npassword=hidden-value\n', encoding='utf-8')
        (self.repo / 'docs').mkdir()
        (self.repo / 'docs/existing.md').write_text('Existing documentation.', encoding='utf-8')
        (self.repo / '.env').write_text('SECRET=do-not-collect', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.git('remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.jobs = []
        self.worker = dict(id='worker', name='Maya', organization_id='org', project=str(self.repo), use_worktree=True)
        self.group = dict(facilitator_id='facilitator', id='product', name='Product', organization_id='org', members=['worker', 'reviewer'], read_only=True)
        self.jobs.append(dict(id='group-run', kind='launch', profile_id='facilitator', state='persona_sent', source_project=str(self.repo)))
        self.store = MagicMock()
        self.store.snapshot.side_effect = lambda **kw: dict(organizations=[dict(id='org', name='Product organization')],
            profiles=[self.worker], groups=[self.group], jobs=self.jobs)
        self.store.action.side_effect = self.meeting
        self.store.update_job.side_effect = lambda identity, **fields: next(j for j in self.jobs if j['id'] == identity).update(fields)
        self.service = Contributions(self.root / 'tasks.sqlite3', self.root, self.store, MagicMock())
        self.addCleanup(self.service.close)
        self.discovery = self.service.discovery
        self.settings = dict(mode='configure', organization_id='org', product_brief='Help new users reach their first validated task.',
            discovery_focus=['onboarding'], do_not_propose='Do not change pricing.', discovery_group_id='product', discovery_repository='repo')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def meeting(self, action, body):
        self.assertEqual(action, 'discuss')
        job = dict(id=uuid.uuid4().hex, kind='discussion', state='queued', organization_id='org', group_id='product', prompt=body['prompt'])
        self.jobs.append(job)
        return {'id': job['id']}

    def configure(self, **changes):
        return self.discovery.action(dict(self.settings, **changes), 'admin', 'admin')

    def start_discovery(self):
        self.configure()
        return self.discovery.action(dict(mode='run', organization_id='org'), 'admin', 'admin')

    def result(self, record, proposals=None, rubric=None, **changes):
        evidence = next(i['id'] for i in record['evidence']['items'] if i['kind'] == 'docs_inventory')
        proposal = dict(type='task', title='Add first-run onboarding guide', description='Document the first complete workflow.',
            problem='The tracked docs inventory has no first-run guide.', impact='New users can start their first task.', evidence=[evidence],
            acceptance_criteria=['Explain account setup and one validated task.'], required_checks=['Verify the documented navigation.'], profile_id='worker')
        proposal.update(changes)
        job = next(j for j in self.jobs if j['id'] == record['job_id'])
        job.update(state='artifact_ready', result=json.dumps(dict(
            rubric=[dict(focus='onboarding', status='thin', evidence=[evidence])] if rubric is None else rubric,
            task_proposals=[proposal] if proposals is None else proposals)))
        self.discovery.advance()
        return self.discovery.get(record['id'], 'org')

    def approve(self, record, **changes):
        return self.discovery.action(dict(mode='proposal', organization_id='org', discovery_id=record['id'],
            proposal_key=record['proposals'][0]['key'], decision='accept', **changes), 'admin', 'admin')

    def test_manual_discovery_without_host_task_uses_bounded_redacted_evidence(self):
        record = self.start_discovery()
        serialized = json.dumps(record['evidence'])
        self.assertLess(len(serialized.encode()), discovery.EVIDENCE_LIMIT)
        self.assertNotIn('hidden-value', serialized)
        self.assertNotIn('do-not-collect', serialized)
        self.assertNotIn('.env', serialized)
        self.assertEqual(record['evidence']['source_sha'], self.git('rev-parse', 'HEAD'))
        self.assertIn('Drafts only', next(j for j in self.jobs if j['kind'] == 'discussion')['prompt'])
        self.assertLessEqual(len(next(j for j in self.jobs if j['kind'] == 'discussion')['prompt']), 8000)
        self.assertEqual(self.service.snapshot()['tasks'], [])
        self.assertNotIn('task_id', next(j for j in self.jobs if j['kind'] == 'discussion'))

    def test_run_replays_same_window_and_does_not_start_duplicate_meetings(self):
        record = self.start_discovery()
        again = self.discovery.action(dict(mode='run', organization_id='org'), 'admin', 'admin')
        self.assertEqual(again['id'], record['id'])
        self.store.action.assert_called_once()

    def test_schedule_is_opt_in_and_pause_stops_dispatch(self):
        self.configure()
        self.discovery.advance()
        self.store.action.assert_not_called()
        self.configure(discovery_enabled=True)
        self.discovery.advance()
        self.store.action.assert_called_once()
        self.configure(discovery_paused=True)
        with patch('discovery.now', return_value=discovery.now() + timedelta(days=8)):
            self.discovery.advance()
        self.store.action.assert_called_once()

    def test_pause_remains_available_when_repository_disappears(self):
        self.configure(discovery_enabled=True)
        self.repo.rename(self.root / 'gone')
        policy = self.discovery.action(dict(mode='configure', organization_id='org', discovery_paused=True), 'admin', 'admin')['policy']
        self.assertTrue(policy['discovery_paused'])

    def test_finished_meeting_produces_proposals_but_no_tasks_until_approval(self):
        record = self.result(self.start_discovery())
        self.assertEqual(record['state'], 'ready')
        self.assertTrue(record['proposals'][0]['needs_review'])
        self.assertEqual(self.service.snapshot()['tasks'], [])
        approved = self.approve(record)
        task = self.service.get(approved['proposals'][0]['task_id'])
        self.assertEqual(task['state'], 'draft')
        self.assertEqual(task['source']['discovery_id'], record['id'])
        self.assertIn('Acceptance criteria:', task['description'])
        self.assertEqual(self.service.queued_assignments(), [])
        self.approve(record)
        self.assertEqual(len(self.service.snapshot()['tasks']), 1)

    def test_unrecognized_evidence_and_missing_checks_require_attention(self):
        record = self.result(self.start_discovery(), evidence=['invented-reference'])
        self.assertEqual(record['state'], 'needs_attention')
        self.assertIn('not supplied', record['error'])
        self.assertEqual(self.service.snapshot()['tasks'], [])

    def test_empty_proposals_are_a_valid_outcome(self):
        record = self.result(self.start_discovery(), proposals=[])
        self.assertEqual(record['state'], 'ready')
        self.assertEqual(record['proposals'], [])

    def test_backlog_dedupe_prevents_materialization(self):
        existing = self.service.action('create', dict(request_id='create-existing', title='Add first run onboarding guide',
            description='Already approved.', repository='repo', base_ref='', profile_id='worker'), 'admin', 'admin')
        record = self.result(self.start_discovery())
        approved = self.approve(record)
        self.assertEqual(approved['proposals'][0]['state'], 'duplicate')
        self.assertEqual(approved['proposals'][0]['duplicate_task_id'], existing['id'])
        self.assertEqual(len(self.service.snapshot()['tasks']), 1)

    def test_initiative_approval_starts_one_bounded_planning_meeting(self):
        record = self.result(self.start_discovery(), type='initiative')
        approved = self.approve(record)
        planning_id = approved['proposals'][0]['planning_id']
        child = self.discovery.get(planning_id, 'org')
        self.assertEqual(child['parent_id'], record['id'])
        self.assertEqual(child['policy']['discovery_max_proposals'], 5)
        self.assertIn('Approved planning:', child['prompt'])
        self.assertNotEqual(child['evidence_file'], record['evidence_file'])
        self.approve(record)
        self.assertEqual(len([j for j in self.jobs if j['kind'] == 'discussion']), 2)
        child = self.result(child, type='initiative')
        self.assertEqual(child['state'], 'needs_attention')
        self.assertEqual(self.service.snapshot()['tasks'], [])

    def test_approval_rechecks_assignee_and_organization(self):
        record = self.result(self.start_discovery())
        self.worker['project'] = str(self.root)
        with self.assertRaisesRegex(ValueError, 'worktree agent'):
            self.approve(record)
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.discovery.action(dict(mode='run', organization_id='org'), 'operator', 'operator')
        with self.assertRaisesRegex(ValueError, 'Organization not found'):
            self.discovery.action(dict(mode='run', organization_id='other'), 'admin', 'admin')

    def test_large_proposal_keeps_its_structured_contract_within_task_limits(self):
        record = self.result(self.start_discovery(), description='x' * 3500,
            acceptance_criteria=['criterion ' + 'a' * 290] * 8, required_checks=['check ' + 'b' * 290] * 8)
        approved = self.approve(record)
        task = self.service.get(approved['proposals'][0]['task_id'])
        self.assertLessEqual(len(task['description']), 8000)
        self.assertIn('Acceptance criteria:', task['description'])
        self.assertIn('Required checks:', task['description'])
        self.assertIn('check ' + 'b' * 290, task['description'])

    def test_duplicate_decision_can_be_overridden_by_the_operator(self):
        existing = self.service.action('create', dict(request_id='create-existing', title='Add first run onboarding guide',
            description='Already approved.', repository='repo', base_ref='', profile_id='worker'), 'admin', 'admin')
        record = self.result(self.start_discovery())
        blocked = self.approve(record)
        self.assertEqual(blocked['proposals'][0]['state'], 'duplicate')
        approved = self.approve(blocked, allow_duplicate=True)
        self.assertEqual(approved['proposals'][0]['state'], 'draft')
        task = self.service.get(approved['proposals'][0]['task_id'])
        self.assertEqual(task['source']['overrode_duplicate_task'], existing['id'])
        self.assertEqual(len(self.service.snapshot()['tasks']), 2)

    def test_planning_meeting_may_omit_the_rubric(self):
        record = self.result(self.start_discovery(), type='initiative')
        approved = self.approve(record)
        child = self.discovery.get(approved['proposals'][0]['planning_id'], 'org')
        child = self.result(child, rubric=[])
        self.assertEqual(child['state'], 'ready')
        self.assertEqual(child['rubric'], [])
        self.assertEqual(len(child['proposals']), 1)

    def test_deep_json_result_requires_attention_without_raising(self):
        record = self.start_discovery()
        job = next(j for j in self.jobs if j['id'] == record['job_id'])
        job.update(state='artifact_ready', result='[' * 3000 + ']' * 3000)
        # Interpreters differ on whether this parses or raises RecursionError;
        # either way the poller must survive and record the failure.
        self.discovery.advance()
        record = self.discovery.get(record['id'], 'org')
        self.assertEqual(record['state'], 'needs_attention')
        self.assertTrue(record['error'])

    def test_parse_converts_recursion_failure_to_value_error(self):
        record = self.start_discovery()
        with patch.object(discovery.json, 'loads', side_effect=RecursionError('maximum recursion depth exceeded')):
            with self.assertRaisesRegex(ValueError, 'valid JSON'):
                self.discovery.parse(record, dict(result='[[]]'))

    def test_naive_or_malformed_schedule_timestamps_do_not_break_scheduling(self):
        self.configure(discovery_enabled=True)
        with closing(self.service.connect()) as db, db:
            policy = self.service.policy('org')
            policy.update(discovery_next_at='2026-01-01', discovery_retry_at='2030-01-01')
            db.execute('INSERT OR REPLACE INTO policies VALUES (?,?)', ('org', json.dumps(policy)))
        self.discovery.advance()  # A naive future retry must block, not raise.
        self.store.action.assert_not_called()
        with closing(self.service.connect()) as db, db:
            policy = self.service.policy('org')
            policy.update(discovery_next_at='2026-01-01', discovery_retry_at='not-a-date')
            db.execute('INSERT OR REPLACE INTO policies VALUES (?,?)', ('org', json.dumps(policy)))
        self.discovery.advance()  # Malformed retry is ignored; the naive past window is due.
        self.store.action.assert_called_once()

    def test_missing_discussion_record_requires_attention(self):
        record = self.start_discovery()
        self.jobs.clear()
        self.discovery.advance()
        record = self.discovery.get(record['id'], 'org')
        self.assertEqual(record['state'], 'needs_attention')
        self.assertIn('unavailable', record['error'])

    def test_configuration_preserves_follow_up_policy_and_rejects_unbounded_cadence(self):
        self.service.set_automation_policy(dict(organization_id='org', auto_queue_proposals=True, daily_cap=2), 'admin')
        self.configure()
        self.assertEqual(self.service.policy('org')['daily_cap'], 2)
        with self.assertRaisesRegex(ValueError, 'Invalid discovery limit'):
            self.configure(discovery_interval_hours=1)

    def test_failed_dispatch_is_backed_off_and_reconciled_with_same_request(self):
        self.configure(discovery_enabled=True)
        self.store.action.side_effect = ValueError('Members not ready')
        self.discovery.advance()
        self.assertEqual(self.store.action.call_count, 1)
        self.assertIn('Members not ready', self.discovery.policy('org')['discovery_error'])
        self.discovery.advance()
        self.assertEqual(self.store.action.call_count, 1)
        self.store.action.side_effect = self.meeting
        record = self.discovery.action(dict(mode='run', organization_id='org'), 'admin', 'admin')
        requests = [call.args[1]['request_id'] for call in self.store.action.call_args_list]
        self.assertEqual(requests[0], requests[-1])
        self.assertEqual(record['state'], 'meeting')

    def test_recovery_reprocesses_existing_artifact_without_resending(self):
        record = self.result(self.start_discovery(), evidence=['invented'])
        request = dict(mode='recover', organization_id='org', discovery_id=record['id'])
        with self.assertRaisesRegex(ValueError, 'Inspect'):
            self.discovery.action(request, 'admin', 'admin')
        job = next(j for j in self.jobs if j['id'] == record['job_id'])
        evidence = record['delivered_evidence_ids'][0]
        data = json.loads(job['result'])
        data['task_proposals'][0]['evidence'] = [evidence]
        job['result'] = json.dumps(data)
        recovered = self.discovery.action(dict(request, inspected=True), 'admin', 'admin')
        self.assertEqual(recovered['state'], 'ready')
        self.store.action.assert_called_once()

    def test_missing_checks_and_exceeded_cap_are_rejected(self):
        record = self.result(self.start_discovery(), required_checks=[])
        self.assertEqual(record['state'], 'needs_attention')
        self.assertIn('required_checks', record['error'])
        job = next(j for j in self.jobs if j['id'] == record['job_id'])
        data = json.loads(job['result'])
        data['task_proposals'] *= 4
        with self.assertRaisesRegex(ValueError, 'cap'):
            self.discovery.parse(record, dict(result=json.dumps(data)))

    def test_pending_reservation_survives_restart_and_expired_window(self):
        self.configure()
        self.store.action.side_effect = ValueError('Members not ready')
        with self.assertRaises(ValueError):
            self.discovery.action(dict(mode='run', organization_id='org'), 'admin', 'admin')
        original_request = self.store.action.call_args.args[1]
        self.discovery = discovery.Discovery(self.service)
        self.store.action.side_effect = self.meeting
        with patch('discovery.now', return_value=discovery.now() + timedelta(days=8)):
            record = self.discovery.action(dict(mode='run', organization_id='org'), 'admin', 'admin')
        self.assertEqual(original_request, self.store.action.call_args.args[1])
        self.assertEqual(record['state'], 'meeting')

    def test_unready_group_preparation_error_is_persisted(self):
        self.jobs.clear()
        self.configure()
        with self.assertRaisesRegex(ValueError, 'not ready'):
            self.discovery.action(dict(mode='run', organization_id='org'), 'admin', 'admin')
        record = self.discovery.snapshot()['meetings'][0]
        self.assertEqual(record['state'], 'pending')
        self.assertIn('not ready', record['error'])

    def test_operator_task_snapshot_excludes_product_direction(self):
        self.configure()
        serialized = json.dumps(self.service.snapshot(role='operator'))
        self.assertNotIn('product_brief', serialized)
        self.assertNotIn('Help new users', serialized)

    def test_retry_rejects_changed_frozen_evidence_copy(self):
        self.configure()
        self.store.action.side_effect = ValueError('Members not ready')
        with self.assertRaises(ValueError):
            self.discovery.action(dict(mode='run', organization_id='org'), 'admin', 'admin')
        record = self.discovery.snapshot()['meetings'][0]
        Path(record['evidence_file']).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'evidence copy changed'):
            self.discovery.action(dict(mode='run', organization_id='org'), 'admin', 'admin')
        self.store.action.assert_called_once()

    def test_http_discovery_routes_require_admin_and_create_taskless_meeting(self):
        import threading
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        import server as gateway
        server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        server.token = 'admin-token'
        server.operators = MagicMock()
        server.operators.identify.side_effect = lambda token: dict(name='viewer', role='operator') if token == 'operator-token' else None
        server.contributions = self.service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def request(path, token=None, data=None):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            return urlopen(Request(base + path, headers=headers, data=None if data is None else json.dumps(data).encode()))
        try:
            for token, status in ((None, 401), ('operator-token', 403)):
                with self.assertRaises(HTTPError) as error:
                    request('/api/discovery', token)
                self.assertEqual(error.exception.code, status)
            with self.assertRaises(HTTPError) as error:
                request('/api/tasks/discovery', 'operator-token', self.settings)
            self.assertEqual(error.exception.code, 400)
            with request('/api/tasks/discovery', 'admin-token', self.settings) as response:
                self.assertIn('policy', json.load(response))
            with request('/api/tasks/discovery', 'admin-token', dict(mode='run', organization_id='org')) as response:
                self.assertEqual(json.load(response)['state'], 'meeting')
            with request('/api/discovery', 'admin-token') as response:
                self.assertEqual(len(json.load(response)['meetings']), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class DedupeTests(unittest.TestCase):
    def test_title_similarity_normalizes_case_and_punctuation(self):
        self.assertEqual(discovery.duplicate('First-run onboarding guide!', [dict(id='task', title='First run onboarding guide')]), 'task')
        self.assertIsNone(discovery.duplicate('Fix backend timeout', [dict(id='task', title='First run onboarding guide')]))

    def test_one_generic_shared_word_is_not_a_duplicate_but_exact_equality_is(self):
        self.assertIsNone(discovery.duplicate('Improve onboarding checklist', [dict(id='task', title='Improve checkout flow')]))
        self.assertEqual(discovery.duplicate('Onboarding', [dict(id='task', title='Onboarding')]), 'task')

    def test_redactor_handles_bearer_and_url_credentials(self):
        text = discovery.redact('Authorization: Bearer raw-token https://user:password@host/path?token=abc')
        self.assertNotIn('raw-token', text)
        self.assertNotIn('password@', text)

    def test_redactor_handles_private_keys_basic_auth_and_block_secrets(self):
        text = discovery.redact('Authorization: Basic dXNlcjpwYXNzd29yZA==\nAWS_SECRET_ACCESS_KEY=abc123\n'
                                '-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n'
                                'password: |\n  hunter2\nnext: ok')
        self.assertNotIn('dXNlcjpwYXNzd29yZA', text)
        self.assertNotIn('abc123', text)
        self.assertNotIn('MIIabc', text)
        self.assertNotIn('hunter2', text)
        self.assertIn('next: ok', text)

    def test_redactor_handles_quoted_keys_chomping_pgp_and_prose(self):
        text = discovery.redact('{"token": "abc123"}\npassword: |-\n  hunter3\n'
                                '-----BEGIN PGP PRIVATE KEY BLOCK-----\nPGPabc\n-----END PGP PRIVATE KEY BLOCK-----\n'
                                'secret: "a b c"\nBasic authentication is required.')
        self.assertNotIn('abc123', text)
        self.assertNotIn('hunter3', text)
        self.assertNotIn('PGPabc', text)
        self.assertNotIn('"a b c"', text)
        self.assertIn('Basic authentication is required.', text)
