"""Storage polling is cached; cleanup rechecks authoritative job/run activity."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import execution_storage
from storage_service import StorageService
import workspace_storage


class Records:
    def __init__(self, values=()):
        self.values = list(values)

    def job_records(self, launches_only=False):
        return copy.deepcopy([value for value in self.values if not launches_only or value.get('kind') == 'launch'])

    def storage_records(self):
        return copy.deepcopy(self.values)

    def update_job(self, identity, **fields):
        value = next(value for value in self.values if value['id'] == identity)
        value.update(fields)
        return copy.deepcopy(value)

    def update_storage_finish(self, identity, value):
        self.update_job(identity, storage_finish=value)


class StorageServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.projects = self.base / 'projects'
        self.project = self.projects / 'game'
        self.project.mkdir(parents=True)
        self.root = self.base / 'managed'
        self.environment = patch.dict(os.environ, HERDR_PROJECTS=str(self.projects),
                                      HERDR_WORKSPACE_STORAGE_ROOT=str(self.root),
                                      HERDR_BUILD_CAPACITY_ROOT=str(self.base / 'lane'))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = workspace_storage.Manager(self.root, self.projects)
        self.organizations, self.validation = Records(), Records()
        self.service = StorageService(self.organizations, self.validation, manager=self.manager)
        self.agent_id, self.run_id = 'a' * 32, 'b' * 32

    def allocate(self, identity='output', execution=None):
        path = self.manager.allocate(identity, kind='build-output', project=str(self.project),
                                     execution_id=execution or self.agent_id)
        (path / 'object').write_bytes(b'x' * 4096)
        return path

    def closed_job(self):
        return dict(id=self.agent_id, kind='launch', state='finished', profile_id='agent-profile',
                    session_closed_at='2026-10-22T00:00:00Z', storage_binding=dict(outputs=['managed']),
                    storage_finish=dict(state='complete'))

    def test_closed_sessions_and_terminal_validation_close_storage_but_open_sessions_remain_active(self):
        self.allocate()
        self.allocate('validation-output', execution=self.run_id)
        self.organizations.values = [dict(self.closed_job(), session_closed_at=None)]
        self.validation.values = [dict(id=self.run_id, state='running')]
        before = self.service.scan()['inventory']['entries']
        self.assertTrue(all(not entry['eligible'] for entry in before))
        self.assertEqual(self.service.active_ids(), {self.agent_id, self.run_id})
        self.organizations.values = [self.closed_job()]
        self.validation.values[0]['state'] = 'complete'
        after = self.service.scan()['inventory']['entries']
        self.assertTrue(all(entry['state'] == 'closed' and entry['eligible'] for entry in after))
        self.assertEqual(self.service.active_ids(), set())

    def test_pending_profile_work_keeps_a_closed_launch_protected(self):
        path = self.allocate()
        self.organizations.values = [self.closed_job(), dict(id='chat', kind='chat',
            state='waiting_for_input', profile_id='agent-profile')]
        entry = self.service.scan()['inventory']['entries'][0]
        self.assertEqual(entry['state'], 'closed')
        self.assertFalse(entry['eligible'])
        self.assertIn('active', entry['reason'])
        with self.assertRaisesRegex(ValueError, 'active or unavailable'):
            self.service.action(dict(action='preview', ids=['output']))
        self.assertTrue(path.exists())

    def test_dashboard_snapshot_does_not_walk_query_jobs_or_invoke_git(self):
        self.allocate()
        self.organizations.values = [self.closed_job()]
        first = self.service.scan()
        with patch.object(self.manager, 'inventory', side_effect=AssertionError('inventory walk')), \
                patch.object(self.organizations, 'job_records', side_effect=AssertionError('jobs queried')), \
                patch.object(self.validation, 'storage_records', side_effect=AssertionError('runs queried')), \
                patch.object(workspace_storage, '_scan', side_effect=AssertionError('filesystem walk')), \
                patch.object(workspace_storage.subprocess, 'run', side_effect=AssertionError('Git invoked')):
            for _ in range(3):
                cached = self.service.snapshot()
                self.assertEqual(cached['sampled_at'], first['sampled_at'])
                self.assertEqual(cached['inventory'], first['inventory'])
        cached['inventory']['entries'].clear()
        self.assertTrue(self.service.snapshot()['inventory']['entries'])

    def test_one_failed_agent_finalization_keeps_healthy_storage_visible_and_retries(self):
        self.allocate('bad')
        self.allocate('healthy', execution=self.run_id)
        bad = dict(self.closed_job(), storage_finish=dict(state='pending'), storage_binding=dict(identity=self.agent_id))
        good = dict(self.closed_job(), id=self.run_id, profile_id='other', storage_finish=dict(state='pending'),
                    storage_binding=dict(identity=self.run_id))
        self.organizations.values = [bad, good]
        def finish(binding, *arguments, **options):
            if binding['identity'] == self.agent_id:
                raise workspace_storage.StorageBusy('private cache busy')
            return dict(errors=[])
        with patch.object(execution_storage, 'finish', side_effect=finish):
            result = self.service.scan()
        self.assertTrue(result['available'])
        self.assertEqual(len(result['inventory']['entries']), 2)
        self.assertTrue(next(row for row in result['inventory']['entries'] if row['id'] == 'healthy')['eligible'])
        self.assertEqual(bad['storage_finish']['state'], 'pending')
        self.assertIn('private cache busy', bad['storage_finish']['error'])
        self.assertEqual(result['finalization_errors'][0]['execution_id'], self.agent_id)
        with patch.object(execution_storage, 'finish', return_value=dict(errors=[])):
            recovered = self.service.scan()
        self.assertEqual(recovered['finalization_errors'], [])
        self.assertEqual(bad['storage_finish']['state'], 'complete')

    def test_one_failed_validation_finalization_does_not_hide_healthy_owners(self):
        self.allocate('bad', execution=self.agent_id)
        self.allocate('healthy', execution=self.run_id)
        self.validation.values = [dict(id=self.agent_id, state='complete'), dict(id=self.run_id, state='complete')]
        close = self.manager.close_execution
        def blocked(identity):
            if identity == self.agent_id:
                raise OSError('one run cannot be closed')
            return close(identity)
        with patch.object(self.manager, 'close_execution', side_effect=blocked):
            result = self.service.scan()
        self.assertTrue(result['available'])
        self.assertEqual(self.validation.values[0]['storage_finish']['state'], 'pending')
        self.assertIn(self.agent_id, self.service.active_ids())
        self.assertTrue(next(row for row in result['inventory']['entries'] if row['id'] == 'healthy')['eligible'])
        self.service.scan()
        self.assertEqual(self.validation.values[0]['storage_finish']['state'], 'complete')

    def test_reclaim_requires_exact_preview_generations_and_rechecks_resumed_execution(self):
        path = self.allocate()
        self.organizations.values = [self.closed_job()]
        self.service.scan()
        preview = self.service.action(dict(action='preview', ids=['output']))['preview']
        with self.assertRaisesRegex(ValueError, 'exact preview generations'):
            self.service.action(dict(action='reclaim', ids=['output'], expected={}))
        self.manager.allocate('output', kind='build-output', project=str(self.project), execution_id=self.agent_id)
        self.manager.close_execution(self.agent_id)
        result = self.service.action(dict(action='reclaim', ids=['output'], expected=preview['expected']))['result']
        self.assertEqual(result['reclaimed'], [])
        self.assertIn('changed since the preview', result['skipped'][0]['reason'])
        self.assertTrue(path.exists())

    def test_active_authority_change_after_preview_prevents_cleanup(self):
        path = self.allocate()
        self.organizations.values = [self.closed_job()]
        self.service.scan()
        preview = self.service.action(dict(action='preview', ids=['output']))['preview']
        self.organizations.values[0].update(state='running', session_closed_at=None)
        result = self.service.action(dict(action='reclaim', ids=['output'], expected=preview['expected']))['result']
        self.assertEqual(result['reclaimed'], [])
        self.assertIn('active', result['skipped'][0]['reason'])
        self.assertTrue(path.exists())

    @unittest.skipUnless(workspace_storage.ANCHORED_DELETION, 'Requires native anchored deletion.')
    def test_reviewed_cleanup_reclaims_only_managed_output_and_keeps_source_receipts(self):
        path = self.allocate()
        source = self.project / 'source.rs'
        source.write_text('fn main() {}', encoding='utf-8')
        evidence = self.base / 'validation-run.json'
        evidence.write_text('{"state":"complete"}', encoding='utf-8')
        self.organizations.values = [self.closed_job()]
        self.service.scan()
        preview = self.service.action(dict(action='preview', ids=['output']))['preview']
        result = self.service.action(dict(action='reclaim', ids=['output'], expected=preview['expected']))
        self.assertEqual(result['result']['reclaimed'], ['output'])
        self.assertFalse(path.exists())
        self.assertTrue(source.exists())
        self.assertTrue(evidence.exists())
        self.assertEqual(result['snapshot']['inventory']['entries'], [])

    def test_missing_activity_authority_fails_closed(self):
        path = self.allocate()
        self.manager.close_execution(self.agent_id)
        self.organizations.job_records = Mock(side_effect=RuntimeError('store unavailable'))
        with self.assertLogs(level='ERROR'):
            self.assertIsNone(self.service.active_ids())
        with self.assertLogs(level='ERROR'):
            result = self.manager.reclaim(['output'], active_execution_ids=self.service.active_ids, dry_run=False)
        self.assertEqual(result['reclaimed'], [])
        self.assertIn('unknown', result['skipped'][0]['reason'])
        self.assertTrue(path.exists())

    def test_failed_activity_list_queries_keep_healthy_inventory_visible_but_disable_retention(self):
        self.allocate()
        self.manager.close_execution(self.agent_id)
        for owner, method in ((self.organizations, 'job_records'), (self.validation, 'storage_records')):
            with self.subTest(method=method), patch.object(owner, method, side_effect=RuntimeError('history unavailable')), \
                    patch.object(self.manager, 'maintain') as maintain:
                observed = self.service.scan(maintain=True)
                self.assertTrue(observed['available'], observed)
                self.assertEqual(len(observed['inventory']['entries']), 1)
                self.assertFalse(observed['inventory']['activity_known'])
                self.assertFalse(observed['inventory']['entries'][0]['eligible'])
                self.assertEqual(observed['finalization_errors'][0]['scope'], 'activity')
                maintain.assert_not_called()

    def test_configuration_is_validated_and_snapshot_reports_new_policy(self):
        result = self.service.action(dict(action='configure', build_mode='full', output_budget_bytes=4096))
        self.assertEqual(result['snapshot']['policy']['build_mode'], 'full')
        self.assertEqual(result['snapshot']['policy']['output_budget_bytes'], 4096)
        with self.assertRaisesRegex(ValueError, 'Unknown storage action or field'):
            self.service.action(dict(action='configure', arbitrary=True))
        with self.assertRaisesRegex(ValueError, 'nonnegative integer'):
            self.service.action(dict(action='configure', output_budget_bytes=-1))
        with self.assertRaisesRegex(ValueError, 'between one and 200'):
            self.service.action(dict(action='preview', ids=[]))

    def test_live_build_lane_prevents_manual_cleanup_even_for_closed_storage(self):
        import build_capacity
        path = self.allocate()
        self.manager.close_execution(self.agent_id)
        self.service.scan()
        preview = self.service.action(dict(action='preview', ids=['output']))['preview']
        with build_capacity.try_idle() as idle:
            self.assertTrue(idle)
            with self.assertRaisesRegex(ValueError, 'A build is active'):
                self.service.action(dict(action='reclaim', ids=['output'], expected=preview['expected']))
        self.assertTrue(path.exists())

    def test_requested_scan_resumes_after_another_storage_action_finishes(self):
        self.allocate()
        with self.service.operation:
            response = self.service.action(dict(action='scan'))
            self.assertTrue(response['snapshot']['scanning'])
        deadline = time.monotonic() + 3
        while self.service.snapshot()['scanning'] and time.monotonic() < deadline:
            time.sleep(.01)
        snapshot = self.service.snapshot()
        self.assertFalse(snapshot['scanning'])
        self.assertTrue(snapshot['available'], snapshot.get('error'))
        self.assertEqual(len(snapshot['inventory']['entries']), 1)


if __name__ == '__main__':
    unittest.main()
