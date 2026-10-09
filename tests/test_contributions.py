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


class TaskBaseTests(unittest.TestCase):
    @patch('project_git.configured_base', return_value=None)
    @patch('project_git.git')
    def test_origin_default_is_resolved_to_explicit_branch(self, git, configured):
        git.side_effect = ['refs/remotes/origin/develop', '', 'a' * 40]
        self.assertEqual(contributions.task_base(Path('/repo'), ''), ('refs/remotes/origin/develop', 'a' * 40))

    @patch('project_git.git')
    def test_missing_explicit_base_has_actionable_error(self, git):
        git.side_effect = ['', ValueError('Git information unavailable')]
        with self.assertRaisesRegex(ValueError, 'Fetch remote updates'):
            contributions.task_base(Path('/repo'), 'refs/remotes/origin/main')

    @patch('project_git.configured_base', return_value='refs/remotes/origin/trunk')
    @patch('project_git.git')
    def test_configured_base_precedes_the_origin_default(self, git, configured):
        git.side_effect = ['', 'b' * 40]
        self.assertEqual(contributions.task_base(Path('/repo'), ' '),
                         ('refs/remotes/origin/trunk', 'b' * 40))

    @patch('project_git.configured_base', return_value=None)
    @patch('project_git.git')
    def test_ambiguous_default_branches_require_an_explicit_choice(self, git, configured):
        # origin/HEAD is unset and both main and master exist: do not guess.
        git.side_effect = [ValueError('no origin head'), '', '']
        with self.assertRaisesRegex(ValueError, 'explicit origin base branch'):
            contributions.task_base(Path('/repo'), '')


class PatchExportTests(unittest.TestCase):
    def test_binary_patch_is_exact_and_applies_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()
            git('init')
            (root / 'text.txt').write_text('base')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'base')
            base = git('rev-parse', 'HEAD')
            (root / 'binary.dat').write_bytes(b'\x00binary\xff')
            (root / 'text.txt').write_text('candidate')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'candidate')
            head = git('rev-parse', 'HEAD')
            service = object.__new__(Contributions)
            service.get = MagicMock(return_value=dict(base_sha=base, head_sha=head))
            service.path_for = MagicMock(return_value=root)
            content, filename = service.patch('task')
            self.assertIn(b'GIT binary patch', content)
            self.assertIn(base.encode(), content)
            self.assertIn(head.encode(), content)
            self.assertTrue(filename.endswith('.patch'))
            self.assertEqual(git('status', '--porcelain'), '')
            with self.assertRaisesRegex(ValueError, 'not truncated'):
                service.patch('task', limit=1)
            git('checkout', '--detach', base)
            patchfile = root.parent / (root.name + '.patch')
            try:
                patchfile.write_bytes(content)
                git('apply', str(patchfile))
                self.assertEqual((root / 'text.txt').read_text(), 'candidate')
                self.assertEqual((root / 'binary.dat').read_bytes(), b'\x00binary\xff')
            finally:
                patchfile.unlink(missing_ok=True)


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

    def test_automatic_receipt_capture_and_build_are_exact_and_deduplicated(self):
        task = self.candidate()
        task.update(state='implementing', auto_validate=True)
        task.pop('head_sha')
        self.service.save(task, 'policy', 'admin')
        run = self.store.snapshot.return_value['jobs'][0]
        run.update(profile_id='worker', completion_token='fresh-token')
        tree = Path(task['worktree'])
        # Put the receipt exclusion in Git metadata, keeping the worktree clean.
        exclude = self.repo / '.git/info/exclude'
        exclude.write_text('/.ci-cache/\n')
        receipt = tree / '.ci-cache/herdr-guidance' / ('task-' + task['id']) / 'receipt.json'
        receipt.parent.mkdir(parents=True)
        head = self.git('rev-parse', 'HEAD', path=tree).strip()
        receipt.write_text(json.dumps(dict(outcome='complete', commit=head, run_id='run', token='fresh-token', tests=[])))
        self.service.validation = MagicMock()
        self.service.validation.snapshot.return_value = {'executor': 'service'}
        self.service.validation.submit.return_value = dict(id='build1', state='queued', checks=[])
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
            self.service.advance_automatic()
        captured = self.service.get(task['id'])
        self.assertEqual(captured['head_sha'], head)
        self.assertEqual(captured['completion_receipt']['commit'], head)
        self.assertEqual(captured['builds'][head]['state'], 'queued')
        self.assertEqual(self.service.validation.submit.call_count, 1)
        self.assertTrue(captured['commit_graph'])
        # A failed result does not produce an automatic retry.
        captured['builds'][head]['state'] = 'failed'
        self.service.save(captured, 'build_result', 'runner')
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
        self.assertEqual(self.service.validation.submit.call_count, 1)

    def test_automatic_receipt_rejects_stale_token_and_missing_service(self):
        task = self.candidate()
        task.update(auto_validate=True, state='implementing')
        task.pop('head_sha')
        self.service.save(task, 'policy', 'admin')
        run = self.store.snapshot.return_value['jobs'][0]
        run.update(profile_id='worker', completion_token='new-session')
        tree = Path(task['worktree'])
        (self.repo / '.git/info/exclude').write_text('/.ci-cache/\n')
        receipt = tree / '.ci-cache/herdr-guidance' / ('task-' + task['id']) / 'receipt.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps(dict(outcome='complete', commit=self.git('rev-parse', 'HEAD', path=tree).strip(), run_id='run', token='old-session', tests=[])))
        self.service.validation = MagicMock()
        self.service.validation.snapshot.return_value = {'executor': 'service'}
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
        self.assertIn('does not match', self.service.get(task['id'])['automation_error'])
        self.service.validation.submit.assert_not_called()
        # A receipt from the right session still cannot validate another SHA.
        receipt.write_text(json.dumps(dict(outcome='complete', commit=self.base, run_id='run', token='new-session', tests=[])))
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
        self.assertIn('does not match', self.service.get(task['id'])['automation_error'])
        self.service.validation.submit.assert_not_called()
        # Matching completion does not override an uncommitted worktree.
        receipt.write_text(json.dumps(dict(outcome='complete', commit=self.git('rev-parse', 'HEAD', path=tree).strip(), run_id='run', token='new-session', tests=[])))
        (tree / 'file').write_text('uncommitted')
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
        self.assertNotIn('head_sha', self.service.get(task['id']))
        self.service.validation.submit.assert_not_called()
        self.service.validation.snapshot.return_value = {'executor': 'gateway'}
        with self.assertRaisesRegex(ValueError, 'durable build service'):
            self.service.action('policy', dict(request_id='policy-guard', task_id=task['id'], auto_validate=True), 'admin', 'admin')

    def test_detail_loads_legacy_history_without_recapture_or_write(self):
        task = self.candidate()
        task.pop('commit_graph', None)
        self.service.save(task, 'legacy', 'admin')
        before = self.service.get(task['id'])
        self.store.snapshot.return_value['jobs'][0].update(kind='launch', profile_id='worker',
            profile=dict(name='Maya at launch', role='Frontend developer', runtime='opencode'),
            completion_token='private-token', session_history=[dict(alias='old-session', pane_id='w1:p1', session_closed_at='yesterday', secret='hidden')])
        self.profile['name'] = 'Renamed current profile'
        detail = self.service.detail(task['id'])
        self.assertEqual(detail['participants'][0]['name'], 'Maya at launch')
        self.assertEqual(detail['commit_graph'][0]['sha'], task['head_sha'])
        self.assertEqual(detail['commit_graph'][0]['parents'], [self.base])
        self.assertEqual(detail['commit_graph'][0]['subject'], 'implementation')
        self.assertEqual(detail['commit_graph'][-1]['sha'], self.base)
        self.assertNotIn('completion_token', detail['sessions'][0])
        self.assertNotIn('secret', detail['sessions'][0]['history'][0])
        self.assertEqual(self.service.get(task['id']), before)
        change = self.service.commit_detail(task['id'], task['head_sha'])
        self.assertIn('+implementation', change['diff'])
        self.assertEqual(change['comparison'], 'first parent')
        with self.assertRaisesRegex(ValueError, 'task history'):
            self.service.commit_detail(task['id'], 'f' * 40)
        self.assertEqual(self.service.get(task['id']), before)

    def test_detail_history_preserves_both_merge_parents(self):
        task = self.candidate()
        (self.repo / 'upstream').write_text('upstream changes')
        self.git('add', '.')
        self.git('commit', '-m', 'upstream update')
        upstream = self.git('rev-parse', 'HEAD').strip()
        self.git('merge', '--no-ff', 'main', '-m', 'merge upstream', path=task['worktree'])
        merged = self.service.action('candidate', dict(request_id='merged-candidate', task_id=task['id']), 'admin', 'admin')
        history = self.service.detail(task['id'])['commit_graph']
        self.assertEqual(history[0]['sha'], merged['head_sha'])
        self.assertEqual(history[0]['parents'], [task['head_sha'], upstream])
        self.assertEqual(history[0]['subject'], 'merge upstream')
        self.assertEqual(self.service.commit_detail(task['id'], merged['head_sha'])['comparison'], 'first parent')

    def test_detail_keeps_persisted_participation_when_profile_changes(self):
        task = self.candidate()
        task['participants'] = [dict(run_id='run', profile_id='worker', name='Original Maya', role='Frontend', provenance='task_launch')]
        self.service.save(task, 'participation', 'admin')
        self.store.snapshot.return_value['jobs'][0].update(profile=dict(name='New name'))
        detail = self.service.detail(task['id'])
        self.assertEqual(len(detail['participants']), 1)
        self.assertEqual(detail['participants'][0]['name'], 'Original Maya')

    def test_reviewed_completion_is_explicit_pinned_and_does_not_claim_validation(self):
        task = self.candidate()
        body = dict(request_id='complete-1', task_id=task['id'], head_sha=task['head_sha'], upstream_sha=self.base,
                    outcome='incorporated_elsewhere', reason='Reviewed task changes already included by other upstream work.', inspected=True)
        with self.assertRaisesRegex(ValueError, 'Review the exact'):
            self.service.action('complete', dict(body, request_id='no-inspection', inspected=False), 'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'Fetched base changed'):
            self.service.action('complete', dict(body, request_id='stale-base', upstream_sha='f' * 40), 'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.service.action('complete', body, 'viewer', 'operator')
        completed = self.service.action('complete', body, 'admin', 'admin')
        self.assertEqual(completed['state'], 'completed')
        self.assertEqual(completed['completion']['validation'], 'not_verified')
        self.assertEqual(completed['completion']['candidate'], task['head_sha'])
        self.assertEqual(self.service.action('complete', body, 'admin', 'admin')['state'], 'completed')
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.service.action('publish', dict(request_id='after-done', task_id=task['id'], head_sha=task['head_sha']), 'admin', 'admin')

    def test_automatic_completion_requires_exact_tree_and_verified_checks(self):
        task = self.candidate()
        head = task['head_sha']
        task['builds'] = {head: dict(target=head, state='complete', required_checks_verified=False)}
        self.service.save(task, 'build_result', 'runner')
        self.git('update-ref', 'refs/remotes/origin/main', head)
        self.service.reconcile_completed()
        self.assertEqual(self.service.get(task['id'])['state'], 'review_ready')
        task['builds'][head]['required_checks_verified'] = True
        self.service.save(task, 'build_result', 'runner')
        self.git('update-ref', 'refs/remotes/origin/main', self.base)
        self.service.reconcile_completed()
        self.assertEqual(self.service.get(task['id'])['state'], 'review_ready')
        self.git('update-ref', 'refs/remotes/origin/main', head)
        self.service.reconcile_completed()
        completed = self.service.get(task['id'])
        self.assertEqual(completed['state'], 'completed')
        self.assertEqual(completed['completion']['validation'], 'verified')
        before = completed['audit']
        self.service.reconcile_completed()
        self.assertEqual(self.service.get(task['id'])['audit'], before)

    def test_group_review_produces_deduplicated_draft_proposals_without_launch(self):
        task = self.candidate()
        group = dict(id='group', organization_id='org', name='Project Review')
        self.store.snapshot.return_value['groups'] = [group]
        self.store.action.return_value = dict(id='meeting', state='queued')
        body = dict(task_id=task['id'], request_id='review-1', group_id='group')
        reviewed = self.service.action('discuss', body, 'admin', 'admin')
        prompt = self.store.action.call_args.args[1]['prompt']
        self.assertIn('task_proposals', prompt)
        self.assertIn('Read-only discussion', prompt)
        self.assertEqual(reviewed['meetings'][0]['job_id'], 'meeting')
        task = self.service.get(task['id'])
        task.update(auto_review=True, review_group_id='group')
        self.service.save(task, 'review_policy', 'admin')
        count = self.store.action.call_count
        self.service.advance_reviews()
        self.assertEqual(self.store.action.call_count, count)  # Manual review already covered this state.
        job = dict(id='meeting', organization_id='org', task_id=task['id'], kind='discussion', state='artifact_ready',
                   result='```json\n' + json.dumps({'task_proposals': [dict(title='Add status coverage', description='Add a regression check. Acceptance: filters compose. Run widget tests.', profile_id='worker')]}) + '\n```')
        self.store.snapshot.return_value['jobs'].append(job)
        proposal = self.service.detail(task['id'])['meeting_results'][0]['proposals'][0]
        created = self.service.action('proposal', dict(task_id=task['id'], request_id='approve-proposal', meeting_id='meeting', proposal_key=proposal['key']), 'admin', 'admin')
        follow_id = created['follow_up_tasks'][proposal['key']]
        self.assertEqual(self.service.get(follow_id)['state'], 'draft')
        self.assertEqual(self.service.get(follow_id)['source']['meeting_id'], 'meeting')
        self.service.action('proposal', dict(task_id=task['id'], request_id='approve-proposal-again', meeting_id='meeting', proposal_key=proposal['key']), 'admin', 'admin')
        self.assertEqual(len(self.service.snapshot()['tasks']), 2)
        self.assertEqual(self.store.action.call_count, count)
        job['state'] = 'running'
        with self.assertRaisesRegex(ValueError, 'finalized'):
            self.service.action('proposal', dict(task_id=task['id'], request_id='unfinished-proposal', meeting_id='meeting', proposal_key=proposal['key']), 'admin', 'admin')

    def test_automatic_group_review_is_opt_in_and_deduplicated(self):
        task = self.candidate()
        self.store.snapshot.return_value['groups'] = [dict(id='group', organization_id='org', name='Review')]
        self.store.action.return_value = dict(id='auto-meeting', state='queued')
        self.service.advance_reviews()
        self.store.action.assert_not_called()
        self.service.action('review_policy', dict(task_id=task['id'], request_id='enable-review', group_id='group', auto_review=True), 'admin', 'admin')
        self.service.advance_reviews()
        self.service.advance_reviews()
        self.assertEqual(self.store.action.call_count, 1)
        self.assertEqual(self.service.get(task['id'])['meetings'][0]['job_id'], 'auto-meeting')
        with self.assertRaisesRegex(ValueError, 'active group'):
            self.service.action('review_policy', dict(task_id=task['id'], request_id='wrong-group', group_id='other', auto_review=True), 'admin', 'admin')

    def test_auto_review_without_a_group_is_recorded_not_fatal(self):
        task = self.candidate()
        task.update(auto_review=True, state='review_ready')
        self.service.save(task, 'review_policy', 'admin')
        # A missing group must be a recorded waiting reason, never a poller crash.
        self.service.advance_reviews()
        self.assertIn('active review group', self.service.get(task['id'])['review_error'])

    def test_auto_review_ignores_transient_build_states_but_not_evidence(self):
        task = self.candidate()
        task['builds'] = {task['head_sha']: dict(state='queued')}
        self.service.save(task, 'build', 'admin')
        self.store.snapshot.return_value['groups'] = [dict(id='group', organization_id='org', name='Review')]
        self.store.action.return_value = dict(id='auto-meeting', state='queued')
        self.service.action('review_policy', dict(task_id=task['id'], request_id='review-1', group_id='group', auto_review=True), 'admin', 'admin')
        self.service.advance_reviews()
        count = self.store.action.call_count
        stored = self.service.get(task['id'])
        stored['builds'][stored['head_sha']]['state'] = 'running'
        self.service.save(stored, 'build', 'runner')
        self.service.advance_reviews()
        self.assertEqual(self.store.action.call_count, count)  # queued and running collapse.
        stored = self.service.get(task['id'])
        stored['builds'][stored['head_sha']].update(state='complete', required_checks_verified=False)
        self.service.save(stored, 'build_result', 'runner')
        self.service.advance_reviews()
        self.assertEqual(self.store.action.call_count, count + 1)  # Changed evidence reviews again.

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
        resource = Path(contributions.__file__).parent / 'skills/herdr-worktree-integration/references/tools.md'
        self.assertIn(resource.resolve().as_posix(), prompt)
        self.assertTrue(resource.is_file())
        self.assertNotIn(resource.read_text(encoding='utf-8').strip(), prompt)
        self.assertNotIn('## Merge procedure', prompt)
        self.assertIn('Never push', arguments['task_prompt'])
        self.assertIn('user.name=Herdr-Agent', prompt)
        self.assertIn('Never infer the operator identity', prompt)
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
        with patch.object(contributions.Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(ValueError, 'tool reference is missing'):
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

    def test_revalidating_a_deduplicated_build_keeps_its_evidence(self):
        task = self.candidate()
        self.service.validation = MagicMock()
        self.service.validation.submit.return_value = dict(id='build-1', state='queued', checks=[])
        self.service.action('build', dict(request_id='build-1', task_id=task['id'], target=task['head_sha']), 'admin', 'admin')
        stored = self.service.get(task['id'])
        stored['builds'][task['head_sha']] = dict(run_id='build-1', target=task['head_sha'], state='complete',
                                                  required_checks_verified=True, exit_code=0,
                                                  checks=[dict(id='test', status='passed')])
        self.service.save(stored, 'build_result', 'runner')
        # The durable queue deduplicates to the existing run; the recorded
        # evidence must survive instead of being replaced by the queue record.
        self.service.validation.submit.return_value = dict(id='build-1', state='complete', checks=[])
        self.service.action('build', dict(request_id='build-2', task_id=task['id'], target=task['head_sha']), 'admin', 'admin')
        again = self.service.get(task['id'])['builds'][task['head_sha']]
        self.assertTrue(again['required_checks_verified'])
        self.assertEqual(again['exit_code'], 0)
        self.assertEqual(again['checks'], [dict(id='test', status='passed')])

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
        task = self.candidate()
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
            commit_path = '/api/tasks/commit?id=' + task['id'] + '&sha=' + task['head_sha']
            with self.assertRaises(HTTPError) as unauthenticated:
                urlopen(base + commit_path)
            self.assertEqual(unauthenticated.exception.code, 401)
            commit = json.load(urlopen(Request(base + commit_path, headers=operator_headers)))
            self.assertEqual(commit['sha'], task['head_sha'])
            self.assertIn('+implementation', commit['diff'])
            with self.assertRaises(HTTPError) as unrelated:
                urlopen(Request(base + '/api/tasks/commit?id=' + task['id'] + '&sha=' + 'f' * 40, headers=operator_headers))
            self.assertEqual(unrelated.exception.code, 400)

            with self.assertRaises(HTTPError) as denied:
                urlopen(Request(base + '/api/github', headers=operator_headers))
            self.assertEqual(denied.exception.code, 403)
            configured = json.load(urlopen(Request(base + '/api/github', headers={'Authorization': 'Bearer ' + gateway.token})))
            self.assertEqual(configured['login'], 'publisher')
            for endpoint, method in (('/api/tasks', 'snapshot'), ('/api/tasks/detail?id=' + self.task['id'], 'detail'), (commit_path, 'commit_detail')):
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

    def _receipt(self, task, outcome='complete', commit=None, token='handoff-token', run_id='run', **extra):
        tree = Path(task['worktree'])
        (self.repo / '.git/info/exclude').write_text('/.ci-cache/\n')
        receipt = tree / '.ci-cache/herdr-guidance' / ('task-' + task['id']) / 'receipt.json'
        receipt.parent.mkdir(parents=True, exist_ok=True)
        head = commit or self.git('rev-parse', 'HEAD', path=tree).strip()
        receipt.write_text(json.dumps(dict(outcome=outcome, commit=head, run_id=run_id, token=token,
                                           tests=[], **extra)))
        return head

    def test_launch_hands_off_a_verified_finished_execution(self):
        task = self.candidate()
        run = self.store.snapshot.return_value['jobs'][0]
        run.update(kind='launch', profile_id='worker', task_id=task['id'], completion_token='handoff-token')
        self._receipt(task)
        successor = self.service.action('create', dict(self.body, request_id='create-2', title='Next task'), 'admin', 'admin')
        self.store.action.reset_mock()
        self.store.action.return_value = dict(id='run2')
        with patch('collaboration.current_run'):
            launched = self.service.action('launch', dict(request_id='launch-next', task_id=successor['id']), 'admin', 'admin')
        self.assertEqual(launched['run_id'], 'run2')
        self.assertEqual(launched['state'], 'implementing')
        self.assertIsNone(launched.get('handoff'))
        self.store.manage_session.assert_called_once()
        request = self.store.manage_session.call_args[0][0]
        self.assertEqual((request['mode'], request['job_id'], request['inspected']), ('finish', 'run', True))
        # The predecessor keeps its evidence and state; only its session finished.
        self.assertEqual(self.service.get(task['id'])['state'], 'review_ready')

    def test_launch_refuses_handoff_without_verified_evidence(self):
        task = self.candidate()
        self.store.snapshot.return_value['jobs'][0].update(kind='launch', profile_id='worker', task_id=task['id'], completion_token='handoff-token')
        successor = self.service.action('create', dict(self.body, request_id='create-2', title='Next task'), 'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'busy with task'):
            self.service.action('launch', dict(request_id='launch-next', task_id=successor['id']), 'admin', 'admin')
        self.store.manage_session.assert_not_called()
        self.store.action.assert_not_called()
        self.assertIsNone(self.service.get(successor['id']).get('run_id'))
        # A receipt from a different session token is also not evidence.
        self._receipt(task, token='stale-token')
        with self.assertRaisesRegex(ValueError, 'busy with task'):
            self.service.action('launch', dict(request_id='launch-next-2', task_id=successor['id']), 'admin', 'admin')

    def test_no_changes_receipt_completes_without_capture_or_build(self):
        tree = self.root / 'tree'
        self.git('worktree', 'add', '-b', self.task['branch'], str(tree), self.base)
        self.task.update(worktree=str(tree), run_id='run', state='implementing', auto_validate=True)
        self.service.save(self.task, 'launch', 'admin')
        self.store.snapshot.return_value['jobs'] = [dict(id='run', state='persona_sent', profile_id='worker', completion_token='token')]
        self._receipt(self.task, outcome='no_changes', commit=self.base, token='token',
                      reason='The behavior is already covered by the existing code.')
        self.service.validation = MagicMock()
        self.service.validation.snapshot.return_value = {'executor': 'service'}
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
        completed = self.service.get(self.task['id'])
        self.assertEqual(completed['state'], 'completed')
        self.assertEqual(completed['completion']['outcome'], 'no_changes')
        self.assertEqual(completed['completion']['validation'], 'not_applicable')
        self.service.validation.submit.assert_not_called()
        request = self.store.manage_session.call_args[0][0]
        self.assertEqual((request['mode'], request['job_id']), ('finish', 'run'))

    def test_assignment_queue_starts_in_order_when_the_agent_is_free(self):
        successor = self.service.action('create', dict(self.body, request_id='create-2', title='Next task'), 'admin', 'admin')
        queued = self.service.action('assignment', dict(request_id='queue-1', task_id=successor['id'], mode='queue'), 'admin', 'admin')
        self.assertEqual(queued['assignment']['state'], 'queued')
        self.assertEqual(queued['assignment']['position'], 1)
        self.store.action.reset_mock()
        self.store.action.return_value = dict(id='run2')
        self.service.dispatch_assignments()
        launched = self.service.get(successor['id'])
        self.assertEqual(launched['run_id'], 'run2')
        self.assertEqual(launched['state'], 'implementing')
        self.assertIsNone(launched.get('assignment'))
        self.assertEqual(self.service.queued_assignments(), [])

    def test_queue_waits_for_a_busy_agent_and_surfaces_unverified_evidence(self):
        blocker = self.service.action('create', dict(self.body, request_id='create-2', title='Blocker'), 'admin', 'admin')
        successor = self.service.action('create', dict(self.body, request_id='create-3', title='Next'), 'admin', 'admin')
        self.store.snapshot.return_value['jobs'] = [dict(id='run', kind='launch', state='persona_sent', profile_id='worker', task_id=blocker['id'])]
        self.service.action('assignment', dict(request_id='queue-1', task_id=successor['id'], mode='queue'), 'admin', 'admin')
        self.store.action.reset_mock()
        self.service.dispatch_assignments()
        self.store.action.assert_not_called()
        self.assertEqual(self.service.get(successor['id'])['assignment']['state'], 'queued')
        # A candidate without a verified receipt blocks the handoff with a precise reason.
        blocker['state'] = 'review_ready'
        self.service.save(blocker, 'candidate', 'admin')
        self.service.dispatch_assignments()
        waiting = self.service.get(successor['id'])
        self.assertIn('busy with task', waiting['assignment_error'])
        self.assertEqual(self.service.queued_assignments()[0]['task_id'], successor['id'])

    def test_assignment_move_cancel_and_draft_only(self):
        successor = self.service.action('create', dict(self.body, request_id='create-2', title='Next'), 'admin', 'admin')
        self.service.action('assignment', dict(request_id='queue-1', task_id=successor['id'], mode='queue'), 'admin', 'admin')
        moved = self.service.action('assignment', dict(request_id='move-1', task_id=successor['id'], mode='move', position=7), 'admin', 'admin')
        self.assertEqual(moved['assignment']['position'], 7)
        cancelled = self.service.action('assignment', dict(request_id='cancel-1', task_id=successor['id'], mode='cancel'), 'admin', 'admin')
        self.assertIsNone(cancelled.get('assignment'))
        self.assertEqual(self.service.queued_assignments(), [])
        started = self.candidate()
        with self.assertRaisesRegex(ValueError, 'not started'):
            self.service.action('assignment', dict(request_id='queue-2', task_id=started['id'], mode='queue'), 'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'position'):
            self.service.action('assignment', dict(request_id='move-2', task_id=successor['id'], mode='move', position=-1), 'admin', 'admin')

    def test_completed_task_cannot_handoff_after_head_changes(self):
        task = self.candidate()
        task.update(state='completed', completion={'candidate': task['head_sha']})
        self.service.save(task, 'complete', 'admin')
        run = dict(id='run', state='persona_sent')
        self.git('commit', '--allow-empty', '-m', 'later work', path=task['worktree'])
        with patch('collaboration.current_run') as current:
            ready, reason = self.service.execution_finished(task, run)
        self.assertFalse(ready)
        self.assertIn('HEAD changed', reason)
        current.assert_not_called()

    def test_no_changes_receipt_does_not_complete_a_dirty_checkout(self):
        tree = self.root / 'tree'
        self.git('worktree', 'add', '-b', self.task['branch'], str(tree), self.base)
        self.task.update(worktree=str(tree), run_id='run', state='implementing', auto_validate=True)
        self.service.save(self.task, 'launch', 'admin')
        self.store.snapshot.return_value['jobs'] = [dict(id='run', state='persona_sent', completion_token='token')]
        self._receipt(self.task, outcome='no_changes', commit=self.base, token='token', reason='Already implemented.')
        (tree / 'file').write_text('uncommitted work')
        self.service.validation = MagicMock()
        self.service.validation.snapshot.return_value = {'executor': 'service'}
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
        saved = self.service.get(self.task['id'])
        self.assertEqual(saved['state'], 'implementing')
        self.assertIn('clean checkout', saved['automation_error'])
        self.store.manage_session.assert_not_called()

    def test_finished_execution_still_allows_automatic_candidate_validation(self):
        task = self.candidate()
        task.update(auto_validate=True, completion_receipt={'commit': task['head_sha']})
        self.service.save(task, 'policy', 'admin')
        self.store.snapshot.return_value['jobs'][0]['state'] = 'finished'
        self.service.validation = MagicMock()
        self.service.validation.snapshot.return_value = {'executor': 'service'}
        self.service.validation.submit.return_value = dict(id='build1', state='queued', checks=[])
        with patch('collaboration.current_run') as live:
            self.service.advance_automatic()
        live.assert_not_called()
        self.service.validation.submit.assert_called_once()
        self.assertIn(task['head_sha'], self.service.get(task['id'])['builds'])

    def test_manual_launch_counts_pending_and_uncertain_task_reservations(self):
        self.store.snapshot.return_value['jobs'] = [
            dict(id=str(i), kind='launch', task_id='other-' + str(i), profile_id='other-' + str(i), state=state)
            for i, state in enumerate(('queued', 'running', 'needs_attention', 'error'))]
        with self.assertRaisesRegex(ValueError, 'capacity is full'):
            self.service.action('launch', dict(request_id='launch-at-limit', task_id=self.task['id']), 'admin', 'admin')
        self.store.action.assert_not_called()
        # Error launches still reserve the agent until explicitly reconciled.
        self.assertEqual(self.service.active_execution('other-3')['state'], 'error')

    def test_handoff_can_replace_a_task_execution_at_capacity(self):
        task = self.candidate()
        run = self.store.snapshot.return_value['jobs'][0]
        run.update(kind='launch', profile_id='worker', task_id=task['id'], completion_token='handoff-token')
        self._receipt(task)
        self.store.snapshot.return_value['jobs'].extend([
            dict(id=str(i), kind='launch', task_id='other-' + str(i), profile_id='other-' + str(i), state='queued')
            for i in range(3)])
        successor = self.service.action('create', dict(self.body, request_id='create-2'), 'admin', 'admin')
        self.store.action.return_value = dict(id='run2')
        with patch('collaboration.current_run'):
            result = self.service.action('launch', dict(request_id='launch-at-limit', task_id=successor['id']), 'admin', 'admin')
        self.assertEqual(result['run_id'], 'run2')
        self.store.manage_session.assert_called_once()

    def test_queue_record_and_task_update_roll_back_together(self):
        with closing(self.service.connect()) as db, db:
            db.execute("CREATE TRIGGER fail_task BEFORE INSERT ON tasks BEGIN SELECT RAISE(ABORT, 'simulated crash'); END")
        with self.assertRaises(sqlite3.Error):
            self.service.action('assignment', dict(request_id='queue-failure', task_id=self.task['id'], mode='queue'), 'admin', 'admin')
        self.assertEqual(self.service.queued_assignments(), [])
        self.assertNotIn('assignment', self.service.get(self.task['id']))

    def test_queue_cancellation_does_not_need_the_repository(self):
        self.service.action('assignment', dict(request_id='queue-1', task_id=self.task['id'], mode='queue'), 'admin', 'admin')
        self.repo.rename(self.root / 'unavailable-repo')
        cancelled = self.service.action('assignment', dict(request_id='cancel-1', task_id=self.task['id'], mode='cancel'), 'admin', 'admin')
        self.assertNotIn('assignment', cancelled)
        self.assertEqual(self.service.queued_assignments(), [])

    def test_launch_reconciles_a_reserved_session_after_task_save_failure(self):
        queued = self.service.action('assignment', dict(request_id='queue-1', task_id=self.task['id'], mode='queue'), 'admin', 'admin')
        def reserve(*args):
            job = dict(id='reserved-run', kind='launch', task_id=self.task['id'], profile_id='worker', state='queued')
            self.store.snapshot.return_value['jobs'] = [job]
            return {'id': job['id']}
        self.store.action.side_effect = reserve
        save = self.service.save
        failed = False
        def interrupted(task, action, actor):
            nonlocal failed
            if action == 'launch' and not failed:
                failed = True
                raise OSError('simulated crash after reservation')
            return save(task, action, actor)
        request = dict(request_id='launch-retry', task_id=self.task['id'])
        with patch.object(self.service, 'save', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.service.action('launch', request, 'admin', 'admin')
        self.assertEqual(len(self.service.queued_assignments()), 1)
        self.service.dispatch_assignments()
        result = self.service.get(self.task['id'])
        replay = self.service.action('launch', request, 'admin', 'admin')
        self.assertEqual(replay['run_id'], 'reserved-run')
        self.assertEqual(result['run_id'], 'reserved-run')
        self.store.action.assert_called_once()
        self.assertEqual(self.service.queued_assignments(), [])

    def test_fifo_does_not_skip_a_failed_task_for_the_same_agent(self):
        first = self.service.action('create', dict(self.body, request_id='create-2'), 'admin', 'admin')
        second = self.service.action('create', dict(self.body, request_id='create-3'), 'admin', 'admin')
        for task, request in ((first, 'queue-1'), (second, 'queue-2')):
            self.service.action('assignment', dict(request_id=request, task_id=task['id'], mode='queue'), 'admin', 'admin')
        with patch.object(self.service, 'perform', side_effect=ValueError('first launch blocked')) as dispatch:
            self.service.dispatch_assignments()
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(dispatch.call_args.args[2], first['id'])
        self.assertEqual(len(self.service.queued_assignments()), 2)

    def test_published_candidate_can_handoff_with_receipt(self):
        task = self.candidate()
        task['state'] = 'pr_open'
        self.service.save(task, 'pull', 'admin')
        run = dict(id='run', state='persona_sent', completion_token='handoff-token')
        self._receipt(task)
        with patch('collaboration.current_run'):
            ready, reason = self.service.execution_finished(task, run)
        self.assertTrue(ready, reason)

    def test_long_busy_queue_does_not_starve_another_agent(self):
        blocker = dict(self.task, state='implementing')
        self.service.save(blocker, 'launch', 'admin')
        self.store.snapshot.return_value['jobs'] = [dict(id='busy', kind='launch', profile_id='worker', task_id=blocker['id'], state='persona_sent')]
        other = dict(self.profile, id='other-worker')
        self.store.snapshot.return_value['profiles'].append(other)
        for i in range(51):
            task = dict(self.task, id=f'{i:032x}', profile_id='worker' if i < 50 else 'other-worker',
                        audit=[], assignment=dict(state='queued', position=i + 1))
            self.service.save(task, 'assignment', 'admin')
        self.store.action.return_value = dict(id='free-run')
        self.service.dispatch_assignments()
        launched = self.service.get(f'{50:032x}')
        self.assertEqual(launched['state'], 'implementing')
        self.assertEqual(launched['run_id'], 'free-run')
        self.store.action.assert_called_once()

    def test_cancelled_assignment_does_not_regain_a_late_scheduler_error(self):
        self.service.action('assignment', dict(request_id='queue-1', task_id=self.task['id'], mode='queue'), 'admin', 'admin')
        perform = self.service.perform
        def cancelled(*args):
            perform('assignment', {'mode': 'cancel'}, self.task['id'], 'admin')
            raise ValueError('late dispatch failure')
        with patch.object(self.service, 'perform', side_effect=cancelled):
            self.service.dispatch_assignments()
        task = self.service.get(self.task['id'])
        self.assertNotIn('assignment', task)
        self.assertNotIn('assignment_error', task)

    def test_terminal_reservation_blocks_relaunch_instead_of_attaching(self):
        self.store.snapshot.return_value['jobs'] = [dict(id='dead-run', kind='launch', task_id=self.task['id'],
                                                         profile_id='worker', state='finished')]
        with self.assertRaisesRegex(ValueError, 'finished or released launch reservation'):
            self.service.action('launch', dict(request_id='launch-dead', task_id=self.task['id']), 'admin', 'admin')
        self.store.action.assert_not_called()
        self.assertIsNone(self.service.get(self.task['id']).get('run_id'))

    def _meeting(self, task, proposals, job_id='meeting', state='artifact_ready', group_id='review'):
        task.setdefault('meetings', []).append(dict(job_id=job_id, group_id=group_id, group_name='Project Review',
                                                    at=contributions.stamp(), signature='a' * 20))
        self.service.save(task, 'discuss', 'admin')
        jobs = self.store.snapshot.return_value['jobs']
        jobs[:] = [j for j in jobs if j.get('id') != job_id]
        jobs.append(dict(id=job_id, kind='discussion', organization_id='org', group_id=group_id, state=state,
                         result='```json\n' + json.dumps({'task_proposals': proposals}) + '\n```'))
        return self.service.get(task['id'])

    def test_automation_policy_requires_admin_validates_and_is_snapshotted(self):
        self.store.snapshot.return_value['organizations'] = [dict(id='org')]
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.service.action('automation', dict(request_id='a-1', organization_id='org', auto_queue_proposals=True), 'operator', 'operator')
        with self.assertRaisesRegex(ValueError, 'valid organization'):
            self.service.action('automation', dict(request_id='a-2', organization_id='', auto_queue_proposals=True), 'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.service.action('automation', dict(request_id='a-3', organization_id='nope', auto_queue_proposals=True), 'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'automation limit'):
            self.service.action('automation', dict(request_id='a-4', organization_id='org', daily_cap=1000), 'admin', 'admin')
        with self.assertRaisesRegex(ValueError, 'policy value'):
            self.service.action('automation', dict(request_id='a-5', organization_id='org', paused='yes'), 'admin', 'admin')
        saved = self.service.action('automation', dict(request_id='a-6', organization_id='org', auto_queue_proposals=True,
            max_per_meeting=2, max_open_per_agent=3, max_follow_up_depth=2, daily_cap=7), 'admin', 'admin')
        self.assertEqual(saved['policy']['max_per_meeting'], 2)
        snapshot = self.service.snapshot()
        self.assertTrue(snapshot['automation']['org']['auto_queue_proposals'])
        self.assertEqual(snapshot['automation']['org']['daily_cap'], 7)
        updated = self.service.action('automation', dict(request_id='a-7', organization_id='org', paused=True), 'admin', 'admin')
        self.assertEqual(updated['policy']['max_per_meeting'], 2)
        self.assertTrue(updated['policy']['paused'])

    def test_auto_queue_materializes_qualifying_proposals_with_caps_and_depth(self):
        self.store.snapshot.return_value['organizations'] = [dict(id='org')]
        task = self.candidate()
        self.service.action('automation', dict(request_id='a-1', organization_id='org', auto_queue_proposals=True,
            max_per_meeting=1, daily_cap=5, max_follow_up_depth=1), 'admin', 'admin')
        proposals = [dict(title='Add tests', description='Cover the remaining boundary case with checks.', profile_id='worker'),
                     dict(title='Update docs', description='Document the new flag and its required checks.', profile_id='worker')]
        self._meeting(task, proposals)
        self.service.advance_proposals()
        origin = self.service.get(task['id'])
        marker = origin['auto_queue']['meeting']
        self.assertEqual(len(marker['queued']), 1)
        self.assertEqual(len(marker['skipped']), 1)
        self.assertIn('meeting limit', list(marker['skipped'].values())[0])
        created = self.service.get(origin['follow_up_tasks'][marker['queued'][0]])
        self.assertEqual(created['state'], 'draft')
        self.assertEqual(created['assignment']['state'], 'queued')
        self.assertEqual(created['source']['depth'], 1)
        self.assertEqual(created['source']['task_id'], task['id'])
        self.assertIn('auto_queued', created['source'])
        self.assertEqual(self.service.queued_assignments()[0]['task_id'], created['id'])
        # A second pass is a no-op; the marker and deterministic identity hold.
        before = len(self.service.snapshot()['tasks'])
        self.service.advance_proposals()
        self.assertEqual(len(self.service.snapshot()['tasks']), before)

    def test_auto_queue_respects_depth_pause_and_disabled_policy(self):
        self.store.snapshot.return_value['organizations'] = [dict(id='org')]
        task = self.candidate()
        task['source'] = dict(task_id='parent', depth=1)
        self.service.action('automation', dict(request_id='a-1', organization_id='org', auto_queue_proposals=True, max_follow_up_depth=1), 'admin', 'admin')
        task = self._meeting(task, [dict(title='Deep', description='Follow-up beyond the depth limit.', profile_id='worker')], job_id='deep')
        self.service.advance_proposals()
        marker = self.service.get(task['id'])['auto_queue']['deep']
        self.assertEqual(marker['queued'], [])
        self.assertIn('depth', list(marker['skipped'].values())[0])
        # Paused and disabled policies leave finalized meetings unprocessed.
        self.service.action('automation', dict(request_id='a-2', organization_id='org', paused=True), 'admin', 'admin')
        task = self._meeting(task, [dict(title='Paused', description='Must wait while automation is paused.', profile_id='worker')], job_id='paused')
        self.service.advance_proposals()
        self.assertNotIn('paused', self.service.get(task['id']).get('auto_queue', {}))
        self.service.action('automation', dict(request_id='a-3', organization_id='org', paused=False, auto_queue_proposals=False), 'admin', 'admin')
        task = self._meeting(task, [dict(title='Off', description='Must not start while automation is off.', profile_id='worker')], job_id='off')
        self.service.advance_proposals()
        self.assertNotIn('off', self.service.get(task['id']).get('auto_queue', {}))

    def test_auto_queue_skips_ineligible_assignees_and_review_requests(self):
        self.store.snapshot.return_value['organizations'] = [dict(id='org')]
        task = self.candidate()
        self.store.snapshot.return_value['profiles'].append(dict(self.profile, id='outsider', project=str(self.root / 'other')))
        self.service.action('automation', dict(request_id='a-1', organization_id='org', auto_queue_proposals=True, max_per_meeting=5), 'admin', 'admin')
        proposals = [
            dict(title='Needs review', description='Changes scope and must stay a draft.', profile_id='worker', needs_review=True),
            dict(title='Wrong repo', description='Assignee works in another repository.', profile_id='outsider'),
            dict(title='Valid work', description='A necessary, self-contained follow-up with checks.', profile_id='worker'),
        ]
        self._meeting(task, proposals)
        self.service.advance_proposals()
        marker = self.service.get(task['id'])['auto_queue']['meeting']
        self.assertEqual(len(marker['queued']), 1)
        reasons = ' '.join(marker['skipped'].values())
        self.assertIn('operator review', reasons)
        self.assertIn('another repository', reasons)

    def test_auto_queue_enforces_agent_and_daily_caps_and_replays_policy(self):
        self.store.snapshot.return_value['organizations'] = [dict(id='org')]
        task = self.candidate()
        existing = self.service.action('create', dict(self.body, request_id='create-cap', title='Existing queued work'), 'admin', 'admin')
        self.service.action('assignment', dict(request_id='queue-cap', task_id=existing['id'], mode='queue'), 'admin', 'admin')
        self.service.action('automation', dict(request_id='a-1', organization_id='org', auto_queue_proposals=True,
            max_open_per_agent=1, max_per_meeting=5, daily_cap=5), 'admin', 'admin')
        task = self._meeting(task, [dict(title='Capped', description='Must wait for the assignee queue.', profile_id='worker')], job_id='capped')
        self.service.advance_proposals()
        marker = self.service.get(task['id'])['auto_queue']['capped']
        self.assertEqual(marker['queued'], [])
        self.assertIn('queue limit', list(marker['skipped'].values())[0])
        # A zero daily cap blocks even with a free agent.
        self.service.action('assignment', dict(request_id='cancel-cap', task_id=existing['id'], mode='cancel'), 'admin', 'admin')
        self.service.action('automation', dict(request_id='a-2', organization_id='org', daily_cap=0), 'admin', 'admin')
        task = self._meeting(task, [dict(title='Daily capped', description='Must respect the daily cap.', profile_id='worker')], job_id='daily')
        self.service.advance_proposals()
        marker = self.service.get(task['id'])['auto_queue']['daily']
        self.assertEqual(marker['queued'], [])
        self.assertIn('daily', list(marker['skipped'].values())[0])
        # Replaying a policy request returns its stored response.
        replay = self.service.action('automation', dict(request_id='a-1', organization_id='org', auto_queue_proposals=True,
            max_open_per_agent=1, max_per_meeting=5, daily_cap=5), 'admin', 'admin')
        self.assertEqual(replay['policy']['daily_cap'], 5)
        # The replay must not re-execute: the stored policy still reflects a-2.
        self.assertEqual(self.service.policy('org')['daily_cap'], 0)

    def test_auto_queue_does_not_start_reviews_created_before_enabling(self):
        self.store.snapshot.return_value['organizations'] = [dict(id='org')]
        task = self.candidate()
        task = self._meeting(task, [dict(title='Old review', description='Created before automation was enabled.', profile_id='worker')], job_id='old')
        task['meetings'][-1]['at'] = '2000-01-01T00:00:00+00:00'
        self.service.save(task, 'discuss', 'admin')
        self.service.action('automation', dict(request_id='a-1', organization_id='org', auto_queue_proposals=True), 'admin', 'admin')
        self.service.advance_proposals()
        marker = self.service.get(task['id'])['auto_queue']['old']
        self.assertEqual(marker['queued'], [])
        self.assertIn('before automatic follow-up', marker['unavailable'])

    def test_worker_reported_follow_up_creates_one_draft_task(self):
        task = self.candidate()
        task.update(state='implementing', auto_validate=True)
        task.pop('head_sha')
        self.service.save(task, 'policy', 'admin')
        run = self.store.snapshot.return_value['jobs'][0]
        run.update(profile_id='worker', completion_token='token')
        tree = Path(task['worktree'])
        (self.repo / '.git/info/exclude').write_text('/.ci-cache/\n')
        receipt = tree / '.ci-cache/herdr-guidance' / ('task-' + task['id']) / 'receipt.json'
        receipt.parent.mkdir(parents=True)
        head = self.git('rev-parse', 'HEAD', path=tree).strip()
        receipt.write_text(json.dumps(dict(outcome='complete', commit=head, run_id='run', token='token', tests=[],
                                           follow_up=dict(title='Add regression test',
                                                          description='Cover the boundary case found during this task.'))))
        self.service.validation = MagicMock()
        self.service.validation.snapshot.return_value = {'executor': 'service'}
        self.service.validation.submit.return_value = dict(id='build1', state='queued', checks=[])
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
        origin = self.service.get(task['id'])
        markers = [m for m in origin['follow_up_tasks'] if m.startswith('receipt:')]
        self.assertEqual(len(markers), 1)
        created = self.service.get(origin['follow_up_tasks'][markers[0]])
        self.assertEqual(created['state'], 'draft')
        self.assertNotIn('assignment', created)
        self.assertTrue(created['source']['receipt_follow_up'])
        self.assertEqual(created['source']['depth'], 1)
        with patch('collaboration.current_run'):
            self.service.advance_automatic()
        self.assertEqual(len(self.service.get(task['id'])['follow_up_tasks']), 1)

    def test_discussion_prompt_states_automatic_follow_up_policy(self):
        self.store.snapshot.return_value['organizations'] = [dict(id='org')]
        self.store.snapshot.return_value['groups'] = [dict(id='review', organization_id='org', name='Project Review')]
        self.store.action.return_value = dict(id='meeting-1')
        self.service.action('automation', dict(request_id='a-1', organization_id='org', auto_queue_proposals=True, max_per_meeting=2), 'admin', 'admin')
        self.service.action('discuss', dict(request_id='d-1', task_id=self.task['id'], group_id='review'), 'admin', 'admin')
        prompt = self.store.action.call_args[0][1]['prompt']
        self.assertIn('automatic follow-up enabled', prompt)
        self.assertIn('needs_review=true', prompt)
