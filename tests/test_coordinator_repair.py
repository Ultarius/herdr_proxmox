"""Repair real stored reports without replaying uncertain terminal input."""
from contextlib import closing
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_organizations as fixtures
from integration_coordinator import IntegrationCoordinator


class ReportRepairTests(unittest.TestCase):
    tearDown = fixtures.OrganizationTests.tearDown
    command = fixtures.OrganizationTests.command
    action = fixtures.OrganizationTests.action
    organization = fixtures.OrganizationTests.organization
    drain = fixtures.OrganizationTests.drain

    def setUp(self):
        fixtures.OrganizationTests.setUp(self)
        self.org = self.organization()
        self.profile = self.action('hire', organization_id=self.org, name='Coordinator',
                                   role='Integration coordinator', runtime='codex',
                                   project=str(self.projects), persona='summarize reports')
        self.run_id = self.action('launch', organization_id=self.org, profile_id=self.profile)
        self.drain()
        self.service = IntegrationCoordinator(self.root / 'integration.sqlite3', self.store)
        self.addCleanup(self.service.close)
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}):
            config = self.service.configure(dict(repository='repo', profile_id=self.profile))
        run = self.store.job_records(launches_only=True)[0]
        self.report = dict(id='interrupted-report', organization_id=self.org, kind='chat',
                           profile_id=self.profile, state='needs_attention', runs=[run],
                           participants=[self.profile], error='Uncertain terminal delivery', result='')
        with self.store.lock, closing(self.store.connect()) as db, db:
            self.store.put(db, 'jobs', self.report)
        config['report_job_id'] = self.report['id']
        config['report_digest'] = 'old-digest'
        with closing(self.service.connect()) as db, db:
            db.execute('UPDATE settings SET data=? WHERE repository=?', (json.dumps(config), 'repo'))
        self.body = dict(repository='repo', job_id=self.report['id'], mode='recover')
        self.store.jobs_changed.clear()

    def save_reply(self, text, name='reply.md'):
        path = self.store.path.parent / 'chat-replies' / self.report['id'] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_ordinary_chat_recovery_after_timeout_is_idempotent_without_input(self):
        self.save_reply('Late ordinary answer')
        body = dict(job_id=self.report['id'], organization_id=self.org, inspected=True)
        before = len(self.calls)
        self.assertEqual(self.store.recover_chat(body, 'reviewer'), {'resolution': 'recovered'})
        self.assertEqual(self.store.recover_chat(body, 'reviewer'), {'resolution': 'recovered'})
        job = next(j for j in self.store.job_records() if j['id'] == self.report['id'])
        self.assertEqual(job['result'], 'Late ordinary answer')
        self.assertEqual(job['previous_error'], 'Uncertain terminal delivery')
        self.assertEqual(job['recovered_by'], 'reviewer')
        self.assertFalse(any(call[0:2] == ('agent', 'prompt') for call in self.calls[before:]))
        with self.assertRaisesRegex(ValueError, 'Inspect'):
            self.store.recover_chat(dict(body, inspected=False), 'reviewer')

    def test_recovery_reads_the_attempt_reply_from_delivery_evidence(self):
        # Existing jobs keep reply.md; an attempt file recorded before submission
        # is the one recovery reads, and a stale legacy file is ignored.
        name = 'reply-abcdefabcdef.md'
        self.store.update_job(self.report['id'],
                              delivery=dict(stage='session_idle_reply_pending', reply_name=name))
        self.save_reply('Attempt evidence', name=name)
        self.save_reply('Stale legacy content')
        result = self.service.repair_report(self.body)
        self.assertEqual(result['resolution'], 'recovered')
        job = self.store.job_records()[-1]
        self.assertEqual(job['result'], 'Attempt evidence')
        self.assertEqual(job['delivery']['stage'], 'reply_verified')
        self.assertTrue(job['delivery']['recovered'])

    def test_recovery_refuses_an_unexpected_reply_file_name(self):
        self.store.update_job(self.report['id'],
                              delivery=dict(stage='session_idle_reply_pending', reply_name='../escape.md'))
        self.save_reply('content')
        with self.assertRaisesRegex(ValueError, 'unsupported reply file name'):
            self.service.repair_report(self.body)

    def worker_event(self, phase='integrating'):
        event_id = 'a' * 64
        self.store.update_job(self.report['id'], integration_event=event_id)
        event = dict(id=event_id, repository='repo', profile_id=self.profile, run_id=self.run_id,
                     name='Worker', path='repo', target='b' * 40, state='needs_attention',
                     interrupted_phase=phase, job_id=self.report['id'], recovery={'ref': 'refs/herdr/recovery/test'})
        with closing(self.service.connect()) as db, db:
            self.service.save(db, event)
        return dict(id=event_id, job_id=self.report['id'], mode='recover', inspected=True)

    def test_worker_result_recovery_is_idle_bound_audited_and_idempotent(self):
        body = self.worker_event()
        self.save_reply('{"outcome":"integrated","tests":{"status":"passed"}}')
        before = len(self.calls)
        recovered = self.service.recover_worker(body, actor='damien')
        self.assertEqual(recovered['state'], 'integrating')
        self.assertEqual(recovered['recovery']['ref'], 'refs/herdr/recovery/test')
        self.assertEqual(self.store.job_records()[-1]['state'], 'answered')
        audit = self.service.snapshot()['audit']
        self.assertEqual(self.service.recover_worker(body, actor='damien'), recovered)
        self.assertEqual(self.service.snapshot()['audit'], audit)
        self.assertFalse(any(args[:2] == ('agent', 'prompt') for args, _ in self.calls[before:]))
        with self.assertRaises(ValueError):
            self.service.recover_worker(body, actor='another-operator')
        with patch('integration_coordinator.project_git.git', return_value='0'), \
                patch('integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            self.service.tick()
        finished = self.service.event(body['id'])
        self.assertEqual(finished['state'], 'completed')
        self.assertTrue(finished['verification']['target_incorporated'])

    def test_worker_validation_continuation_requires_verified_commit_and_idle_original_session(self):
        body = dict(self.worker_event(), mode='validate')
        with patch('integration_coordinator.project_git.git', return_value='1'), \
                patch('integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            with self.assertRaisesRegex(ValueError, 'incorporated'):
                self.service.recover_worker(body)
        with patch('integration_coordinator.project_git.git', return_value='0'), \
                patch('integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            alias = self.report['runs'][0]['alias']
            self.agents[alias]['agent_status'] = 'blocked'
            with self.assertRaises(ValueError):
                self.service.recover_worker(body)
            self.agents[alias]['agent_status'] = 'idle'
            result = self.service.recover_worker(body)
        self.assertEqual(result['state'], 'validation_ready')
        self.assertNotIn('job_id', result)
        self.assertEqual(result['attempt'], 1)
        self.assertEqual(result['verification'], {'target_incorporated': True, 'conflicts': 0, 'merging': False})
        self.assertEqual(self.store.job_records()[-1]['state'], 'superseded')
        self.assertEqual(result['recovery']['ref'], 'refs/herdr/recovery/test')

    def test_worker_recovery_rejects_changed_session_and_agent_lock_contention(self):
        body = self.worker_event()
        self.save_reply('{}')
        alias = self.report['runs'][0]['alias']
        self.agents[alias]['agent_session'] = {'value': 'replaced-session'}
        with self.assertRaises(ValueError):
            self.service.recover_worker(body)
        lock = self.store.agent_locks.setdefault(self.profile, threading.RLock())
        entered, release = threading.Event(), threading.Event()
        def occupy():
            with lock:
                entered.set()
                release.wait(5)
        thread = threading.Thread(target=occupy)
        thread.start()
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaisesRegex(ValueError, 'executing'):
                self.service.recover_worker(body)
        finally:
            release.set()
            thread.join(2)

    def test_worker_recovery_rejects_uninspected_stale_wrong_event_and_unsafe_output(self):
        body = self.worker_event('validating')
        for changed in ({'inspected': False}, {'job_id': 'stale'}):
            with self.assertRaises(ValueError):
                self.service.recover_worker({**body, **changed})
        with self.assertRaises(ValueError):
            self.service.recover_worker(body)
        self.save_reply('x' * 40001)
        with self.assertRaises(ValueError):
            self.service.recover_worker(body)
        self.store.update_job(self.report['id'], integration_event='other-event')
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            self.service.recover_worker(body)

    def test_saved_reply_is_recovered_without_prompting_and_retry_is_idempotent(self):
        self.save_reply('Collected worker evidence: merge complete; validation pending.')
        before = len(self.calls)
        result = self.service.repair_report(self.body)
        self.assertEqual(result['resolution'], 'recovered')
        job = self.store.job_records()[-1]
        self.assertEqual(job['state'], 'answered')
        self.assertIn('validation pending', job['result'])
        self.assertEqual(job['previous_error'], 'Uncertain terminal delivery')
        self.assertEqual(job['error'], '')
        # Recovery verifies saved reply delivery without pretending the terminal
        # turn was proven by the CLI.
        self.assertEqual(job['delivery']['stage'], 'reply_verified')
        self.assertTrue(job['delivery']['recovered'])
        self.assertGreater(job['delivery']['bytes'], 0)
        self.assertTrue(self.store.jobs_changed.is_set())
        self.assertFalse(any(args[:2] == ('agent', 'prompt') for args, _ in self.calls[before:]))
        audit = self.service.snapshot()['audit']
        self.assertEqual(self.service.repair_report(self.body), result)
        self.assertEqual(self.service.snapshot()['audit'], audit)

    def test_missing_reply_requires_explicit_inspection_before_fresh_summary(self):
        with self.assertRaisesRegex(ValueError, 'Reply recovery failed'):
            self.service.repair_report(self.body)
        with self.assertRaisesRegex(ValueError, 'Inspect'):
            self.service.repair_report(dict(self.body, mode='fresh'))
        body = dict(self.body, mode='fresh', inspected=True)
        self.assertEqual(self.service.repair_report(body)['resolution'], 'fresh')
        config = self.service.snapshot()['configurations'][0]
        self.assertNotIn('report_job_id', config)
        self.assertNotIn('report_digest', config)
        self.assertEqual(config['report_epoch'], 1)
        self.assertEqual(config['report_state'], 'waiting')
        self.assertTrue(config['report_pending'])
        self.assertEqual(self.store.job_records()[-1]['state'], 'superseded')
        self.service.repair_report(body)
        self.assertEqual(self.service.snapshot()['configurations'][0]['report_epoch'], 1)

    def test_busy_or_changed_session_cannot_recover_partial_output(self):
        self.save_reply('Potentially partial reply')
        alias = self.report['runs'][0]['alias']
        self.agents[alias]['agent_status'] = 'working'
        with self.assertRaises(ValueError):
            self.service.repair_report(self.body)
        with self.assertRaises(ValueError):
            self.service.repair_report(dict(self.body, mode='fresh', inspected=True))
        self.agents[alias]['agent_status'] = 'idle'
        self.agents[alias]['agent_session'] = {'value': 'different-session'}
        with self.assertRaises(ValueError):
            self.service.repair_report(self.body)

    def test_stale_report_and_oversized_reply_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.repair_report(dict(self.body, job_id='different-report'))
        self.save_reply('x' * 40001)
        with self.assertRaises(ValueError):
            self.service.repair_report(self.body)

    def test_nonterminal_jobs_and_missing_binding_cannot_be_recovered(self):
        self.save_reply('Should not be accepted')
        self.store.update_job(self.report['id'], state='running')
        with self.assertRaises(ValueError):
            self.service.repair_report(self.body)
        self.store.update_job(self.report['id'], state='needs_attention', runs=[])
        with self.assertRaises(ValueError):
            self.service.repair_report(self.body)

    def test_released_binding_relaunches_same_profile_once(self):
        self.action('release', organization_id=self.org, job_id=self.run_id)
        body = dict(self.body, mode='fresh', inspected=True)
        self.service.repair_report(body)
        self.drain()
        self.service.repair_report(body)
        self.assertEqual(len(self.store.profile_records()), 1)
        self.assertEqual(len(self.store.job_records(launches_only=True)), 2)

    def test_symlink_output_and_an_occupied_agent_lock_are_rejected(self):
        self.save_reply('Saved reply')
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaises(ValueError):
                self.service.repair_report(self.body)
        entered, release = threading.Event(), threading.Event()
        lock = self.store.agent_locks[self.profile]
        def hold_lock():
            with lock:
                entered.set()
                release.wait(2)
        thread = threading.Thread(target=hold_lock)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, 'still executing'):
                self.service.repair_report(self.body)
        finally:
            release.set()
            thread.join(2)
