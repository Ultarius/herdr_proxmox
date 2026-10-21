"""Candidate-scoped approvals with real committed evidence and durable requests."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

import contributions
import environment_adapters
import environment_plan
import environment_policy
import environment_recipes
import project_analysis
import server
import task_validation_environment as recovery
import toolchain_policy
import validation


class ValidationEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = self.root / 'projects'
        self.repo = self.projects / 'game'
        self.repo.mkdir(parents=True)
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.git('remote', 'add', 'origin', 'https://github.com/owner/repo')
        (self.repo / 'Cargo.toml').write_text('[package]\nname="game"\nversion="0.1.0"\n')
        (self.repo / 'README.md').write_text('```\ncargo test --no-default-features\n```\n')
        self.base = self.commit('base')
        self.approved_revision = self.base
        (self.repo / 'source.rs').write_text('fn implemented() {}\n')
        self.candidate = self.commit('candidate')
        self.git('checkout', '--detach', self.base)
        self.approved = dict(plan_digest='a' * 64, toolchains=[], checks=[], lockfiles={},
            declarations=environment_policy.declarations(environment_policy.tree(self.repo, self.base), ['Cargo.toml', 'README.md']),
            recipes=[dict(id='headless', cwd='.', arguments=[sys.executable, '-c', 'print("candidate checked")'],
                          evidence='README.md', required=True, timeout_seconds=30)], blockers=[], validation_blockers=[])
        self.frozen = dict(plan_digest='b' * 64, digest='c' * 64)
        store = Mock()
        store.snapshot.return_value = dict(jobs=[], profiles=[], groups=[], organizations=[])
        store.knowledge_jobs.return_value = []
        self.service = contributions.Contributions(self.root / 'tasks.sqlite3', self.projects, store, Mock())
        # The real Linux descriptor containment boundary has its own suite.
        # This test supplies one known temporary repository to exercise policy,
        # request authorization, durable audit and queue bindings on every host.
        self.service.path_for = Mock(return_value=self.repo)
        self.service.frozen_environment = Mock(side_effect=lambda task: copy.deepcopy(self.frozen))
        self.task = dict(id='1' * 32, title='Captured work', repository='game', github_repository='owner/repo',
            organization_id='org', run_id='run', profile_id='worker', state='review_ready',
            head_sha=self.candidate, base_sha=self.base, candidate_revision='revision-1',
            builds={self.candidate: dict(state='error', blocker='policy_changed')})
        self.service.save(self.task, 'fixture', 'operator')
        self.resolver = patch.object(toolchain_policy, 'PROJECT_RESOLVER', self.resolve)
        self.resolver.start()
        self.addCleanup(self.resolver.stop)

    def git(self, *arguments):
        return subprocess.check_output(['git', '-C', str(self.repo), *arguments], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def resolve(self, root, revision=None):
        sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', revision or 'HEAD']).decode().strip()
        if sha == self.approved_revision:
            return copy.deepcopy(self.approved)
        # No separately prepared plan exists for the implementation commit.
        candidate = copy.deepcopy(self.approved)
        candidate.update(plan_digest=None, recipes=[], declarations=environment_policy.declarations(
            environment_policy.tree(root, sha), ['Cargo.toml']))
        return candidate

    def request(self, mode='review', role='admin', request_id=None, **body):
        return self.service.action('validation_environment', dict(task_id=self.task['id'], mode=mode,
            request_id=request_id or 'review-' + os.urandom(8).hex(), **body), 'Damien', role)

    def approve(self, review, **body):
        return self.request('approve', **dict(candidate=review['candidate'], review_digest=review['review_digest'],
                                             inspected=True, **body))

    def test_explicit_approval_preserves_commit_session_and_failed_build_then_binds_queue(self):
        reviewed = self.request()['validation_environment_review']
        self.assertEqual(reviewed['candidate'], self.candidate)
        self.assertEqual(reviewed['previous_plan_digest'], self.frozen['plan_digest'])
        before = self.service.get(self.task['id'])
        approved = self.approve(reviewed)
        self.assertEqual(approved['head_sha'], before['head_sha'])
        self.assertEqual(approved['run_id'], before['run_id'])
        self.assertEqual(approved['builds'], before['builds'])
        self.assertEqual(approved['validation_environment']['approved_by'], 'Damien')
        self.assertEqual(self.frozen, dict(plan_digest='b' * 64, digest='c' * 64))
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)
        self.service.store.action.assert_not_called()
        self.service.store.manage_session.assert_not_called()
        self.service.validation = Mock()
        self.service.validation.submit.return_value = dict(id='queued-run', state='queued')
        self.service.action('build', dict(request_id='build-recovered', task_id=self.task['id'],
                            target=self.candidate), 'Damien', 'admin')
        event_id = self.service.validation.submit.call_args.args[0]['id']
        event = self.service.build_event(event_id)
        self.assertEqual(event['target'], self.candidate)
        self.assertEqual(event['frozen_plan_digest'], self.approved['plan_digest'])
        self.assertEqual(event['frozen_policy_digest'], toolchain_policy.digest(self.approved))
        self.assertEqual(event['policy_transition']['approved_by'], 'Damien')

    def test_viewer_cannot_review_or_approve_and_review_does_not_approve(self):
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.request(role='viewer')
        review = self.request()['validation_environment_review']
        self.assertNotIn('validation_environment', self.service.get(self.task['id']))
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.request('approve', role='viewer', candidate=review['candidate'],
                         review_digest=review['review_digest'], inspected=True)
        with self.assertRaisesRegex(ValueError, 'Review it again'):
            self.request('approve', candidate=review['candidate'], review_digest=review['review_digest'], inspected=False)
        with self.assertRaisesRegex(ValueError, 'Review it again'):
            self.request('approve', candidate=self.candidate, review_digest='forged', inspected=True)

    def test_candidate_run_revision_and_policy_changes_invalidate_review(self):
        for field, replacement in (('head_sha', self.base), ('run_id', 'other-run'), ('candidate_revision', 'revision-2')):
            with self.subTest(field=field):
                self.service.save(copy.deepcopy(self.task), 'reset', 'admin')
                review = self.request()['validation_environment_review']
                changed = self.service.get(self.task['id'])
                changed[field] = replacement
                self.service.save(changed, 'changed', 'admin')
                with self.assertRaisesRegex(ValueError, 'Review it again'):
                    self.approve(review)
        self.service.save(copy.deepcopy(self.task), 'reset', 'admin')
        review = self.request()['validation_environment_review']
        self.approved['plan_digest'] = 'd' * 64
        with self.assertRaisesRegex(ValueError, 'Review it again'):
            self.approve(review)

    def test_no_silent_policy_waiver_for_changed_or_deleted_readme(self):
        for contents in ('Different validation instructions', None):
            self.git('checkout', '--detach', self.candidate)
            readme = self.repo / 'README.md'
            if contents is None:
                readme.unlink()
            else:
                readme.write_text(contents)
            changed = self.commit('changed evidence')
            self.git('checkout', '--detach', self.base)
            task = dict(self.task, head_sha=changed)
            self.service.save(task, 'changed candidate', 'admin')
            with self.assertRaisesRegex(ValueError, 'README.md'):
                self.request()

    def test_unprepared_or_unapproved_environment_is_refused(self):
        for field, value in (('blockers', ['Rust not prepared']), ('validation_blockers', ['No selected checks']), ('recipes', [])):
            with self.subTest(field=field):
                with patch.dict(self.approved, {field: value}):
                    with self.assertRaisesRegex(ValueError, 'Prepare the approved'):
                        self.request()

    def test_approval_is_idempotent_and_invalidates_with_candidate_or_plan(self):
        review = self.request()['validation_environment_review']
        body = dict(candidate=review['candidate'], review_digest=review['review_digest'], inspected=True,
                    request_id='approve-once')
        first = self.request('approve', **body)
        repeated = self.request('approve', **body)
        self.assertEqual(first['validation_environment']['id'], repeated['validation_environment']['id'])
        self.assertIsNotNone(recovery.binding(first, self.candidate, self.approved))
        self.assertIsNone(recovery.binding(dict(first, head_sha=self.base), self.base, self.approved))
        self.assertIsNone(recovery.binding(first, self.candidate, dict(self.approved, plan_digest='d' * 64)))

    def test_candidate_approval_cannot_authorize_merge_target_or_merge_evidence(self):
        reviewed = self.request()['validation_environment_review']
        approved = self.approve(reviewed)
        merged = dict(approved, state='merged', merge_sha=self.base)
        self.assertIsNone(recovery.binding(approved, self.base, self.approved))
        self.assertIsNone(recovery.binding(merged, self.base, self.approved))
        self.assertIsNone(recovery.binding(dict(merged, merge_sha=self.candidate), self.candidate, self.approved))
        evidence = dict(target=self.base, plan_digest=self.approved['plan_digest'],
                        policy_digest=toolchain_policy.digest(self.approved))
        with patch.object(toolchain_policy, 'evidence_matches', return_value=True):
            self.assertTrue(self.service.policy_matches(approved, dict(evidence, target=self.candidate), self.approved))
            self.assertFalse(self.service.policy_matches(merged, evidence, self.approved))
        self.service.save(merged, 'merged', 'operator')
        self.service.validation = Mock()
        self.service.validation.submit.return_value = dict(id='merge-run', state='queued')
        self.service.action('build', dict(request_id='merge-build', task_id=self.task['id'], target=self.base), 'Damien', 'admin')
        event = self.service.build_event(self.service.validation.submit.call_args.args[0]['id'])
        self.assertEqual(event['target'], self.base)
        self.assertNotIn('policy_transition', event)
        self.assertEqual(event['frozen_policy_digest'], self.frozen['digest'])

    def test_upstream_only_protected_drift_requires_branch_update_not_policy_waiver(self):
        (self.repo / 'Cargo.toml').write_text('[package]\nname="game"\nversion="0.2.0"\n')
        upstream = self.commit('upstream policy input changed')
        self.approved_revision = upstream
        self.approved['declarations'] = environment_policy.declarations(
            environment_policy.tree(self.repo, upstream), ['Cargo.toml', 'README.md'])
        with self.assertRaisesRegex(ValueError, 'upstream validation inputs changed.*Update the task branch'):
            self.request()
        self.assertNotIn('validation_environment', self.service.get(self.task['id']))

    def test_repair_transition_evidence_requires_its_exact_target(self):
        transition = dict(candidate=self.candidate, plan_digest=self.approved['plan_digest'],
                          digest=toolchain_policy.digest(self.approved))
        task = dict(self.task, completion_receipt=dict(commit=self.candidate),
                    pr_repair=dict(kind='validation_update', candidate=self.candidate, validation_policy=transition))
        evidence = dict(target=self.candidate, plan_digest=transition['plan_digest'], policy_digest=transition['digest'])
        with patch.object(toolchain_policy, 'evidence_matches', return_value=True):
            self.assertTrue(self.service.policy_matches(task, evidence, self.approved))
            self.assertFalse(self.service.policy_matches(task, dict(evidence, target=self.base), self.approved))
            self.assertFalse(self.service.policy_matches(task, {key: value for key, value in evidence.items()
                                                               if key != 'target'}, self.approved))
            self.assertFalse(self.service.policy_matches(dict(task, state='merged'), evidence, self.approved))

    def test_live_recovery_hint_distinguishes_protected_inputs_and_valid_approval(self):
        self.assertEqual(recovery.status(self.service, self.task)['state'], 'review_required')
        approved = self.approve(self.request()['validation_environment_review'])
        self.assertEqual(recovery.status(self.service, approved)['state'], 'approved')
        self.assertIsNone(recovery.status(self.service, dict(approved, state='implementing')))
        self.assertIsNone(recovery.status(self.service, dict(approved, state='merged')))
        self.git('checkout', '--detach', self.candidate)
        (self.repo / 'README.md').write_text('changed recipe evidence')
        changed = self.commit('changed README')
        self.git('checkout', '--detach', self.base)
        task = dict(self.task, head_sha=changed, builds={changed: dict(state='error', blocker='policy_changed')})
        status = recovery.status(self.service, task)
        self.assertEqual(status['state'], 'unavailable')
        self.assertIn('README.md', status['reason'])

    def test_missing_and_legacy_environment_bindings_fail_closed_without_fake_digest(self):
        for run in (dict(environment_binding_required=True), dict(frozen_plan_digest='unavailable'),
                    dict(frozen_policy_digest='unavailable')):
            comparison = validation.environment_comparison(run, self.approved)
            self.assertFalse(comparison['matches'])
            self.assertTrue(comparison['binding_missing'])
            self.assertIsNone(comparison['frozen_plan_digest'])
            self.assertIsNone(comparison['frozen_policy_digest'])
        legacy = dict(self.approved, plan_digest=None)
        self.assertTrue(validation.environment_comparison(
            dict(frozen_policy_digest=toolchain_policy.digest(legacy)), legacy)['matches'])
        self.assertFalse(validation.environment_comparison(dict(frozen_policy_digest='wrong'), legacy)['matches'])
        self.assertFalse(validation.environment_comparison(dict(target=self.base,
            frozen_plan_digest=self.approved['plan_digest'], frozen_policy_digest=toolchain_policy.digest(self.approved),
            policy_transition=dict(kind='candidate_validation', candidate=self.candidate)), self.approved)['matches'])

    def test_validation_records_distinct_recovery_for_session_queue_and_protected_input_drift(self):
        self.git('checkout', '--detach', self.candidate)
        (self.repo / 'README.md').write_text('Changed validation recipe evidence')
        changed = self.commit('candidate changes protected evidence')
        (self.repo / 'README.md').unlink()
        deleted = self.commit('candidate deletes protected evidence')
        self.git('checkout', '--detach', self.base)
        record = Mock()
        runs = validation.ValidationRuns(self.projects, lambda identifier: None, record=record,
                                         root=self.root / 'validation')
        # Supply only the known test path; exercise real Git worktree creation,
        # policy comparisons, result recording and cleanup on every host.
        runs._checkout = Mock(return_value=self.repo)
        for index, (target, queued, expected) in enumerate((
                (self.candidate, None, 'review_validation_environment'),
                (self.candidate, 'old-queued-policy', 'retry_validation'),
                (changed, None, 'review_protected_inputs'),
                (deleted, None, 'review_protected_inputs'))):
            with self.subTest(recovery=expected, target=target):
                run = dict(id=str(index) * 32, event_id='b' * 64, path='game', target=target,
                    frozen_plan_digest=self.frozen['plan_digest'], frozen_policy_digest=self.frozen['digest'],
                    policy_digest=queued)
                folder = runs.root / run['id']
                folder.mkdir(parents=True)
                with patch('environment_execution.materialize_execution_policy') as execute:
                    runs._execute(run, folder)
                execute.assert_not_called()
                self.assertEqual(run['state'], 'error', run.get('note'))
                self.assertEqual(run['blocker'], 'policy_changed')
                self.assertEqual(run['recovery'], expected)
                self.assertEqual(record.call_args.args[1]['recovery'], expected)
                self.assertFalse((folder / 'tree').exists())
        self.assertIn('recipe evidence', run['note'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)

    def test_real_rust_policy_comparison_does_not_require_a_candidate_preparation_receipt(self):
        (self.repo / 'Cargo.toml').write_text('[package]\nname="game"\nversion="0.1.0"\nrust-version="1.97.1"\n')
        (self.repo / 'rust-toolchain.toml').write_text('[toolchain]\nchannel="1.97.1"\nprofile="minimal"\n')
        base = self.commit('pinned SDK')
        (self.repo / 'source.rs').write_text('fn different_implementation() {}\n')
        candidate = self.commit('new implementation')
        self.git('checkout', '--detach', base)
        inventory = project_analysis.committed_inventory(self.repo, base)
        import project_requirements
        value = project_requirements.analyse(self.repo, inventory=inventory,
                    read=project_analysis.committed_reader(self.repo, base))
        value.update(project='game', source=dict(scope='committed', revision=base))
        plan = environment_plan.build(value, release_entries={})
        spec = dict(capability='rust-toolchain', version='1.97.1')
        plan['requirements'][0].update(version='1.97.1', state='acquire', artifact=spec,
                                     adapter_version=environment_adapters.get('rust-toolchain').version)
        plan['check_candidates'] = environment_recipes.candidates(value, inventory['paths'])
        plan['selected_checks'] = environment_recipes.select(plan, [plan['check_candidates'][0]['id']], 'Damien')
        plan['digest'] = environment_plan.digest(plan)
        receipt = dict(state='ready', digest=plan['digest'], revision=base, receipts=[dict(
            capability='rust-toolchain', version='1.97.1', components=['.'], specification=spec,
            probe=dict(executable=sys.executable, cargo_version='1.97.1'))])
        store = Mock()
        store.prepared.side_effect = lambda project, sha: (plan, receipt) if sha == base else (None, None)
        resolver = environment_policy.EnvironmentPolicies(self.projects, store)
        with patch.object(toolchain_policy, 'PROJECT_RESOLVER', resolver.resolve):
            approved = toolchain_policy.resolve_committed(self.repo)
            raw_candidate = toolchain_policy.resolve_committed(self.repo, revision=candidate)
            self.assertIn('README.md', toolchain_policy.changes(approved, raw_candidate))
            compared = toolchain_policy.candidate_policy(self.repo, approved, revision=candidate)
            self.assertEqual(toolchain_policy.changes(approved, compared), [])
            self.assertTrue(raw_candidate['blockers'])  # No candidate receipt was invented.
        self.assertEqual(self.git('rev-parse', 'HEAD'), base)

    def test_http_review_and_approval_route_use_the_real_task_authorization_and_audit(self):
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'a' * 48
        gateway.contributions = self.service
        gateway.operators = SimpleNamespace(identify=lambda token: None)
        worker = threading.Thread(target=gateway.serve_forever, daemon=True)
        worker.start()
        def post(body):
            request = Request('http://127.0.0.1:' + str(gateway.server_port) + '/api/tasks/validation_environment',
                data=json.dumps(dict(task_id=self.task['id'], **body)).encode(),
                headers={'Authorization': 'Bearer ' + gateway.token})
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)
                return json.load(response)
        try:
            review = post(dict(mode='review', request_id='http-review'))['validation_environment_review']
            result = post(dict(mode='approve', request_id='http-approve', candidate=self.candidate,
                               review_digest=review['review_digest'], inspected=True))
            self.assertEqual(result['validation_environment']['candidate'], self.candidate)
            stored = self.service.get(self.task['id'])
            self.assertEqual(stored['audit'][-1]['action'], 'validation_environment_approved')
            self.assertEqual(stored['audit'][-1]['policy_digest'], toolchain_policy.digest(self.approved))
        finally:
            gateway.shutdown()
            gateway.server_close()
            worker.join(timeout=2)

    @unittest.skipUnless(sys.platform == 'linux', 'Actual validation uses the native Linux project boundary.')
    def test_actual_candidate_check_runs_only_after_explicit_transition(self):
        runs = validation.ValidationRuns(self.projects, lambda identifier: None, root=self.root / 'validation')
        first = dict(id='a' * 32, event_id='b' * 64, repository='game', path='game', target=self.candidate,
                     frozen_plan_digest=self.frozen['plan_digest'], frozen_policy_digest=self.frozen['digest'], actor='Damien')
        folder = runs.root / first['id']
        folder.mkdir(parents=True)
        runs._execute(first, folder)
        self.assertEqual(first['blocker'], 'policy_changed')
        self.assertEqual(first['recovery'], 'review_validation_environment')
        self.assertNotIn('candidate checked', (folder / 'log.txt').read_text())
        self.assertIn(self.frozen['plan_digest'], (folder / 'log.txt').read_text())
        reviewed = self.request()['validation_environment_review']
        approved = self.approve(reviewed)
        transition = recovery.binding(approved, self.candidate, self.approved)
        second = dict(first, id='c' * 32, frozen_plan_digest=transition['plan_digest'],
                      frozen_policy_digest=transition['digest'], policy_transition=transition)
        for name in ('blocker', 'note', 'environment_comparison', 'state'):
            second.pop(name, None)
        folder = runs.root / second['id']
        folder.mkdir(parents=True)
        with patch.dict(os.environ, {'HERDR_BUILD_CAPACITY_ROOT': str(self.root / 'capacity')}):
            runs._execute(second, folder)
        self.assertEqual(second['state'], 'complete', second.get('note'))
        self.assertTrue(second['required_checks_verified'])
        self.assertIn('candidate checked', (folder / 'log.txt').read_text())
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)


if __name__ == '__main__':
    unittest.main()
