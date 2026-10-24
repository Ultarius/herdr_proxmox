"""Protected publication cannot turn upstream read/PR access into Git writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from github_api import GitHub, GitHubError
import contributions
from repository_connections import personal_token
# unittest discovery loads these modules directly; module-qualified local runs
# load them inside the tests namespace. Support both without requiring cwd.
if __package__:
    from . import test_contributions as task_fixtures
    from . import test_release_coordination as release_fixtures
else:
    import test_contributions as task_fixtures
    import test_release_coordination as release_fixtures

UPSTREAM = 'SourceOwner/project'
FORK = 'ForkOwner/project'
PUSH = 'github_pat_' + 'p' * 40
PR = 'github_pat_' + 'r' * 40
SHA = 'a' * 40

class PolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        policy = self.root / 'policy.json'
        policy.write_text(json.dumps(dict(version=1, protected=[dict(upstream=UPSTREAM, fork=FORK, mode='fork_only', arrangement='manual_adoption')])), encoding='utf-8')
        self.api = GitHub(self.root / 'github.json', policy_path=policy)
        self.body = dict(token=PUSH, pr_token=PR, repositories=[UPSTREAM],
                         publication={UPSTREAM: dict(mode='fork', publishing_repository=FORK)})

    def response(self, path, method='GET', body=None, token=None, **kwargs):
        self.assertEqual(method, 'GET', 'Setup must not write to GitHub')
        if path == '/user':
            return dict(login='upstream-user' if token == PR else 'fork-user')
        if path == '/repos/' + UPSTREAM:
            self.assertEqual(token, PR)
            return dict(full_name=UPSTREAM, default_branch='main', permissions=dict(push=False))
        if path == '/repos/' + FORK:
            self.assertEqual(token, PUSH)
            return dict(full_name=FORK, default_branch='main', fork=True,
                        source=dict(full_name=UPSTREAM), permissions=dict(push=True))
        if path == '/repos/' + UPSTREAM + '/branches/main':
            self.assertEqual(token, PR)
            return dict(commit=dict(sha=SHA))
        if path == '/repos/' + FORK + '/branches/main':
            self.assertEqual(token, PR)
            raise GitHubError(404)
        raise AssertionError(path)

    def configure(self):
        with patch.object(self.api, 'request', side_effect=self.response) as request:
            result = self.api.configure(self.body)
        return result, request

    def test_private_fork_probe_degrades_to_manual_without_probing_a_future_branch(self):
        result, request = self.configure()
        entry = result['publication'][UPSTREAM]
        self.assertEqual(entry['arrangement'], 'manual_adoption')
        self.assertEqual(entry['capabilities'][FORK], 'branch_unavailable')
        self.assertFalse(entry['allow_direct'])
        self.assertEqual(self.api.publication_options()[UPSTREAM], [FORK])
        self.assertEqual(self.api.pr_credential(UPSTREAM).token, PR)
        self.assertTrue(all('release-' not in call.args[0] for call in request.call_args_list))
        self.assertNotIn(PR, json.dumps(result))
        self.assertNotIn(PUSH, json.dumps(result))
        for name in (UPSTREAM, UPSTREAM.lower()):
            with self.assertRaisesRegex(ValueError, 'never a push'):
                self.api.allow_publishing(UPSTREAM, name)
        self.api.allow_publishing(UPSTREAM, FORK)

    def test_setup_requires_two_distinct_fine_grained_credentials(self):
        for changes in (dict(token='ghp_' + 'x' * 40), dict(pr_token='ghu_' + 'x' * 40),
                        dict(pr_token=None), dict(pr_token=PUSH)):
            with self.subTest(changes=changes), patch.object(self.api, 'request') as request:
                with self.assertRaises(ValueError):
                    self.api.configure(dict(self.body, **changes))
                request.assert_not_called()
        self.assertEqual(personal_token('ghp_' + 'x' * 40), 'ghp_' + 'x' * 40)

    def test_mutable_setup_cannot_change_policy_or_remap_a_protected_upstream(self):
        before = self.api.policy_path.read_bytes()
        for extra in (dict(protected_publication={}), dict(publication={UPSTREAM: dict(mode='direct')}),
                      dict(publication={UPSTREAM: dict(mode='fork', publishing_repository='other/repo')}),
                      dict(repositories=['other/repo'], publication={'other/repo': dict(mode='fork', publishing_repository=UPSTREAM)}),
                      dict(repositories=['other/repo'], publication={'other/repo': dict(mode='fork', publishing_repository=FORK)})):
            with self.subTest(extra=extra), patch.object(self.api, 'request', side_effect=self.response):
                with self.assertRaises(ValueError):
                    self.api.configure(dict(self.body, **extra))
        self.assertEqual(self.api.policy_path.read_bytes(), before)
        self.configure()
        with patch.object(self.api, 'request', side_effect=self.response) as request:
            self.api.configure(dict(self.body, pr_token=''))
        request.assert_any_call('/user', token=PR)
        request.assert_any_call('/repos/' + FORK + '/branches/main', token=PR, credential_role='pull request token')

    def test_facades_and_final_transport_block_upstream_writes(self):
        self.configure()
        client = self.api.upstream_client(UPSTREAM)
        forbidden = [('/pulls', 'POST', {}), ('/pulls/1/merge', 'PUT', {}), ('/dispatches', 'POST', {}),
                     ('/branches/main/rename', 'POST', {}), ('/git/refs', 'POST', {}),
                     ('/contents/file', 'PUT', {}), ('/releases', 'POST', {}), ('', 'PATCH', {}),
                     ('/pulls/1', 'PATCH', {'base': 'other'})]
        with patch('github_api.build_opener') as network:
            for suffix, method, body in forbidden:
                with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                    client.request('/repos/' + UPSTREAM + suffix, method, body)
                with self.assertRaises(ValueError):
                    self.api.request('/repos/' + UPSTREAM + suffix, method, body, token=PR, credential_role='pull request token')
            with self.assertRaises(ValueError):
                self.api.fork_client(UPSTREAM, FORK).request('/repos/' + UPSTREAM)
            network.assert_not_called()
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{}'
        response.headers = {}
        with patch('github_api.build_opener', return_value=opener):
            client.request('/repos/' + UPSTREAM + '/pulls/1')
            self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'), 'Bearer ' + PR)
            self.api.fork_client(UPSTREAM, FORK).request('/repos/' + FORK)
            self.assertEqual(opener.open.call_args.args[0].get_header('Authorization'), 'Bearer ' + PUSH)

    def test_publisher_enforces_policy_before_git_and_post_publish_probe_checks_sha(self):
        self.configure()
        with patch('contributions.subprocess.run') as git:
            for destination in (UPSTREAM, 'other/repo'):
                with self.assertRaises(ValueError):
                    contributions.push(self.root, 'herdr/task-abcdefabcdef', SHA, self.api, destination, upstream=UPSTREAM)
            git.assert_not_called()
        with patch.object(self.api, 'request', return_value=dict(commit=dict(sha='b' * 40))):
            with self.assertRaisesRegex(ValueError, 'differs'):
                self.api.probe_published_head(UPSTREAM, FORK, 'herdr/task-abcdefabcdef', SHA)
        with patch.object(self.api, 'request', side_effect=GitHubError(404)):
            self.assertEqual(self.api.probe_published_head(UPSTREAM, FORK, 'branch', SHA)['state'], 'unavailable')

    def test_missing_policy_is_empty_but_present_mutable_policy_fails_closed(self):
        missing = GitHub(self.root / 'github.json', policy_path=self.root / 'missing.json')
        self.assertIsNone(missing.protected_policy(UPSTREAM))
        mutable = self.root / 'policy.json'
        mutable.write_bytes(self.api.policy_path.read_bytes())
        protected = GitHub(self.root / 'github.json', policy_path=mutable, require_root_policy=True)
        with self.assertRaisesRegex(ValueError, 'policy'):
            protected.protected_policy(UPSTREAM)

    def test_askpass_only_answers_for_the_fork_and_rejects_the_shared_store(self):
        helper = Path(__file__).parents[1] / 'web/gateway/github_askpass.py'
        path = self.root / 'push-only.json'
        path.write_text(json.dumps(dict(token=PUSH)), encoding='utf-8')
        if os.name != 'nt':
            path.chmod(0o600)
        env = dict(os.environ, HERDR_GITHUB_CONFIG=str(path), HERDR_REPOSITORY=FORK)
        def ask(repository):
            return subprocess.run([sys.executable, str(helper), f"Password for 'https://x-access-token@github.com/{repository}.git': "],
                                  env=env, capture_output=True, text=True, timeout=10)
        accepted = ask(FORK)
        self.assertEqual(accepted.returncode, 0)
        self.assertEqual(accepted.stdout.strip(), PUSH)
        rejected = ask(UPSTREAM)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(rejected.stdout, '')
        path.write_text(json.dumps(dict(token=PUSH, pr_token=PR, pr_login='upstream-user')), encoding='utf-8')
        self.assertNotEqual(ask(FORK).returncode, 0)

class ManualTaskTests(unittest.TestCase):
    setUp = task_fixtures.ContributionTests.setUp
    git = task_fixtures.ContributionTests.git
    candidate = task_fixtures.ContributionTests.candidate
    fork = task_fixtures.ContributionTests.fork
    pull_data = task_fixtures.ContributionTests.pull_data

    def published(self):
        task = self.fork(self.candidate())
        task.update(state='published', publish=dict(head_sha=task['head_sha']))
        self.service.save(task, 'publish', 'admin')
        self.api.publication_mode.return_value = 'manual_adoption'
        self.api.manual_pull.return_value = dict(state='awaiting_manual_pr', compare_url='https://github.com/owner/repo/compare/main...contributor:branch', message='Open operator draft PR')
        self.api.probe_published_head.return_value = dict(state='unavailable')
        return task

    def test_missing_operator_pr_preserves_publication_then_adopts_and_reuses_exact_pr(self):
        task = self.published()
        self.api.find_pull.return_value = []
        with patch.object(self.service, 'remote_head', return_value=task['head_sha']):
            waiting = self.service.action('pull', dict(request_id='wait', task_id=task['id']), 'admin', 'admin')
            self.assertEqual(waiting['manual_pr']['state'], 'awaiting_manual_pr')
            self.assertEqual(waiting['publish']['head_sha'], task['head_sha'])
            self.api.request.assert_not_called()
            self.api.find_pull.return_value = [self.pull_data(task)]
            adopted = self.service.action('pull', dict(request_id='adopt', task_id=task['id']), 'admin', 'admin')
            self.assertEqual(adopted['pull']['origin'], 'adopted')
            self.assertNotIn('manual_pr', adopted)
            self.assertNotIn('created with', adopted['pull_notice'])
            reused = self.service.action('pull', dict(request_id='reuse', task_id=task['id']), 'admin', 'admin')
            self.assertEqual(reused['pull']['number'], adopted['pull']['number'])
            self.api.request.assert_not_called()

    def test_invisible_pr_metadata_or_wrong_sha_is_not_adopted(self):
        task = self.published()
        self.api.find_pull.return_value = [self.pull_data(task, head=dict(ref=task['branch'], sha=task['head_sha'], repo=None))]
        with patch.object(self.service, 'remote_head', return_value=task['head_sha']):
            waiting = self.service.action('pull', dict(request_id='hidden', task_id=task['id']), 'admin', 'admin')
            self.assertIn('not verifiable', waiting['manual_pr']['message'])
            self.assertNotIn('pull', waiting)
            self.api.find_pull.return_value = [self.pull_data(task, head=dict(ref=task['branch'], sha='b' * 40, repo=dict(full_name='contributor/repo')))]
            with self.assertRaisesRegex(ValueError, 'exact published'):
                self.service.action('pull', dict(request_id='wrong-sha', task_id=task['id']), 'admin', 'admin')

    def test_adoption_skips_closed_wrong_sha_and_non_draft_prs_before_exact_candidate(self):
        task = self.published()
        closed = self.pull_data(task, number=11, state='closed')
        wrong_sha = self.pull_data(task, number=12, head=dict(ref=task['branch'], sha='b' * 40,
            repo=dict(full_name='contributor/repo')))
        non_draft = self.pull_data(task, number=13, draft=False)
        exact = self.pull_data(task, number=14)
        self.api.find_pull.return_value = [closed, wrong_sha, non_draft, exact]
        with patch.object(self.service, 'remote_head', return_value=task['head_sha']):
            adopted = self.service.action('pull', dict(request_id='adopt-exact', task_id=task['id']), 'admin', 'admin')
        self.assertEqual(adopted['pull']['number'], 14)
        self.assertEqual(adopted['pull']['head_sha'], task['head_sha'])
        self.assertEqual(adopted['pull']['origin'], 'adopted')
        self.assertNotIn('manual_pr', adopted)
        self.api.request.assert_not_called()

    def test_unavailable_upstream_pr_listing_keeps_truthful_manual_handoff(self):
        task = self.published()
        for status in (403, 404):
            with self.subTest(status=status), patch.object(self.service, 'remote_head', return_value=task['head_sha']):
                self.api.find_pull.side_effect = GitHubError(status)
                waiting = self.service.action('pull', dict(request_id='list-unavailable-' + str(status), task_id=task['id']), 'admin', 'admin')
                self.assertEqual(waiting['state'], 'published')
                self.assertEqual(waiting['publish']['head_sha'], task['head_sha'])
                self.assertEqual(waiting['manual_pr']['state'], 'awaiting_manual_pr')
                self.assertEqual(waiting['manual_pr']['listing_status'], status)
                self.assertTrue(waiting['manual_pr']['compare_url'].startswith('https://github.com/'))
                self.assertIn('Restore upstream PR read access', waiting['manual_pr']['message'])
                self.assertNotIn('pull', waiting)
        self.api.request.assert_not_called()

    def test_malformed_head_metadata_keeps_manual_handoff_without_adopting(self):
        task = self.published()
        for index, head in enumerate((None, ['invalid'], 'invalid')):
            with self.subTest(head=head), patch.object(self.service, 'remote_head', return_value=task['head_sha']):
                self.api.find_pull.return_value = [None, self.pull_data(task, head=head)]
                waiting = self.service.action('pull', dict(request_id='malformed-head-' + str(index), task_id=task['id']), 'admin', 'admin')
                self.assertEqual(waiting['state'], 'published')
                self.assertEqual(waiting['manual_pr']['state'], 'awaiting_manual_pr')
                self.assertEqual(waiting['publish']['head_sha'], task['head_sha'])
                self.assertNotIn('pull', waiting)
        self.api.request.assert_not_called()

    def test_unavailable_status_and_reviews_keep_readable_checks_and_recover(self):
        task = self.published()
        pull = self.pull_data(task)
        self.service.record_pull(task, pull)
        self.service.save(task, 'pull', 'admin')
        checks = dict(check_runs=[dict(id=7, name='CI', status='completed', conclusion='success')])
        self.api.request.side_effect = [pull, checks, GitHubError(404), GitHubError(403)]
        unavailable = self.service.action('refresh', dict(request_id='optional-evidence-unavailable', task_id=task['id']), 'admin', 'admin')
        self.assertEqual(unavailable['state'], 'pr_open')
        self.assertEqual(unavailable['checks'][0]['name'], 'CI')
        self.assertEqual(unavailable['statuses'], [])
        self.assertEqual(unavailable['reviews'], [])
        self.assertEqual(unavailable['checks_unavailable']['statuses']['github_status'], 404)
        self.assertEqual(unavailable['checks_unavailable']['reviews']['github_status'], 403)
        self.api.request.side_effect = [pull, checks, dict(statuses=[dict(context='lint', state='success')]), []]
        recovered = self.service.action('refresh', dict(request_id='optional-evidence-recovered', task_id=task['id']), 'admin', 'admin')
        self.assertEqual(recovered['checks_unavailable'], {})
        self.assertEqual(recovered['checks'][0]['name'], 'CI')
        self.assertEqual(recovered['statuses'], [dict(context='lint', state='success')])
        self.assertTrue(all(len(call.args) == 1 for call in self.api.request.call_args_list), 'Refresh may only read evidence')

    def test_unavailable_checks_or_statuses_clear_stale_delivery_success_until_real_reads_succeed(self):
        import task_delivery
        task = self.published()
        pull = self.pull_data(task, mergeable=True)
        self.service.record_pull(task, pull)
        task['delivery_workflow'] = {}
        for field, responses in (
                ('check_runs', [pull, GitHubError(403), dict(statuses=[])]),
                ('statuses', [pull, dict(check_runs=[]), GitHubError(404)])):
            with self.subTest(field=field), patch.object(self.service, 'remote_head', return_value=task['head_sha']):
                task['delivery_workflow']['github_verification'] = dict(sha=task['head_sha'], mergeable=True)
                self.api.request.side_effect = responses
                self.assertFalse(task_delivery.verify_pull(self.service, task, task['head_sha']))
                self.assertIn(field, task['checks_unavailable'])
                self.assertNotIn('github_verification', task['delivery_workflow'])
        self.api.request.side_effect = [pull, dict(check_runs=[]), dict(statuses=[])]
        with patch.object(self.service, 'remote_head', return_value=task['head_sha']):
            self.assertTrue(task_delivery.verify_pull(self.service, task, task['head_sha']))
        self.assertEqual(task['checks_unavailable'], {})
        self.assertEqual(task['delivery_workflow']['github_verification']['sha'], task['head_sha'])

    def test_unavailable_checks_preserve_pr_refresh_but_cannot_complete_delivery(self):
        import task_delivery
        task = self.published()
        self.service.record_pull(task, self.pull_data(task))
        self.service.save(task, 'pull', 'admin')
        self.api.request.side_effect = [self.pull_data(task), GitHubError(403), dict(statuses=[]), []]
        updated = self.service.action('refresh', dict(request_id='refresh', task_id=task['id']), 'admin', 'admin')
        self.assertEqual(updated['state'], 'pr_open')
        self.assertEqual(updated['checks_unavailable']['check_runs']['github_status'], 403)
        updated['delivery_workflow'] = {}
        self.api.request.side_effect = [self.pull_data(task, mergeable=True), GitHubError(404), dict(statuses=[])]
        with patch.object(self.service, 'remote_head', return_value=task['head_sha']):
            self.assertFalse(task_delivery.verify_pull(self.service, updated, task['head_sha']))
        self.assertIn('check_runs', updated['checks_unavailable'])

class ManualReleaseTests(unittest.TestCase):
    setUp = release_fixtures.ReleaseCoordinationTests.setUp
    git = release_fixtures.ReleaseCoordinationTests.git
    task = release_fixtures.ReleaseCoordinationTests.task
    action = release_fixtures.ReleaseCoordinationTests.action
    plan = release_fixtures.ReleaseCoordinationTests.plan
    checked = release_fixtures.ReleaseCoordinationTests.checked
    published = release_fixtures.ReleaseCoordinationTests.published
    queue_pull = release_fixtures.ReleaseCoordinationTests.queue_pull
    pull = release_fixtures.ReleaseCoordinationTests.pull
    github = release_fixtures.ReleaseCoordinationTests.github

    def manual_github(self, release):
        github = self.github(release)
        github.publication_mode.return_value = 'manual_adoption'
        github.manual_pull.return_value = dict(state='awaiting_manual_pr',
            compare_url='https://github.com/example/repo/compare/main...contributor:branch', message='Open draft PR')
        github.probe_published_head.return_value = dict(state='unavailable')
        return github

    def test_release_publish_immediately_exposes_manual_pr_handoff_without_republish(self):
        release = self.checked(self.plan(self.task()))
        release = self.action('freeze', release, head_sha=release['resulting_sha'])
        github = self.manual_github(release)
        self.action('publish', release, head_sha=release['resulting_sha'])
        with patch('contributions.push') as push:
            self.releases.advance(release['id'])
            current = self.releases.get(release['id'])
            self.assertEqual(current['state'], 'published')
            self.assertEqual(current['pull_request']['state'], 'awaiting_manual_pr')
            self.assertEqual(current['pull_request']['message'], 'Open draft PR')
            self.assertEqual(current['pull_request']['compare_url'], github.manual_pull.return_value['compare_url'])
            self.assertEqual(current['pull_request']['head_sha'], release['resulting_sha'])
            self.assertEqual(current['pull_request']['manifest'], release['revision']['digest'])
            self.assertEqual(current['resulting_sha'], release['resulting_sha'])
            self.assertEqual(push.call_count, 1)
            self.assertEqual(push.call_args.args[1:3], (release['branch'], release['resulting_sha']))
            self.assertEqual(push.call_args.args[4], release['publication']['destination'])
            self.assertEqual(push.call_args.kwargs['upstream'], release['publication']['upstream'])
            self.releases.advance(release['id'])
            self.assertEqual(push.call_count, 1)
        github.manual_pull.assert_called_once_with(release['publication']['upstream'], release['publication']['destination'],
            release['branch'], release['revision']['base_ref'].removeprefix('refs/remotes/origin/'))
        github.find_pull.assert_not_called()
        self.assertFalse(any(len(call.args) > 1 and call.args[1] == 'POST' for call in github.request.call_args_list))

    def test_release_adoption_skips_closed_wrong_sha_and_non_draft_prs_before_exact_candidate(self):
        release = self.published()
        github = self.manual_github(release)
        destination = release['publication']['destination']
        closed = self.pull(release, number=21, state='closed')
        wrong_sha = self.pull(release, number=22, head=dict(ref=release['branch'], sha='b' * 40,
            repo=dict(full_name=destination)))
        non_draft = self.pull(release, number=23, draft=False)
        exact = self.pull(release, number=24)
        github.find_pull.return_value = [closed, wrong_sha, non_draft, exact]
        self.queue_pull(release)
        with patch('contributions.push') as push:
            self.releases.advance(release['id'])
        current = self.releases.get(release['id'])
        self.assertEqual(current['state'], 'published')
        self.assertEqual(current['pull_request']['state'], 'complete')
        self.assertEqual(current['pull_request']['number'], 24)
        self.assertEqual(current['pull_request']['head_sha'], release['resulting_sha'])
        self.assertEqual(current['pull_request']['origin'], 'adopted')
        self.assertFalse(any(len(call.args) > 1 and call.args[1] == 'POST' for call in github.request.call_args_list))
        push.assert_not_called()

    def test_release_unavailable_pr_listing_keeps_published_manual_handoff(self):
        release = self.published()
        github = self.manual_github(release)
        for status in (403, 404):
            with self.subTest(status=status):
                github.find_pull.side_effect = GitHubError(status)
                self.queue_pull(self.releases.get(release['id']))
                with patch('contributions.push') as push:
                    self.releases.advance(release['id'])
                current = self.releases.get(release['id'])
                self.assertEqual(current['state'], 'published')
                self.assertEqual(current['pull_request']['state'], 'awaiting_manual_pr')
                self.assertEqual(current['pull_request']['listing_status'], status)
                self.assertTrue(current['pull_request']['compare_url'].startswith('https://github.com/'))
                self.assertIn('Restore read access', current['pull_request']['message'])
                self.assertEqual(current['pull_request']['head_sha'], release['resulting_sha'])
                push.assert_not_called()
        self.assertFalse(any(len(call.args) > 1 and call.args[1] == 'POST' for call in github.request.call_args_list))

    def test_release_malformed_head_metadata_keeps_published_manual_handoff(self):
        release = self.published()
        github = self.manual_github(release)
        for head in (None, ['invalid'], 'invalid'):
            with self.subTest(head=head):
                github.find_pull.return_value = [None, self.pull(release, head=head)]
                self.queue_pull(self.releases.get(release['id']))
                with patch('contributions.push') as push:
                    self.releases.advance(release['id'])
                current = self.releases.get(release['id'])
                self.assertEqual(current['state'], 'published')
                self.assertEqual(current['pull_request']['state'], 'awaiting_manual_pr')
                self.assertEqual(current['pull_request']['head_sha'], release['resulting_sha'])
                self.assertNotIn('number', current['pull_request'])
                push.assert_not_called()
        self.assertFalse(any(len(call.args) > 1 and call.args[1] == 'POST' for call in github.request.call_args_list))

    def test_release_manual_handoff_and_adoption_never_create_or_republish(self):
        release = self.published()
        github = self.github(release)
        github.publication_mode.return_value = 'manual_adoption'
        github.manual_pull.return_value = dict(state='awaiting_manual_pr', compare_url='https://github.com/example/repo/compare/main...contributor:branch', message='Open draft PR')
        github.probe_published_head.return_value = dict(state='unavailable')
        self.queue_pull(release)
        with patch('contributions.push') as push:
            self.releases.advance(release['id'])
            waiting = self.releases.get(release['id'])
            self.assertEqual(waiting['pull_request']['state'], 'awaiting_manual_pr')
            self.assertEqual(waiting['state'], 'published')
            self.assertFalse(any(len(call.args) > 1 and call.args[1] == 'POST' for call in github.request.call_args_list))
            github.find_pull.return_value = [self.pull(release)]
            self.queue_pull(waiting)
            self.releases.advance(release['id'])
            adopted = self.releases.get(release['id'])['pull_request']
            self.assertEqual(adopted['origin'], 'adopted')
            self.assertEqual(adopted['head_sha'], release['resulting_sha'])
            push.assert_not_called()
