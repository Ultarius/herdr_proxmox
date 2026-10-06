import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import project_git
from project_git import inspect


@unittest.skipUnless(shutil.which('git'), 'Git required')
class ProjectGitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.run_git('init', '-b', 'main')
        self.run_git('config', 'user.email', 'test@example.test')
        self.run_git('config', 'user.name', 'Test')
        (self.repo / 'file.txt').write_text('before\n')
        (self.repo / 'deleted.txt').write_text('remove me\n')
        self.run_git('add', '.')
        self.run_git('commit', '-m', 'initial')

    def run_git(self, *args):
        subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)

    def test_worktree_counts_and_tracked_diff(self):
        (self.repo / 'file.txt').write_text('after\n')
        (self.repo / 'deleted.txt').unlink()
        (self.repo / 'new.txt').write_text('new\n')
        self.run_git('worktree', 'add', '-b', 'agent-work', str(self.root / 'agent'))
        data = inspect(self.root, {'path': 'repo'})
        self.assertEqual(len(data['worktrees']), 2)
        tree = next(t for t in data['worktrees'] if t['path'] == 'repo')
        self.assertEqual(tree['counts'], {'added': 1, 'deleted': 1, 'modified': 1})
        self.assertIsNone(tree['upstream'])
        self.assertIn('+after', inspect(self.root, {'path': 'repo', 'file': 'file.txt'})['diff'])
        agent = next(t for t in data['worktrees'] if t['path'] == 'agent')
        self.assertEqual(agent['branch'], 'agent-work')
        self.assertEqual(agent['changes'], [])

    def test_rejects_escape_and_non_repository(self):
        self.assertFalse(inspect(self.root, {'path': ''})['repository'])
        for path in ('../outside', '/outside', 'repo/../outside'):
            with self.assertRaises(ValueError):
                inspect(self.root, {'path': path})
        with self.assertRaises(ValueError):
            inspect(self.root, {'path': 'repo', 'file': '../secret'})

    def test_rename_records_and_literal_pathspec(self):
        self.run_git('mv', 'file.txt', 'renamed.txt')
        tree = inspect(self.root, {'path': 'repo'})['worktrees'][0]
        self.assertEqual(len(tree['changes']), 1)
        self.assertEqual(tree['changes'][0]['path'], 'renamed.txt')
        self.assertEqual(tree['changes'][0]['kind'], 'modified')
        self.assertEqual(inspect(self.root, {'path': 'repo', 'file': '*'})['diff'], '')

    def test_fetch_updates_tracking_only_and_worktree_warning(self):
        old = project_git.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.run_git('worktree', 'add', '-b', 'agent-work', str(self.root / 'agent'))
        self.run_git('update-ref', 'refs/remotes/origin/main', old)
        (self.repo / 'file.txt').write_text('remote update\n')
        self.run_git('add', '.')
        self.run_git('commit', '-m', 'remote update')
        new = project_git.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.run_git('reset', '--hard', old)
        (self.root / 'agent/file.txt').write_text('unfinished agent work\n')
        original = project_git.git
        def simulated(path, *args, **kwargs):
            if args == ('remote', 'get-url', 'origin'):
                return 'https://example.test/repository.git\n'
            if 'fetch' in args:
                self.assertIn('+refs/heads/*:refs/remotes/origin/*', args)
                original(path, 'update-ref', 'refs/remotes/origin/main', new)
                (self.repo / '.git/FETCH_HEAD').write_text(new)
                return ''
            return original(path, *args, **kwargs)
        with patch.object(project_git, 'git', side_effect=simulated):
            result = inspect(self.root, {'path': 'repo', 'action': 'fetch'})
        self.assertIsNotNone(result['last_fetch'])
        self.assertIsNone(result['fetch_error'])
        self.assertEqual(original(self.repo, 'rev-parse', 'HEAD').strip(), old)
        agent = next(t for t in result['worktrees'] if t['path'] == 'agent')
        self.assertEqual(agent['base'], 'origin/main')
        self.assertEqual(agent['base_behind'], 1)
        self.assertEqual(agent['counts']['modified'], 1)
        self.assertEqual((self.root / 'agent/file.txt').read_text(), 'unfinished agent work\n')

    def test_fetch_failure_keeps_local_status(self):
        with patch.object(project_git, 'fetch', side_effect=ValueError('Remote fetch failed.')):
            data = inspect(self.root, {'path': 'repo', 'action': 'fetch'})
        self.assertEqual(data['fetch_error'], 'Remote fetch failed.')
        self.assertTrue(data['repository'])
        self.assertEqual([t['path'] for t in data['worktrees']], ['repo'])

    def test_unreadable_worktree_is_reported_instead_of_dropped(self):
        self.run_git('worktree', 'add', '-b', 'agent-work', str(self.root / 'agent'))
        original = project_git.checkout
        def flaky(root, path, base=None):
            if path.name == 'agent':
                raise ValueError('Git information is unavailable for this checkout.')
            return original(root, path, base)
        with patch.object(project_git, 'checkout', side_effect=flaky):
            data = inspect(self.root, {'path': 'repo'})
        agent = next(t for t in data['worktrees'] if t['path'] == 'agent')
        self.assertEqual(agent['error'], 'Git information is unavailable for this checkout.')
        self.assertNotIn('changes', agent)

    def test_base_branch_metadata_uses_a_short_name(self):
        self.run_git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        tree = inspect(self.root, {'path': 'repo'})['worktrees'][0]
        self.assertEqual(tree['base'], 'origin/main')
        self.assertEqual(tree['base_behind'], 0)
        self.assertEqual(tree['base_ahead'], 0)

    def test_missing_base_branch_leaves_counts_empty(self):
        tree = inspect(self.root, {'path': 'repo'})['worktrees'][0]
        self.assertIsNone(tree['base'])
        self.assertIsNone(tree['base_behind'])
        self.assertIsNone(tree['base_ahead'])


    def test_recovery_snapshot_preserves_index_files_and_pins_untracked_content(self):
        (self.repo / 'file.txt').write_text('staged\n')
        self.run_git('add', 'file.txt')
        (self.repo / 'file.txt').write_text('unstaged\n')
        (self.repo / 'new.txt').write_text('untracked\n')
        before = project_git.git(self.repo, 'status', '--porcelain=v1')
        snapshot = project_git.recovery_snapshot(self.root, 'repo', 'a' * 64)
        self.assertEqual(before, project_git.git(self.repo, 'status', '--porcelain=v1'))
        self.assertEqual(project_git.git(self.repo, 'show', snapshot['ref'] + ':file.txt'), 'unstaged\n')
        self.assertEqual(project_git.git(self.repo, 'show', snapshot['ref'] + '^2:file.txt'), 'staged\n')
        self.assertEqual(project_git.git(self.repo, 'show', snapshot['ref'] + ':new.txt'), 'untracked\n')
        self.assertEqual(snapshot['commit'], project_git.recovery_snapshot(self.root, 'repo', 'a' * 64)['commit'])

    def test_recovery_restores_files_and_staging_in_a_separate_worktree(self):
        (self.repo / 'file.txt').write_text('staged\n')
        self.run_git('add', 'file.txt')
        (self.repo / 'file.txt').write_text('unstaged\n')
        (self.repo / 'deleted.txt').unlink()
        self.run_git('add', 'deleted.txt')
        (self.repo / 'new.txt').write_text('untracked\n')
        before = project_git.git(self.repo, 'status', '--porcelain=v1')
        original_head = project_git.git(self.repo, 'rev-parse', 'HEAD').strip()
        snapshot = project_git.recovery_snapshot(self.root, 'repo', 'b' * 64)
        recovery = self.root / 'recovered'
        self.run_git('worktree', 'add', '--detach', str(recovery), snapshot['ref'] + '^1')
        project_git.git(recovery, 'restore', '--source=' + snapshot['ref'], '--worktree', '--', '.')
        project_git.git(recovery, 'read-tree', snapshot['ref'] + '^2')
        self.assertEqual(project_git.git(recovery, 'rev-parse', 'HEAD').strip(), original_head)
        self.assertEqual(project_git.git(recovery, 'status', '--porcelain=v1'), before)
        self.assertEqual((recovery / 'file.txt').read_text(), 'unstaged\n')
        self.assertEqual(project_git.git(recovery, 'show', ':file.txt'), 'staged\n')
        self.assertEqual((recovery / 'new.txt').read_text(), 'untracked\n')
        self.assertFalse((recovery / 'deleted.txt').exists())
        self.assertEqual(project_git.git(self.repo, 'status', '--porcelain=v1'), before)
