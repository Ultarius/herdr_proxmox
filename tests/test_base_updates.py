"""Real Git fast-forward, preservation, idempotency and repository lock tests."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import base_updates
import project_git
from repository_lock import common_directory, repository_lock


@unittest.skipUnless(shutil.which('git'), 'Git required')
class BaseUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'trunk')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.test')
        (self.repo / 'file').write_text('original')
        self.git('add', '.')
        self.git('commit', '-m', 'initial')
        self.old = self.git('rev-parse', 'HEAD')
        self.worker = self.root / 'worker'
        self.git('worktree', 'add', '-b', 'worker', str(self.worker))
        (self.repo / 'file').write_text('new')
        self.git('commit', '-am', 'incoming')
        self.target = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/remotes/origin/trunk', self.target)
        self.git('reset', '--hard', self.old)
        self.body = dict(path='repo', branch='trunk', base='refs/remotes/origin/trunk',
                         head=self.old, target=self.target, request_id='request-1')

    def git(self, *args, path=None):
        return subprocess.run(['git', '-C', str(path or self.repo), *args], check=True,
                              capture_output=True).stdout.decode().strip()

    def update(self, **changes):
        return base_updates.update(self.root, {**self.body, **changes}, 'damien', 'admin')

    def test_fast_forward_pins_old_head_and_leaves_dirty_worker_untouched(self):
        (self.worker / 'file').write_text('unfinished worker work')
        result = self.update()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.target)
        self.assertEqual(self.git('rev-parse', result['recovery_ref']), self.old)
        self.assertEqual(self.git('rev-parse', 'HEAD', path=self.worker), self.old)
        self.assertEqual((self.worker / 'file').read_text(), 'unfinished worker work')
        self.assertEqual(self.update(), result)
        self.assertEqual(len(base_updates.history(common_directory(self.repo))), 1)
        with self.assertRaises(ValueError):
            base_updates.update(self.root, self.body, 'another-admin', 'admin')

    def test_dirty_diverged_busy_and_stale_updates_never_move_branch(self):
        (self.repo / 'untracked').write_text('keep')
        self.assertEqual(self.update()['state'], 'blocked')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.old)
        (self.repo / 'untracked').unlink()
        result = base_updates.update(self.root, {**self.body, 'request_id': 'busy'}, 'damien', 'admin', busy=lambda p: True)
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(self.update(request_id='stale', head=self.target)['state'], 'blocked')
        (self.repo / 'local').write_text('local work')
        self.git('add', '.')
        self.git('commit', '-m', 'local divergence')
        diverged = self.git('rev-parse', 'HEAD')
        self.assertEqual(self.update(request_id='diverged', head=diverged)['state'], 'blocked')
        self.assertEqual(self.git('rev-parse', 'HEAD'), diverged)

    def test_admin_expected_branch_exact_ref_and_shared_checkout_required(self):
        with self.assertRaisesRegex(ValueError, 'administrator'):
            base_updates.update(self.root, self.body, 'kit', 'operator')
        with self.assertRaisesRegex(ValueError, 'shared'):
            self.update(path='worker')
        self.assertEqual(self.update(branch='main')['state'], 'blocked')
        self.git('update-ref', 'refs/remotes/origin/trunk', self.old)
        self.assertEqual(self.update(request_id='moved-ref')['state'], 'blocked')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.old)

    def test_linked_worktrees_share_exclusive_reentrant_lock(self):
        self.assertEqual(common_directory(self.repo), common_directory(self.worker))
        result = []
        with repository_lock(self.repo):
            with repository_lock(self.worker):
                def attempt():
                    try:
                        with repository_lock(self.worker, timeout=0.05):
                            result.append('unexpected')
                    except ValueError:
                        result.append('busy')
                thread = threading.Thread(target=attempt)
                thread.start()
                thread.join(2)
                self.assertFalse(thread.is_alive())
        self.assertEqual(result, ['busy'])
        with repository_lock(self.worker, timeout=0.1):
            pass

    def test_read_only_worktree_list_never_waits_for_the_operation_lock(self):
        holding, release = threading.Event(), threading.Event()

        def hold():
            with repository_lock(self.repo):
                holding.set()
                release.wait(5)

        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(holding.wait(2))
        try:
            listing = project_git.git(self.repo, 'worktree', 'list', '--porcelain')
        finally:
            release.set()
            thread.join(3)
        self.assertIn('worktree', listing)

    def test_cross_process_lock_blocks_linked_worktree_mutation(self):
        code = """import sys
from repository_lock import repository_lock
try:
    with repository_lock(sys.argv[1], timeout=0.1):
        sys.exit(2)
except ValueError:
    sys.exit(0)
"""
        import os
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / 'web/gateway'))
        with repository_lock(self.repo):
            result = subprocess.run([sys.executable, '-c', code, str(self.worker)], env=environment,
                                    capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_active_operation_refuses_without_modifying_files(self):
        operation = self.repo / '.git/MERGE_HEAD'
        operation.write_text(self.target)
        result = self.update()
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.old)
        self.assertTrue(operation.exists())

    def test_base_update_does_not_execute_post_merge_hook(self):
        hook = self.repo / '.git/hooks/post-merge'
        hook.write_text('#!/bin/sh\nprintf side-effect > unwanted-hook-effect\n')
        hook.chmod(0o755)
        self.assertEqual(self.update()['state'], 'complete')
        self.assertFalse((self.repo / 'unwanted-hook-effect').exists())

    def test_fast_forward_never_overwrites_ignored_local_file(self):
        (self.repo / '.gitignore').write_text('ignored.txt\n')
        self.git('add', '.gitignore')
        self.git('commit', '-m', 'ignore local file')
        old = self.git('rev-parse', 'HEAD')
        (self.repo / 'ignored.txt').write_text('incoming tracked file')
        self.git('add', '-f', 'ignored.txt')
        self.git('commit', '-m', 'track ignored path')
        target = self.git('rev-parse', 'HEAD')
        self.git('reset', '--hard', old)
        (self.repo / 'ignored.txt').write_text('valuable ignored local content')
        self.git('update-ref', 'refs/remotes/origin/trunk', target)
        result = self.update(head=old, target=target)
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(self.git('rev-parse', 'HEAD'), old)
        self.assertEqual((self.repo / 'ignored.txt').read_text(), 'valuable ignored local content')

    def test_pending_request_recovers_after_branch_was_advanced(self):
        common = common_directory(self.repo)
        recovery = 'refs/herdr/base-updates/' + self.body['request_id']
        self.git('update-ref', recovery, self.old)
        base_updates.save(common / 'herdr-base-updates', dict(
            request_id=self.body['request_id'], selection={k: self.body[k] for k in ('path', 'branch', 'base', 'head', 'target')},
            actor='damien', state='pending', old_head=self.old, target=self.target, recovery_ref=recovery))
        self.git('merge', '--ff-only', self.target)
        result = self.update()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['recovery_ref'], recovery)
        self.assertEqual(self.git('rev-parse', recovery), self.old)
