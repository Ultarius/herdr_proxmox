"""Exact-evidence group reviews and recoverable release PR effects, with real Git."""
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
sys.path.insert(0, str(Path(__file__).parent))
import test_release_candidates as fixtures
import test_release_assembly as assembly
from contributions import Contributions
from github_api import GitHubCredential
from release_candidates import ReleaseCandidates
import release_coordination


class ReleaseCoordinationTests(unittest.TestCase):
    setUp = fixtures.ReleaseCandidateTests.setUp
    git = fixtures.ReleaseCandidateTests.git
    task = fixtures.ReleaseCandidateTests.task
    action = fixtures.ReleaseCandidateTests.action
    plan = assembly.ContinuousAssemblyTests.plan
    checked = assembly.ContinuousAssemblyTests.checked

    def reviewable(self):
        identifier = self.task()
        self.tasks[identifier].update(organization_id='org', profile_id='worker', run_id='launch')
        self.service.store.snapshot.return_value = dict(groups=[dict(id='group', name='Release reviewers',
            organization_id='org', members=['reviewer'])], profiles=[dict(id='reviewer', organization_id='org', project=str(self.repo))])
        release = self.action('create', repository='repo', version='review')
        release = self.action('membership', release, task_id=identifier, included=True)
        return identifier, release

    def reviewed_job(self, **extra):
        return dict(id='discussion', kind='discussion', purpose='release_review', group_id='group',
                    organization_id='org', state='artifact_ready', result='Cite evidence; capture and validate missing tasks.',
                    prompt=self.service.store.action.call_args.args[1]['prompt'] if self.service.store.action.called else '', **extra)

    def published(self):
        identifier = self.task()
        release = self.checked(self.plan(identifier))
        release = self.action('freeze', release, head_sha=release['resulting_sha'])
        self.action('publish', release, head_sha=release['resulting_sha'])
        with patch('contributions.push'):
            self.releases.advance(release['id'])
        return self.releases.get(release['id'])

    def queue_pull(self, release, **changes):
        body = dict(head_sha=release['resulting_sha'], reviewed=True, **release_coordination.pull_preview(release))
        body.update(changes)
        return self.action('pull', release, **body)

    def pull(self, release, **changes):
        publication = release['publication']
        result = dict(number=15, state='open', draft=True,
            head=dict(ref=release['branch'], sha=release['resulting_sha'], repo=dict(full_name=publication['destination'])),
            base=dict(ref='main', repo=dict(full_name=publication['upstream'])))
        result.update(changes)
        return result

    def github(self, release, *, lost=False):
        github = self.service.github
        github.find_pull.return_value = []
        github.pr_credential.return_value = GitHubCredential('pr-token', 'upstream-owner', 'pull request token')
        def request(path, method='GET', body=None, **extra):
            if method == 'POST':
                if lost:
                    github.find_pull.return_value = [self.pull(release)]
                    raise OSError('response lost')
                return self.pull(release)
            return dict(object=dict(sha=release['resulting_sha']))
        github.request.side_effect = request
        return github

    def test_live_task_owner_is_visible_without_claiming_incorporation(self):
        identifier, release = self.reviewable()
        self.tasks[identifier]['state'] = 'implementing'
        presented = self.releases.present(copy.deepcopy(self.releases.get(release['id'])))
        self.assertEqual(presented['members'][0]['work'], dict(profile_id='worker', run_id='launch',
            head_sha=self.tasks[identifier]['head_sha'], state='implementing'))
        self.assertEqual(presented['counts']['incorporated'], 0)
        self.assertNotIn('work', self.releases.get(release['id'])['members'][0])
        guidance = self.releases.task_guidance(self.tasks[identifier])
        self.assertIn('review (' + release['id'] + ')', guidance)
        self.assertIn('not authorization', guidance)
        self.action('membership', release, task_id=identifier, included=False)
        self.assertEqual(self.releases.task_guidance(self.tasks[identifier]), '')

    def test_review_reservation_replays_and_does_not_dispatch_on_the_request_path(self):
        _, release = self.reviewable()
        body = dict(id=release['id'], edit_version=release['edit_version'], request_id='review-once', group_id='group')
        queued = self.releases.action('review', body, 'admin', 'admin')
        self.assertEqual(queued, self.releases.action('review', body, 'admin', 'admin'))
        self.assertEqual(queued['review']['state'], 'queued')
        self.assertEqual(queued['state'], 'collecting')
        self.service.store.action.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.releases.action('review', dict(body, request_id='denied'), 'reader', 'operator')
        with self.assertRaisesRegex(ValueError, 'already queued'):
            self.action('review', queued, group_id='group')

    def test_empty_collecting_release_cannot_queue_a_discussion(self):
        release = self.action('create', repository='repo', version='empty-review')
        with self.assertRaisesRegex(ValueError, 'Select at least one task'):
            self.action('review', release, group_id='group')
        self.assertNotIn('review', self.releases.get(release['id']))
        self.service.store.snapshot.assert_not_called()
        self.service.store.action.assert_not_called()

    def test_wrong_repository_or_organization_group_is_refused_before_dispatch(self):
        identifier, release = self.reviewable()
        directory = self.service.store.snapshot.return_value
        directory['profiles'][0]['project'] = str(self.repo.parent)
        with self.assertRaisesRegex(ValueError, 'repository'):
            self.action('review', release, group_id='group')
        directory['profiles'][0]['project'] = str(self.repo)
        self.tasks[identifier]['organization_id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'share an organization'):
            self.action('review', release, group_id='group')
        self.service.store.action.assert_not_called()

    def test_restart_reconciles_lost_discussion_response_with_same_identity(self):
        _, release = self.reviewable()
        self.action('review', release, group_id='group')
        self.service.store.action.side_effect = [OSError('lost reply'), dict(id='discussion')]
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['review']['state'], 'queued')
        self.releases = ReleaseCandidates(self.service)
        self.releases.advance(release['id'])
        calls = self.service.store.action.call_args_list
        self.assertEqual(calls[0].args, calls[1].args)
        self.assertEqual(calls[0].args[1]['purpose'], 'release_review')
        self.assertNotIn('revision', self.releases.get(release['id']))
        self.service.store.job_record.return_value = self.reviewed_job()
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['review']['state'], 'complete')
        self.assertEqual(current['state'], 'collecting')
        self.assertNotIn('validation', current)

    def test_new_task_evidence_stales_review_but_task_activity_does_not(self):
        identifier, release = self.reviewable()
        self.action('review', release, group_id='group')
        self.service.store.action.return_value = dict(id='discussion')
        self.releases.advance(release['id'])
        self.service.store.job_record.return_value = self.reviewed_job()
        self.releases.advance(release['id'])
        self.tasks[identifier]['state'] = 'implementing'
        current = self.releases.present(copy.deepcopy(self.releases.get(release['id'])))
        self.assertTrue(current['review']['current'])
        sha = self.tasks[identifier]['head_sha']
        self.tasks[identifier]['builds'][sha]['policy_digest'] = 'different'
        current = self.releases.present(copy.deepcopy(self.releases.get(release['id'])))
        self.assertFalse(current['review']['current'])
        self.assertEqual(current['review']['report'], self.reviewed_job()['result'])

    def test_changed_evidence_before_dispatch_and_mismatched_job_fail_closed(self):
        identifier, release = self.reviewable()
        self.action('review', release, group_id='group')
        self.tasks[identifier]['head_sha'] = 'f' * 40
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['review']['state'], 'stale')
        self.service.store.action.assert_not_called()
        self.action('review', release, group_id='group')
        self.service.store.action.return_value = dict(id='discussion')
        self.releases.advance(release['id'])
        self.service.store.job_record.return_value = dict(self.reviewed_job(), group_id='unrelated')
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['review']['state'], 'needs_attention')

    def test_review_artifact_never_becomes_an_automatic_proposal_or_release_plan(self):
        job = dict(self.reviewed_job(), result=json.dumps(dict(release_plan=dict(name='bad', task_refs=['one']),
                                                              task_proposals=[dict(ref='one')])))
        self.assertEqual(Contributions.discussion_artifact(job), {})
        self.assertEqual(Contributions.discussion_proposals(job), [])

    def test_review_prompt_bounds_and_selected_commit_not_advanced_head(self):
        identifier, release = self.reviewable()
        selected = self.tasks[identifier]['head_sha']
        self.tasks[identifier]['head_sha'] = 'f' * 40
        self.action('review', release, group_id='group')
        release = self.releases.get(release['id'])
        prompt = release_coordination.review_prompt(release, release['review'])
        self.assertIn(selected + ' head-changed', prompt)
        self.assertNotIn('f' * 40, prompt)
        record = release['review']['packet']['members'][0]
        release['review']['packet']['members'] = [dict(record, task_id=str(i).zfill(32)) for i in range(100)]
        self.assertLessEqual(len(release_coordination.review_prompt(release, release['review'])), 8000)

    def test_draft_pr_requires_explicit_exact_commit_approval_and_does_not_publish_again(self):
        release = self.published()
        with self.assertRaisesRegex(ValueError, 'review'):
            self.queue_pull(release, reviewed=False)
        with self.assertRaisesRegex(ValueError, 'exact'):
            self.queue_pull(release, head_sha='f' * 40)
        github = self.github(release)
        queued = self.queue_pull(release)
        self.assertEqual(queued['pull_request']['state'], 'queued')
        with self.assertRaisesRegex(ValueError, 'PR operation'):
            self.action('revise', queued)
        github.request.assert_not_called()
        with patch('contributions.push') as push:
            self.releases.advance(release['id'])
        push.assert_not_called()
        current = self.releases.get(release['id'])
        self.assertEqual(current['state'], 'published')
        self.assertEqual(current['pull_request']['url'], 'https://github.com/example/repo/pull/15')
        payload = next(c for c in github.request.call_args_list if len(c.args) > 1 and c.args[1] == 'POST').args[2]
        self.assertTrue(payload['draft'])
        self.assertEqual(payload['head'], 'contributor:' + release['branch'])
        self.assertEqual(payload['base'], 'main')

    def test_release_pr_keeps_single_token_source_metadata(self):
        release = self.published()
        github = self.github(release)
        github.pr_credential.return_value = GitHubCredential('push-token', 'contributor', 'push token')
        self.queue_pull(release)
        self.releases.advance(release['id'])
        post = next(call for call in github.request.call_args_list if len(call.args) > 1 and call.args[1] == 'POST')
        self.assertEqual(post.kwargs, {'token': 'push-token', 'credential_role': 'push token'})
        self.assertEqual(self.releases.get(release['id'])['pull_request']['state'], 'complete')

    def test_pr_validation_reason_survives_release_failure(self):
        self.rejected_pull(already_exists=False)

    def test_validation_failure_still_reconciles_an_existing_pr(self):
        self.rejected_pull(already_exists=True)

    def rejected_pull(self, *, already_exists):
        from github_api import GitHubError
        release = self.published()
        github = self.github(release)
        calls = []
        def rejected(path, method='GET', body=None, **extra):
            if method == 'POST':
                calls.append(body)
                if already_exists:
                    github.find_pull.return_value = [self.pull(release)]
                raise GitHubError(422, repository=release['publication']['upstream'],
                    operation='Create draft pull request', credential_role='pull request token',
                    validation=('GitHub could not accept the head branch or fork.',))
            return dict(object=dict(sha=release['resulting_sha']))
        github.request.side_effect = rejected
        self.queue_pull(release)
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['state'], 'published')
        self.assertEqual(len(calls), 1)
        if already_exists:
            self.assertEqual(current['pull_request']['state'], 'complete')
            self.assertTrue(current['pull_request']['reused'])
        else:
            self.assertEqual(current['pull_request']['state'], 'needs_attention')
            self.assertIn('head branch or fork', current['pull_request']['error'])
            self.assertIn('GitHub rejected', current['pull_request']['error'])
            self.assertIn('pull request token.', current['pull_request']['error'])

    def test_lost_pr_response_reconciles_existing_exact_head_after_restart(self):
        release = self.published()
        github = self.github(release, lost=True)
        self.queue_pull(release)
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['pull_request']['state'], 'needs_attention')
        self.releases = ReleaseCandidates(self.service)
        self.queue_pull(release)
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['pull_request']['state'], 'complete')
        self.assertTrue(current['pull_request']['reused'])
        self.assertEqual(sum(len(c.args) > 1 and c.args[1] == 'POST' for c in github.request.call_args_list), 1)

    def test_policy_or_remote_head_drift_refuses_pr_without_reopening_assembly(self):
        release = self.published()
        github = self.github(release)
        self.queue_pull(release)
        with patch('toolchain_policy.resolve_committed', return_value=dict(fixtures.POLICY, checks=['changed'])):
            self.releases.advance(release['id'])
        github.request.assert_not_called()
        self.assertEqual(self.releases.get(release['id'])['state'], 'published')
        self.queue_pull(release)
        github.request.side_effect = None
        github.request.return_value = dict(object=dict(sha='f' * 40))
        self.releases.advance(release['id'])
        self.assertIn('does not match', self.releases.get(release['id'])['pull_request']['error'])
        github.find_pull.assert_not_called()

    def test_existing_pr_with_foreign_or_stale_head_is_not_adopted(self):
        release = self.published()
        github = self.github(release)
        stale = self.pull(release)
        stale['head']['sha'] = 'e' * 40
        github.find_pull.return_value = [stale]
        self.queue_pull(release)
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['pull_request']['state'], 'needs_attention')
        self.assertEqual(len(github.request.call_args_list), 1)  # Remote ref read, no POST.

    def test_completed_pr_save_acknowledgement_loss_does_not_downgrade_receipt(self):
        release = self.published()
        self.github(release)
        self.queue_pull(release)
        save = self.releases.save
        def lost(record, db=None):
            save(record, db)
            if db is None and (record.get('pull_request') or {}).get('state') == 'complete':
                raise OSError('lost saved acknowledgement')
        with patch.object(self.releases, 'save', side_effect=lost):
            self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['pull_request']['state'], 'complete')

    def test_uncertain_discussion_retry_preserves_packet_and_dispatch_identity(self):
        _, release = self.reviewable()
        self.action('review', release, group_id='group')
        self.service.store.action.side_effect = ValueError('uncertain response')
        self.releases.advance(release['id'])
        original = self.releases.get(release['id'])['review']
        self.assertEqual(original['state'], 'needs_attention')
        with self.assertRaisesRegex(ValueError, 'already queued'):
            self.action('review', release, group_id='group')
        self.action('review', release, retry=True)
        self.service.store.action.side_effect = None
        self.service.store.action.return_value = dict(id='discussion')
        self.releases.advance(release['id'])
        review = self.releases.get(release['id'])['review']
        self.assertEqual(review['state'], 'running')
        self.assertEqual(review['digest'], original['digest'])
        self.assertEqual(self.service.store.action.call_args_list[0].args,
                         self.service.store.action.call_args_list[1].args)

    def test_review_and_pull_http_routes_require_administrator(self):
        import threading
        from types import SimpleNamespace
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        import server
        _, release = self.reviewable()
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'admin-secret'
        gateway.operators = SimpleNamespace(identify=lambda token: dict(name='operator', role='admin' if token == 'admin-secret' else 'operator'))
        self.service.releases = self.releases
        gateway.contributions = self.service
        self.addCleanup(gateway.server_close)
        threading.Thread(target=gateway.serve_forever, daemon=True).start()
        self.addCleanup(gateway.shutdown)
        endpoint = 'http://127.0.0.1:' + str(gateway.server_port) + '/api/releases/'
        body = dict(id=release['id'], edit_version=release['edit_version'], request_id='http-review', group_id='group')
        for action in ('review', 'review_policy', 'pull'):
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(endpoint + action, data=json.dumps(body).encode(), headers={'Authorization': 'Bearer reader'}))
            self.assertEqual(denied.exception.code, 400)
            denied.exception.close()
        with urlopen(Request(endpoint + 'review', data=json.dumps(body).encode(), headers={'Authorization': 'Bearer admin-secret'})) as response:
            self.assertEqual(json.load(response)['review']['state'], 'queued')
        self.service.store.action.assert_not_called()

    def auto_reviewable(self, limit=3):
        identifier, release = self.reviewable()
        self.action('review_policy', release, enabled=True, group_id='group', max_reviews=limit)
        release = self.action('assembly', release, enabled=True)
        return identifier, self.checked(release)

    def test_automatic_review_reserves_once_then_dispatches_once_after_complete_assembly(self):
        _, release = self.auto_reviewable()
        self.releases.wake.clear()
        self.releases.advance(release['id'])
        self.assertTrue(self.releases.wake.is_set())
        reserved = self.releases.get(release['id'])
        self.assertEqual(reserved['review']['state'], 'queued')
        self.assertEqual(reserved['review_policy']['reviews_used'], 1)
        self.assertEqual(reserved['state'], 'assembled')
        self.service.store.action.assert_not_called()
        self.service.store.action.return_value = dict(id='discussion')
        self.releases = ReleaseCandidates(self.service)
        self.releases.advance(release['id'])
        self.service.store.job_record.return_value = self.reviewed_job()
        self.releases.advance(release['id'])
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.service.store.action.assert_called_once()
        self.assertEqual(current['review_policy']['reviews_used'], 1)
        self.assertEqual(current['review']['state'], 'complete')
        self.assertEqual(current['state'], 'assembled')  # No freeze, publish or PR effect.

    def test_automatic_review_waits_for_delivery_inspection(self):
        identifier, release = self.auto_reviewable()
        task = self.tasks[identifier]
        task.update(await_verification=True, delivery_workflow=dict(sha=task['head_sha'], state='waiting_agent'))
        self.releases.advance(release['id'])
        waiting = self.releases.get(release['id'])
        self.assertNotIn('review', waiting)
        self.assertIn('delivery inspections', waiting['review_policy']['notice'])
        task['delivery_workflow']['state'] = 'complete'
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['review_policy']['reviews_used'], 1)

    def test_delivery_evidence_regression_before_dispatch_stales_reserved_review(self):
        identifier, release = self.auto_reviewable()
        self.releases.advance(release['id'])
        self.tasks[identifier]['await_verification'] = True
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['review']['state'], 'stale')
        self.assertEqual(current['review_policy']['reviews_used'], 1)
        self.service.store.action.assert_not_called()

    def test_source_check_regression_prevents_automatic_review(self):
        identifier, release = self.auto_reviewable()
        self.tasks[identifier]['builds'][self.tasks[identifier]['head_sha']]['state'] = 'failed'
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertNotIn('review', current)
        self.service.store.action.assert_not_called()

    def test_automatic_review_refuses_a_partial_assembly(self):
        identifier, release = self.reviewable()
        pending = self.task('pending')
        self.tasks[pending]['organization_id'] = 'org'
        self.tasks[pending]['builds'].clear()
        self.action('membership', release, task_id=pending, included=True)
        self.action('review_policy', release, enabled=True, group_id='group', max_reviews=3)
        release = self.action('assembly', release, enabled=True)
        checked = self.checked(release)
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(len(checked['revision']['members']), 1)
        self.assertNotIn('review', current)
        self.assertIn('every selected task', current['review_policy']['notice'])
        self.service.store.action.assert_not_called()

    def test_revoked_automatic_review_is_not_dispatched_and_reenable_does_not_reset_budget(self):
        _, release = self.auto_reviewable(limit=1)
        self.releases.advance(release['id'])
        self.action('review_policy', release, enabled=False)
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['review']['state'], 'cancelled')
        self.action('review_policy', current, enabled=True, group_id='group', max_reviews=1)
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['review_policy']['reviews_used'], 1)
        self.service.store.action.assert_not_called()

    def test_automatic_review_budget_survives_new_assembly_and_revision(self):
        identifier, release = self.auto_reviewable(limit=1)
        self.releases.advance(release['id'])
        self.service.store.action.return_value = dict(id='discussion')
        self.releases.advance(release['id'])
        self.service.store.job_record.return_value = self.reviewed_job()
        self.releases.advance(release['id'])
        newer = self.task('newer')
        self.tasks[identifier].update(head_sha=self.tasks[newer]['head_sha'], builds=self.tasks[newer]['builds'])
        checked = self.checked(release)
        self.assertEqual(checked['generation'], 2)
        self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertIn('budget exhausted', current['review_policy']['notice'])
        self.service.store.action.assert_called_once()
        revised = self.action('revise', current)
        self.assertEqual(revised['review_policy']['reviews_used'], 1)
        self.assertEqual(len(revised['review_policy']['reserved']), 1)

    def test_oversized_automatic_review_does_not_save_a_partial_dispatch_or_spend(self):
        _, release = self.auto_reviewable()
        with patch('release_coordination.review_prompt', side_effect=ValueError('packet too large')):
            self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertNotIn('review', current)
        self.assertEqual(current['review_policy'].get('reviews_used', 0), 0)
        self.assertIn('too large', current['review_policy']['notice'])

    def test_manual_review_of_current_evidence_satisfies_automatic_trigger_without_another_spend(self):
        _, release = self.auto_reviewable()
        self.action('review', release, group_id='group')
        self.service.store.action.return_value = dict(id='discussion')
        self.releases.advance(release['id'])
        self.assertEqual(self.releases.get(release['id'])['review_policy'].get('reviews_used', 0), 0)
        self.service.store.action.assert_called_once()

    def test_review_policy_validates_limit_and_group_and_is_not_implicit_in_assembly(self):
        _, release = self.reviewable()
        self.action('assembly', release, enabled=True)
        self.assertNotIn('review_policy', self.releases.get(release['id']))
        for limit in (0, True, 21):
            with self.assertRaisesRegex(ValueError, 'review limit'):
                self.action('review_policy', release, enabled=True, group_id='group', max_reviews=limit)
        with self.assertRaisesRegex(ValueError, 'review group'):
            self.action('review_policy', release, enabled=True, group_id='unknown', max_reviews=1)

    def test_automatic_review_cannot_be_enabled_after_publication(self):
        release = self.published()
        with self.assertRaisesRegex(ValueError, 'before publication'):
            self.action('review_policy', release, enabled=True, group_id='group', max_reviews=1)
        self.service.store.action.assert_not_called()
