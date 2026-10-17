"""Contract tests for web/gateway/task_delivery.py against a focused fake service.

Each function is exercised for at least one positive and one negative outcome,
including the denied and repair-budget-exhausted paths. External collaborators
(Git, repository lock, GitHub, other gateway modules) are replaced with small
deterministic fakes so the contract of this module is asserted directly.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))

import task_delivery


@contextmanager
def _no_lock(path, timeout=15):
    yield path


class FakeGitHub:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, path, *args, **kwargs):
        self.calls.append(path)
        return self.responses.pop(0)


class FakeService:
    """The smallest surface task_delivery reads or writes."""

    def __init__(self, task=None, jobs=None, identity=None, policy=True, remote=None, receipt=None):
        self.task = task
        self.jobs = [] if jobs is None else jobs
        self.identity = dict(agent_status='done') if identity is None else identity
        self.policy = policy
        self.remote = remote
        self.receipt = {} if receipt is None else receipt
        self.saved = []
        self.performed = []
        self.github = FakeGitHub()
        self.store = SimpleNamespace(snapshot=self._snapshot, identity=self._identity)

    def _snapshot(self, live_status=False):
        return {'jobs': self.jobs}

    def _identity(self, run, ready=False):
        return self.identity

    def get(self, task_id):
        return self.task

    def save(self, task, action, actor):
        self.saved.append(action)
        self.task = task

    def perform(self, kind, payload, task_id, actor):
        self.performed.append((kind, payload))
        return self.task

    def tree_for(self, task):
        return '/tmp/task-tree'

    def read_receipt(self, task, run, head, path):
        return self.receipt

    def policy_matches(self, task, evidence):
        return self.policy

    def remote_head(self, task):
        return self.remote


def task(**changes):
    value = dict(id='task-1', state='review_ready', branch='herdr/task-1',
                 head_sha='a' * 40, run_id='run-1', github_repository='owner/repo', builds={})
    value.update(changes)
    return value


class RepairAllowedTests(unittest.TestCase):
    def test_validation_failure_within_budget_is_allowed(self):
        self.assertTrue(task_delivery.repair_allowed(
            task(builds={'a' * 40: dict(state='failed')}, delivery_repair_count=0),
            dict(result_kind='validation_failed')))

    def test_exhausted_repair_budget_is_denied(self):
        self.assertFalse(task_delivery.repair_allowed(
            task(builds={'a' * 40: dict(state='failed')}, delivery_repair_count=task_delivery.MAX_VALIDATION_REPAIRS),
            dict(result_kind='validation_failed')))

    def test_non_validation_failure_and_non_failed_build_are_denied(self):
        self.assertFalse(task_delivery.repair_allowed(
            task(builds={'a' * 40: dict(state='failed')}), dict(result_kind='delivery_failed')))
        self.assertFalse(task_delivery.repair_allowed(
            task(builds={'a' * 40: dict(state='complete')}), dict(result_kind='validation_failed')))


class VerifyAuthorizationTests(unittest.TestCase):
    def test_matching_destination_is_accepted(self):
        entry = task(publishing_repository='contributor/repo',
                     publication_authorization=dict(repository='contributor/repo',
                                                    upstream='owner/repo', branch='herdr/task-1'))
        self.assertIsNone(task_delivery.verify_authorization(entry))

    def test_changed_destination_is_denied(self):
        entry = task(publishing_repository='contributor/repo',
                     publication_authorization=dict(repository='someone/repo',
                                                    upstream='owner/repo', branch='herdr/task-1'))
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            task_delivery.verify_authorization(entry)

    def test_missing_or_wrong_branch_authorization_is_denied(self):
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            task_delivery.verify_authorization(task())


class VerifyCheckoutTests(unittest.TestCase):
    def test_clean_checkout_returns_the_receipt(self):
        head = 'a' * 40
        service = FakeService(receipt=dict(outcome='complete', tests=[dict(command='test', status='passed')]))
        with patch('project_git.git', side_effect=[head + '\n', 'herdr/task-1\n', '']), \
                patch('project_git.merge_state', return_value=False), \
                patch('repository_lock.repository_lock', _no_lock):
            receipt = task_delivery.verify_checkout(service, task(), dict(id='run-1'), head)
        self.assertEqual(receipt['outcome'], 'complete')

    def test_dirty_checkout_is_denied(self):
        head = 'a' * 40
        service = FakeService()
        with patch('project_git.git', side_effect=[head + '\n', 'herdr/task-1\n', ' M file\n']), \
                patch('project_git.merge_state', return_value=False), \
                patch('repository_lock.repository_lock', _no_lock):
            with self.assertRaisesRegex(ValueError, 'unchanged, clean'):
                task_delivery.verify_checkout(service, task(), dict(id='run-1'), head)

    def test_wrong_head_or_merge_in_progress_is_denied(self):
        head = 'a' * 40
        service = FakeService()
        with patch('project_git.git', side_effect=['b' * 40 + '\n', 'herdr/task-1\n', '']), \
                patch('project_git.merge_state', return_value=False), \
                patch('repository_lock.repository_lock', _no_lock):
            with self.assertRaisesRegex(ValueError, 'unchanged, clean'):
                task_delivery.verify_checkout(service, task(), dict(id='run-1'), head)
        with patch('project_git.git', side_effect=[head + '\n', 'herdr/task-1\n', '']), \
                patch('project_git.merge_state', return_value=True), \
                patch('repository_lock.repository_lock', _no_lock):
            with self.assertRaisesRegex(ValueError, 'unchanged, clean'):
                task_delivery.verify_checkout(service, task(), dict(id='run-1'), head)


class VerifyPullTests(unittest.TestCase):
    head = 'a' * 40

    def entry(self):
        return task(publish=dict(head_sha=self.head), pull=dict(number=7, head_sha=self.head),
                    base_ref='refs/remotes/origin/main', delivery_workflow={})

    def pull_data(self, **changes):
        value = dict(number=7, state='open', mergeable=True,
                     head=dict(ref='herdr/task-1', sha=self.head, repo=dict(full_name='owner/repo')),
                     base=dict(ref='main', sha='c' * 40, repo=dict(full_name='owner/repo')))
        value.update(changes)
        return value

    def test_matching_open_pull_and_green_checks_pass(self):
        entry = self.entry()
        service = FakeService(remote=self.head)
        service.github = FakeGitHub([
            self.pull_data(),
            {'check_runs': [dict(name='CI', status='completed', conclusion='success')]},
            {'statuses': [dict(context='ci/tests', state='success')]},
        ])
        self.assertTrue(task_delivery.verify_pull(service, entry, self.head))
        self.assertEqual(entry['delivery_workflow']['github_verification']['sha'], self.head)

    def test_unpublished_or_mismatched_branch_is_denied(self):
        entry = self.entry()
        entry['publish']['head_sha'] = 'd' * 40
        service = FakeService(remote=self.head)
        with self.assertRaisesRegex(ValueError, 'does not match the validated candidate'):
            task_delivery.verify_pull(service, entry, self.head)

    def test_merge_conflicts_and_failed_ci_are_denied(self):
        entry = self.entry()
        service = FakeService(remote=self.head)
        service.github = FakeGitHub([self.pull_data(mergeable=False)])
        with self.assertRaisesRegex(ValueError, 'merge conflicts'):
            task_delivery.verify_pull(service, entry, self.head)
        service.github = FakeGitHub([
            self.pull_data(),
            {'check_runs': [dict(name='CI', status='completed', conclusion='failure')]},
        ])
        with self.assertRaisesRegex(ValueError, 'CI failed'):
            task_delivery.verify_pull(service, entry, self.head)

    def test_incomplete_checks_are_not_a_pass(self):
        entry = self.entry()
        service = FakeService(remote=self.head)
        service.github = FakeGitHub([
            self.pull_data(),
            {'check_runs': [dict(name='CI', status='in_progress', conclusion=None)]},
        ])
        self.assertFalse(task_delivery.verify_pull(service, entry, self.head))


class AcceptRepairTests(unittest.TestCase):
    old = 'a' * 40
    candidate = 'b' * 40

    def flow(self, **changes):
        value = dict(sha=self.old, state='waiting_agent', result_kind='validation_failed', repair_allowed=True)
        value.update(changes)
        return value

    def test_authorized_repair_is_captured_and_rebuilt(self):
        entry = task(head_sha=self.old, delivery_repair_count=0,
                     builds={self.old: dict(state='failed')})
        flow = self.flow()
        service = FakeService(task=entry, receipt=dict(outcome='complete', tests=[dict(command='test')]))
        with patch('project_git.git'), patch('repository_lock.repository_lock', _no_lock), \
                patch('task_delivery.verify_checkout', return_value=service.receipt):
            task_delivery.accept_repair(service, entry, dict(id='run-1'), flow,
                                        dict(candidate=self.candidate, summary='repaired'))
        self.assertEqual(entry['delivery_repair_count'], 1)
        self.assertEqual(entry['delivery_workflow']['sha'], self.candidate)
        self.assertEqual(entry['delivery_workflow']['state'], 'waiting_verification')
        self.assertIn(('candidate', {}), service.performed)
        self.assertIn(('build', dict(target=self.candidate)), service.performed)

    def test_denied_repair_without_authorization_is_rejected(self):
        entry = task(head_sha=self.old, builds={self.old: dict(state='failed')})
        service = FakeService(task=entry)
        with self.assertRaisesRegex(ValueError, 'does not authorize'):
            task_delivery.accept_repair(service, entry, dict(id='run-1'), self.flow(repair_allowed=False),
                                        dict(candidate=self.candidate))
        self.assertNotIn('delivery_repair_count', entry)

    def test_exhausted_repair_budget_is_rejected(self):
        entry = task(head_sha=self.old, delivery_repair_count=task_delivery.MAX_VALIDATION_REPAIRS,
                     builds={self.old: dict(state='failed')})
        service = FakeService(task=entry)
        with self.assertRaisesRegex(ValueError, 'does not authorize'):
            task_delivery.accept_repair(service, entry, dict(id='run-1'), self.flow(),
                                        dict(candidate=self.candidate))

    def test_candidate_must_be_a_new_full_sha(self):
        entry = task(head_sha=self.old, delivery_repair_count=0,
                     builds={self.old: dict(state='failed')})
        service = FakeService(task=entry)
        with self.assertRaisesRegex(ValueError, 'new full commit SHA'):
            task_delivery.accept_repair(service, entry, dict(id='run-1'), self.flow(),
                                        dict(candidate=self.old))
        with self.assertRaisesRegex(ValueError, 'new full commit SHA'):
            task_delivery.accept_repair(service, entry, dict(id='run-1'), self.flow(),
                                        dict(candidate='short'))


class ReconcileTests(unittest.TestCase):
    head = 'a' * 40

    def test_verified_candidate_advances_to_waiting_verification(self):
        entry = task(head_sha=self.head)
        service = FakeService(task=entry, jobs=[dict(id='run-1', state='persona_sent')],
                              identity=dict(agent_status='done'))
        with patch('pr_repairs.active', return_value=False):
            task_delivery.reconcile(service, entry)
        self.assertEqual(entry['delivery_workflow']['state'], 'waiting_verification')
        self.assertIn('delivery_started', service.saved)

    def test_terminal_task_before_inspection_raises_attention(self):
        entry = task(state='merged', await_verification=True, head_sha=self.head)
        service = FakeService(task=entry)
        task_delivery.reconcile(service, entry)
        self.assertEqual(entry['delivery_workflow']['state'], 'attention')
        self.assertIn('delivery_terminal_attention', service.saved)

    def test_changed_toolchain_policy_is_denied(self):
        entry = task(head_sha=self.head,
                     builds={self.head: dict(state='complete', required_checks_verified=True, target=self.head)})
        service = FakeService(task=entry, policy=False)
        with patch('pr_repairs.active', return_value=False):
            with self.assertRaisesRegex(ValueError, 'toolchain policy'):
                task_delivery.reconcile(service, entry)
        self.assertIn('delivery_policy_blocked', service.saved)

    def test_missing_original_session_is_denied(self):
        entry = task(head_sha=self.head)
        service = FakeService(task=entry, jobs=[])
        with patch('pr_repairs.active', return_value=False):
            with self.assertRaisesRegex(ValueError, 'session is unavailable'):
                task_delivery.reconcile(service, entry)


if __name__ == '__main__':
    unittest.main()
