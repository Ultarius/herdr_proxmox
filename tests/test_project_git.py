import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
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
