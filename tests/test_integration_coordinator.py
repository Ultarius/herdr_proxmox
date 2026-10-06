"""Durable inbox state-machine tests; no live model or network needed."""
import json
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from integration_coordinator import IntegrationCoordinator, report_digest


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

    def job_records(self, launches_only=False):
        return [j for j in self.jobs.values() if not launches_only or j['kind'] == 'launch']

    def profile_records(self):
        return list(self.profiles)

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
        plan = patch('integration_coordinator.merge_mode', return_value='fast_forward')
        plan.start()
        self.addCleanup(plan.stop)
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
        self.assertIn('do not execute a merge', self.store.calls[0]['prompt'])
        self.assertIn('does not itself revoke the developer role', self.store.calls[0]['prompt'])

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
        merge = self.store.calls[-1]
        self.assertEqual(merge['profile_id'], 'worker')
        self.assertIn('git merge --no-edit <target>', merge['prompt'])
        self.assertIn('Missing required suites/toolchains', merge['prompt'])
        self.store.jobs[self.event()['job_id']].update(state='answered', result=json.dumps({
            'outcome': 'integrated', 'tests': {'status': 'passed', 'summary': 'unit tests passed'}}))
        with patch('integration_coordinator.project_git.git', return_value='0'), patch(
                'integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            self.service.tick()
        self.assertEqual(self.event()['state'], 'completed')
        self.assertEqual(self.event()['tests']['status'], 'passed')
        report = [c for c in self.store.calls if c['profile_id'] == 'coordinator'][-1]['prompt']
        self.assertIn('Do not inspect repositories or other worktrees', report)
        payload = json.loads(report.split('\n', 1)[1])
        self.assertEqual(payload[0]['tests']['summary'], 'unit tests passed')
        self.assertTrue(payload[0]['verification']['target_incorporated'])
        self.assertEqual(payload[0]['recovery']['ref'], 'refs/herdr/recovery/test')

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


    def test_failed_coordinator_launch_does_not_block_worker_delivery_and_is_visible(self):
        self.store.jobs['coordinator'].update(state='needs_attention', error='Invalid root pane')
        self.service.observe([self.notice])
        self.service.tick()
        self.assertEqual(self.store.calls[0]['profile_id'], 'worker')
        self.assertEqual(self.event()['state'], 'deciding')
        config = self.service.snapshot()['configurations'][0]
        self.assertEqual(config['coordinator_state'], 'needs_attention')
        self.assertEqual(config['coordinator_error'], 'Invalid root pane')


    def test_outage_preserves_terminal_outcomes_without_new_audit_entries(self):
        self.service.observe([self.notice])
        for state in ('completed', 'blocked', 'needs_attention', 'superseded'):
            with closing(self.service.connect()) as db, db:
                event = self.event()
                event.update(state=state, reason='Original evidence', tests={'status': 'passed', 'summary': 'test command'})
                self.service.save(db, event)
            before = len(self.service.snapshot()['audit'])
            self.store.jobs['coordinator']['state'] = 'needs_attention'
            self.service.tick()
            self.assertEqual(self.event()['reason'], 'Original evidence')
            self.assertEqual(self.event()['tests']['summary'], 'test command')
            self.assertEqual(len(self.service.snapshot()['audit']), before)

    def test_decision_and_merge_continue_during_coordinator_outage(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.store.jobs['coordinator']['state'] = 'needs_attention'
        self.answer({'decision': 'integrate_now', 'reason': 'Ready'})
        self.assertEqual(self.event()['state'], 'ready')
        self.service.tick()
        self.assertEqual(self.event()['state'], 'integrating')
        self.assertEqual(len(self.store.calls), 2)

    def test_merge_result_finishes_during_coordinator_outage(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer({'decision': 'integrate_now', 'reason': 'Ready'})
        self.service.tick()
        self.store.jobs['coordinator']['state'] = 'needs_attention'
        event = self.event()
        self.store.jobs[event['job_id']].update(state='answered', result=json.dumps({
            'outcome': 'integrated', 'tests': {'status': 'passed', 'summary': 'tests passed'}}))
        with patch('integration_coordinator.project_git.git', return_value='0'), patch(
                'integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            self.service.tick()
        self.assertEqual(self.event()['state'], 'completed')
        self.assertEqual(self.event()['tests']['summary'], 'tests passed')

    def test_terminal_report_job_does_not_block_later_reports(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer({'decision': 'blocked', 'reason': 'First blocker'})
        self.service.tick()
        reports = [c for c in self.store.calls if c['profile_id'] == 'coordinator']
        self.assertEqual(len(reports), 1)
        # A resolved report must allow a later outcome to be summarized.
        config = self.service.snapshot()['configurations'][0]
        self.store.jobs[config['report_job_id']]['state'] = 'answered'
        with closing(self.service.connect()) as db, db:
            event = self.event()
            event.update(state='completed', reason='Second outcome')
            self.service.save(db, event)
        self.service.tick()
        reports = [c for c in self.store.calls if c['profile_id'] == 'coordinator']
        self.assertEqual(len(reports), 2)
        self.assertIn('Second outcome', reports[1]['prompt'])

    def test_uncertain_report_waits_for_operator_resolution(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer({'decision': 'blocked', 'reason': 'First blocker'})
        config = self.service.snapshot()['configurations'][0]
        with closing(self.service.connect()) as db, db:
            event = self.event()
            event.update(reason='New evidence')
            self.service.save(db, event)
        for state in ('uncertain', 'needs_attention'):
            self.store.jobs[config['report_job_id']]['state'] = state
            self.service.tick()
            self.assertEqual(len([c for c in self.store.calls if c['profile_id'] == 'coordinator']), 1)
        snapshot = self.service.snapshot()['configurations'][0]
        self.assertEqual(snapshot['report_state'], 'needs_attention')
        # Replacing a session after explicitly releasing its binding makes a
        # fresh report safe without replaying the old report's content.
        self.store.jobs[config['report_job_id']]['runs'] = [{'id': 'old-coordinator'}]
        self.store.jobs['old-coordinator'] = dict(id='old-coordinator', kind='launch',
                                                 profile_id='coordinator', state='released')
        self.service.tick()
        self.assertEqual(len([c for c in self.store.calls if c['profile_id'] == 'coordinator']), 2)

    def test_report_digest_notices_changes_beyond_the_prompt_bound(self):
        first = [dict(id='a', state='blocked', reason='x'), dict(id='b', state='completed', reason='y')]
        shortened = first[:1]  # the 6000-byte cap dropped the second event
        changed = [first[0], dict(id='b', state='completed', reason='changed')]
        self.assertEqual(report_digest(first, shortened), report_digest(list(first), shortened))
        self.assertNotEqual(report_digest(first, shortened), report_digest(changed, shortened))

    def test_report_digest_detects_test_and_verification_changes(self):
        original = [dict(id='a', state='completed', reason='x' * 500)]
        for changes in ({'reason': 'x' * 499 + 'y'}, {'tests': {'status': 'not_run'}},
                        {'verification': {'target_incorporated': True}},
                        {'recovery': {'ref': 'refs/herdr/recovery/new'}}):
            changed = [dict(original[0], **changes)]
            self.assertNotEqual(report_digest(original, original), report_digest(changed, original))

    def test_missing_tests_preserve_merge_and_validation_retry_does_not_merge(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer({'decision': 'integrate_now'})
        self.service.tick()
        recovery = self.event()['recovery']
        self.store.jobs[self.event()['job_id']].update(state='answered', result=json.dumps({
            'outcome': 'integrated', 'tests': {'status': 'not_run', 'summary': 'Flutter missing; Python passed'}}))
        with patch('integration_coordinator.project_git.git', return_value='0'), patch(
                'integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            self.service.tick()
        self.assertEqual(self.event()['state'], 'validation_pending')
        self.assertTrue(self.event()['verification']['target_incorporated'])
        self.service.retry(self.event()['id'])
        self.service.tick()
        self.assertEqual(self.event()['state'], 'validating')
        self.assertEqual(self.event()['recovery'], recovery)
        self.assertEqual(self.recovery.call_count, 1)
        prompt = self.store.calls[-1]['prompt']
        self.assertIn('Validation only', prompt)
        self.assertIn('Do not merge again', prompt)
        self.assertEqual(self.store.calls[-1]['profile_id'], 'worker')
        self.store.jobs[self.event()['job_id']].update(state='answered', result=json.dumps({
            'outcome': 'integrated', 'tests': {'status': 'passed', 'summary': 'All required checks passed'}}))
        with patch('integration_coordinator.project_git.git', return_value='0'), patch(
                'integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            self.service.tick()
        self.assertEqual(self.event()['state'], 'completed')

    def test_divergent_branch_dispatches_a_normal_merge(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer({'decision': 'integrate_now'})
        with patch('integration_coordinator.merge_mode', return_value='merge'):
            self.service.tick()
        self.assertEqual(self.event()['merge_mode'], 'merge')
        self.assertIn('use git merge --no-edit ' + self.notice['target'], self.store.calls[-1]['prompt'])

    def test_failed_tests_and_conflicts_are_not_completed(self):
        for target, status, conflicts, expected in (
                ('b' * 40, 'failed', 0, 'validation_failed'),
                ('c' * 40, 'passed', 1, 'blocked')):
            with self.subTest(status=status, conflicts=conflicts):
                self.service.observe([dict(self.notice, target=target)])
                self.service.tick()
                self.answer({'decision': 'integrate_now'})
                self.service.tick()
                self.store.jobs[self.event()['job_id']].update(state='answered', result=json.dumps({
                    'outcome': 'integrated', 'tests': {'status': status, 'summary': 'Check output'}}))
                with patch('integration_coordinator.project_git.git', return_value='0'), patch(
                        'integration_coordinator.project_git.summary', return_value={'conflicts': conflicts, 'merging': False}):
                    self.service.tick()
                self.assertEqual(self.event()['state'], expected)

    def test_pause_collects_inflight_result_without_delivering_new_work(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer({'decision': 'integrate_now'})
        self.service.tick()
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}):
            self.service.configure(dict(repository='repo', enabled=False))
        self.store.jobs[self.event()['job_id']].update(state='answered', result=json.dumps({
            'outcome': 'integrated', 'tests': {'status': 'passed', 'summary': 'Tests passed'}}))
        before = len(self.store.calls)
        with patch('integration_coordinator.project_git.git', return_value='0'), patch(
                'integration_coordinator.project_git.summary', return_value={'conflicts': 0, 'merging': False}):
            self.service.tick()
        self.assertEqual(self.event()['state'], 'completed')
        self.assertEqual(len(self.store.calls), before)

    def test_pending_report_waits_then_clears_once_dispatched(self):
        with closing(self.service.connect()) as db, db:
            self.service.save(db, dict(id='e' * 64, repository='repo', profile_id='worker', name='Max',
                                       path='repo', target='a' * 40, state='blocked',
                                       coordinator_id='coordinator', organization_id='org', reason='Blocker'))
            config = json.loads(db.execute('SELECT data FROM settings').fetchone()[0])
            config.update(report_pending=True)
            config.pop('report_job_id', None)
            config.pop('report_digest', None)
            db.execute('UPDATE settings SET data=?', (json.dumps(config),))
        self.assertEqual(self.service.snapshot()['configurations'][0]['report_state'], 'waiting')
        self.service.tick()
        config = self.service.snapshot()['configurations'][0]
        self.assertFalse(config['report_pending'])
        self.assertEqual(config['report_state'], 'queued')

    def test_paused_coordination_rejects_retries_and_repairs(self):
        self.service.observe([self.notice])
        self.service.tick()
        self.answer({'decision': 'defer', 'reason': 'Later'})
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}):
            self.service.configure(dict(repository='repo', enabled=False))
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.service.retry(self.event()['id'])
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.service.repair_report(dict(repository='repo', job_id='report1', mode='recover'))
