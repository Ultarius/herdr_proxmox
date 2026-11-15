"""Real Git integration and restart boundaries for pinned release manifests."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import random
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import project_git
import toolchain_policy
from release_candidates import ReleaseCandidates, ReleaseChanged, digest


POLICY = dict(toolchains=[], declarations={}, lockfiles={}, checks=[])


class ReleaseCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        projects = root / 'projects'
        projects.mkdir()
        self.repo = projects / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.test')
        self.git('config', 'core.autocrlf', 'false')
        (self.repo / 'base.txt').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.git('remote', 'add', 'origin', 'https://github.com/example/repo.git')
        database = root / 'tasks.sqlite3'
        self.tasks = {}
        self.service = SimpleNamespace(projects=projects, connect=lambda: sqlite3.connect(database, timeout=10),
            path_for=lambda task: projects / task['repository'], get=lambda identifier: copy.deepcopy(self.tasks[identifier]),
            task_records=lambda identifiers, db=None: {identifier: copy.deepcopy(self.tasks[identifier])
                                                      for identifier in identifiers if identifier in self.tasks},
            publishing_default=lambda upstream: 'contributor/repo', github=Mock(), store=Mock(), validation=None)
        self.releases = ReleaseCandidates(self.service)
        self.resolver = patch('toolchain_policy.resolve_committed', return_value=copy.deepcopy(POLICY))
        self.resolver.start()
        self.addCleanup(self.resolver.stop)
        self.protected = patch('pr_repairs.protected_changes', return_value=[])
        self.protected.start()
        self.addCleanup(self.protected.stop)
        self.base_patch = patch('contributions.task_base', return_value=('refs/remotes/origin/main', self.base))
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)
        self.sequence = 0

    def git(self, *args, tree=None):
        result = subprocess.run(['git', '-C', str(tree or self.repo), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def task(self, name='change', contents='change\n', filename=None):
        self.git('checkout', '-B', name, self.base)
        (self.repo / (filename or name + '.txt')).write_text(contents)
        self.git('add', '.')
        self.git('commit', '-m', name)
        sha = self.git('rev-parse', 'HEAD')
        self.git('checkout', 'main')
        identifier = str(len(self.tasks) + 1).zfill(32)
        self.tasks[identifier] = dict(id=identifier, title=name, repository='repo', head_sha=sha,
            state='review_ready', builds={sha: dict(target=sha, state='complete', required_checks_verified=True,
                policy_digest=toolchain_policy.digest(POLICY))})
        return identifier

    def action(self, action, release=None, **extra):
        self.sequence += 1
        body = dict(request_id='test-release-' + str(self.sequence))
        if release:
            current = self.releases.get(release['id'])
            body.update(id=current['id'], edit_version=current['edit_version'])
            if current.get('revision'):
                body['revision'] = current['revision']['digest']
        body.update(extra)
        return self.releases.action(action, body, 'operator', 'admin')

    def frozen(self, *tasks, version='alpha.15'):
        release = self.action('create', repository='repo', version=version)
        for identifier in tasks:
            release = self.action('membership', release, task_id=identifier, included=True)
        return self.action('freeze', release)

    def discussion_plan(self, *identifiers, job_id='discussion', **changes):
        for identifier in identifiers:
            self.tasks[identifier].setdefault('organization_id', 'org')
        refs = [str(index) for index in range(len(identifiers))]
        job = dict(id=job_id, organization_id='org', group_id='group', state='artifact_ready', created_at='2026-10-18')
        proposals = [dict(ref=ref, key=ref) for ref in refs]
        plan = dict(name='alpha.15', task_refs=refs)
        plan.update(changes)
        return self.releases.from_discussion(job, dict(release_plan=plan), proposals,
            dict(zip(refs, identifiers)), dict(auto_create_releases=True), 'group_auto_queue')

    def test_capture_refusal_does_not_stop_active_release_processing(self):
        release = self.action('create', repository='repo', version='worker-survival')
        release['state'] = 'integrating'
        self.releases.save(release)
        for error in (ValueError('plan unavailable'), ReleaseChanged('plan changed')):
            with self.subTest(error=error):
                self.releases.stopped.clear()
                with patch.object(self.releases, 'capture_planned_candidates', side_effect=error), \
                        patch.object(self.releases, 'advance') as advance, \
                        patch.object(self.releases.wake, 'wait', side_effect=lambda _: self.releases.stopped.set()):
                    self.releases.poll()
                advance.assert_called_once_with(release['id'])

    def poll_once(self, now=101):
        self.releases.stopped.clear()
        with patch('release_candidates.time.monotonic', return_value=now), \
                patch.object(self.releases, 'capture_planned_candidates'), \
                patch.object(self.releases, 'advance') as advance, \
                patch.object(self.releases.wake, 'wait', side_effect=lambda _: self.releases.stopped.set()):
            self.releases.poll()
        return advance

    def test_idle_poll_throttles_unchanged_records_and_prunes_inactive_cache_entries(self):
        release = self.action('create', repository='repo', version='idle-throttle')
        release = self.action('assembly', release, enabled=True)
        self.releases.assembly_checked[release['id']] = (100, release['record_version'])
        self.releases.assembly_checked['retired'] = (100, 1)
        self.poll_once().assert_not_called()
        self.assertNotIn('retired', self.releases.assembly_checked)
        self.poll_once(now=110).assert_called_once_with(release['id'])

    def test_queued_review_and_pr_bypass_a_fresh_idle_timestamp(self):
        release = self.action('create', repository='repo', version='queued-effects')
        for state, field in (('collecting', 'review'), ('published', 'pull_request')):
            with self.subTest(field=field):
                current = self.releases.get(release['id'])
                current.pop('review', None)
                current.pop('pull_request', None)
                current.update(state=state, **{field: dict(state='queued')})
                self.releases.save(current)
                self.releases.assembly_checked[current['id']] = (100, current['record_version'])
                self.poll_once().assert_called_once_with(current['id'])

    def test_explicit_action_invalidates_fresh_idle_throttle_by_record_version(self):
        release = self.action('create', repository='repo', version='changed-action')
        release = self.action('assembly', release, enabled=True)
        self.releases.assembly_checked[release['id']] = (100, release['record_version'])
        self.releases.wake.clear()
        self.action('assembly', release, enabled=True, max_runs=21)
        self.assertTrue(self.releases.wake.is_set())
        self.poll_once().assert_called_once_with(release['id'])

    def test_validation_completion_wakes_worker_and_invalidates_idle_throttle(self):
        release = self.action('create', repository='repo', version='validation-wake')
        release.update(state='validating', continuous=dict(enabled=True), resulting_sha='a' * 40,
            revision=dict(policy_digest='policy', purpose='assembly'),
            validation_event=dict(id='event', release_id=release['id']))
        self.releases.save(release)
        self.releases.assembly_checked[release['id']] = (100, release['record_version'])
        self.releases.wake.clear()
        self.assertTrue(self.releases.record_validation('event', dict(target='a' * 40,
            state='complete', required_checks_verified=True, policy_digest='policy')))
        self.assertTrue(self.releases.wake.is_set())
        self.assertEqual(self.releases.get(release['id'])['state'], 'assembled')
        self.poll_once().assert_called_once_with(release['id'])

    def test_unchanged_planned_capture_never_claims_the_sqlite_writer(self):
        identifier = self.task()
        result = self.discussion_plan(identifier)
        self.releases.capture_planned_candidates()
        before = self.releases.get(result['id'])
        statements = []
        connect = self.service.connect
        def traced():
            db = connect()
            db.set_trace_callback(statements.append)
            return db
        with patch.object(self.service, 'connect', side_effect=traced):
            self.releases.capture_planned_candidates()
        self.assertNotIn('BEGIN IMMEDIATE', statements)
        self.assertFalse(any(sql.startswith(('INSERT', 'UPDATE', 'DELETE')) for sql in statements))
        self.assertEqual(self.releases.get(result['id'])['record_version'], before['record_version'])

    def test_planned_capture_rechecks_evidence_after_read_only_preflight(self):
        identifier = self.task()
        result = self.discussion_plan(identifier)
        before = self.releases.get(result['id'])
        read = self.service.task_records
        calls = []
        def raced(identifiers, db=None):
            calls.append(True)
            if len(calls) == 2:
                self.tasks[identifier]['builds'].clear()
            return read(identifiers, db)
        with patch.object(self.service, 'task_records', side_effect=raced):
            self.releases.capture_planned_candidates()
        after = self.releases.get(result['id'])
        self.assertIsNone(after['members'][0]['candidate_sha'])
        self.assertEqual(after['record_version'], before['record_version'])

    def test_freeze_retains_managed_revision_identity_alongside_exact_sha(self):
        identifier = self.task()
        self.tasks[identifier]['candidate_revision'] = 'a' * 64
        release = self.action('create', version='managed.alpha', repository='repo')
        release = self.action('membership', release, task_id=identifier, included=True)
        frozen = self.action('freeze', release)
        member = frozen['revision']['members'][0]
        self.assertEqual(member['task_revision_id'], 'a' * 64)
        self.assertEqual(member['candidate_sha'], self.tasks[identifier]['head_sha'])

    def test_seeded_capture_restart_and_late_evidence_sequences_preserve_frozen_shas(self):
        identifier = self.task()
        original = self.tasks[identifier]['head_sha']
        evidence = copy.deepcopy(self.tasks[identifier]['builds'][original])
        for seed in range(6):
            with self.subTest(seed=seed):
                randomizer = random.Random(seed)
                self.tasks[identifier]['head_sha'] = original
                self.tasks[identifier]['builds'] = {original: copy.deepcopy(evidence)}
                result = self.discussion_plan(identifier, job_id='discussion-' + str(seed), name='alpha.' + str(seed))
                for _ in range(16):
                    event = randomizer.choice(('advance', 'pass', 'late', 'restart', 'capture'))
                    task = self.tasks[identifier]
                    if event == 'advance':
                        task['head_sha'] = self.base if task['head_sha'] == original else original
                    elif event == 'pass':
                        task['builds'][task['head_sha']] = dict(evidence, target=task['head_sha'])
                    elif event == 'late':
                        old = self.base if task['head_sha'] == original else original
                        task['builds'][old] = dict(evidence, target=old)
                    elif event == 'restart':
                        self.releases = ReleaseCandidates(self.service)
                    else:
                        self.releases.capture_planned_candidates()
                        recorded = task['builds'].get(task['head_sha'], {})
                        selected = self.releases.get(result['id'])['members'][0]['candidate_sha']
                        self.assertEqual(selected, task['head_sha'] if recorded.get('required_checks_verified') else None)
                task['builds'][task['head_sha']] = dict(evidence, target=task['head_sha'])
                self.releases.capture_planned_candidates()
                frozen = self.action('freeze', self.releases.get(result['id']))
                pinned = copy.deepcopy(frozen['revision'])
                task['head_sha'] = self.base if task['head_sha'] == original else original
                task['builds'][task['head_sha']] = dict(evidence, target=task['head_sha'])
                self.releases = ReleaseCandidates(self.service)
                self.releases.capture_planned_candidates()
                self.assertEqual(self.releases.get(result['id'])['revision'], pinned)

    def test_planned_candidates_follow_only_verified_heads_and_stop_at_freeze(self):
        identifier = self.task()
        result = self.discussion_plan(identifier)
        release = self.releases.get(result['id'])
        self.assertIsNone(release['members'][0]['candidate_sha'])
        self.releases.capture_planned_candidates()
        captured = self.releases.get(result['id'])
        first = self.tasks[identifier]['head_sha']
        self.assertEqual(captured['members'][0]['candidate_sha'], first)
        self.assertGreater(captured['edit_version'], release['edit_version'])
        # A new head invalidates the automatic selection until its own checks pass.
        self.tasks[identifier]['head_sha'] = self.base
        self.releases.capture_planned_candidates()
        self.assertIsNone(self.releases.get(result['id'])['members'][0]['candidate_sha'])
        self.tasks[identifier]['builds'][self.base] = dict(target=first, state='complete', required_checks_verified=True)
        self.releases.capture_planned_candidates()
        self.assertIsNone(self.releases.get(result['id'])['members'][0]['candidate_sha'])
        self.tasks[identifier]['head_sha'] = first
        self.releases.capture_planned_candidates()
        frozen = self.action('freeze', self.releases.get(result['id']))
        self.assertEqual(frozen['revision']['members'][0]['candidate_sha'], first)
        self.tasks[identifier]['head_sha'] = self.base
        self.releases.capture_planned_candidates()
        self.assertEqual(self.releases.get(result['id'])['revision'], frozen['revision'])

    def test_planned_candidate_capture_rejects_stale_operator_selection_and_noop_saves(self):
        identifier = self.task()
        result = self.discussion_plan(identifier)
        before = self.releases.get(result['id'])
        self.releases.capture_planned_candidates()
        captured = self.releases.get(result['id'])
        self.releases.capture_planned_candidates()
        self.assertEqual(self.releases.get(result['id'])['record_version'], captured['record_version'])
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            self.releases.action('freeze', dict(id=before['id'], edit_version=before['edit_version'],
                request_id='stale-planned-freeze'), 'operator', 'admin')
        # Replaying the original discussion cannot restore a member removed by an operator.
        self.action('membership', captured, task_id=identifier, included=False)
        self.discussion_plan(identifier)
        self.assertEqual(self.releases.get(result['id'])['members'], [])

    def test_discussion_plan_refusals_are_explanatory_and_never_modify_other_releases(self):
        identifier = self.task()
        job = dict(id='discussion', organization_id='org', state='artifact_ready')
        proposals = [dict(ref='work', key='key')]
        created = dict(key=identifier)
        self.tasks[identifier]['organization_id'] = 'org'
        def propose(plan, policy=None, items=None, mapping=None):
            return self.releases.from_discussion(job, dict(release_plan=plan), items or proposals,
                created if mapping is None else mapping,
                dict(auto_create_releases=True) if policy is None else policy, 'group_auto_queue')
        valid = dict(name='alpha.15', task_refs=['work'])
        for plan in ([], dict(name='../bad', task_refs=['work']), dict(name='alpha.15', task_refs=[]),
                     dict(name='alpha.15', task_refs=['work', 'work']), dict(name='alpha.15', task_refs=['absent'])):
            with self.subTest(plan=plan):
                self.assertEqual(propose(plan)['state'], 'not_created')
        self.assertEqual(propose(valid, policy={})['state'], 'not_created')
        self.assertEqual(propose(valid, policy=dict(auto_create_releases=True, paused=True))['state'], 'not_created')
        self.assertEqual(propose(valid, items=proposals * 2)['state'], 'not_created')
        self.assertEqual(propose(valid, mapping={})['state'], 'not_created')
        self.tasks[identifier]['organization_id'] = 'other'
        self.assertIn('organization', propose(valid)['reason'])
        self.assertEqual(self.releases.snapshot(), [])
        self.tasks[identifier]['organization_id'] = 'org'
        manual = self.action('create', repository='repo', version='alpha.15')
        self.assertIn('already exists', propose(valid)['reason'])
        self.assertEqual(self.releases.get(manual['id'])['members'], [])

    def test_discussion_plan_cannot_span_repositories(self):
        first, second = self.task('first'), self.task('second')
        self.tasks[second]['repository'] = 'other-repo'
        result = self.discussion_plan(first, second)
        self.assertEqual(result['state'], 'not_created')
        self.assertIn('one repository', result['reason'])
        self.assertEqual(self.releases.snapshot(), [])

    def test_discussion_release_receipt_survives_disabled_permission_but_refuses_artifact_drift(self):
        identifier = self.task()
        result = self.discussion_plan(identifier)
        recovered = self.releases.from_discussion(
            dict(id='discussion', organization_id='org', state='artifact_ready'),
            dict(release_plan=dict(name='alpha.15', task_refs=['0'])), [], {}, {}, 'group_auto_queue')
        self.assertEqual(recovered, result)
        changed = self.discussion_plan(identifier, name='alpha.16')
        self.assertEqual(changed['state'], 'not_created')
        self.assertIn('artifact changed', changed['reason'])
        self.assertEqual(self.releases.get(result['id'])['version'], 'alpha.15')
        self.assertEqual(len(self.releases.snapshot()), 1)

    def test_revising_a_discussion_release_retains_automatic_membership_and_provenance(self):
        identifier = self.task()
        result = self.discussion_plan(identifier)
        self.releases.capture_planned_candidates()
        frozen = self.action('freeze', self.releases.get(result['id']))
        frozen['state'] = 'blocked'
        self.releases.save(frozen)
        revised = self.action('revise', frozen)
        self.assertEqual(revised['source']['job_id'], 'discussion')
        self.assertEqual(revised['organization_id'], 'org')
        self.assertEqual(revised['members'][0]['selection'], 'automatic')
        self.assertNotIn('revision', revised)

    def test_request_replay_and_stale_selection_are_checked(self):
        body = dict(request_id='same-request', repository='repo', version='alpha.15')
        first = self.releases.action('create', body, 'operator', 'admin')
        self.assertEqual(first, self.releases.action('create', body, 'operator', 'admin'))
        with self.assertRaisesRegex(ValueError, 'another release action'):
            self.releases.action('create', dict(body, version='alpha.16'), 'operator', 'admin')
        identifier = self.task()
        self.action('membership', first, task_id=identifier, included=True)
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            self.releases.action('freeze', dict(id=first['id'], edit_version=0, request_id='stale'), 'operator', 'admin')

    def test_only_admin_can_mutate_and_repository_cannot_escape(self):
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.releases.action('create', {}, 'reader', 'operator')
        with self.assertRaisesRegex(ValueError, 'inside the projects'):
            self.action('create', repository='../', version='escape')

    def test_membership_is_many_to_many_and_freeze_pins_exact_sha(self):
        identifier = self.task()
        first = self.frozen(identifier)
        second = self.frozen(identifier, version='alpha.16')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['revision']['members'], second['revision']['members'])
        with self.assertRaisesRegex(ValueError, 'manifest is frozen'):
            self.action('membership', first, task_id=identifier, included=False)
        self.tasks[identifier]['head_sha'] = 'a' * 40
        self.releases.advance(first['id'])
        result = self.releases.get(first['id'])
        self.assertEqual(result['results'][identifier]['state'], 'incorporated')
        self.assertEqual(self.tasks[identifier]['state'], 'review_ready')

    def test_changed_candidate_and_missing_evidence_refuse_freeze(self):
        identifier = self.task()
        release = self.action('create', repository='repo', version='alpha.15')
        release = self.action('membership', release, task_id=identifier, included=True)
        original = self.tasks[identifier]['head_sha']
        self.tasks[identifier]['head_sha'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'candidate changed') as refused:
            self.action('freeze', release)
        self.assertIn(self.tasks[identifier]['title'], str(refused.exception))
        self.assertIn(original[:12], str(refused.exception))
        self.assertIn(('b' * 40)[:12], str(refused.exception))
        self.assertIn('Selected changes', str(refused.exception))
        self.tasks[identifier]['head_sha'] = original
        self.tasks[identifier]['builds'][original]['required_checks_verified'] = False
        with self.assertRaisesRegex(ValueError, 'required checks must pass'):
            self.action('freeze', release)

    def test_changed_selection_is_explicit_reviewed_and_order_preserving(self):
        first, second = self.task('first'), self.task('second')
        release = self.action('create', repository='repo', version='selection-review')
        for identifier in (first, second):
            release = self.action('membership', release, task_id=identifier, included=True)
        selected = release['members'][0]['candidate_sha']
        reviewed = 'b' * 40
        self.tasks[first]['head_sha'] = reviewed
        presented = self.releases.present(copy.deepcopy(self.releases.get(release['id'])))
        self.assertTrue(presented['members'][0]['candidate_changed'])
        self.assertEqual(presented['members'][0]['current_candidate_sha'], reviewed)
        self.assertEqual(self.releases.get(release['id'])['members'][0]['candidate_sha'], selected)
        for previous, candidate in (('f' * 40, reviewed), (selected, 'a' * 40)):
            with self.assertRaisesRegex(ValueError, 'reviewed candidate changed again'):
                self.action('membership', release, task_id=first, included=True,
                    previous_candidate_sha=previous, candidate_sha=candidate)
            self.assertEqual(self.releases.get(release['id'])['members'][0]['candidate_sha'], selected)
        updated = self.action('membership', release, task_id=first, included=True,
            previous_candidate_sha=selected, candidate_sha=reviewed)
        self.assertEqual([member['task_id'] for member in updated['members']], [first, second])
        self.assertEqual(updated['members'][0]['candidate_sha'], reviewed)
        self.assertFalse(updated['members'][0]['candidate_changed'])
        self.assertEqual(updated['state'], 'collecting')
        with self.assertRaisesRegex(ValueError, 'required checks must pass'):
            self.action('freeze', updated)

    def test_two_independent_changes_integrate_without_touching_project(self):
        first, second = self.task('first'), self.task('second')
        release = self.frozen(first, second)
        self.releases.advance(release['id'])
        self.releases.advance(release['id'])
        result = self.releases.present(self.releases.get(release['id']))
        tree = self.releases.worktree(result)
        self.assertEqual(result['counts']['incorporated'], 2)
        self.assertTrue((tree / 'first.txt').exists())
        self.assertTrue((tree / 'second.txt').exists())
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('rev-list', '--count', self.base + '..' + result['checkpoint']), '4')

    def test_crash_after_merge_before_checkpoint_recovers_without_duplicate(self):
        identifier = self.task()
        release = self.frozen(identifier)
        with patch.object(self.releases, 'checkpoint', side_effect=RuntimeError('crash')):
            with self.assertRaisesRegex(RuntimeError, 'crash'):
                self.releases.advance(release['id'])
        interrupted = self.releases.get(release['id'])
        tree = self.releases.worktree(interrupted)
        merged = self.git('rev-parse', 'HEAD', tree=tree)
        restarted = ReleaseCandidates(self.service)
        restarted.advance(release['id'])
        recovered = restarted.get(release['id'])
        self.assertEqual(recovered['checkpoint'], merged)
        self.assertNotIn('intent', recovered)
        self.assertEqual(self.git('rev-parse', 'HEAD', tree=tree), merged)

    def test_crash_after_ref_update_before_database_checkpoint_recovers(self):
        identifier = self.task()
        release = self.frozen(identifier)
        original_save = self.releases.save
        def fail_checkpoint(record, db=None):
            if record.get('checkpoint') != self.base:
                raise RuntimeError('crash')
            return original_save(record, db)
        with patch.object(self.releases, 'save', side_effect=fail_checkpoint):
            with self.assertRaises(RuntimeError):
                self.releases.advance(release['id'])
        restarted = ReleaseCandidates(self.service)
        restarted.advance(release['id'])
        self.assertEqual(restarted.get(release['id'])['results'][identifier]['state'], 'incorporated')

    def test_already_incorporated_commit_is_not_merged_twice(self):
        first = self.task()
        second = '9' * 32
        self.tasks[second] = dict(copy.deepcopy(self.tasks[first]), id=second, title='same commit')
        release = self.frozen(first, second)
        self.releases.advance(release['id'])
        head = self.releases.get(release['id'])['checkpoint']
        self.releases.advance(release['id'])
        result = self.releases.present(self.releases.get(release['id']))
        self.assertEqual(result['checkpoint'], head)
        self.assertEqual(result['counts']['incorporated'], 2)

    def test_conflict_is_retained_and_manual_resolution_resumes(self):
        first = self.task('first', 'first\n', 'base.txt')
        second = self.task('second', 'second\n', 'base.txt')
        release = self.frozen(first, second)
        self.releases.advance(release['id'])
        self.releases.advance(release['id'])
        # First failed merge is journaled; recovery explains the conflict files.
        self.action('resume', release)
        self.releases.advance(release['id'])
        blocked = self.releases.get(release['id'])
        self.assertEqual(blocked['state'], 'blocked')
        self.assertIn('base.txt', blocked['error'])
        tree = self.releases.worktree(blocked)
        (tree / 'base.txt').write_text('resolved\n')
        self.git('add', 'base.txt', tree=tree)
        self.git('commit', '-m', 'resolved', tree=tree)
        self.action('resume', release)
        self.releases.advance(release['id'])
        result = self.releases.get(release['id'])
        self.assertEqual(result['results'][second]['state'], 'incorporated')
        self.assertNotIn('intent', result)

    def test_external_ref_move_is_never_overwritten(self):
        identifier = self.task()
        release = self.frozen(identifier)
        self.releases.advance(release['id'])
        self.git('update-ref', 'refs/heads/' + release['branch'], self.base)
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['state'], 'blocked')
        self.assertEqual(self.git('rev-parse', release['branch']), self.base)

    def test_combined_validation_is_separate_and_late_result_cannot_reopen(self):
        identifier = self.task()
        release = self.frozen(identifier)
        self.releases.advance(release['id'])
        validation = Mock()
        validation.snapshot.return_value = dict(runs=[])
        self.service.validation = validation
        self.releases.advance(release['id'])
        waiting = self.releases.get(release['id'])
        self.assertEqual(waiting['state'], 'validating')
        event = waiting['validation_event']
        self.assertEqual(event['target'], waiting['checkpoint'])
        failure = dict(target=event['target'], state='failed', required_checks_verified=False,
                       policy_digest=toolchain_policy.digest(POLICY), note='Combined test failed')
        self.releases.record_validation(event['id'], failure)
        self.assertEqual(self.releases.get(release['id'])['state'], 'validation_failed')
        self.assertEqual(self.tasks[identifier]['builds'][self.tasks[identifier]['head_sha']]['state'], 'complete')
        self.action('retry', waiting)
        self.releases.advance(release['id'])
        retried = self.releases.get(release['id'])
        self.assertNotEqual(retried['validation_event']['id'], event['id'])
        summary = dict(failure, state='complete', required_checks_verified=True)
        self.releases.record_validation(retried['validation_event']['id'], summary)
        self.assertEqual(self.releases.get(release['id'])['state'], 'ready')
        self.action('publish', release, head_sha=retried['checkpoint'])
        self.releases.record_validation(retried['validation_event']['id'], failure)
        self.assertEqual(self.releases.get(release['id'])['state'], 'publishing')
        with patch('contributions.push') as push:
            self.releases.advance(release['id'])
        self.assertEqual(push.call_args.args[-1], 'contributor/repo')
        self.assertEqual(self.releases.get(release['id'])['state'], 'published')

    def test_revision_retains_previous_manifest_and_run(self):
        identifier = self.task()
        release = self.frozen(identifier)
        self.releases.advance(release['id'])
        # A missing executor is visible and recoverable.
        self.releases.advance(release['id'])
        previous = self.releases.get(release['id'])
        revised = self.action('revise', release)
        self.assertEqual(revised['state'], 'collecting')
        next_revision = self.action('freeze', revised)
        self.assertNotEqual(next_revision['revision']['digest'], previous['revision']['digest'])
        with closing(self.service.connect()) as db:
            retained = json.loads(db.execute('SELECT data FROM release_revisions WHERE id=?',
                                            (previous['revision']['digest'],)).fetchone()[0])
        self.assertEqual(retained['checkpoint'], previous['checkpoint'])
        history = self.releases.history(release['id'])
        self.assertEqual([item['generation'] for item in history['revisions']], [2, 1])
        inspected = self.releases.history(release['id'], revision=previous['revision']['digest'])
        self.assertTrue(inspected['read_only'])
        self.assertEqual(inspected['release']['revision'], previous['revision'])
        self.assertEqual(inspected['release']['error'], previous['error'])
        self.assertEqual(self.releases.get(release['id'])['revision'], next_revision['revision'])

    def test_policy_change_blocks_integration(self):
        release = self.frozen(self.task())
        with patch('toolchain_policy.resolve_committed', return_value=dict(POLICY, changed=True)):
            self.releases.advance(release['id'])
        self.assertIn('policy changed', self.releases.get(release['id'])['error'])

    def test_freeze_distinguishes_old_policy_from_recorded_check_success(self):
        identifier = self.task()
        release = self.action('create', repository='repo', version='old-check-policy')
        release = self.action('membership', release, task_id=identifier, included=True)
        evidence = self.tasks[identifier]['builds'][self.tasks[identifier]['head_sha']]
        evidence['policy_digest'] = 'old-policy'
        member = self.releases.present(release)['members'][0]
        self.assertIn('Recorded checks passed', member['eligibility_reason'])
        self.assertIn('policy changed', member['eligibility_reason'])
        with self.assertRaisesRegex(ValueError, 'different policy.*Validate latest commit'):
            self.action('freeze', release)
        self.assertEqual(self.releases.get(release['id'])['state'], 'collecting')
        evidence.pop('policy_digest')
        with self.assertRaisesRegex(ValueError, 'no policy identity.*Validate latest commit'):
            self.action('freeze', release)
        evidence['policy_digest'] = toolchain_policy.digest(POLICY)
        self.assertEqual(self.action('freeze', release)['state'], 'integrating')

    def test_no_second_submission_after_lost_validation_response(self):
        release = self.frozen(self.task())
        self.releases.advance(release['id'])
        validation = Mock()
        validation.snapshot.return_value = dict(runs=[])
        self.service.validation = validation
        self.releases.advance(release['id'])
        event = self.releases.get(release['id'])['validation_event']
        validation.snapshot.return_value = dict(runs=[dict(event_id=event['id'], state='running')])
        self.releases.advance(release['id'])
        self.assertEqual(validation.submit.call_count, 1)

    def test_concurrent_workers_share_the_repository_claim(self):
        identifier = self.task()
        release = self.frozen(identifier)
        second_worker = ReleaseCandidates(self.service)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker.advance, release['id']) for worker in (self.releases, second_worker)]
            for future in futures:
                future.result(timeout=30)
        result = self.releases.get(release['id'])
        self.assertEqual(result['results'][identifier]['state'], 'incorporated')
        self.assertEqual(self.git('rev-list', '--count', self.base + '..' + result['checkpoint']), '2')

    def test_stale_worker_cannot_overwrite_callback_or_operator_state(self):
        release = self.frozen(self.task())
        stale = self.releases.get(release['id'])
        newer = self.releases.get(release['id'])
        newer.update(state='blocked', error='Operator needs to review this')
        self.releases.save(newer)
        stale['state'] = 'ready'
        with self.assertRaises(ReleaseChanged):
            self.releases.save(stale)
        self.assertEqual(self.releases.get(release['id'])['error'], 'Operator needs to review this')

    def test_explicit_exclusion_aborts_only_pinned_merge_and_keeps_denominator(self):
        first = self.task('first', 'first\n', 'base.txt')
        second = self.task('second', 'second\n', 'base.txt')
        release = self.frozen(first, second)
        self.releases.advance(release['id'])
        self.releases.advance(release['id'])
        self.action('exclude', release, task_id=second, reason='Defer the conflicting change to alpha.16')
        self.releases.advance(release['id'])
        result = self.releases.present(self.releases.get(release['id']))
        self.assertEqual(result['counts']['selected'], 2)
        self.assertEqual(result['counts']['incorporated'], 1)
        self.assertEqual(result['counts']['excluded'], 1)
        self.assertIn('alpha.16', result['results'][second]['reason'])
        self.assertFalse(project_git.merge_state(self.releases.worktree(result)))
        self.assertEqual((self.releases.worktree(result) / 'base.txt').read_text(), 'first\n')

    def test_conflict_agent_uses_pinned_environment_and_verified_merge_result(self):
        first = self.task('first', 'first\n', 'base.txt')
        second = self.task('second', 'second\n', 'base.txt')
        release = self.frozen(first, second)
        self.releases.advance(release['id'])
        self.releases.advance(release['id'])
        store = self.service.store
        store.snapshot.return_value = dict(profiles=[dict(id='agent', organization_id='org', project=str(self.repo))])
        run = dict(id='launch', profile_id='agent', kind='launch', state='persona_sent',
                   toolchain_policy=dict(policy=POLICY, digest=toolchain_policy.digest(POLICY)))
        store.job_records.return_value = [run]
        jobs = {'launch': run, 'chat': dict(id='chat', state='running')}
        store.job_record.side_effect = lambda identifier: jobs[identifier]
        store.action.return_value = dict(id='chat')
        self.action('repair', release, profile_id='agent')
        self.releases.advance(release['id'])
        repairing = self.releases.get(release['id'])
        self.assertEqual(repairing['state'], 'repairing')
        dispatched = store.action.call_args.args[1]
        self.assertIn(str(self.releases.worktree(repairing)), dispatched['prompt'])
        self.assertEqual(dispatched['expected_run_id'], 'launch')
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['state'], 'repairing')
        tree = self.releases.worktree(repairing)
        (tree / 'base.txt').write_text('resolved\n')
        self.git('add', '.', tree=tree)
        self.git('commit', '-m', 'agent resolution', tree=tree)
        jobs['chat']['state'] = 'answered'
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['results'][second]['state'], 'incorporated')

    def test_changed_agent_environment_refuses_conflict_dispatch(self):
        first = self.task('first', 'first\n', 'base.txt')
        second = self.task('second', 'second\n', 'base.txt')
        release = self.frozen(first, second)
        self.releases.advance(release['id'])
        self.releases.advance(release['id'])
        store = self.service.store
        store.snapshot.return_value = dict(profiles=[dict(id='agent', organization_id='org', project=str(self.repo))])
        store.job_records.return_value = [dict(id='launch', profile_id='agent', kind='launch', state='persona_sent')]
        store.job_record.return_value = dict(toolchain_policy=dict(digest='old-policy'))
        self.action('repair', release, profile_id='agent')
        self.releases.advance(release['id'])
        self.assertIn('different environment', self.releases.get(release['id'])['error'])
        store.action.assert_not_called()

    def test_validation_lookup_is_not_limited_to_the_visible_history_page(self):
        release = self.frozen(self.task())
        self.releases.advance(release['id'])
        self.service.validation = Mock()
        self.service.validation.snapshot.return_value = dict(runs=[])
        self.releases.advance(release['id'])
        event = self.releases.get(release['id'])['validation_event']
        with closing(self.service.connect()) as db, db:
            db.executemany('INSERT INTO release_candidates VALUES (?,?)', [
                (format(i + 1000, '032x'), json.dumps(dict(id=format(i + 1000, '032x'), state='collecting', members=[])))
                for i in range(101)])
        self.assertNotIn(release['id'], [item['id'] for item in self.releases.snapshot()])
        self.assertEqual(self.releases.event(event['id']), event)

    def test_foreign_validation_target_is_consumed_as_a_visible_failure(self):
        release = self.frozen(self.task())
        self.releases.advance(release['id'])
        self.service.validation = Mock()
        self.service.validation.snapshot.return_value = dict(runs=[])
        self.releases.advance(release['id'])
        event = self.releases.get(release['id'])['validation_event']
        self.assertTrue(self.releases.record_validation(event['id'], dict(target='f' * 40, state='complete',
            required_checks_verified=True, policy_digest=toolchain_policy.digest(POLICY))))
        result = self.releases.get(release['id'])
        self.assertEqual(result['state'], 'validation_failed')
        self.assertIn('does not match', result['error'])
        self.assertNotIn('validation', result)

    def test_snapshot_batches_eligibility_across_collecting_releases(self):
        members = []
        for index in range(100):
            identifier = format(index, '032x')
            self.tasks[identifier] = dict(id=identifier, title='Task', repository='repo', head_sha='a' * 40, builds={})
            members.append(dict(task_id=identifier, candidate_sha='a' * 40))
        with closing(self.service.connect()) as db, db:
            for number in range(2):
                record = dict(id=format(number + 1000, '032x'), state='collecting', members=members)
                db.execute('INSERT INTO release_candidates VALUES (?,?)', (record['id'], json.dumps(record)))
        self.service.task_records = Mock(wraps=self.service.task_records)
        self.service.get = Mock(side_effect=AssertionError('Per-member reads must not occur'))
        result = self.releases.snapshot()
        self.service.task_records.assert_called_once()
        self.assertEqual(set(self.service.task_records.call_args.args[0]), set(self.tasks))
        self.assertEqual(len(result[0]['members']), 100)
        self.assertIn('not passed', result[0]['members'][0]['eligibility_reason'])

    def test_freeze_git_inspection_does_not_hold_sqlite_writer_and_rechecks_tasks(self):
        identifier = self.task()
        release = self.action('create', repository='repo', version='alpha.15')
        release = self.action('membership', release, task_id=identifier, included=True)
        original = self.tasks[identifier]['head_sha']
        def inspect(*args):
            with closing(self.service.connect()) as db, db:
                db.execute('PRAGMA busy_timeout=50')
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS unrelated_write (value TEXT)')
                db.execute("INSERT INTO unrelated_write VALUES ('not blocked by Git')")
            self.tasks[identifier]['head_sha'] = 'f' * 40
            return []
        with patch('pr_repairs.protected_changes', side_effect=inspect):
            with self.assertRaisesRegex(ReleaseChanged, 'validation changed during freeze'):
                self.action('freeze', release)
        self.assertEqual(self.releases.get(release['id'])['state'], 'collecting')
        self.assertEqual(self.releases.get(release['id'])['generation'], 0)
        self.tasks[identifier]['head_sha'] = original
        replacement = self.task('replacement')
        self.tasks[identifier] = dict(copy.deepcopy(self.tasks[replacement]), id=identifier)
        self.action('membership', release, task_id=identifier, included=True)
        frozen = self.action('freeze', release)
        self.assertEqual(frozen['revision']['members'][0]['candidate_sha'], self.tasks[replacement]['head_sha'])

    def test_duplicate_names_are_refused_transactionally_without_breaking_legacy_rows(self):
        first = self.action('create', repository='repo', version='alpha.15')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.action('create', repository='repo', version='ALPHA.15')
        # Existing databases with duplicate labels still open; no startup index
        # migration makes an otherwise inspectable deployment unavailable.
        duplicate = dict(first, id='f' * 32)
        with closing(self.service.connect()) as db, db:
            db.execute('INSERT INTO release_candidates VALUES (?,?)', (duplicate['id'], json.dumps(duplicate)))
        self.assertEqual(len(ReleaseCandidates(self.service).snapshot()), 2)

    def test_published_release_listing_survives_a_live_policy_error(self):
        release = self.frozen(self.task())
        release = self.releases.get(release['id'])
        release.update(state='published', resulting_sha='b' * 40)
        self.releases.save(release)
        self.service.github.publication_mode.side_effect = ValueError('Operator policy requires repair.')
        displayed = self.releases.snapshot()[0]
        self.assertEqual(displayed['state'], 'published')
        self.assertEqual(displayed['publication_error'], 'Operator policy requires repair.')
        self.assertFalse(displayed['manual_pr_required'])
        self.service.github.publication_mode.side_effect = None
        self.service.github.publication_mode.return_value = 'manual_adoption'
        recovered = self.releases.snapshot()[0]
        self.assertNotIn('publication_error', recovered)
        self.assertTrue(recovered['manual_pr_required'])

    def test_release_push_requires_the_recorded_run_not_only_a_branch_pattern(self):
        from contributions import push
        identifier, sha = 'a' * 32, 'b' * 40
        branch = 'herdr/release-' + identifier + '-r1'
        record = dict(id=identifier, state='publishing', branch=branch, resulting_sha=sha,
                      revision=dict(generation=1), publication=dict(destination='contributor/repo'))
        with patch('contributions.sys.platform', 'win32'), patch('contributions.subprocess.run') as transport:
            with self.assertRaisesRegex(ValueError, 'recorded release run'):
                push(self.repo, branch, sha, self.service.github, 'contributor/repo', upstream='owner/repo')
            for bad in (dict(record, resulting_sha='c' * 40), dict(record, state='ready'),
                        dict(record, revision=dict(generation=2))):
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    push(self.repo, branch, sha, self.service.github, 'contributor/repo', upstream='owner/repo', release_lookup=lambda _: bad)
            with self.assertRaisesRegex(ValueError, 'Linux LXC'):
                push(self.repo, branch, sha, self.service.github, 'contributor/repo', upstream='owner/repo', release_lookup=lambda _: record)
        transport.assert_not_called()

    def test_revision_history_is_paginated_and_scoped_to_its_release(self):
        release = self.frozen(self.task())
        current = self.releases.get(release['id'])
        with closing(self.service.connect()) as db, db:
            for generation in range(2, 26):
                snapshot = copy.deepcopy(current)
                snapshot['revision']['generation'] = generation
                snapshot['revision'].pop('digest')
                snapshot['revision']['digest'] = digest(snapshot['revision'])
                db.execute('INSERT INTO release_revisions VALUES (?,?)', (snapshot['revision']['digest'], json.dumps(snapshot)))
        first = self.releases.history(release['id'])
        second = self.releases.history(release['id'], before=first['next_before'])
        self.assertEqual([r['generation'] for r in first['revisions']], list(range(25, 5, -1)))
        self.assertEqual([r['generation'] for r in second['revisions']], [5, 4, 3, 2, 1])
        self.assertIsNone(second['next_before'])
        other = self.action('create', repository='repo', version='alpha.16')
        with self.assertRaisesRegex(ValueError, 'not found for this release'):
            self.releases.history(other['id'], revision=current['revision']['digest'])
        for cursor in (0, -1, True, '6', 2**64):
            with self.assertRaisesRegex(ValueError, 'history cursor'):
                self.releases.history(release['id'], before=cursor)

    def test_missing_active_manifest_is_a_visible_blocker_not_a_worker_exception(self):
        release = self.frozen(self.task())
        for manifest in (None, {}, {'members': []}):
            stored = self.releases.get(release['id'])
            stored.update(state='integrating', revision=manifest)
            self.releases.save(stored)
            self.releases.advance(release['id'])
            blocked = self.releases.get(release['id'])
            self.assertEqual(blocked['state'], 'blocked')
            self.assertIn('manifest is missing or incomplete', blocked['error'])


if __name__ == '__main__':
    unittest.main()
