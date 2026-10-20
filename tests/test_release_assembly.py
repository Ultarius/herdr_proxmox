"""Real-Git continuous assembly, explicit admission and bounded agent effects."""
import copy
import json
from pathlib import Path
import sys
from contextlib import closing
from unittest.mock import Mock, patch
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
sys.path.insert(0, str(Path(__file__).parent))
import test_release_candidates as fixtures
from release_candidates import ReleaseCandidates, ReleaseChanged
import release_assembly
import project_git
import toolchain_policy
POLICY = fixtures.POLICY


class ContinuousAssemblyTests(unittest.TestCase):
    setUp = fixtures.ReleaseCandidateTests.setUp
    git = fixtures.ReleaseCandidateTests.git
    task = fixtures.ReleaseCandidateTests.task
    action = fixtures.ReleaseCandidateTests.action

    def plan(self, *identifiers, **grant):
        release = self.action('create', repository='repo', version='continuous')
        for identifier in identifiers:
            release = self.action('membership', release, task_id=identifier, included=True)
        return self.action('assembly', release, enabled=True, **grant)

    def checked(self, release):
        self.service.validation = Mock()
        self.service.validation.snapshot.return_value = dict(runs=[])
        for _ in range(12):
            self.releases.advance(release['id'])
            current = self.releases.get(release['id'])
            if current['state'] == 'validating':
                event = current['validation_event']
                self.releases.record_validation(event['id'], dict(target=event['target'], state='complete',
                    required_checks_verified=True, policy_digest=current['revision']['policy_digest']))
                return self.releases.get(release['id'])
            self.assertNotEqual(current['state'], 'blocked', current.get('error'))
        self.fail('Assembly did not reach combined validation')

    def agent(self):
        store = self.service.store
        store.snapshot.return_value = dict(profiles=[dict(id='agent', organization_id='org', project=str(self.repo))])
        run = dict(id='launch', profile_id='agent', kind='launch', state='persona_sent',
                   toolchain_policy=dict(policy=POLICY, digest=toolchain_policy.digest(POLICY)))
        jobs = dict(launch=run, chat=dict(id='chat', state='running'))
        store.job_records.return_value = [run]
        store.job_record.side_effect = lambda identifier: jobs[identifier]
        store.action.return_value = dict(id='chat')
        return jobs

    def conflict(self, **grant):
        first = self.task('first', 'first\n', 'base.txt')
        second = self.task('second', 'second\n', 'base.txt')
        release = self.plan(first, second, **grant)
        for _ in range(3):
            self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['state'], 'blocked')
        return self.releases.get(release['id'])

    def test_old_source_policy_is_recorded_and_combined_current_policy_precedes_explicit_freeze(self):
        identifier = self.task()
        task = self.tasks[identifier]
        task['builds'][task['head_sha']]['policy_digest'] = 'historically-approved-policy'
        release = self.plan(identifier)
        with self.assertRaisesRegex(ValueError, 'combined assembly validation'):
            self.action('freeze', release)
        assembled = self.checked(release)
        self.assertEqual(assembled['state'], 'assembled')
        self.assertEqual(assembled['revision']['members'][0]['source_validation']['policy_digest'], 'historically-approved-policy')
        self.assertEqual(assembled['validation']['policy_digest'], toolchain_policy.digest(POLICY))
        with self.assertRaisesRegex(ValueError, 'before publishing'):
            self.action('publish', release, head_sha=assembled['resulting_sha'])
        with self.assertRaisesRegex(ValueError, 'exact checked assembly'):
            self.action('freeze', assembled, head_sha='f' * 40)
        frozen = self.action('freeze', assembled, head_sha=assembled['resulting_sha'])
        self.assertEqual(frozen['state'], 'ready')
        self.assertFalse(frozen['continuous']['enabled'])
        self.assertEqual(frozen['revision']['digest'], assembled['revision']['digest'])
        self.action('publish', frozen, head_sha=frozen['resulting_sha'])
        with patch('contributions.push') as push:
            self.releases.advance(release['id'])
        push.assert_called_once()

    def test_partial_scope_is_assembled_but_cannot_be_frozen_as_complete_release(self):
        first, second = self.task('first'), self.task('second')
        pending = self.tasks[second]
        evidence = pending['builds'].pop(pending['head_sha'])
        release = self.plan(first, second)
        checked = self.checked(release)
        self.assertEqual(len(checked['revision']['members']), 1)
        presented = self.releases.present(copy.deepcopy(checked))
        self.assertEqual(presented['counts']['selected'], 2)
        self.assertEqual(presented['counts']['incorporated'], 1)
        with self.assertRaisesRegex(ValueError, 'current complete assembly'):
            self.action('freeze', checked, head_sha=checked['resulting_sha'])
        pending['builds'][pending['head_sha']] = evidence
        next_run = self.checked(release)
        self.assertEqual(len(next_run['revision']['members']), 2)
        self.assertEqual(next_run['generation'], 2)
        retained = self.releases.history(release['id'], revision=checked['revision']['digest'])
        self.assertEqual(len(retained['release']['members']), 1)

    def test_new_heads_during_validation_do_not_mutate_manifest_or_transfer_old_receipt(self):
        identifier = self.task('first')
        release = self.plan(identifier)
        self.service.validation = Mock()
        self.service.validation.snapshot.return_value = dict(runs=[])
        for _ in range(3):
            self.releases.advance(release['id'])
        old = self.releases.get(release['id'])
        self.assertEqual(old['state'], 'validating')
        newer = self.task('newer')
        self.tasks[identifier].update(head_sha=self.tasks[newer]['head_sha'], builds=self.tasks[newer]['builds'])
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['revision'], old['revision'])
        self.releases.record_validation(old['validation_event']['id'], dict(target=old['resulting_sha'],
            state='complete', required_checks_verified=True, policy_digest=old['revision']['policy_digest']))
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.action('freeze', release, head_sha=old['resulting_sha'])
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['generation'], 2)
        self.releases.record_validation(old['validation_event']['id'], dict(target=old['resulting_sha'], state='failed'))
        self.assertEqual(self.releases.get(release['id'])['generation'], 2)
        self.assertNotEqual(self.releases.get(release['id'])['state'], 'validation_failed')

    def test_restart_reuses_current_assembly_and_its_run_budget(self):
        release = self.plan(self.task())
        self.releases.advance(release['id'])
        original = self.releases.get(release['id'])
        self.releases = ReleaseCandidates(self.service)
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['revision'], original['revision'])
        self.assertEqual(current['continuous']['runs_used'], 1)

    def test_changed_policy_requires_new_assembly_and_source_unknowns_fail_closed(self):
        identifier = self.task()
        release = self.plan(identifier)
        assembled = self.checked(release)
        with patch('toolchain_policy.resolve_committed', return_value=dict(POLICY, changed=True)):
            with self.assertRaisesRegex(ValueError, 'policy changed'):
                self.action('freeze', assembled, head_sha=assembled['resulting_sha'])
            self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['generation'], 2)
        self.tasks[identifier]['builds'][self.tasks[identifier]['head_sha']].pop('policy_digest')
        scope, eligible = release_assembly.selections(self.releases.get(release['id']), self.tasks)
        self.assertEqual(eligible, [])

    def test_automatic_conflict_repair_is_reserved_once_and_verified(self):
        jobs = self.agent()
        release = self.conflict(auto_repair=True, profile_id='agent', max_repairs=1)
        self.releases.advance(release['id'])
        queued = self.releases.get(release['id'])
        self.assertEqual(queued['state'], 'repair_queued')
        self.assertEqual(queued['continuous']['repairs_used'], 1)
        self.releases = ReleaseCandidates(self.service)
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['state'], 'repairing')
        self.releases.advance(release['id'])
        self.service.store.action.assert_called_once()
        tree = self.releases.worktree(queued)
        (tree / 'base.txt').write_text('resolved\n')
        self.git('add', '.', tree=tree)
        self.git('commit', '-m', 'resolve existing merge', tree=tree)
        jobs['chat']['state'] = 'answered'
        self.releases.advance(release['id'])
        result = self.releases.get(release['id'])
        self.assertEqual(result['results'][result['revision']['members'][1]['task_id']]['state'], 'incorporated')

    def test_revocation_stops_queued_automatic_dispatch_and_budget_prevents_agent_loops(self):
        jobs = self.agent()
        release = self.conflict(auto_repair=True, profile_id='agent', max_repairs=1)
        self.releases.advance(release['id'])
        stopped = self.action('assembly', release, enabled=False)
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['state'], 'blocked')
        self.service.store.action.assert_not_called()
        self.action('assembly', stopped, enabled=True, auto_repair=True, profile_id='agent', max_repairs=1)
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertIn('budget exhausted', current['assembly_notice'])
        self.assertEqual(current['continuous']['repairs_used'], 1)

    def test_run_budget_and_selection_race_prevent_automatic_unbounded_rebuilds(self):
        identifier = self.task()
        release = self.plan(identifier, max_runs=1)
        checked = self.checked(release)
        newer = self.task('newer')
        self.tasks[identifier].update(head_sha=self.tasks[newer]['head_sha'], builds=self.tasks[newer]['builds'])
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['revision']['digest'], checked['revision']['digest'])
        self.assertIn('run budget exhausted', current['assembly_notice'])
        self.action('assembly', current, enabled=True, max_runs=2)
        read = self.service.task_records
        count = []
        def race(identifiers, db=None):
            if db is not None:
                self.tasks[identifier]['builds'].clear()
            count.append(True)
            return read(identifiers, db)
        with patch.object(self.service, 'task_records', side_effect=race):
            self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['generation'], 1)

    def test_configuration_requires_admin_and_protected_inputs_remain_blocked(self):
        self.service.store.snapshot.return_value = dict(profiles=[])
        release = self.action('create', repository='repo', version='grant-check')
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.releases.action('assembly', dict(id=release['id'], request_id='no-role', enabled=True), 'operator', 'operator')
        for fields in (dict(max_runs=0), dict(max_repairs=True), dict(auto_repair=True, profile_id='missing')):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.action('assembly', release, enabled=True, **fields)
        identifier = self.task()
        release = self.action('membership', release, task_id=identifier, included=True)
        self.action('assembly', release, enabled=True)
        with patch('pr_repairs.protected_changes', return_value=['pyproject.toml']):
            self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['state'], 'collecting')
        self.assertIn('protected policy', current['assembly_notice'])

    def test_uncertain_agent_dispatch_reuses_reserved_identity_without_another_attempt(self):
        self.agent()
        release = self.conflict(auto_repair=True, profile_id='agent', max_repairs=1)
        self.releases.advance(release['id'])
        save = self.releases.save
        lost = []
        def interrupted(record, db=None):
            if record['state'] == 'repairing' and not lost:
                lost.append(True)
                raise OSError('lost dispatch checkpoint')
            return save(record, db)
        with patch.object(self.releases, 'save', side_effect=interrupted):
            self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['state'], 'repair_queued')
        self.releases.advance(release['id'])
        calls = self.service.store.action.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[1]['request_id'], calls[1].args[1]['request_id'])
        self.assertEqual(self.releases.get(release['id'])['continuous']['repairs_used'], 1)
        self.assertEqual(self.releases.get(release['id'])['state'], 'repairing')

    def test_committed_dispatch_ack_loss_does_not_downgrade_or_repeat_the_agent_job(self):
        self.agent()
        release = self.conflict(auto_repair=True, profile_id='agent', max_repairs=1)
        self.releases.advance(release['id'])
        save = self.releases.save
        def interrupted(record, db=None):
            answer = save(record, db)
            if record['state'] == 'repairing' and db is None:
                raise OSError('lost saved acknowledgement')
            return answer
        with patch.object(self.releases, 'save', side_effect=interrupted):
            self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['state'], 'repairing')
        self.releases.advance(release['id'])
        self.service.store.action.assert_called_once()

    def test_agent_selection_change_revokes_the_pending_old_agent_dispatch(self):
        self.agent()
        release = self.conflict(auto_repair=True, profile_id='agent', max_repairs=2)
        self.releases.advance(release['id'])
        self.service.store.snapshot.return_value['profiles'].append(
            dict(id='other', organization_id='org', project=str(self.repo)))
        self.action('assembly', release, enabled=True, auto_repair=True, profile_id='other')
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['state'], 'blocked')
        self.service.store.action.assert_not_called()

    def test_upstream_only_protected_drift_is_preserved_when_old_task_is_assembled(self):
        identifier = self.task('old-code')
        approved = '[project]\nrequires-python = ">=3.12"\n'
        (self.repo / 'pyproject.toml').write_text(approved)
        self.git('add', '.')
        self.git('commit', '-m', 'approved upstream policy')
        newer_base = self.git('rev-parse', 'HEAD')
        self.protected.stop()  # Exercise the actual protected-input comparison.
        release = self.plan(identifier)
        with patch('contributions.task_base', return_value=('refs/remotes/origin/main', newer_base)):
            checked = self.checked(release)
        self.assertEqual(self.git('show', checked['resulting_sha'] + ':pyproject.toml'), approved.strip())
        self.assertEqual(checked['revision']['members'][0]['source_boundary'], self.base)

    def test_revising_partial_assembly_preserves_the_full_selected_scope(self):
        first, second = self.task('first'), self.task('pending')
        self.tasks[second]['builds'].clear()
        release = self.plan(first, second)
        checked = self.checked(release)
        revised = self.action('revise', checked)
        self.assertEqual([m['task_id'] for m in revised['members']], [first, second])
        self.assertNotIn('continuous', revised)

    def test_agent_reply_with_wrong_commit_parents_stays_blocked(self):
        jobs = self.agent()
        release = self.conflict(auto_repair=True, profile_id='agent', max_repairs=2)
        self.releases.advance(release['id'])
        self.releases.advance(release['id'])
        tree = self.releases.worktree(self.releases.get(release['id']))
        self.git('merge', '--abort', tree=tree)
        self.git('commit', '--allow-empty', '-m', 'wrong result', tree=tree)
        jobs['chat']['state'] = 'answered'
        self.releases.advance(release['id'])
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['state'], 'blocked')
        self.assertIn('Unexpected integration HEAD', current['error'])
        self.assertEqual(current['continuous']['repairs_used'], 1)
        self.service.store.action.assert_called_once()

    def test_http_assembly_authorization_requires_admin_and_is_explicit(self):
        import threading
        from types import SimpleNamespace
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        import server
        release = self.action('create', repository='repo', version='http-assembly')
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'admin-secret'
        gateway.operators = SimpleNamespace(identify=lambda token: dict(name='operator', role='admin' if token == 'admin-secret' else 'operator'))
        self.service.releases = self.releases
        gateway.contributions = self.service
        self.addCleanup(gateway.server_close)
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(gateway.shutdown)
        endpoint = 'http://127.0.0.1:' + str(gateway.server_port) + '/api/releases/assembly'
        body = dict(id=release['id'], edit_version=release['edit_version'], request_id='http-enable', enabled=True)
        with self.assertRaises(HTTPError) as denied:
            urlopen(Request(endpoint, data=json.dumps(body).encode(), headers={'Authorization': 'Bearer reader'}))
        self.assertEqual(denied.exception.code, 400)
        denied.exception.close()
        self.assertNotIn('continuous', self.releases.get(release['id']))
        with urlopen(Request(endpoint, data=json.dumps(body).encode(), headers={'Authorization': 'Bearer admin-secret'})) as response:
            enabled = json.load(response)
        self.assertTrue(enabled['continuous']['enabled'])
        self.assertEqual(enabled['state'], 'collecting')
        self.assertNotIn('revision', enabled)
