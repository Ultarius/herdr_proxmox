"""Coordinator profile creation uses normal durable organization actions."""
import unittest
from unittest.mock import patch
import test_organizations as fixtures
from integration_coordinator import IntegrationCoordinator
from permissions import prepare_permissions


class CoordinatorCreationTests(unittest.TestCase):
    setUp = fixtures.OrganizationTests.setUp
    tearDown = fixtures.OrganizationTests.tearDown
    command = fixtures.OrganizationTests.command
    action = fixtures.OrganizationTests.action
    organization = fixtures.OrganizationTests.organization
    drain = fixtures.OrganizationTests.drain

    def test_create_coordinator_retries_do_not_create_duplicate_sessions(self):
        org = self.organization()
        (self.projects / 'repo').mkdir()
        service = IntegrationCoordinator(self.root / 'integration.sqlite3', self.store)
        body = dict(repository='repo', profile_id='new', organization_id=org, enabled=True)
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}), patch('organizations.prepare_permissions', side_effect=lambda profile, output: prepare_permissions(profile, output, home=self.root / 'home')):
            first = service.configure(body)
            self.drain()
            second = service.configure(body)
            self.drain()
        self.assertEqual(first['profile_id'], second['profile_id'])
        profiles = self.store.snapshot()['profiles']
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]['role'], 'Integration coordinator')
        launches = [j for j in self.store.snapshot()['jobs'] if j['kind'] == 'launch']
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0]['state'], 'persona_sent', launches[0].get('error'))

    def test_already_launched_coordinator_is_reused(self):
        org = self.organization()
        (self.projects / 'repo').mkdir(exist_ok=True)
        profile = self.action('hire', organization_id=org, name='Coordinator',
                              role='Integration coordinator', runtime='codex',
                              project=str(self.projects / 'repo'), persona='summarize')
        self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        service = IntegrationCoordinator(self.root / 'integration.sqlite3', self.store)
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}):
            result = service.configure(dict(repository='repo', profile_id='new', organization_id=org))
        self.assertEqual(result['profile_id'], profile)
        self.assertEqual(len(self.store.job_records(launches_only=True)), 1)
        service.close()


    def test_imported_coordinator_profile_is_reused_instead_of_duplicated(self):
        org = self.organization()
        (self.projects / 'repo').mkdir(exist_ok=True)
        # A configuration import carries the coordinator profile but not the
        # coordination settings, so enabling coordination must reuse it.
        imported = self.action('hire', organization_id=org, name='Integration coordinator',
                               role='Integration coordinator', runtime='opencode', manager_id='',
                               project=str(self.projects / 'repo'), use_worktree=True,
                               permission_mode='dashboard_outputs', persona='imported coordinator')
        service = IntegrationCoordinator(self.root / 'integration.sqlite3', self.store)
        with patch('integration_coordinator.project_git.inspect', return_value={'repository': True, 'repository_path': 'repo'}), patch('organizations.prepare_permissions', side_effect=lambda profile, output: prepare_permissions(profile, output, home=self.root / 'home')):
            config = service.configure(dict(repository='repo', profile_id='new', organization_id=org, enabled=True))
            self.drain()
        service.close()
        self.assertEqual(config['profile_id'], imported)
        coordinators = [p for p in self.store.snapshot()['profiles'] if p['role'] == 'Integration coordinator']
        self.assertEqual(len(coordinators), 1)
        launches = [j for j in self.store.snapshot()['jobs']
                    if j['kind'] == 'launch' and j.get('profile_id') == imported]
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0]['state'], 'persona_sent', launches[0].get('error'))

    def test_real_store_watcher_coordinator_wiring_has_no_recursive_snapshot(self):
        from integration import IntegrationWatcher
        service = IntegrationCoordinator(self.root / 'integration.sqlite3', self.store)
        watcher = IntegrationWatcher(self.projects, self.store.active_checkouts, interval=3600, coordinator=service)
        self.addCleanup(watcher.close)
        self.addCleanup(service.close)
        # Production wiring is one-way: watcher reads the store; the store
        # must not call back into the watcher or coordinator.
        self.assertNotIn('integration', self.store.snapshot())
        self.assertNotIn('integration', self.store.state_snapshot())
        self.assertIn('coordination', watcher.snapshot())
        with patch.object(self.store, 'snapshot', side_effect=AssertionError('full snapshot on coordinator read')):
            self.assertEqual(service.snapshot()['events'], [])
            watcher.refresh()
