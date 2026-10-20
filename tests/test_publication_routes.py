"""Publication grants, migration and exact-candidate release receipts."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
sys.path.insert(0, str(Path(__file__).parent))
import publication_routes as routes
import test_contributions as contributions
import test_release_candidates as releases
import test_release_coordination as coordination


class PublicationRouteTests(unittest.TestCase):
    setUp = contributions.ContributionTests.setUp
    git = contributions.ContributionTests.git
    candidate = contributions.ContributionTests.candidate
    inherited_task = contributions.ContributionTests.inherited_task
    _delivery_task = contributions.ContributionTests._delivery_task
    _receipt = contributions.ContributionTests._receipt
    _delivery_reply = contributions.ContributionTests._delivery_reply
    pull_data = contributions.ContributionTests.pull_data

    def create(self, **policy):
        with closing(self.service.connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO policies VALUES (?,?)', ('org', json.dumps(policy)))
        return self.service.action('create', dict(self.body, request_id='new-route'), 'admin', 'admin')

    def test_new_configuration_defaults_to_release_without_task_auto_publication(self):
        task = self.create(auto_publish=True, auto_capture=True)
        self.assertEqual(task['publication_mode'], 'release')
        self.assertFalse(task['auto_publish'])
        self.assertTrue(task['auto_capture'])
        self.assertFalse(self.service.detail(task['id'])['task_publication_allowed'])
        self.assertIn('release_deliveries', self.service.detail(task['id']))

    def test_backend_refuses_every_task_publication_entry_before_effects(self):
        task = self.create(publication_mode='release')
        for action in ('publish', 'pull', 'publication'):
            with self.subTest(action=action), patch.object(self.service, 'path_for') as path:
                with self.assertRaisesRegex(ValueError, 'release delivery'):
                    self.service.perform(action, dict(task_id=task['id']), task['id'], 'admin')
                path.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'release delivery'):
            self.service.delivery_settings(task, True, True, 'admin')

    def test_route_is_frozen_and_repository_override_can_opt_into_both(self):
        task = self.create(publication_mode='release', publication_repositories={'repo': 'both'})
        self.assertEqual(task['publication_mode'], 'both')
        with closing(self.service.connect()) as db, db:
            db.execute('UPDATE policies SET data=? WHERE id=?', (json.dumps(dict(publication_mode='release')), 'org'))
        self.assertEqual(routes.mode(self.service.get(task['id'])), 'both')
        routes.require_task_publication(self.service.get(task['id']))

    def test_policy_validates_modes_and_keeps_existing_task_route(self):
        self.store.snapshot.return_value['organizations'] = [dict(id='org')]
        self.service.set_automation_policy(dict(organization_id='org', publication_mode='release',
            publication_repositories={'repo': 'both'}), 'admin')
        self.assertEqual(routes.mode(self.service.get(self.task['id'])), 'task')
        for field, value in [('publication_mode', 'invalid'), ('publication_repositories', {'repo': 'invalid'})]:
            with self.assertRaises(ValueError):
                self.service.set_automation_policy(dict(organization_id='org', **{field: value}), 'admin')

    def test_agent_handoff_finishes_without_release_publication(self):
        import task_delivery
        with closing(self.service.connect()) as db, db:
            db.execute('UPDATE policies SET data=? WHERE id=?', (json.dumps(dict(publication_mode='release')), 'org'))
        task, run, _ = self._delivery_task(publish=False, inherited=True)
        self.assertEqual(task['publication_mode'], 'release')
        with patch('collaboration.current_run'):
            task_delivery.advance(self.service)
            waiting = self.service.get(task['id'])
            self.assertEqual(waiting['delivery_workflow']['state'], 'waiting_agent')
            self._delivery_reply(task, run)
            task_delivery.advance(self.service)
            done = self.service.get(task['id'])
            self.assertEqual(done['delivery_workflow']['state'], 'complete')
            self.assertTrue(self.service.execution_finished(done, run)[0])
        self.api.publication_preflight.assert_not_called()


class MigrationTests(unittest.TestCase):
    def test_upgrade_preserves_legacy_tasks_and_orgs_once(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, data TEXT)')
            db.execute('CREATE TABLE policies (id TEXT PRIMARY KEY, data TEXT)')
            db.execute('INSERT INTO tasks VALUES (?,?)', ('task', json.dumps(dict(organization_id='org'))))
            routes.migrate(db)
            self.assertEqual(json.loads(db.execute('SELECT data FROM tasks').fetchone()[0])['publication_mode'], 'task')
            self.assertEqual(json.loads(db.execute('SELECT data FROM policies').fetchone()[0])['publication_mode'], 'task')
            db.execute('UPDATE policies SET data=?', (json.dumps(dict(publication_mode='release')),))
            routes.migrate(db)
            self.assertEqual(json.loads(db.execute('SELECT data FROM policies').fetchone()[0])['publication_mode'], 'release')


class SingleReleaseTests(unittest.TestCase):
    setUp = releases.ReleaseCandidateTests.setUp
    git = releases.ReleaseCandidateTests.git
    task = releases.ReleaseCandidateTests.task
    action = releases.ReleaseCandidateTests.action

    def test_shortcut_is_exact_head_idempotent_and_only_collects(self):
        identifier = self.task()
        sha = self.tasks[identifier]['head_sha']
        release = self.action('single', task_id=identifier, head_sha=sha)
        again = self.action('single', task_id=identifier, head_sha=sha)
        self.assertEqual(release['id'], again['id'])
        self.assertEqual(release['state'], 'collecting')
        self.assertEqual(release['members'][0]['candidate_sha'], sha)
        self.assertNotIn('revision', release)
        self.service.github.request.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'current captured'):
            self.action('single', task_id=identifier, head_sha='f' * 40)


class ReleaseReceiptTests(unittest.TestCase):
    setUp = coordination.ReleaseCoordinationTests.setUp
    git = coordination.ReleaseCoordinationTests.git
    task = coordination.ReleaseCoordinationTests.task
    action = coordination.ReleaseCoordinationTests.action
    plan = coordination.ReleaseCoordinationTests.plan
    checked = coordination.ReleaseCoordinationTests.checked
    published = coordination.ReleaseCoordinationTests.published
    queue_pull = coordination.ReleaseCoordinationTests.queue_pull
    github = coordination.ReleaseCoordinationTests.github
    pull = coordination.ReleaseCoordinationTests.pull
    poll_once = releases.ReleaseCandidateTests.poll_once

    def receipt(self, identifier):
        task = copy.deepcopy(self.tasks[identifier])
        with closing(self.service.connect()) as db, db:
            routes.decorate(db, [task])
        return task['release_deliveries'][0]

    def test_branch_pr_and_verified_merge_are_distinct_and_source_sha_is_immutable(self):
        release = self.published()
        identifier = release['revision']['members'][0]['task_id']
        self.assertEqual(self.receipt(identifier)['stage'], 'branch_published')
        github = self.github(release)
        self.queue_pull(release)
        self.releases.advance(release['id'])
        self.assertEqual(self.receipt(identifier)['stage'], 'pr_open')
        self.tasks[identifier]['head_sha'] = 'f' * 40
        self.assertFalse(self.receipt(identifier)['current_candidate_matches'])
        self.action('refresh', release)
        github.request.side_effect = None
        github.request.return_value = self.pull(release, state='closed', merged=True,
            merge_commit_sha='e' * 40, merged_at='2026-10-18T12:00:00Z')
        self.releases.advance(release['id'])
        receipt = self.receipt(identifier)
        self.assertEqual(receipt['stage'], 'merged')
        self.assertEqual(receipt['pull']['merge_sha'], 'e' * 40)
        self.assertEqual(self.tasks[identifier]['state'], 'review_ready')
        self.action('refresh', self.releases.get(release['id']))
        github.request.return_value = self.pull(release)
        self.releases.advance(release['id'])
        self.assertEqual(self.receipt(identifier)['stage'], 'merged')
        self.assertIn('contradicts', self.releases.get(release['id'])['pull_refresh_error'])

    def test_foreign_head_cannot_create_merge_evidence(self):
        release = self.published()
        github = self.github(release)
        self.queue_pull(release)
        self.releases.advance(release['id'])
        self.action('refresh', release)
        pull = self.pull(release, state='closed', merged=True, merge_commit_sha='e' * 40)
        pull['head']['sha'] = 'f' * 40
        github.request.side_effect = None
        github.request.return_value = pull
        self.releases.advance(release['id'])
        identifier = release['revision']['members'][0]['task_id']
        self.assertEqual(self.receipt(identifier)['stage'], 'pr_open')
        self.assertIn('identity', self.releases.get(release['id'])['pull_refresh_error'])

    def test_worker_picks_up_legacy_completed_pr_without_refresh_fields(self):
        release = self.published()
        self.github(release)
        self.queue_pull(release)
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        current['pull_request'].pop('remote_state')
        current['pull_request'].pop('next_refresh_at')
        self.releases.save(current)
        self.poll_once().assert_called_once_with(release['id'])
