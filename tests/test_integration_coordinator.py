"""Durable inbox state-machine tests; no live model or network needed."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from integration_coordinator import IntegrationCoordinator


class Store:
    def __init__(self, root):
        self.projects = root
        self.jobs = {p: dict(id=p, profile_id=p, organization_id='org', kind='launch', state='persona_sent')
                     for p in ('worker', 'coordinator')}
        self.profiles = [dict(id=p, organization_id='org') for p in self.jobs]
        self.busy = set()
        self.calls, self.requests = [], {}

    def snapshot(self, **kwargs):
        return dict(jobs=list(self.jobs.values()), profiles=self.profiles)

    def identity(self, run):
        if run['profile_id'] in self.busy:
            raise ValueError('Busy')

    def action(self, name, body):
        if body['request_id'] in self.requests:
            return self.requests[body['request_id']]
        self.calls.append(body)
        job_id = 'chat-' + str(len(self.calls))
        self.jobs[job_id] = dict(id=job_id, kind='chat', state='queued',
                                 profile_id=body['profile_id'], integration_event=body.get('integration_event'))
        self.requests[body['request_id']] = {'id': job_id}
        return {'id': job_id}


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.path = self.root / 'coordination.sqlite3'
        self.service = IntegrationCoordinator(self.path, self.store)
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}):
            self.service.configure(dict(repository='repo', profile_id='coordinator', enabled=True))
        recovery = patch('integration_coordinator.project_git.recovery_snapshot', return_value=dict(ref='refs/herdr/recovery/test', commit='b' * 40))
        self.recovery = recovery.start()
        self.addCleanup(recovery.stop)
        self.notice = dict(repository='repo', target='a' * 40, behind=1, profile_id='worker',
                           run_id='worker', name='Max', path='repo', branch='feature', base='origin/main')

    def event(self):
        return self.service.snapshot()['events'][0]

    def answer(self, decision):
        event = self.event()
        self.store.jobs[event['job_id']].update(state='answered', result=json.dumps(decision))
        self.service.tick()

    def test_busy_agent_stays_queued_and_duplicate_events_are_suppressed(self):
        self.store.busy.add('worker')
        self.service.observe([self.notice, self.notice])
        self.service.tick()
        self.assertEqual(len(self.service.snapshot()['events']), 1)
        self.assertEqual(self.event()['state'], 'waiting')
        self.assertEqual(self.store.calls, [])
        self.store.busy.clear()
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.event()['state'], 'deciding')
        self.assertEqual(len(self.store.calls), 1)
        self.assertIn('a' * 40, self.store.calls[0]['prompt'])
        self.assertIn('Do not merge', self.store.calls[0]['prompt'])

    def test_restart_preserves_waiting_and_never_replays_uncertain_delivery(self):
        self.service.observe([self.notice])
        self.service = IntegrationCoordinator(self.path, self.store)
        self.service.tick()
        self.store.jobs[self.event()['job_id']]['state'] = 'uncertain'
        self.service = IntegrationCoordinator(self.path, self.store)
        self.service.tick()
        self.assertEqual(self.event()['state'], 'needs_attention')
        self.assertEqual(len([c for c in self.store.calls if c['profile_id'] == 'worker']), 1)

    def test_deferred_decision_is_collected_and_follows_up_after_user_task(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer(dict(decision='defer', reason='Finish current feature', checkpoint='After tests'))
        self.assertEqual(self.event()['state'], 'deferred')
        self.service.tick()
        self.assertEqual(self.event()['state'], 'deferred')
        reports = [c for c in self.store.calls if c['profile_id'] == 'coordinator']
        self.assertEqual(len(reports), 1)
        self.assertIn('Finish current feature', reports[0]['prompt'])
        self.store.jobs['user-task'] = dict(id='user-task', kind='chat', profile_id='worker', state='answered')
        self.service.tick()
        self.assertEqual(self.event()['state'], 'waiting')
        self.service.tick()
        self.assertEqual(self.event()['state'], 'deciding')

    def test_merge_requires_valid_worker_decision_and_verifies_git(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer(dict(decision='integrate_now', reason='API change relevant'))
        self.assertEqual(self.event()['state'], 'ready')
        self.service.tick()
        self.assertEqual(self.event()['state'], 'integrating')
        self.assertIn('merge exact commit ' + 'a' * 40, self.store.calls[-1]['prompt'])
        self.store.jobs[self.event()['job_id']].update(state='answered', result='Claimed complete')
        with patch('integration_coordinator.project_git.git', return_value='1'), patch(
                'integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            self.service.tick()
        self.assertEqual(self.event()['state'], 'blocked')

    def test_malformed_decision_does_not_authorize_merge_and_pause_prevents_delivery(self):
        self.service.observe([self.notice])
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}):
            self.service.configure(dict(repository='repo', enabled=False))
        self.service.tick()
        self.assertEqual(self.store.calls, [])
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}):
            self.service.configure(dict(repository='repo', profile_id='coordinator', enabled=True))
        self.service.tick()
        self.answer({'decision': 'discard_everything'})
        self.assertEqual(self.event()['state'], 'needs_attention')
        self.assertFalse(any('merge exact commit' in c['prompt'] for c in self.store.calls))

    def test_new_main_supersedes_only_undelivered_work(self):
        self.service.observe([self.notice])
        self.service.observe([dict(self.notice, target='b' * 40)])
        states = {e['target']: e['state'] for e in self.service.snapshot()['events']}
        self.assertEqual(states['a' * 40], 'superseded')
        self.assertEqual(states['b' * 40], 'waiting')
        self.service.tick()
        self.service.observe([dict(self.notice, target='c' * 40)])
        states = {e['target']: e['state'] for e in self.service.snapshot()['events']}
        self.assertEqual(states['b' * 40], 'deciding')
        self.assertEqual(states['c' * 40], 'waiting')

    def test_completed_requires_reported_passed_tests_and_verified_git(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer({'decision': 'integrate_now'})
        self.service.tick()
        self.store.jobs[self.event()['job_id']].update(state='answered', result=json.dumps({
            'outcome': 'integrated', 'tests': {'status': 'passed', 'summary': 'unit tests passed'}}))
        with patch('integration_coordinator.project_git.git', return_value='0'), patch(
                'integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            self.service.tick()
        self.assertEqual(self.event()['state'], 'completed')
        self.assertEqual(self.event()['tests']['status'], 'passed')

    def test_non_object_decision_requires_review_without_retry(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer(['integrate_now'])
        self.assertEqual(self.event()['state'], 'needs_attention')
        with self.assertRaises(ValueError):
            self.service.retry(self.event()['id'])

    def test_crash_after_chat_acceptance_reattaches_without_resending(self):
        self.service.observe([self.notice])
        with patch.object(self.service, 'save', side_effect=RuntimeError('simulated process crash')):
            with self.assertRaises(RuntimeError):
                self.service.tick()
        self.assertEqual(len(self.store.calls), 1)
        accepted_job = next(j for j in self.store.jobs.values() if j['kind'] == 'chat')
        accepted_job['state'] = 'uncertain'
        self.service = IntegrationCoordinator(self.path, self.store)
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.event()['state'], 'needs_attention')
        self.assertEqual(len([c for c in self.store.calls if c['profile_id'] == 'worker']), 1)


    def test_recovery_failure_prevents_merge_delivery_and_is_audited(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer(dict(decision='integrate_now', reason='Ready'))
        self.recovery.side_effect = ValueError('Snapshot failed')
        self.service.tick()
        self.assertEqual(self.event()['state'], 'ready')
        self.assertFalse(any('You chose integrate_now' in c['prompt'] for c in self.store.calls))
        self.assertTrue(any(a.get('reason') == 'Snapshot failed' for a in self.service.snapshot()['audit']))

    def test_snapshot_is_persisted_before_merge_and_survives_restart(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer(dict(decision='integrate_now', reason='Ready'))
        self.service.tick()
        recovered = IntegrationCoordinator(self.path, self.store).snapshot()
        self.assertEqual(recovered['events'][0]['recovery']['commit'], 'b' * 40)
        self.assertTrue(any(a.get('recovery') for a in recovered['audit']))
