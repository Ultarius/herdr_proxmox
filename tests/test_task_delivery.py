"""Delivery state transitions use validation-run identity as well as commit identity."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from contributions import Contributions
import task_delivery


class RevalidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = dict(id='original-session', state='persona_sent', alias='worker', profile_id='worker')
        self.reply = dict(id='old-inspection', kind='chat', state='answered', runs=[self.run],
            organization_id='org', profile_id='worker',
            delivery=dict(stage='reply_verified'), result=json.dumps(dict(
                sha='a' * 40, outcome='needs_attention', summary='The previous validation failed.')))
        self.jobs = [self.run, self.reply]
        self.store = Mock()
        self.store.knowledge_jobs.return_value = []
        self.store.snapshot.side_effect = lambda **kwargs: dict(jobs=copy.deepcopy(self.jobs))
        def job_record(identifier):
            job = next((job for job in self.jobs if job['id'] == identifier), None)
            if job is None:
                raise ValueError('Job is unavailable')
            return copy.deepcopy(job)
        self.store.job_record.side_effect = job_record
        self.store.job_for_request.return_value = None
        self.store.identity.return_value = dict(agent_status='idle')
        self.store.action.return_value = dict(id='new-inspection')
        self.service = Contributions(self.root / 'tasks.sqlite3', self.root, self.store, Mock())
        self.service.validation = Mock()
        self.service.policy_matches = Mock(return_value=True)
        old = dict(run_id='old-failed-run', state='error')
        self.task = dict(id='1' * 32, title='Revalidated candidate', head_sha='a' * 40, run_id=self.run['id'], profile_id='worker',
            state='review_ready', organization_id='org', github_repository='owner/repo', repository='repo',
            await_verification=True, auto_publish=False, completion_receipt=dict(commit='a' * 40),
            builds={'a' * 40: dict(target='a' * 40, state='complete', required_checks_verified=True,
                run_id='new-passing-run', checks=[dict(id='test', status='passed')])},
            delivery_workflow=dict(sha='a' * 40, state='attention', result_kind='validation_failed',
                failure_stage='verification', error='Old agent blocked', chat_id=self.reply['id'], attempt=0,
                feedback_prompt='Frozen failure. Evidence: ' + json.dumps(dict(sha='a' * 40, validation=old))))
        self.save()

    def save(self):
        self.service.save(self.task, 'fixture', 'test')

    def advance(self):
        # These unit tests exercise the actual durable task state machine.
        # Checkout/session verification have independent real-Git/Linux suites.
        with patch('collaboration.current_run', return_value=self.run), \
                patch('task_delivery.verify_checkout', return_value=dict(tests=[])):
            task_delivery.advance(self.service)
        return self.service.get(self.task['id'])

    def test_same_commit_pass_supersedes_verified_failure_and_requires_fresh_inspection(self):
        task = self.advance()
        flow = task['delivery_workflow']
        self.assertEqual(flow['state'], 'waiting_agent')
        self.assertEqual(flow['validation']['run_id'], 'new-passing-run')
        self.assertEqual(flow['attempt'], 1)
        self.assertTrue(task_delivery.pending(task))
        self.assertEqual(task['head_sha'], self.task['head_sha'])
        old = task['delivery_history'][-1]
        self.assertEqual(old['chat_id'], 'old-inspection')
        self.assertEqual(old['validation']['run_id'], 'old-failed-run')
        self.assertEqual(old['superseded_by_validation'], 'new-passing-run')
        self.assertNotIn('feedback_prompt', old)
        arguments = self.store.action.call_args.args[1]
        self.assertTrue(arguments['request_id'].endswith('-1'))
        self.assertIn('new-passing-run', arguments['prompt'])
        self.assertNotIn('old-failed-run', arguments['prompt'])
        self.assertEqual(self.store.action.call_count, 1)
        # A restart/poll still waits for the new chat; an old answer cannot finish it.
        self.jobs.append(dict(id='new-inspection', state='queued', runs=[self.run]))
        self.assertEqual(self.advance()['delivery_workflow']['state'], 'waiting_agent')
        self.assertEqual(self.store.action.call_count, 1)
        self.jobs[-1].update(state='answered', delivery=dict(stage='reply_verified'),
            result=json.dumps(dict(sha='a' * 40, outcome='reviewed', summary='Inspected the passing checks.')))
        task = self.advance()
        self.assertEqual(task['delivery_workflow']['state'], 'complete')
        self.assertFalse(task_delivery.pending(task))
        self.service.validation.submit.assert_not_called()
        self.store.manage_session.assert_not_called()

    def test_pending_and_uncertain_chats_are_not_replayed_or_released(self):
        for state, stage in (('queued', None), ('running', 'submitting'), ('waiting_for_input', 'blocked'),
                             ('needs_attention', 'blocked'), ('uncertain', 'unknown'), ('answered', 'unknown')):
            with self.subTest(state=state):
                self.reply.update(state=state, delivery=dict(stage=stage))
                self.save()
                task = self.advance()
                expected = 'waiting_agent' if state in task_delivery.ACTIVE_INSPECTIONS else 'attention'
                self.assertEqual(task['delivery_workflow']['state'], expected)
                self.assertIn('new-passing-run passed', task['delivery_workflow']['error'])
                self.assertNotIn('Old agent blocked', task['delivery_workflow']['error'])
                self.assertEqual(task['delivery_workflow']['previous_inspection_error'], 'Old agent blocked')
                self.assertNotIn('delivery_history', task)
                self.assertTrue(task_delivery.pending(task))
                count = len(task['audit'])
                self.assertEqual(len(self.advance()['audit']), count)
        self.store.action.assert_not_called()

    def test_recorded_failure_binding_does_not_need_legacy_prompt_parsing(self):
        flow = self.task['delivery_workflow']
        flow['validation'] = dict(run_id='old-failed-run', state='error')
        flow.pop('feedback_prompt')
        self.save()
        self.assertEqual(self.advance()['delivery_workflow']['validation']['run_id'], 'new-passing-run')

    def test_crash_before_enqueue_uses_passing_evidence_without_sending_frozen_failure(self):
        flow = self.task['delivery_workflow']
        flow.update(state='notifying_agent')
        flow.pop('chat_id')
        request = task_delivery.inspection_request_id(self.task, flow)
        self.save()
        task = self.advance()
        self.store.job_for_request.assert_called_once_with(request)
        self.assertEqual(task['delivery_workflow']['attempt'], 1)
        self.assertEqual(task['delivery_workflow']['state'], 'waiting_agent')
        self.assertIn('new-passing-run', self.store.action.call_args.args[1]['prompt'])
        self.assertNotIn('old-failed-run', self.store.action.call_args.args[1]['prompt'])
        self.assertEqual(self.store.action.call_count, 1)

    def test_crash_after_enqueue_recovers_the_chat_id_and_waits_before_refreshing(self):
        flow = self.task['delivery_workflow']
        flow.update(state='notifying_agent')
        flow.pop('chat_id')
        self.reply.update(state='queued')
        self.store.job_for_request.side_effect = lambda request: copy.deepcopy(self.reply)
        self.save()
        task = self.advance()
        self.assertEqual(task['delivery_workflow']['chat_id'], self.reply['id'])
        self.assertEqual(task['delivery_workflow']['state'], 'waiting_agent')
        self.store.action.assert_not_called()
        self.store.update_job.assert_any_call('old-inspection', task_id=self.task['id'], delivery_inspection=True)
        self.reply.update(state='answered')
        task = self.advance()
        self.assertEqual(task['delivery_workflow']['chat_id'], 'new-inspection')
        self.assertEqual(self.store.action.call_count, 1)

    def test_missing_and_invalid_old_jobs_expose_operator_recovery_from_waiting_agent(self):
        for value in ('missing', dict(unexpected='result'), 'invalid JSON'):
            with self.subTest(value=value):
                self.jobs[:] = [self.run] if value == 'missing' else [self.run, self.reply]
                self.reply['result'] = value
                self.task['delivery_workflow']['state'] = 'waiting_agent'
                self.save()
                task = self.advance()
                flow = task['delivery_workflow']
                self.assertEqual(flow['state'], 'attention')
                self.assertFalse(flow['revalidation_wait']['inspection_pending'])
                if value == 'missing':
                    self.assertIn('job is unavailable', flow['error'])
                    self.assertNotIn('recover its saved chat reply', flow['error'])
                else:
                    self.assertIn('recover its saved chat reply', flow['error'])
                self.assertIn('Retry verification and delivery inspection', flow['error'])
        self.store.action.assert_not_called()

    def test_retry_mutation_rechecks_active_chat_even_with_stale_attention_state(self):
        for state in task_delivery.ACTIVE_INSPECTIONS:
            with self.subTest(state=state):
                self.reply['state'] = state
                self.save()
                with patch.object(self.service, 'path_for', return_value=self.root), \
                        patch('contributions.remote_info', return_value='owner/repo'):
                    with self.assertRaisesRegex(ValueError, 'previous delivery inspection is still active'):
                        self.service.action('policy', dict(task_id=self.task['id'], request_id='retry-' + state,
                            auto_validate=True, auto_publish=False, retry_delivery=True), 'admin', 'admin')
                flow = self.service.get(self.task['id'])['delivery_workflow']
                self.assertEqual(flow['attempt'], 0)
                self.assertEqual(flow['chat_id'], 'old-inspection')
        self.store.action.assert_not_called()
        self.service.validation.submit.assert_not_called()

    def test_wait_preserves_original_error_and_run_ids_in_history(self):
        self.reply['state'] = 'uncertain'
        self.save()
        self.advance()
        self.reply['state'] = 'answered'
        task = self.advance()
        entry = task['delivery_history'][-1]
        self.assertEqual(entry['previous_inspection_error'], 'Old agent blocked')
        self.assertEqual(entry['revalidation_wait']['previous_run_id'], 'old-failed-run')

    def test_cancelled_and_legacy_unverified_complete_inspections_can_be_superseded(self):
        for state in ('cancelled', 'complete'):
            with self.subTest(state=state):
                self.task['delivery_workflow']['validation'] = dict(run_id='old-failed-run', state=state,
                                                                    required_checks_verified=False)
                self.save()
                self.assertEqual(self.advance()['delivery_workflow']['validation']['run_id'], 'new-passing-run')

    def test_refresh_reads_one_job_without_a_full_snapshot(self):
        self.store.snapshot.reset_mock()
        self.assertEqual(task_delivery.refresh_failed_inspection(self.service, self.task,
            self.task['delivery_workflow']), 'refreshed')
        self.store.job_record.assert_called_once_with('old-inspection')
        self.store.snapshot.assert_not_called()

    def test_new_inspection_still_requires_the_original_session_to_be_ready(self):
        from herdr_errors import HerdrError
        with patch('collaboration.current_run', side_effect=HerdrError('Live permission prompt',
                code='agent_blocked', delivery='none')):
            task_delivery.advance(self.service)
        task = self.service.get(self.task['id'])
        self.assertEqual(task['delivery_workflow']['state'], 'attention')
        self.assertIn('Live permission prompt', task['delivery_workflow']['error'])
        self.store.action.assert_not_called()
        self.assertTrue(task_delivery.pending(task))

    def test_repaired_reply_is_left_to_existing_repair_acceptance(self):
        self.reply['result'] = json.dumps(dict(sha='a' * 40, outcome='repaired', candidate='b' * 40, summary='New commit'))
        self.assertIsNone(task_delivery.refresh_failed_inspection(self.service, self.task, self.task['delivery_workflow']))
        self.store.action.assert_not_called()

    def test_only_new_verified_current_policy_results_can_refresh_a_validation_failure(self):
        build = self.task['builds']['a' * 40]
        original = copy.deepcopy(build)
        for changes in (dict(run_id='old-failed-run'), dict(run_id=None), dict(state='error'),
                        dict(required_checks_verified=False), dict(target='b' * 40)):
            with self.subTest(changes=changes):
                self.task['builds']['a' * 40] = dict(original, **changes)
                self.save()
                self.assertEqual(self.advance()['delivery_workflow']['error'], 'Old agent blocked')
        self.task['builds']['a' * 40] = original
        self.service.policy_matches.return_value = False
        self.save()
        self.assertEqual(self.advance()['delivery_workflow']['error'], 'Old agent blocked')
        self.store.action.assert_not_called()

    def test_publication_failures_and_unbound_legacy_reports_keep_explicit_recovery(self):
        for changes in (dict(result_kind='delivery_failed', failure_stage='credentials'),
                        dict(feedback_prompt='No frozen validation identity')):
            with self.subTest(changes=changes):
                task = copy.deepcopy(self.task)
                task['delivery_workflow'].update(changes)
                self.service.save(task, 'fixture', 'test')
                self.assertEqual(self.advance()['delivery_workflow']['error'], 'Old agent blocked')
        self.store.action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
