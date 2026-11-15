import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from project_files import ProjectJobs, redact, contains_mount


class ProjectJobTests(unittest.TestCase):
    def test_background_clone_is_persisted_and_duplicate_does_not_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered, release = threading.Event(), threading.Event()
            def clone(*args, **kwargs):
                entered.set()
                release.wait(3)
            with patch('project_files.clone_repository', side_effect=clone) as command:
                service = ProjectJobs(root / 'projects', root / 'jobs.sqlite3')
                try:
                    body = dict(url='https://example.com/repo.git', folder='repo')
                    job = service.start(body)
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(service.snapshot()['jobs'][0]['state'], 'running')
                    with self.assertRaises(ValueError):
                        service.start(body)
                    release.set()
                finally:
                    release.set()
                    service.close()
                self.assertEqual(command.call_count, 1)
            restored = ProjectJobs(root / 'projects', root / 'jobs.sqlite3')
            try:
                self.assertEqual(restored.snapshot()['jobs'][0]['state'], 'completed')
                job.update(state='running')
                restored.save(job)
            finally:
                restored.close()
            restarted = ProjectJobs(root / 'projects', root / 'jobs.sqlite3')
            try:
                self.assertEqual(restarted.snapshot()['jobs'][0]['state'], 'interrupted')
            finally:
                restarted.close()

    def test_diagnostics_redact_credentials_and_are_bounded(self):
        result = redact('fatal: https://user:secret@example.com/repo token=secret password:secret ' + 'x' * 3000)
        self.assertNotIn('secret', result)
        self.assertLessEqual(len(result), 2000)


class CloneRemovalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='herdr-clone-removal-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.projects = self.root / 'projects'
        self.projects.mkdir()
        self.service = ProjectJobs(self.projects, self.root / 'jobs.sqlite3')
        self.addCleanup(self.service.close)

    def job(self, state='failed', folder='repo', identifier='a' * 32):
        target = self.projects / folder
        target.mkdir(exist_ok=True)
        (target / 'local-work.txt').write_text('Keep unless deletion was confirmed')
        identity = target.stat()
        job = dict(id=identifier, state=state, folder=folder, cwd=str(target), url='https://example.com/repo.git',
                   destination_identity=[identity.st_dev, identity.st_ino])
        self.service.save(job)
        return job, target

    def test_history_removal_is_durable_idempotent_and_preserves_files(self):
        job, target = self.job('completed')
        check = Mock(side_effect=AssertionError('History removal must not inspect live projects'))
        result = self.service.remove(dict(id=job['id']), in_use=check)
        self.assertFalse(result['folder_deleted'])
        self.assertTrue((target / 'local-work.txt').is_file())
        self.assertEqual(self.service.snapshot()['jobs'], [])
        restored = ProjectJobs(self.projects, self.service.path)
        try:
            self.assertEqual(restored.snapshot()['jobs'], [])
            self.assertTrue(restored.remove(dict(id=job['id']))['removed'])
        finally:
            restored.close()
        check.assert_not_called()

    def test_folder_deletion_requires_explicit_confirmation_and_usage_checks(self):
        job, target = self.job()
        body = dict(id=job['id'], delete_folder=True)
        for confirmation in (None, '', 'other', '../repo'):
            with self.subTest(confirmation=confirmation), self.assertRaisesRegex(ValueError, 'exact folder name'):
                self.service.remove(dict(body, confirm_folder=confirmation), in_use=lambda _: False)
            self.assertTrue(target.is_dir())
        body['confirm_folder'] = 'repo'
        with self.assertRaisesRegex(ValueError, 'usage checks are unavailable'):
            self.service.remove(body)
        with self.assertRaisesRegex(ValueError, 'in use'):
            self.service.remove(body, in_use=lambda _: True)
        self.assertEqual(len(self.service.snapshot()['jobs']), 1)
        result = self.service.remove(body, in_use=lambda _: False)
        self.assertTrue(result['folder_deleted'])
        self.assertFalse(target.exists())
        self.assertEqual(self.service.snapshot()['jobs'], [])

    def test_queued_and_running_jobs_cannot_be_removed(self):
        for index, state in enumerate(('queued', 'running')):
            job, target = self.job(state, state, format(index + 1, '032x'))
            for delete in (False, True):
                with self.subTest(state=state, delete=delete), self.assertRaisesRegex(ValueError, 'finish'):
                    self.service.remove(dict(id=job['id'], delete_folder=delete, confirm_folder=state), in_use=lambda _: False)
                self.assertTrue(target.exists())

    def test_invalid_requests_cannot_choose_arbitrary_paths(self):
        job, target = self.job()
        for body in (None, [], {}, {'id': '../repo'}, {'id': job['id'], 'delete_folder': 'false'},
                     {'id': job['id'], 'cwd': str(target)}, {'id': job['id'], 'folder': 'repo'}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.service.remove(body, in_use=lambda _: False)
        self.assertTrue(target.exists())
        job['folder'] = '../outside'
        self.service.save(job)
        with self.assertRaises(ValueError):
            self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='../outside'), in_use=lambda _: False)

    def test_failure_keeps_the_history_entry_for_retry(self):
        job, target = self.job()
        with patch('project_files.shutil.rmtree', side_effect=PermissionError('Files are busy')):
            with self.assertRaises(PermissionError):
                self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
        self.assertTrue(target.exists())
        self.assertEqual(self.service.snapshot()['jobs'][0]['id'], job['id'])

    def test_newer_clone_and_replaced_destination_are_preserved(self):
        job, target = self.job()
        self.job(identifier='b' * 32)
        with self.assertRaisesRegex(ValueError, 'newer clone'):
            self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
        self.assertTrue(target.exists())
        self.service.remove(dict(id='b' * 32))
        identity = target.stat()
        job['destination_identity'] = [identity.st_dev, identity.st_ino]
        self.service.save(job)
        preserved = self.projects / 'original'
        self.assertEqual(target.resolve().parent, self.projects)
        self.assertEqual(preserved.resolve().parent, self.projects)
        target.rename(preserved)
        target.mkdir()
        with self.assertRaisesRegex(ValueError, 'replaced'):
            self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
        self.assertTrue(target.is_dir())
        self.assertTrue((preserved / 'local-work.txt').is_file())

    def test_connection_assignments_and_linked_worktrees_block_deletion(self):
        job, target = self.job('completed')
        self.service.connections = Mock()
        self.service.connections.snapshot.return_value = {'bindings': [{'path': str(target), 'connection_id': 'key'}]}
        body = dict(id=job['id'], delete_folder=True, confirm_folder='repo')
        with self.assertRaisesRegex(ValueError, 'Unassign'):
            self.service.remove(body, in_use=lambda _: False)
        self.service.connections = None
        (target / '.git' / 'worktrees' / 'linked').mkdir(parents=True)
        from contextlib import nullcontext
        with patch('repository_lock.repository_lock', return_value=nullcontext()):
            with self.assertRaisesRegex(ValueError, 'linked worktrees'):
                self.service.remove(body, in_use=lambda _: False)
        self.assertTrue(target.exists())

    def test_legacy_or_invalid_identity_allows_only_history_removal(self):
        job, target = self.job()
        original = self.projects / 'original'
        self.assertEqual(original.resolve().parent, self.projects)
        self.assertEqual(target.resolve().parent, self.projects)
        target.rename(original)
        target.mkdir()
        (target / 'unrelated').write_text('Keep replacement')
        for identity in (None, [], [True, 1], [1, 0], ['1', 2]):
            job['destination_identity'] = identity
            self.service.save(job)
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, 'no verified original folder identity'):
                self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
            self.assertEqual((target / 'unrelated').read_text(), 'Keep replacement')
        result = self.service.remove(dict(id=job['id']))
        self.assertFalse(result['folder_deleted'])
        self.assertTrue((target / 'unrelated').exists())

    def test_clone_reservation_holds_removal_lock_until_job_is_durable(self):
        job, target = self.job()
        original = self.projects / 'original'
        self.assertEqual(original.resolve().parent, self.projects)
        self.assertEqual(target.resolve().parent, self.projects)
        target.rename(original)
        entered, release, removing = threading.Event(), threading.Event(), threading.Event()
        errors = []
        save = self.service.save

        def delayed_save(reservation):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Reservation was not released')
            save(reservation)

        def start():
            try:
                self.service.start(dict(url='https://example.com/repo.git', folder='repo'))
            except Exception as error:
                errors.append(error)

        def remove():
            removing.set()
            try:
                self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
            except Exception as error:
                errors.append(error)

        starter = threading.Thread(target=start)
        remover = threading.Thread(target=remove)
        with patch.object(self.service, 'save', side_effect=delayed_save), patch.object(self.service.worker, 'submit'):
            try:
                starter.start()
                self.assertTrue(entered.wait(2))
                acquired = self.service.lock.acquire(blocking=False)
                if acquired:
                    self.service.lock.release()
                self.assertFalse(acquired, 'Folder creation and job persistence must hold the removal lock')
                remover.start()
                self.assertTrue(removing.wait(2))
            finally:
                release.set()
                starter.join(3)
                if remover.ident is not None:
                    remover.join(3)
        self.assertFalse(starter.is_alive())
        self.assertFalse(remover.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertIn('newer clone', str(errors[0]))
        self.assertTrue(target.is_dir())
        self.assertEqual(len(self.service.snapshot()['jobs']), 2)

    def test_mounts_are_refused_and_linux_bind_mounts_are_detected(self):
        job, target = self.job()
        with patch('project_files.contains_mount', return_value=True):
            with self.assertRaisesRegex(ValueError, 'mounted directory'):
                self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
        self.assertTrue(target.exists())
        with patch('project_files.sys.platform', 'linux'), patch('project_files.Path.read_text', return_value=
                '43 21 0:30 /host/source /projects/repo/cache rw - ext4 /dev/root rw\n'):
            self.assertTrue(contains_mount(Path('/projects/repo')))
            self.assertFalse(contains_mount(Path('/projects/other')))

    def test_unreadable_or_invalid_mount_list_has_actionable_error(self):
        job, target = self.job()
        for failure in (PermissionError('opaque system error'), UnicodeError('invalid encoding')):
            with self.subTest(failure=failure), patch('project_files.sys.platform', 'linux'), \
                    patch('project_files.Path.read_text', side_effect=failure):
                with self.assertRaisesRegex(ValueError, 'Could not inspect filesystem mounts. The folder was preserved.'):
                    self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
            self.assertTrue(target.exists())
        with patch('project_files.sys.platform', 'linux'), patch('project_files.Path.read_text', return_value='malformed'):
            with self.assertRaisesRegex(ValueError, 'Could not inspect filesystem mounts'):
                contains_mount(target)

    @unittest.skipIf(sys.platform == 'win32', 'Real gateway repository deletion requires POSIX open-file unlink semantics')
    def test_completed_git_clone_deletes_under_the_real_repository_lock(self):
        import subprocess
        job, target = self.job('completed')
        subprocess.run(['git', 'init', '-q', str(target)], check=True)
        result = self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
        self.assertTrue(result['folder_deleted'])
        self.assertFalse(target.exists())

    def test_symlink_destination_is_refused_and_nested_symlinks_are_not_followed(self):
        job, target = self.job()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('Keep')
        link = target / 'outside-link'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks is unavailable')
        self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
        self.assertEqual((outside / 'keep').read_text(), 'Keep')
        job, target = self.job(identifier='b' * 32)
        self.assertEqual(target.resolve().parent, self.projects)
        saved = self.projects / 'saved'
        self.assertEqual(saved.resolve().parent, self.projects)
        target.rename(saved)
        target.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.remove(dict(id=job['id'], delete_folder=True, confirm_folder='repo'), in_use=lambda _: False)
        self.assertTrue((outside / 'keep').exists())
