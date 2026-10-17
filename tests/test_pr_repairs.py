"""Conflict repair tests: real Git histories, simulated agent sessions and GitHub."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

import test_contributions as fixtures
import pr_repairs
from herdr_errors import HerdrError
import server


class RepairTests(unittest.TestCase):
    git = fixtures.ContributionTests.git
    candidate = fixtures.ContributionTests.candidate
    pull_data = fixtures.ContributionTests.pull_data

    def setUp(self):
        fixtures.ContributionTests.setUp(self)
        self.task = self.candidate()
        self.profile.update(name='Nora', runtime='opencode', role='Engineer')
        # Diverged histories modifying the same line cause a real Git conflict.
        (self.repo / 'file').write_text('upstream\n')
        self.git('add', '.')
        self.git('commit', '-m', 'upstream change')
        self.upstream = self.git('rev-parse', 'HEAD').strip()
        self.git('update-ref', 'refs/remotes/origin/main', self.upstream)
        self.task.update(publish=dict(head_sha=self.task['head_sha'], repository='owner/repo'),
                         assigned_agent=dict(id='worker', name='Nora', role='Engineer', runtime='opencode'))
        self.pull = self.pull_data(self.task, mergeable=False,
            base=dict(ref='main', sha=self.upstream, repo=dict(full_name='owner/repo')))
        self.service.record_pull(self.task, self.pull)
        self.service.save(self.task, 'pull', 'admin')
        self.api.request.return_value = self.pull
        self.run = dict(id='run', kind='launch', state='persona_sent', profile_id='worker',
                        organization_id='org', task_id=self.task['id'], completion_token='old-token')
        self.store.snapshot.return_value['jobs'] = [self.run]
        self.store.identity.return_value = dict(agent_status='done')
        self.store.model_validator = None
        self.store.manage_session.side_effect = self.session
        self.fetch = patch('pr_repairs.project_git.fetch')
        self.fetch.start()
        self.addCleanup(self.fetch.stop)
        self.git('config', 'core.excludesfile', str(self.root / 'exclude'))
        (self.root / 'exclude').write_text('/.ci-cache/\n')

    def session(self, body, actor):
        if body['mode'] == 'finish':
            self.run.update(state='finished', session_archive_id='archive')
        else:
            self.run.update(state='queued', session_request_id=body['request_id'], completion_token='repair-token')
        return {'id': 'run'}

    def request(self, **body):
        return self.service.action('repair', dict(request_id='repair-request', task_id=self.task['id'],
            head_sha=self.task['head_sha'], **body), 'admin', 'admin')

    def start(self):
        self.request()
        pr_repairs.advance(self.service)
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'running')
        self.run['state'] = 'persona_sent'

    def resolve(self, token='repair-token'):
        tree = Path(self.task['worktree'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.git('merge', '--no-edit', self.upstream, path=tree)
        (tree / 'file').write_text('implementation and upstream\n')
        self.git('add', 'file', path=tree)
        self.git('commit', '-m', 'resolve conflicts', path=tree)
        head = self.git('rev-parse', 'HEAD', path=tree).strip()
        directory = tree / '.ci-cache/herdr-guidance' / ('task-' + self.task['id'])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'receipt.json').write_text(json.dumps(dict(outcome='complete', commit=head,
            run_id='run', token=token, tests=[dict(command='test', status='passed')])))
        return head

    def advance(self):
        with patch('collaboration.current_run', return_value=self.run):
            pr_repairs.advance(self.service)

    def test_queue_is_idempotent_and_pins_both_commits(self):
        task = self.request()
        again = self.service.action('repair', dict(request_id='second-click', task_id=task['id'],
            head_sha=task['head_sha']), 'admin', 'admin')
        self.assertEqual(task['pr_repair']['id'], again['pr_repair']['id'])
        self.assertIn('already in progress', again['action_message'])
        self.assertNotIn('action_message', self.service.get(task['id']))
        self.assertEqual(task['pr_repair']['message'], again['pr_repair']['message'])
        self.assertEqual(task['pr_repair']['upstream_sha'], self.upstream)
        self.store.manage_session.assert_not_called()

    def test_unknown_mergeability_and_changed_head_never_queue(self):
        self.pull['mergeable'] = None
        with self.assertRaisesRegex(ValueError, 'not confirmed'):
            self.request()
        self.pull['mergeable'] = False
        self.pull['head']['sha'] = 'a' * 40
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.request()
        self.assertNotIn('pr_repair', self.service.get(self.task['id']))

    def test_busy_agent_and_discussion_reservation_do_not_close_or_prompt(self):
        self.request()
        self.store.identity.return_value = dict(agent_status='working')
        pr_repairs.advance(self.service)
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'queued')
        self.store.manage_session.assert_not_called()
        self.store.identity.return_value = dict(agent_status='done')
        self.store.snapshot.return_value['jobs'].append(dict(id='meeting', state='waiting_for_input', participants=['worker']))
        pr_repairs.advance(self.service)
        self.store.manage_session.assert_not_called()

    def test_dirty_checkout_requires_inspection_without_closing(self):
        self.request()
        (Path(self.task['worktree']) / 'file').write_text('uncommitted\n')
        pr_repairs.advance(self.service)
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'needs_attention')
        self.assertIn('unfinished work', task['pr_repair']['message'])
        self.store.manage_session.assert_not_called()

    def test_archive_failure_never_dispatches_a_repair(self):
        self.request()
        self.store.manage_session.side_effect = ValueError('Identity ambiguous')
        pr_repairs.advance(self.service)
        self.assertEqual(self.run['state'], 'persona_sent')
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'needs_attention')
        self.assertEqual(self.store.manage_session.call_count, 1)

    def test_changed_base_while_queued_requires_a_fresh_inspected_request(self):
        first = self.request()['pr_repair']['id']
        self.pull['base']['sha'] = self.base
        pr_repairs.advance(self.service)
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'needs_attention')
        task = self.service.action('repair', dict(request_id='recheck', task_id=self.task['id'],
            mode='retry', inspected=True), 'admin', 'admin')
        self.assertNotEqual(first, task['pr_repair']['id'])
        self.assertEqual(task['pr_repair']['upstream_sha'], self.base)
        self.store.manage_session.assert_not_called()

    def test_dispatch_reuses_branch_and_archives_before_launch(self):
        self.start()
        calls = [item.args[0] for item in self.store.manage_session.call_args_list]
        self.assertEqual([item['mode'] for item in calls], ['finish', 'handover'])
        prompt = calls[1]['task_prompt']
        self.assertIn(self.upstream, prompt)
        self.assertIn(self.task['branch'], prompt)
        self.assertIn('{{HERDR_TASK_TOKEN}}', prompt)
        self.assertIn('do not', prompt.lower())
        self.assertEqual(self.git('symbolic-ref', '--short', 'HEAD', path=self.task['worktree']).strip(), self.task['branch'])
        self.assertTrue(self.service.get(self.task['id'])['pr_repair']['recovery']['ref'])
        self.store.action.assert_not_called()

    def test_lost_submission_response_adopts_delivery_without_resending(self):
        self.request()
        original = self.session
        def lost(body, actor):
            result = original(body, actor)
            if body['mode'] == 'handover':
                raise OSError('response lost')
            return result
        self.store.manage_session.side_effect = lost
        pr_repairs.advance(self.service)
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'needs_attention')
        self.service.action('repair', dict(request_id='resume-lost', task_id=self.task['id'], mode='retry',
            inspected=True), 'admin', 'admin')
        before = self.store.manage_session.call_count
        pr_repairs.advance(self.service)
        self.assertEqual(self.store.manage_session.call_count, before)
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'running')

    def test_transient_read_failure_recovers_without_an_operator_retry_or_prompt(self):
        self.start()
        with patch('collaboration.current_run', side_effect=OSError('temporary socket failure')):
            pr_repairs.advance(self.service)
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'running')
        self.assertEqual(task['pr_repair']['monitoring_failures'], 1)
        self.assertFalse(any(a['action'] == 'pr_repair_needs_attention' for a in task['audit']))
        before = self.store.manage_session.call_count
        self.store.identity.return_value = dict(agent_status='working')
        self.advance()
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'running')
        self.assertEqual(self.store.manage_session.call_count, before)

    def test_persistent_read_failure_is_visible_and_recovers_without_resubmission(self):
        self.start()
        before = self.store.manage_session.call_count
        with patch('collaboration.current_run', side_effect=HerdrError('CLI timed out', 'timeout', 'agent get')):
            for _ in range(4):
                pr_repairs.advance(self.service)
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'monitoring_unavailable')
        self.assertEqual(task['pr_repair']['monitoring_failures'], 4)
        self.assertIn('4 consecutive', task['pr_repair']['message'])
        self.assertEqual(sum(a['action'] == 'pr_repair_monitoring_unavailable' for a in task['audit']), 1)
        with self.assertRaisesRegex(ValueError, 'Wait for conflict repair'):
            self.service.perform('publish', dict(head_sha=task['head_sha']), task['id'], 'admin')
        self.store.identity.return_value = dict(agent_status='working')
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'running')
        self.assertNotIn('monitoring_failures', task['pr_repair'])
        self.assertEqual(self.store.manage_session.call_count, before)

    def test_changed_identity_requires_inspection_and_is_not_auto_recovered(self):
        self.start()
        with patch('collaboration.current_run', side_effect=ValueError('Agent binding changed')):
            pr_repairs.advance(self.service)
        before = self.store.manage_session.call_count
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'needs_attention')
        self.assertNotIn('monitoring_retry', task['pr_repair'])
        self.assertEqual(self.store.manage_session.call_count, before)

    def test_blocked_then_allowed_continues_to_validate_new_commit(self):
        self.start()
        self.store.identity.return_value = dict(agent_status='blocked')
        self.advance()
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'waiting_for_input')
        self.store.identity.return_value = dict(agent_status='done')
        head = self.resolve()
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['head_sha'], head)
        self.assertEqual(task['base_sha'], self.upstream)
        self.assertEqual(task['pr_repair']['state'], 'validating')
        self.assertEqual(task['pull']['number'], 7)
        self.assertEqual(task['publish']['head_sha'], self.task['head_sha'])
        # A build for the old candidate cannot finish this repair.
        task['builds'] = {head: dict(target=self.task['head_sha'], state='complete', required_checks_verified=True)}
        self.service.save(task, 'build_result', 'runner')
        self.service.validation = None
        self.advance()
        self.assertEqual(self.service.get(task['id'])['pr_repair']['state'], 'validating')
        task = self.service.get(task['id'])
        task['builds'][head] = dict(target=head, state='complete', required_checks_verified=True)
        self.service.save(task, 'build_result', 'runner')
        self.advance()
        self.assertEqual(self.service.get(task['id'])['pr_repair']['state'], 'ready')
        self.api.find_pull.assert_not_called()

    def test_old_receipt_token_cannot_complete_the_new_attempt(self):
        self.start()
        self.resolve(token='old-token')
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'running')
        self.assertEqual(task['head_sha'], self.task['head_sha'])

    def test_publish_is_blocked_during_repair_and_preserves_pr_on_success(self):
        self.request()
        with self.assertRaisesRegex(ValueError, 'Wait for conflict repair'):
            self.service.action('publish', dict(request_id='early-publish', task_id=self.task['id'],
                head_sha=self.task['head_sha']), 'admin', 'admin')

    def test_validated_repair_updates_the_existing_fork_branch_and_pr(self):
        self.task['publishing_repository'] = 'contributor/repo'
        self.task['publish']['repository'] = 'contributor/repo'
        self.pull['head']['repo']['full_name'] = 'contributor/repo'
        self.service.save(self.task, 'publication', 'admin')
        self.start()
        head = self.resolve()
        self.advance()
        task = self.service.get(self.task['id'])
        task['builds'] = {head: dict(target=head, state='complete', required_checks_verified=True)}
        self.service.save(task, 'build_result', 'runner')
        self.advance()
        with patch.object(self.service, 'remote_head', side_effect=[self.task['head_sha'], head]), patch('contributions.push') as push:
            result = self.service.action('publish', dict(request_id='publish-repaired', task_id=task['id'], head_sha=head), 'admin', 'admin')
        self.assertEqual(push.call_args.args[-1], 'contributor/repo')
        self.assertEqual(push.call_args.args[1:3], (self.task['branch'], head))
        self.assertEqual(result['pull']['number'], 7)
        self.assertEqual(result['state'], 'pr_open')
        self.assertEqual(result['pr_repair']['state'], 'published')
        self.assertIsNone(result['pull']['mergeable'])
        self.api.find_pull.assert_not_called()

    def test_existing_taskless_sessions_do_not_exhaust_task_execution_capacity(self):
        self.request()
        self.store.snapshot.return_value['jobs'].extend(dict(id='general-' + str(i), kind='launch',
            profile_id='other-' + str(i), organization_id='org', state='persona_sent') for i in range(6))
        pr_repairs.advance(self.service)
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'running')

    def test_another_task_keeps_its_agent_until_verified_finished(self):
        self.request()
        self.run.update(state='finished', session_archive_id='archive')
        other = self.service.action('create', dict(self.body, request_id='other-task'), 'admin', 'admin')
        other['state'] = 'implementing'
        self.service.save(other, 'launch', 'admin')
        self.store.snapshot.return_value['jobs'].append(dict(id='other-run', task_id=other['id'], kind='launch',
            profile_id='worker', organization_id='org', state='persona_sent'))
        pr_repairs.advance(self.service)
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'queued')
        self.store.manage_session.assert_not_called()

    def test_a_claimed_repair_without_upstream_incorporation_is_rejected(self):
        self.start()
        tree = Path(self.task['worktree'])
        (tree / 'another').write_text('unrelated commit\n')
        self.git('add', 'another', path=tree)
        self.git('commit', '-m', 'not a repair', path=tree)
        head = self.git('rev-parse', 'HEAD', path=tree).strip()
        directory = tree / '.ci-cache/herdr-guidance' / ('task-' + self.task['id'])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'receipt.json').write_text(json.dumps(dict(outcome='complete', commit=head,
            run_id='run', token='repair-token', tests=[])))
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'needs_attention')
        self.assertIn('incorporate', task['pr_repair']['message'])
        self.assertEqual(task['head_sha'], self.task['head_sha'])

    def test_queued_cancellation_sends_nothing_and_no_longer_dispatches(self):
        self.request()
        self.service.action('repair', dict(request_id='cancel-repair', task_id=self.task['id'], mode='cancel'), 'admin', 'admin')
        pr_repairs.advance(self.service)
        self.store.manage_session.assert_not_called()
        self.assertEqual(self.service.get(self.task['id'])['pr_repair']['state'], 'cancelled')

    def test_restart_launches_count_towards_the_daily_session_budget(self):
        self.run['session_starts'] = ['2020-01-01T00:00:00Z', fixtures.contributions.stamp(), fixtures.contributions.stamp()]
        self.assertEqual(self.service.session_budget('org')['started'], 2)

    def test_cancellation_cannot_abandon_a_submitted_repair_queued_for_adoption(self):
        self.request()
        task = self.service.get(self.task['id'])
        self.run['session_request_id'] = 'pr-repair-' + task['pr_repair']['id']
        with self.assertRaisesRegex(ValueError, 'already submitted'):
            self.service.action('repair', dict(request_id='cancel-submitted', task_id=task['id'], mode='cancel'), 'admin', 'admin')
        self.assertEqual(self.service.get(task['id'])['pr_repair']['state'], 'queued')
        self.store.manage_session.assert_not_called()

    def test_http_route_reaches_admin_repair_action_and_rejects_operator(self):
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'a' * 48
        gateway.contributions = self.service
        gateway.operators = SimpleNamespace(identify_digest=lambda digest: None)
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps(dict(request_id='http-repair', task_id=self.task['id'], head_sha=self.task['head_sha'])).encode()
            request = Request('http://127.0.0.1:' + str(gateway.server_port) + '/api/tasks/repair', data=payload,
                              headers={'Authorization': 'Bearer ' + gateway.token})
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)['pr_repair']['state'], 'queued')
            with self.assertRaisesRegex(ValueError, 'administrator'):
                self.service.action('repair', dict(request_id='operator-repair', task_id=self.task['id']), 'operator', 'operator')
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=2)

    def test_http_detail_after_repair_receipt_list_returns_preview(self):
        self.start()
        self.store.identity.return_value = dict(agent_status='done')
        head = self.resolve()
        self.advance()
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'a' * 48
        gateway.contributions = self.service
        gateway.operators = SimpleNamespace(identify_digest=lambda digest: None)
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request('http://127.0.0.1:' + str(gateway.server_port)
                              + '/api/tasks/detail?id=' + self.task['id'],
                              headers={'Authorization': 'Bearer ' + gateway.token})
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                detail = json.load(response)
            self.assertEqual(detail['head_sha'], head)
            self.assertEqual(detail['pr_repair']['state'], 'validating')
            self.assertIn('Worker-reported checks', detail['pull_preview']['body'])
            self.assertIn('test', detail['pull_preview']['body'])
            self.assertIn('not verified for this candidate', detail['pull_preview']['body'])
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=2)
