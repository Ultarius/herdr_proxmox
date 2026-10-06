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
