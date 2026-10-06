"""Background integration notices. Read-only: nothing here prompts an agent."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import integration
from integration import IntegrationWatcher
import project_git


@unittest.skipUnless(shutil.which('git'), 'Git required')
class IntegrationTests(unittest.TestCase):
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
        self.run_git('add', '.')
        self.run_git('commit', '-m', 'initial')
        self.base = project_git.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.run_git('update-ref', 'refs/remotes/origin/main', self.base)
        self.watchers = []

    def tearDown(self):
        for watcher in self.watchers:
            watcher.close()

    def run_git(self, *args):
        subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)

    def watcher(self, checkouts):
        # A long interval keeps the thread out of the assertions.
        watcher = IntegrationWatcher(self.root, lambda: checkouts, interval=3600)
        self.watchers.append(watcher)
        return watcher

    def advance_main(self):
        (self.repo / 'file.txt').write_text('remote\n')
        self.run_git('add', '.')
        self.run_git('commit', '-m', 'remote')
        remote = project_git.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.run_git('update-ref', 'refs/remotes/origin/main', remote)
        self.run_git('reset', '--hard', self.base)

    def test_notice_reports_behind_and_keeps_working_files(self):
        self.advance_main()
        self.run_git('worktree', 'add', '-b', 'agent-work', str(self.root / 'agent'))
        agent = self.root / 'agent/file.txt'
        agent.write_text('agent work in progress\n')
        watcher = self.watcher([('p1', 'r1', 'Max', str(self.root / 'agent'))])
        notices = watcher.refresh()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['behind'], 1)
        self.assertEqual(notices[0]['base'], 'origin/main')
        self.assertFalse(notices[0]['conflicts'])
        self.assertTrue(notices[0]['dirty'])
        self.assertFalse(notices[0]['merging'])
        # The check is read-only.
        self.assertEqual(agent.read_text(), 'agent work in progress\n')
        self.assertEqual(project_git.git(self.repo, 'rev-parse', 'HEAD').strip(), self.base)

    def test_current_checkout_produces_no_notice(self):
        watcher = self.watcher([('p1', 'r1', 'Max', str(self.repo))])
        self.assertEqual(watcher.refresh(), [])
        self.assertEqual(watcher.snapshot()['alerts'], [])

    def test_unmerged_conflicts_and_in_progress_merge_are_reported(self):
        self.run_git('checkout', '-b', 'feature')
        (self.repo / 'file.txt').write_text('feature\n')
        self.run_git('commit', '-am', 'feature change')
        self.run_git('checkout', 'main')
        (self.repo / 'file.txt').write_text('main\n')
        self.run_git('commit', '-am', 'main change')
        conflicted = subprocess.run(['git', '-C', str(self.repo), 'merge', 'feature'],
                                    capture_output=True, text=True)
        self.assertNotEqual(conflicted.returncode, 0)
        watcher = self.watcher([('p1', 'r1', 'Max', str(self.repo))])
        notices = watcher.refresh()
        self.assertEqual(notices[0]['conflicts'], 1)
        self.assertTrue(notices[0]['merging'])
        self.assertEqual(notices[0]['branch'], 'main')

    def test_outside_checkout_is_reported_instead_of_raising(self):
        watcher = self.watcher([('p1', 'r1', 'Max', str(self.root / 'ghost'))])
        notices = watcher.refresh()
        self.assertIn('outside the projects directory', notices[0]['error'])

    def test_missing_repository_is_reported_instead_of_raising(self):
        plain = self.root / 'plain'
        plain.mkdir()
        watcher = self.watcher([('p1', 'r1', 'Max', str(plain))])
        notices = watcher.refresh()
        self.assertEqual(notices[0]['error'], 'Git information is unavailable for this checkout.')

    def test_base_is_cached_and_re_resolved_after_the_ttl(self):
        plain = self.root / 'plain'
        plain.mkdir()
        watcher = self.watcher([])
        self.assertIsNone(watcher.base_for(plain))
        self.assertEqual(watcher.base_for(self.repo), 'refs/remotes/origin/main')
        self.assertEqual(watcher.base_for(self.repo), 'refs/remotes/origin/main')
        # An entry that aged out is resolved again, so a later fetch that
        # creates origin/main is noticed.
        with patch.object(integration.time, 'monotonic', return_value=10):
            watcher.bases[self.repo] = (None, 10 - integration.BASE_TTL - 1)
            with patch.object(project_git, 'git', wraps=project_git.git) as resolve:
                self.assertEqual(watcher.base_for(self.repo), 'refs/remotes/origin/main')
                resolve.assert_called_once_with(self.repo, 'rev-parse', '--verify', 'refs/remotes/origin/main')
                resolve.reset_mock()
                self.assertEqual(watcher.base_for(self.repo), 'refs/remotes/origin/main')
                resolve.assert_not_called()

    def test_snapshot_serves_cached_alerts_without_running_git(self):
        self.advance_main()
        watcher = self.watcher([('p1', 'r1', 'Max', str(self.repo))])
        self.assertEqual(len(watcher.refresh()), 1)
        with patch.object(project_git, 'git', side_effect=AssertionError('git on the request path')):
            snapshot = watcher.snapshot()
        self.assertEqual(len(snapshot['alerts']), 1)
        self.assertTrue(snapshot['checked'])
        self.assertEqual(snapshot['interval'], 3600)
        self.assertEqual(integration.LIMIT, 20)
    def test_shared_checkout_notifies_every_agent_but_is_measured_once(self):
        self.advance_main()
        watcher = self.watcher([('p1', 'r1', 'Max', str(self.repo)),
                                ('p2', 'r2', 'Olaf', str(self.repo))])
        with patch.object(project_git, 'summary', wraps=project_git.summary) as measure:
            notices = watcher.refresh()
        self.assertEqual({n['profile_id'] for n in notices}, {'p1', 'p2'})
        self.assertEqual(measure.call_count, 1)
        self.assertTrue(all('last_fetch' in n for n in notices))
