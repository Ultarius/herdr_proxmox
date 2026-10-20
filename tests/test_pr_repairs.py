"""Conflict repair tests: real Git histories, simulated agent sessions and GitHub."""
import json
import copy
import subprocess
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch
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

    def validation_fixture(self):
        self.service.validation = Mock()
        self.service.validation.submit.return_value = dict(id='validation-run', state='queued', checks=[])
        workflow = self.repo / '.github/workflows/ci.yml'
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text('name: approved upstream checks\n')
        self.git('add', '.')
        self.git('commit', '-m', 'upstream validation policy')
        self.upstream = self.git('rev-parse', 'HEAD').strip()
        self.git('update-ref', 'refs/remotes/origin/main', self.upstream)
        self.task.update(state='review_ready')
        self.task.pop('pull', None)
        self.task.pop('publish', None)
        self.service.save(self.task, 'fixture', 'admin')

    def validate(self, request='validate-update', **body):
        return self.service.action('build', dict(request_id=request, task_id=self.task['id'],
            target=self.task['head_sha'], update_branch=True, **body), 'admin', 'admin')

    def test_validation_update_queues_without_a_pr_and_adopts_dispatch_after_restart(self):
        self.validation_fixture()
        task = self.validate()
        self.assertEqual(task['pr_repair']['kind'], 'validation_update')
        self.assertIn('queued for the assigned agent', task['action_message'])
        self.assertNotIn('action_message', self.service.get(task['id']))
        again = self.validate(request='another-validation-click')
        self.assertEqual(task['pr_repair']['id'], again['pr_repair']['id'])
        pr_repairs.advance(self.service)
        calls = self.store.manage_session.call_count
        self.assertEqual(calls, 2)
        prompt = self.store.manage_session.call_args.args[0]['task_prompt']
        self.assertIn(self.upstream, prompt)
        self.assertIn('Protected validation files must match', prompt)
        task = self.service.get(task['id'])
        task['pr_repair']['state'] = 'starting'  # Lost response after handover.
        self.service.save(task, 'restart', 'test')
        pr_repairs.advance(self.service)
        self.assertEqual(self.store.manage_session.call_count, calls)
        self.assertEqual(self.service.get(task['id'])['pr_repair']['state'], 'running')
        self.api.request.assert_not_called()

    def test_validation_update_captures_and_validates_exact_result_with_gateway_executor(self):
        self.validation_fixture()
        self.validate()
        pr_repairs.advance(self.service)
        self.run['state'] = 'persona_sent'
        head = self.resolve()
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['head_sha'], head)
        self.assertEqual(task['base_sha'], self.upstream)
        self.assertEqual(task['state'], 'review_ready')
        self.assertEqual(task['pr_repair']['state'], 'validating')
        self.service.validation.snapshot.return_value = {'executor': 'gateway'}
        with patch.object(self.service, 'perform', wraps=self.service.perform) as perform:
            self.advance()
        self.assertEqual(perform.call_args.args[:2], ('build', {'target': head}))
        self.assertEqual(self.store.manage_session.call_count, 2)
        task = self.service.get(task['id'])
        task['builds'][head] = dict(target=head, state='complete', required_checks_verified=True)
        self.service.save(task, 'validated', 'test')
        self.advance()
        task = self.service.get(task['id'])
        self.assertEqual(task['pr_repair']['state'], 'ready')
        self.assertNotIn('pull', task)

    def test_gateway_capacity_waits_and_retries_without_inspecting_or_relaunching_the_agent(self):
        from validation import ValidationBusy
        self.validation_fixture()
        self.validate()
        pr_repairs.advance(self.service)
        self.run['state'] = 'persona_sent'
        head = self.resolve()
        self.advance()
        self.service.validation.submit.side_effect = [ValidationBusy('Already running'),
            dict(id='validation-run', state='running', checks=[])]
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'validating')
        self.assertIn('Waiting for the current validation', task['pr_repair']['message'])
        self.assertNotIn(head, task.get('builds', {}))
        self.advance()
        task = self.service.get(task['id'])
        self.assertEqual(task['builds'][head]['state'], 'running')
        self.assertEqual(self.store.manage_session.call_count, 2)

    def test_cancelled_validation_releases_the_wait_for_an_explicit_retry(self):
        self.validation_fixture()
        self.validate()
        pr_repairs.advance(self.service)
        self.run['state'] = 'persona_sent'
        head = self.resolve()
        self.advance()
        task = self.service.get(self.task['id'])
        task['builds'] = {head: dict(target=head, state='cancelled')}
        self.service.save(task, 'cancelled-validation', 'test')
        self.advance()
        self.assertEqual(self.service.get(task['id'])['pr_repair']['state'], 'validation_failed')
        self.task['head_sha'] = head
        self.validate(request='retry-after-cancelled-validation')
        self.service.validation.submit.assert_called_once()
        self.assertEqual(self.store.manage_session.call_count, 2)

    def test_background_validation_does_not_authorize_an_agent_update(self):
        self.validation_fixture()
        self.service.perform('build', {'target': self.task['head_sha']}, self.task['id'], 'automatic_task_policy')
        self.assertNotIn('pr_repair', self.service.get(self.task['id']))
        self.store.manage_session.assert_not_called()
        self.service.validation.submit.assert_called_once()

    def test_cancelled_undispatched_update_can_be_requested_by_a_new_click(self):
        self.validation_fixture()
        first = self.validate()['pr_repair']['id']
        self.service.action('repair', dict(request_id='cancel-update', task_id=self.task['id'], mode='cancel'), 'admin', 'admin')
        second = self.validate(request='validate-after-cancellation')['pr_repair']['id']
        self.assertNotEqual(first, second)
        self.store.manage_session.assert_not_called()

    def test_failed_update_validation_can_retry_checks_without_another_agent_turn(self):
        self.validation_fixture()
        self.validate()
        pr_repairs.advance(self.service)
        self.run['state'] = 'persona_sent'
        head = self.resolve()
        self.advance()
        task = self.service.get(self.task['id'])
        task['pr_repair']['state'] = 'validation_failed'
        self.service.save(task, 'failed-validation', 'test')
        self.task['head_sha'] = head
        self.validate(request='retry-updated-checks')
        self.assertEqual(self.service.get(task['id'])['pr_repair']['state'], 'validating')
        self.assertEqual(self.store.manage_session.call_count, 2)

    def test_validation_update_refuses_task_authored_policy_changes(self):
        self.validation_fixture()
        tree = Path(self.task['worktree'])
        (tree / '.github/workflows').mkdir(parents=True)
        (tree / '.github/workflows/ci.yml').write_text('name: weaker task checks\n')
        self.git('add', '.', path=tree)
        self.git('commit', '-m', 'task policy edit', path=tree)
        self.task['head_sha'] = self.git('rev-parse', 'HEAD', path=tree).strip()
        self.service.save(self.task, 'fixture', 'admin')
        with self.assertRaisesRegex(ValueError, 'task changed protected policy'):
            self.validate()
        self.store.manage_session.assert_not_called()

    def test_validation_update_stops_if_upstream_moves_before_dispatch(self):
        self.validation_fixture()
        self.validate()
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        pr_repairs.advance(self.service)
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'needs_attention')
        self.assertIn('upstream changed', task['pr_repair']['message'])
        self.store.manage_session.assert_not_called()

    def test_validation_update_stops_if_upstream_moves_before_acceptance(self):
        self.validation_fixture()
        self.validate()
        pr_repairs.advance(self.service)
        self.run['state'] = 'persona_sent'
        self.resolve()
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'needs_attention')
        self.assertIn('upstream changed', task['pr_repair']['message'])
        self.assertEqual(task['head_sha'], self.task['head_sha'])

    def test_validation_update_explains_a_checkout_that_lags_the_task_base(self):
        self.validation_fixture()
        workflow = self.repo / '.github/workflows/ci.yml'
        workflow.write_text('name: moved upstream checks\n')
        self.git('add', '.github/workflows/ci.yml')
        self.git('commit', '-m', 'upstream policy move')
        moved = self.git('rev-parse', 'HEAD').strip()
        self.git('update-ref', 'refs/remotes/origin/main', moved)
        self.git('reset', '-q', '--hard', self.upstream)
        result = self.validate()
        self.assertIn('approved checkout', result['action_message'])
        self.assertNotIn('pr_repair', self.service.get(self.task['id']))
        self.store.manage_session.assert_not_called()

    def test_checkout_mismatch_does_not_claim_ahead_checkout_is_behind(self):
        self.validation_fixture()
        (self.repo / 'unrelated').write_text('approved checkout ahead of fetched branch\n')
        self.git('add', 'unrelated')
        self.git('commit', '-m', 'ahead checkout')
        result = self.validate()
        self.assertIn('does not match', result['action_message'])
        self.assertNotIn('behind', result['action_message'])
        self.assertNotIn('pr_repair', self.service.get(self.task['id']))
        self.store.manage_session.assert_not_called()

    def test_inspected_validation_update_can_resume_after_a_prelaunch_blocker(self):
        self.validation_fixture()
        self.validate()
        source = Path(self.task['worktree']) / 'file'
        original = source.read_bytes()
        source.write_text('unfinished work\n')
        pr_repairs.advance(self.service)
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'needs_attention')
        self.store.manage_session.assert_not_called()
        source.write_bytes(original)
        self.service.action('repair', dict(request_id='inspected-update', task_id=self.task['id'],
            mode='retry', inspected=True), 'admin', 'admin')
        pr_repairs.advance(self.service)
        self.assertEqual(self.service.get(task['id'])['pr_repair']['state'], 'running')
        self.assertEqual(self.store.manage_session.call_count, 2)

    def test_validation_transition_is_bound_to_the_receipted_candidate_and_preserves_session_policy(self):
        self.validation_fixture()
        import toolchain_policy
        approved = dict(toolchain_policy.resolve_committed(self.repo), plan_digest='approved-plan')
        frozen = dict(digest='original-agent-policy', plan_digest='original-plan')
        with patch('pr_repairs.toolchain_policy.resolve_committed', return_value=approved), \
                patch.object(self.service, 'frozen_environment', return_value=frozen):
            self.validate()
            pr_repairs.advance(self.service)
            self.run['state'] = 'persona_sent'
            head = self.resolve()
            self.advance()
            task = self.service.get(self.task['id'])
            transition = pr_repairs.validation_binding(task)
            self.assertEqual(transition['candidate'], head)
            self.assertEqual(transition['approved_sha'], self.upstream)
            self.assertEqual(transition['plan_digest'], 'approved-plan')
            self.advance()
            event_id = self.service.validation.submit.call_args.args[0]['id']
            event = self.service.build_event(event_id)
            self.assertEqual(event['frozen_plan_digest'], 'approved-plan')
            self.assertEqual(event['policy_transition'], transition)
            self.assertEqual(frozen['digest'], 'original-agent-policy')
            with patch('toolchain_policy.evidence_matches', return_value=True):
                self.assertTrue(self.service.policy_matches(task, dict(
                    plan_digest='approved-plan', policy_digest=transition['digest'])))
            changed = copy.deepcopy(task)
            changed['head_sha'] = 'a' * 40
            self.assertIsNone(pr_repairs.validation_binding(changed))
            changed = copy.deepcopy(task)
            changed['completion_receipt']['commit'] = 'b' * 40
            self.assertIsNone(pr_repairs.validation_binding(changed))

    def test_validation_transition_does_not_adopt_policy_moved_after_candidate_capture(self):
        self.validation_fixture()
        import toolchain_policy
        approved = dict(toolchain_policy.resolve_committed(self.repo), plan_digest='approved-plan')
        moved = dict(approved, plan_digest='different-plan')
        captured = False
        perform = self.service.perform

        def capture_then_move(*args, **kwargs):
            nonlocal captured
            result = perform(*args, **kwargs)
            if args[0] == 'candidate':
                captured = True
            return result

        with patch('pr_repairs.toolchain_policy.resolve_committed',
                   side_effect=lambda _: moved if captured else approved), \
                patch.object(self.service, 'perform', side_effect=capture_then_move):
            self.validate()
            pr_repairs.advance(self.service)
            self.run['state'] = 'persona_sent'
            self.resolve()
            self.advance()
        self.assertTrue(captured)
        transition = pr_repairs.validation_binding(self.service.get(self.task['id']))
        self.assertEqual(transition['digest'], toolchain_policy.digest(approved))
        self.assertEqual(transition['plan_digest'], 'approved-plan')

    def test_validation_update_rejects_agent_policy_edits_after_merge(self):
        self.validation_fixture()
        self.validate()
        pr_repairs.advance(self.service)
        self.run['state'] = 'persona_sent'
        self.resolve()
        tree = Path(self.task['worktree'])
        (tree / '.github/workflows/ci.yml').write_text('name: weakened during repair\n')
        self.git('add', '.', path=tree)
        self.git('commit', '-m', 'policy mutation', path=tree)
        head = self.git('rev-parse', 'HEAD', path=tree).strip()
        receipt = tree / '.ci-cache/herdr-guidance' / ('task-' + self.task['id']) / 'receipt.json'
        data = json.loads(receipt.read_text())
        receipt.write_text(json.dumps(dict(data, commit=head)))
        self.advance()
        task = self.service.get(self.task['id'])
        self.assertEqual(task['pr_repair']['state'], 'needs_attention')
        self.assertIn('explicit review', task['pr_repair']['message'])
        self.assertEqual(task['head_sha'], self.task['head_sha'])

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

    def validated_update(self):
        self.validation_fixture()
        self.validate()
        pr_repairs.advance(self.service)
        self.run['state'] = 'persona_sent'
        head = self.resolve()
        self.advance()
        task = self.service.get(self.task['id'])
        task['publishing_repository'] = 'contributor/repo'
        task['builds'] = {head: dict(target=head, state='complete', required_checks_verified=True)}
        self.service.save(task, 'validated', 'test')
        self.advance()
        self.assertEqual(self.service.get(task['id'])['pr_repair']['state'], 'ready')
        return head

    def test_http_publish_updated_branch_before_first_pr_and_replay(self):
        head = self.validated_update()
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'a' * 48
        gateway.contributions = self.service
        gateway.operators = SimpleNamespace(identify=lambda token: None)
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps(dict(request_id='publish-updated', task_id=self.task['id'], head_sha=head)).encode()
            request = Request('http://127.0.0.1:' + str(gateway.server_port) + '/api/tasks/publish', data=payload,
                              headers={'Authorization': 'Bearer ' + gateway.token})
            with patch.object(self.service, 'remote_head', side_effect=[None, head]) as remote, \
                    patch('contributions.push') as push:
                for _ in range(2):
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        result = json.load(response)
                    self.assertEqual(result['state'], 'published')
                    self.assertEqual(result['publish']['head_sha'], head)
                    self.assertEqual(result['publish']['repository'], 'contributor/repo')
                    self.assertEqual(result['pr_repair']['state'], 'published')
                    self.assertNotIn('pull', result)
                self.assertEqual(remote.call_count, 2)
                push.assert_called_once()
                self.assertEqual(push.call_args.args[-1], 'contributor/repo')
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=2)

    def test_updated_branch_already_pushed_reconciles_and_can_open_first_draft_pr(self):
        head = self.validated_update()
        with patch.object(self.service, 'remote_head', return_value=head), patch('contributions.push') as push:
            task = self.service.action('publish', dict(request_id='reconcile-updated-push',
                task_id=self.task['id'], head_sha=head), 'admin', 'admin')
            push.assert_not_called()
            self.assertEqual(task['state'], 'published')
            self.assertNotIn('pull', task)
            preview = fixtures.contributions.pull_preview(task)
            pull = self.pull_data(task)
            pull['head']['repo']['full_name'] = 'contributor/repo'
            self.api.find_pull.return_value = []
            self.api.request.side_effect = [dict(name='main'), pull]
            result = self.service.action('pull', dict(request_id='first-updated-pr', task_id=task['id'],
                head_sha=head, reviewed=True, title=preview['title'], body=preview['body']), 'admin', 'admin')
        self.assertEqual(result['state'], 'pr_open')
        self.assertEqual(result['pull']['head_sha'], head)
        payload = self.api.request.call_args.args[2]
        self.assertEqual(payload['head'], 'contributor:' + task['branch'])
        self.assertTrue(payload['draft'])

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
        gateway.operators = SimpleNamespace(identify=lambda token: None)
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
        gateway.operators = SimpleNamespace(identify=lambda token: None)
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
