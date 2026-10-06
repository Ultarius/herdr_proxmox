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

    def save_reply(self, text):
        path = self.store.path.parent / 'chat-replies' / self.report['id'] / 'reply.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_saved_reply_is_recovered_without_prompting_and_retry_is_idempotent(self):
        self.save_reply('Collected worker evidence: merge complete; validation pending.')
        before = len(self.calls)
        result = self.service.repair_report(self.body)
        self.assertEqual(result['resolution'], 'recovered')
        job = self.store.job_records()[-1]
        self.assertEqual(job['state'], 'answered')
        self.assertIn('validation pending', job['result'])
        self.assertEqual(job['previous_error'], 'Uncertain terminal delivery')
        self.assertTrue(self.store.jobs_changed.is_set())
        self.assertFalse(any(args[:2] == ('agent', 'prompt') for args, _ in self.calls[before:]))
        audit = self.service.snapshot()['audit']
        self.assertEqual(self.service.repair_report(self.body), result)
        self.assertEqual(self.service.snapshot()['audit'], audit)

    def test_missing_reply_requires_explicit_inspection_before_fresh_summary(self):
        with self.assertRaisesRegex(ValueError, 'Coordinator report reply'):
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
