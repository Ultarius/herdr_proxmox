import json
from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import contributions
from contributions import Contributions, remote_info
from github_api import GitHub, GitHubError
import project_git
import server


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'github.json'
        self.api = GitHub(self.path)

    def test_configuration_never_returns_token_and_is_private(self):
        token = 'github_pat_' + 'x' * 50
        with patch.object(self.api, 'request', side_effect=[{'login': 'owner'}, {'permissions': {'push': True}}]):
            result = self.api.configure(dict(token=token, repositories=['owner/repo']))
        self.assertNotIn(token, json.dumps(result))
        self.assertEqual(result['credential_isolation'], 'shared_unix_user')
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, 'allowlisted'):
            self.api.allow('other/repo')

    def test_refuses_invalid_token_repository_and_access(self):
        with self.assertRaises(ValueError):
            self.api.configure(dict(token='secret\n', repositories=['owner/repo']))
        with self.assertRaises(ValueError):
            self.api.configure(dict(token='x' * 40, repositories=['../repo']))
        with patch.object(self.api, 'request', side_effect=[{'login': 'owner'}, {'permissions': {'push': False}}]):
            with self.assertRaisesRegex(ValueError, 'push access'):
                self.api.configure(dict(token='x' * 40, repositories=['owner/repo']))
        self.assertFalse(self.path.exists())

    def test_errors_and_oversized_responses_never_expose_credentials(self):
        secret = 'github_pat_' + 's' * 40
        opener = MagicMock()
        for status in (401, 403, 404, 409, 422, 500):
            opener.open.side_effect = HTTPError('https://api.github.com/user', status, secret, {}, None)
            with patch('github_api.build_opener', return_value=opener):
                with self.assertRaises(ValueError) as caught:
                    self.api.request('/user', token=secret)
                self.assertNotIn(secret, str(caught.exception))
        opener.open.side_effect = None
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'x' * 1_000_001
        response.headers = {}
        with patch('github_api.build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'oversized'):
                self.api.request('/user', token=secret)

    def test_etag_reuse_and_new_token_does_not_reuse_old_cache(self):
        self.path.write_text(json.dumps(dict(token='x' * 40)))
        self.path.chmod(0o600)
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"login":"owner"}'
        response.headers = {'ETag': 'tag1'}
        with patch('github_api.build_opener', return_value=opener):
            self.api.request('/user')
            opener.open.side_effect = HTTPError('', 304, '', {}, None)
            self.assertEqual(self.api.request('/user')['login'], 'owner')
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_header('If-none-match'), 'tag1')
            opener.open.side_effect = None
            self.api.request('/user', token='y' * 40)
            self.assertIsNone(opener.open.call_args.args[0].get_header('If-none-match'))


@unittest.skipUnless(shutil.which('git'), 'Git required')
class ContributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.test')
        (self.repo / 'file').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.git('remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.api = MagicMock()
        self.api.path = self.root / 'github.json'
        self.api.snapshot.return_value = dict(configured=True)
        self.profile = dict(id='worker', organization_id='org', project=str(self.repo), use_worktree=True)
        self.store = MagicMock()
        self.store.snapshot.return_value = dict(profiles=[self.profile], jobs=[])
        self.service = Contributions(self.root / 'tasks.sqlite3', self.root, self.store, self.api)
        self.body = dict(request_id='create-1', title='Fix task', description='Implement and test.',
                         repository='repo', base_ref='refs/remotes/origin/main', profile_id='worker')
        self.task = self.service.action('create', self.body, 'admin', 'admin')

    def git(self, *args, path=None):
        return subprocess.run(['git', '-C', str(path or self.repo), *args], check=True,
                              capture_output=True, text=True, timeout=15).stdout

    def candidate(self):
        tree = self.root / 'tree'
        self.git('worktree', 'add', '-b', self.task['branch'], str(tree), self.base)
        (tree / 'file').write_text('implementation\n')
        self.git('add', '.', path=tree)
        self.git('commit', '-m', 'implementation', path=tree)
        self.task.update(worktree=str(tree), run_id='run', state='implementing')
        self.service.save(self.task, 'launch', 'admin')
        self.store.snapshot.return_value['jobs'] = [dict(id='run', state='persona_sent')]
        return self.service.action('candidate', dict(request_id='candidate-1', task_id=self.task['id']), 'admin', 'admin')

    def pull_data(self, task, **changes):
        result = dict(number=7, state='open', draft=True, head=dict(ref=task['branch'], sha=task['head_sha'],
                      repo=dict(full_name='owner/repo')), base=dict(ref='main'), mergeable=None)
        result.update(changes)
        return result

    def answers(self, pull):
        """Serve one pull request plus empty checks, statuses and reviews."""
        def answer(path, *arguments, **keywords):
            if '/reviews' in path:
                return []
            if '/pulls/' in path:
                return pull
            return dict(check_runs=[], statuses=[])
        return answer

    def test_remote_parsing_rejects_tokens_untrusted_hosts_and_traversal(self):
        for url, expected in [('https://github.com/owner/repo.git', 'owner/repo'),
                              ('git@github.com:owner/repo.git', 'owner/repo')]:
            self.git('remote', 'set-url', 'origin', url)
            self.assertEqual(remote_info(self.repo), expected)
        for url in ('https://token@github.com/owner/repo', 'https://evil.test/owner/repo', 'https://github.com/../repo'):
            self.git('remote', 'set-url', 'origin', url)
            with self.assertRaises(ValueError):
                remote_info(self.repo)

    def test_create_replay_survives_restart_and_rejects_changed_selection(self):
        restarted = Contributions(self.service.path, self.root, self.store, self.api)
        self.assertEqual(restarted.action('create', self.body, 'admin', 'admin')['id'], self.task['id'])
        with self.assertRaisesRegex(ValueError, 'different content'):
            restarted.action('create', dict(self.body, title='Different'), 'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'administrator'):
            restarted.action('launch', dict(request_id='launch', task_id=self.task['id']), 'operator', 'operator')

    def test_task_captures_explicit_base_and_launch_parameters(self):
        self.store.action.return_value = dict(id='run')
        launched = self.service.action('launch', dict(request_id='launch', task_id=self.task['id']), 'admin', 'admin')
        arguments = self.store.action.call_args.args[1]
        self.assertEqual(arguments['start_sha'], self.base)
        self.assertEqual(arguments['worktree_branch'], self.task['branch'])
        prompt = arguments['task_prompt']
        self.assertIn('Task-local tool acquisition policy:', prompt)
        self.assertIn('Pin the version required by the repository', prompt)
        self.assertIn('Agents currently share the', prompt)
        self.assertIn('A check that never ran remains `not_run`', prompt)
        self.assertNotIn('## Merge procedure', prompt)
        self.assertIn('Never push', arguments['task_prompt'])
        self.assertEqual(project_git.configured_base(self.repo), self.task['base_ref'])
        self.assertEqual(launched['state'], 'implementing')
        self.service.action('launch', dict(request_id='again', task_id=self.task['id']), 'admin', 'admin')
        self.assertEqual(self.store.action.call_count, 1)

    def test_task_prompt_budget_accommodates_the_shared_tool_policy(self):
        # The launch channel caps task_prompt. A maximum-length description plus
        # the shared policy must fit, or a task could be created and never launch.
        root = Path(__file__).parents[1]
        source = (root / 'web/gateway/organizations.py').read_text()
        cap = int(re.search(r"task_prompt', (\d+)", source).group(1))
        self.assertGreater(cap, 8000 + len(contributions.task_tool_guidance()) + 500)

    def test_tool_policy_extraction_reports_an_inconsistent_installation(self):
        with patch.object(contributions.Path, 'read_text', return_value='# incomplete skill'):
            with self.assertRaisesRegex(ValueError, 'missing its tool policy section'):
                contributions.task_tool_guidance()

    def test_candidate_preserves_main_and_blocks_dirty_or_changed_checkout(self):
        task = self.candidate()
        self.assertEqual(task['state'], 'review_ready')
        self.assertIn('implementation', task['diff'])
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base)
        (Path(task['worktree']) / 'untracked').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'Commit task changes'):
            self.service.action('candidate', dict(request_id='dirty', task_id=task['id']), 'admin', 'admin')
        self.assertTrue((Path(task['worktree']) / 'untracked').exists())

    def test_publish_lost_response_reconciles_without_second_push(self):
        task = self.candidate()
        body = dict(request_id='publish', task_id=task['id'], head_sha=task['head_sha'])
        with patch.object(self.service, 'remote_head', side_effect=[None, task['head_sha']]), patch('contributions.push') as push:
            published = self.service.action('publish', body, 'admin', 'admin')
        push.assert_called_once()
        self.assertEqual(published['publish']['head_sha'], task['head_sha'])
        replay = self.service.action('publish', body, 'admin', 'admin')
        self.assertEqual(replay['id'], published['id'])
        self.assertEqual(replay['publish'], published['publish'])
        # A replay never has to re-store the diff or build evidence.
        self.assertNotIn('diff', replay)
        self.assertNotIn('builds', replay)
        # Pending durable request after process termination: remote is already
        # at the reviewed SHA, so reconciliation must not push again.
        with closing(self.service.connect()) as db, db:
            db.execute('UPDATE requests SET data=? WHERE id=?', (json.dumps(dict(state='pending', task_id=task['id'])), 'publish'))
        restarted = Contributions(self.service.path, self.root, self.store, self.api)
        with patch.object(restarted, 'remote_head', return_value=task['head_sha']), patch('contributions.push') as push:
            restarted.action('publish', body, 'admin', 'admin')
            push.assert_not_called()

    def test_publish_rejection_and_moved_head_never_force_or_claim_success(self):
        task = self.candidate()
        with patch.object(self.service, 'remote_head', return_value=None), patch('contributions.push', side_effect=ValueError('Push rejected')):
            with self.assertRaisesRegex(ValueError, 'rejected'):
                self.service.action('publish', dict(request_id='reject', task_id=task['id'], head_sha=task['head_sha']), 'admin', 'admin')
        self.assertNotIn('publish', self.service.get(task['id']))
        with self.assertRaisesRegex(ValueError, 'exact candidate'):
            self.service.action('publish', dict(request_id='moved', task_id=task['id'], head_sha='a' * 40), 'admin', 'admin')

    def test_pull_reuse_and_duplicate_creation_preserve_draft_evidence(self):
        task = self.candidate()
        task['publish'] = dict(head_sha=task['head_sha'])
        self.service.save(task, 'publish', 'admin')
        pull = self.pull_data(task)
        self.api.find_pull.side_effect = [[], [pull]]
        self.api.request.side_effect = GitHubError(422)
        with patch.object(self.service, 'remote_head', return_value=task['head_sha']):
            result = self.service.action('pull', dict(request_id='pull', task_id=task['id']), 'admin', 'admin')
        self.assertEqual(result['pull']['number'], 7)
        payload = self.api.request.call_args.args[2]
        self.assertTrue(payload['draft'])
        self.assertIn('not verified', payload['body'])
        self.assertEqual(payload['base'], 'main')

    def test_pr_changed_head_and_resulting_merge_sha_are_distinct(self):
        task = self.candidate()
        pull = self.pull_data(task, head=dict(ref=task['branch'], sha='a' * 40, repo=dict(full_name='owner/repo')))
        self.service.record_pull(task, pull)
        self.assertTrue(task['candidate_changed'])
        pull.update(merged=True, merge_commit_sha='b' * 40, state='closed')
        self.service.record_pull(task, pull)
        self.assertEqual(task['merge_sha'], 'b' * 40)
        self.assertNotEqual(task['head_sha'], task['merge_sha'])

    def test_build_uses_merged_result_and_retains_exact_target_evidence(self):
        task = self.candidate()
        task.update(state='merged', merge_sha=self.base)
        self.service.save(task, 'merged', 'github')
        self.service.validation = MagicMock()
        self.service.validation.submit.return_value = dict(id='build', state='queued')
        result = self.service.action('build', dict(request_id='build', task_id=task['id'], target=self.base), 'admin', 'admin')
        event_id = self.service.validation.submit.call_args.args[0]['id']
        self.assertEqual(self.service.build_event(event_id)['target'], self.base)
        self.assertNotEqual(self.service.build_event(event_id)['target'], task['head_sha'])
        self.service.record_build(event_id, dict(run_id='build', target=self.base, state='complete', checks=[dict(id='test', status='passed')]))
        self.assertEqual(self.service.get(task['id'])['builds'][self.base]['state'], 'complete')
        # Evidence for another commit is consumed and recorded on the task.
        # Raising here is swallowed by feedback delivery and retried forever.
        self.assertTrue(self.service.record_build(event_id, dict(target=task['head_sha'])))
        mismatched = self.service.get(task['id'])
        self.assertIn('does not match', mismatched['error'])
        self.assertNotIn(task['head_sha'], mismatched['builds'])

    def test_background_poll_survives_storage_faults_and_never_publishes(self):
        task = self.candidate()
        self.service.record_pull(task, self.pull_data(task))
        self.service.save(task, 'pull', 'admin')
        self.api.request.side_effect = self.answers(self.pull_data(task))
        with patch.object(Contributions, 'snapshot', side_effect=sqlite3.OperationalError('database is locked')):
            self.assertFalse(self.service.poll_once())
        with patch.object(Contributions, 'perform', side_effect=sqlite3.OperationalError('database is locked')):
            self.assertTrue(self.service.poll_once())
        self.assertIn('locked', self.service.get(task['id'])['error'])
        self.api.request.assert_not_called()
        # The next cycle clears the recorded failure once GitHub answers again.
        self.assertTrue(self.service.poll_once())
        recovered = self.service.get(task['id'])
        self.assertNotIn('error', recovered)
        self.assertEqual(recovered['state'], 'pr_open')

    def test_malformed_check_records_are_reported_instead_of_crashing_polling(self):
        task = self.candidate()
        self.service.record_pull(task, self.pull_data(task))
        self.service.save(task, 'pull', 'admin')
        self.api.request.side_effect = [self.pull_data(task), {'check_runs': [None]}, {'statuses': []}, []]
        self.assertTrue(self.service.poll_once())
        self.assertIn('invalid check evidence record', self.service.get(task['id'])['error'])
        self.api.request.side_effect = self.answers(self.pull_data(task))
        self.assertTrue(self.service.poll_once())
        self.assertNotIn('error', self.service.get(task['id']))

    def test_unchanged_refresh_keeps_audit_bounded_and_records_nothing(self):
        task = self.candidate()
        self.service.record_pull(task, self.pull_data(task))
        self.service.save(task, 'pull', 'admin')
        self.api.request.side_effect = self.answers(self.pull_data(task))
        first = self.service.action('refresh', dict(request_id='refresh-1', task_id=task['id']), 'admin', 'admin')
        audit = len(self.service.get(task['id'])['audit'])
        for index in range(5):
            again = self.service.action('refresh', dict(request_id='refresh-' + str(index + 2), task_id=task['id']),
                                       'admin', 'admin')
            self.assertEqual(again['updated_at'], first['updated_at'])
        self.assertEqual(len(self.service.get(task['id'])['audit']), audit)
        with closing(self.service.connect()) as db:
            rows = db.execute('SELECT COUNT(*) FROM audit WHERE task_id=?', (task['id'],)).fetchone()[0]
        self.assertLessEqual(rows, 500)
        # A changed pull request is still recorded.
        self.api.request.side_effect = self.answers(
            self.pull_data(task, merged=True, merge_commit_sha=self.base, state='closed'))
        self.service.action('refresh', dict(request_id='refresh-merge', task_id=task['id']), 'admin', 'admin')
        self.assertEqual(self.service.get(task['id'])['state'], 'merged')

    def test_gateway_distinguishes_expired_github_credentials_from_dashboard_auth(self):
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'a' * 48
        gateway.github = self.api
        gateway.contributions = self.service
        gateway.operators = SimpleNamespace(identify=lambda token: None)
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(gateway.server_port)
        try:
            task = self.candidate()
            self.service.record_pull(task, self.pull_data(task))
            self.service.save(task, 'pull', 'admin')
            self.api.request.side_effect = GitHubError(401)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + '/api/tasks/refresh', headers={'Authorization': 'Bearer ' + gateway.token},
                                data=json.dumps(dict(request_id='expired', task_id=task['id'])).encode()))
            self.assertEqual(error.exception.code, 502)
            self.assertEqual(json.load(error.exception)['github_status'], 401)
            self.assertIn('invalid or expired', self.service.get(task['id'])['error'])
            self.assertEqual(urlopen(Request(base + '/api/tasks', headers={'Authorization': 'Bearer ' + gateway.token})).status, 200)
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=2)

    def test_a_pull_request_merged_on_github_is_never_reported_as_closed(self):
        # The pull request list reports merged_at and omits the resulting commit;
        # a contribution merged on GitHub must still be recognised as merged.
        task = self.candidate()
        task['publish'] = dict(state='complete', head_sha=task['head_sha'])
        self.service.save(task, 'publish', 'admin')
        listed = self.pull_data(task, merged_at='2026-01-01T00:00:00Z', state='closed')
        self.assertNotIn('merged', listed)
        self.api.request.side_effect = [
            dict(object=dict(sha=task['head_sha'])),
            self.pull_data(task, merged=True, merge_commit_sha=self.base, state='closed'),
        ]
        self.api.find_pull.return_value = [listed]
        self.service.action('pull', dict(request_id='pull-merged', task_id=task['id']), 'admin', 'admin')
        # The resulting commit is only available from the single pull request.
        self.assertEqual(self.api.request.call_args.args[0].count('/pulls/'), 1)
        saved = self.service.get(task['id'])
        self.assertEqual(saved['state'], 'merged')
        self.assertEqual(saved['merge_sha'], self.base)
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.service.action('publish', dict(request_id='again', task_id=task['id'],
                                                 head_sha=task['head_sha']), 'admin', 'admin')

    def test_merged_pull_request_without_a_resulting_commit_is_reported_honestly(self):
        task = self.candidate()
        self.service.record_pull(task, self.pull_data(task))
        self.service.save(task, 'pull', 'admin')
        merged = self.pull_data(task, merged=True, merge_commit_sha=None, state='closed')
        self.api.request.side_effect = [merged, merged, dict(check_runs=[]), dict(statuses=[]), []]
        self.service.action('refresh', dict(request_id='refresh-1', task_id=task['id']), 'admin', 'admin')
        saved = self.service.get(task['id'])
        self.assertEqual(saved['state'], 'merged')
        self.assertNotIn('merge_sha', saved)
        self.assertIn('without a usable resulting commit', saved['error'])
        with self.assertRaisesRegex(ValueError, 'Capture a candidate'):
            self.service.action('build', dict(request_id='build', task_id=task['id'],
                                              target=task['head_sha']), 'admin', 'admin')

    def test_pull_creation_refuses_an_unexpected_pull_request_list(self):
        task = self.candidate()
        task['publish'] = dict(state='complete', head_sha=task['head_sha'])
        self.service.save(task, 'publish', 'admin')
        self.api.request.return_value = dict(object=dict(sha=task['head_sha']))
        self.api.find_pull.return_value = dict(message='not a list')
        with self.assertRaisesRegex(ValueError, 'invalid pull request list'):
            self.service.action('pull', dict(request_id='pull', task_id=task['id']), 'admin', 'admin')
        self.assertNotIn('pull', self.service.get(task['id']))

    def test_publish_refuses_a_published_branch_that_moved_on_the_remote(self):
        task = self.candidate()
        with patch.object(self.service, 'remote_head', side_effect=[None, task['head_sha']]), \
                patch('contributions.push'):
            task = self.service.action('publish', dict(request_id='first', task_id=task['id'],
                                                       head_sha=task['head_sha']), 'admin', 'admin')
        self.assertEqual(task['state'], 'published')
        with patch.object(self.service, 'remote_head', return_value='b' * 40), patch('contributions.push') as push:
            with self.assertRaisesRegex(ValueError, 'moved on the remote'):
                self.service.action('publish', dict(request_id='second', task_id=task['id'],
                                                     head_sha=task['head_sha']), 'admin', 'admin')
        push.assert_not_called()
        # Publishing the same candidate again never regresses a task that has a PR.
        task.update(pull=self.pull_data(task))
        self.service.save(task, 'pull', 'admin')
        with patch.object(self.service, 'remote_head', return_value=task['head_sha']):
            again = self.service.action('publish', dict(request_id='third', task_id=task['id'],
                                                         head_sha=task['head_sha']), 'admin', 'admin')
        self.assertEqual(again['state'], 'pr_open')
        self.assertFalse(again['candidate_changed'])

    def test_a_task_owned_by_an_operator_action_is_skipped_by_polling(self):
        task = self.candidate()
        self.service.record_pull(task, self.pull_data(task))
        self.service.save(task, 'pull', 'admin')
        self.api.request.side_effect = self.answers(self.pull_data(task))
        holding, release = threading.Event(), threading.Event()

        def hold():
            with self.service.operation('task:' + task['id']):
                holding.set()
                release.wait(timeout=10)

        worker = threading.Thread(target=hold, daemon=True)
        worker.start()
        self.assertTrue(holding.wait(timeout=5))
        try:
            with self.assertRaises(contributions.Busy):
                with self.service.operation('task:' + task['id'], timeout=0.2):
                    pass
            with patch.object(contributions, 'POLL_WAIT_SECONDS', 0.2):
                self.assertTrue(self.service.poll_once())
        finally:
            release.set()
            worker.join(timeout=5)
        self.api.request.assert_not_called()
        self.assertNotIn('error', self.service.get(task['id']))

    def test_push_refuses_other_branches_and_partial_commits(self):
        task = self.candidate()
        for branch, sha in [('main', task['head_sha']), (task['branch'], task['head_sha'][:12]),
                            ('herdr/task-abcdefabcdef', task['head_sha'] + '0')]:
            with self.assertRaisesRegex(ValueError, 'task branch and full reviewed commit'):
                contributions.push(Path(task['worktree']), branch, sha, self.api)
        self.api.allow.assert_not_called()

    def test_push_releases_the_repository_lock_before_the_network_call(self):
        task = self.candidate()
        remote = self.root / 'remote-lock.git'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        real_run = subprocess.run
        held = []

        @contextmanager
        def tracked_lock(*arguments, **keywords):
            held.append('held')
            try:
                yield
            finally:
                held.append('released')

        def run(arguments, **kwargs):
            arguments = [str(remote) if arg == 'https://github.com/owner/repo.git' else arg for arg in arguments]
            if 'push' in arguments:
                # Every other gateway Git operation must be able to run here.
                self.assertEqual(held.count('held'), held.count('released'))
            return real_run(arguments, **kwargs)

        with patch.object(contributions.sys, 'platform', 'linux'), patch.object(contributions, 'repository_lock', tracked_lock), \
                patch.object(contributions.subprocess, 'run', side_effect=run):
            contributions.push(Path(task['worktree']), task['branch'], task['head_sha'], self.api)
        self.assertEqual(held.count('held'), held.count('released'))


    def test_merge_identity_survives_unavailable_check_permissions(self):
        task = self.candidate()
        self.service.record_pull(task, self.pull_data(task))
        self.service.save(task, 'pull', 'admin')
        merged = self.pull_data(task, merged=True, merge_commit_sha=self.base, state='closed')
        self.api.request.side_effect = [merged, GitHubError(403)]
        with self.assertRaises(GitHubError):
            self.service.action('refresh', dict(request_id='refresh', task_id=task['id']), 'admin', 'admin')
        saved = self.service.get(task['id'])
        self.assertEqual(saved['merge_sha'], self.base)
        self.assertEqual(saved['state'], 'merged')
        self.assertIn('denied', saved['error'])

    def test_gateway_routes_authenticate_and_enforce_administrator(self):
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'a' * 48
        gateway.github = self.api
        gateway.contributions = self.service
        gateway.operators = SimpleNamespace(identify=lambda token: dict(name='viewer', role='operator') if token == 'viewer' else None)
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(gateway.server_port)
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(base + '/api/tasks')
            self.assertEqual(error.exception.code, 401)
            response = json.load(urlopen(Request(base + '/api/tasks', headers={'Authorization': 'Bearer ' + gateway.token})))
            self.assertEqual(response['tasks'][0]['id'], self.task['id'])
            self.api.snapshot.return_value = dict(configured=True, login='publisher', repositories=['owner/repo'])
            operator_headers = {'Authorization': 'Bearer viewer'}
            visible = json.load(urlopen(Request(base + '/api/tasks', headers=operator_headers)))
            self.assertEqual(visible['tasks'][0]['description'], self.task['description'])
            self.assertEqual(visible['tasks'][0]['base_sha'], self.base)
            self.assertEqual(visible['github'], {'configured': True})
            detail = json.load(urlopen(Request(base + '/api/tasks/detail?id=' + self.task['id'], headers=operator_headers)))
            self.assertEqual(detail['title'], self.task['title'])
            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(base + '/api/github', headers=operator_headers))
            self.assertEqual(denied.exception.code, 403)
            configured = json.load(urlopen(Request(base + '/api/github', headers={'Authorization': 'Bearer ' + gateway.token})))
            self.assertEqual(configured['login'], 'publisher')
            for endpoint, method in (('/api/tasks', 'snapshot'), ('/api/tasks/detail?id=' + self.task['id'], 'detail')):
                with patch.object(self.service, method, side_effect=sqlite3.OperationalError('database is locked')):
                    with self.assertRaises(HTTPError) as unavailable:
                        urlopen(Request(base + endpoint, headers=operator_headers))
                    self.assertEqual(unavailable.exception.code, 503)
            for endpoint in ('tasks/launch', 'github/configure'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/' + endpoint, headers={'Authorization': 'Bearer viewer'},
                                    data=json.dumps(dict(request_id='denied', task_id=self.task['id'])).encode()))
                self.assertEqual(error.exception.code, 400)
            self.api.configure.assert_not_called()
            self.store.action.assert_not_called()
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=2)

    def test_push_isolated_config_exact_sha_no_force_and_local_bare_rejection(self):
        task = self.candidate()
        remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
        real_run = subprocess.run
        calls = []
        def run(arguments, **kwargs):
            calls.append((list(arguments), dict(kwargs.get('env', {}))))
            arguments = [str(remote) if arg == 'https://github.com/owner/repo.git' else arg for arg in arguments]
            result = real_run(arguments, **kwargs)
            return result
        with patch.object(contributions.sys, 'platform', 'linux'), patch.object(contributions.subprocess, 'run', side_effect=run):
            contributions.push(Path(task['worktree']), task['branch'], task['head_sha'], self.api)
            # A rewind to the original base is non-fast-forward and rejected.
            with self.assertRaisesRegex(ValueError, 'rejected'):
                contributions.push(Path(task['worktree']), task['branch'], self.base, self.api)
        pushed = [args for args, _ in calls if 'push' in args]
        self.assertEqual(len(pushed), 2)
        self.assertIn('--no-force', pushed[0])
        self.assertIn(task['head_sha'] + ':refs/heads/' + task['branch'], pushed[0])
        self.assertTrue(any(arg.startswith('--git-dir=') for arg in pushed[0]))
        for args, env in calls:
            self.assertNotIn('token=', ' '.join(args))
            if 'push' in args:
                self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
