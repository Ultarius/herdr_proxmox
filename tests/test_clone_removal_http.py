import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import server
from project_files import ProjectJobs


class CloneRemovalHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='herdr-clone-http-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.projects = ProjectJobs(self.root / 'projects', self.root / 'jobs.sqlite3')
        self.addCleanup(self.projects.close)
        self.folder = self.projects.root / 'repo'
        self.folder.mkdir(parents=True)
        (self.folder / 'notes').write_text('Local work')
        identity = self.folder.stat()
        self.job = dict(id='a' * 32, folder='repo', cwd=str(self.folder), state='completed',
                        destination_identity=[identity.st_dev, identity.st_ino])
        self.projects.save(self.job)
        self.organizations = Mock()
        self.organizations.checkout_in_use.return_value = False
        self.organizations.snapshot.return_value = {'profiles': [], 'organizations': []}
        self.listener = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.listener.token = 'admin'
        self.listener.operators = Mock()
        self.listener.operators.identify.side_effect = lambda token: {'name': 'reader', 'role': 'operator'} if token == 'reader' else None
        self.listener.projects = self.projects
        self.listener.organizations = self.organizations
        worker = threading.Thread(target=self.listener.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.listener.server_close)
        self.addCleanup(worker.join, 3)
        self.addCleanup(self.listener.shutdown)
        self.base = f'http://127.0.0.1:{self.listener.server_port}/api/projects/jobs/remove'

    def request(self, token='admin', **body):
        return urlopen(Request(self.base, data=json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}))

    def assert_refused(self, message, **changes):
        body = dict(id=self.job['id'], delete_folder=True, confirm_folder='repo')
        body.update(changes)
        with patch('server.command', return_value=[]), self.assertRaises(HTTPError) as error:
            self.request(**body)
        self.assertEqual(error.exception.code, 400)
        self.assertIn(message, json.load(error.exception)['error'])
        self.assertTrue(self.folder.exists())
        self.assertIn(self.job['id'], [job['id'] for job in self.projects.snapshot()['jobs']])

    def test_http_confirmation_is_forwarded_exactly(self):
        for confirmation in (None, '', 'other', '../repo', 'Repo'):
            with self.subTest(confirmation=confirmation):
                self.assert_refused('exact folder name', confirm_folder=confirmation)

    def test_replaced_folder_is_preserved(self):
        original = self.projects.root / 'original'
        self.assertEqual(original.resolve().parent, self.projects.root)
        self.assertEqual(self.folder.resolve().parent, self.projects.root)
        self.folder.rename(original)
        self.folder.mkdir()
        (self.folder / 'unrelated').write_text('Keep replacement')
        self.assert_refused('original clone folder was replaced')
        self.assertEqual((self.folder / 'unrelated').read_text(), 'Keep replacement')

    def test_legacy_entry_cannot_delete_replaced_folder(self):
        self.job.pop('destination_identity', None)
        self.projects.save(self.job)
        original = self.projects.root / 'original'
        self.assertEqual(original.resolve().parent, self.projects.root)
        self.assertEqual(self.folder.resolve().parent, self.projects.root)
        self.folder.rename(original)
        self.folder.mkdir()
        (self.folder / 'unrelated').write_text('Keep replacement')
        self.assert_refused('no verified original folder identity')
        with self.request(id=self.job['id']) as response:
            self.assertFalse(json.load(response)['folder_deleted'])
        self.assertTrue((self.folder / 'unrelated').exists())

    def test_mounts_newer_clone_and_connection_bindings_are_refused(self):
        with patch('project_files.contains_mount', return_value=True):
            self.assert_refused('mounted directory')
        with patch('project_files.Path.is_mount', return_value=True):
            self.assert_refused('not a direct project directory')
        self.projects.connections = Mock()
        self.projects.connections.snapshot.return_value = {'bindings': [{'path': str(self.folder)}]}
        self.assert_refused('Unassign the repository connection')
        self.projects.connections = None
        self.projects.save(dict(self.job, id='b' * 32))
        self.assert_refused('newer clone')

    def test_external_metadata_and_linked_worktrees_are_refused(self):
        metadata = self.folder / '.git'
        metadata.write_text('gitdir: /external')
        self.assert_refused('external Git metadata')
        metadata.unlink()
        (metadata / 'worktrees' / 'linked').mkdir(parents=True)
        with patch('repository_lock.repository_lock', return_value=nullcontext()):
            self.assert_refused('linked worktrees')

    def test_symlink_destination_is_refused(self):
        original = self.projects.root / 'original'
        self.assertEqual(original.resolve().parent, self.projects.root)
        self.assertEqual(self.folder.resolve().parent, self.projects.root)
        self.folder.rename(original)
        try:
            self.folder.symlink_to(original, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks is unavailable')
        self.assert_refused('clone folder changed')
        self.assertTrue((original / 'notes').exists())

    def test_workspace_lookup_precedes_git_lock_and_usage_is_rechecked_inside(self):
        (self.folder / '.git').mkdir()
        locked = False
        events = []

        @contextmanager
        def claim(target):
            nonlocal locked
            locked = True
            events.append('lock')
            try:
                yield
            finally:
                locked = False

        def workspace_lookup(*args):
            self.assertFalse(locked, 'The CLI lookup must not hold the Git operation lock')
            self.assertEqual(args, ('workspace', 'list'))
            events.append('workspaces')
            return []

        def check_usage(target):
            self.assertTrue(locked, 'Durable assignments must be rechecked under the Git lock')
            events.append('assignments')
            return True

        self.organizations.checkout_in_use.side_effect = check_usage
        with patch('server.command', side_effect=workspace_lookup), patch('repository_lock.repository_lock', side_effect=claim):
            with self.assertRaises(HTTPError) as error:
                self.request(id=self.job['id'], delete_folder=True, confirm_folder='repo')
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(events, ['workspaces', 'lock', 'assignments'])
        self.assertTrue((self.folder / 'notes').exists())

    def test_workspace_creation_holds_clone_reservation_lock(self):
        def create_workspace(*args):
            self.assertEqual(args[:2], ('workspace', 'create'))
            available = []

            def inspect_lock():
                acquired = self.projects.lock.acquire(blocking=False)
                available.append(acquired)
                if acquired:
                    self.projects.lock.release()

            inspector = threading.Thread(target=inspect_lock)
            inspector.start()
            inspector.join(2)
            self.assertFalse(inspector.is_alive())
            self.assertEqual(available, [False], 'Workspace creation must serialize with clone removal')
            return {'workspace_id': 'w1'}

        endpoint = self.base.removesuffix('/projects/jobs/remove') + '/workspaces/create'
        with patch('server.PROJECTS', self.projects.root), patch('server.command', side_effect=create_workspace):
            with urlopen(Request(endpoint, data=json.dumps({'cwd': str(self.folder), 'label': 'Workspace'}).encode(),
                                 headers={'Authorization': 'Bearer admin'})) as response:
                self.assertEqual(response.status, 200)

    def test_removal_requires_admin_and_history_removal_keeps_files(self):
        for token, status in (('', 401), ('reader', 403)):
            with self.subTest(token=token), self.assertRaises(HTTPError) as error:
                self.request(token, id=self.job['id'])
            self.assertEqual(error.exception.code, status)
        self.assertEqual(len(self.projects.snapshot()['jobs']), 1)
        with self.request(id=self.job['id']) as response:
            result = json.load(response)
        self.assertFalse(result['folder_deleted'])
        self.assertTrue((self.folder / 'notes').exists())

    def test_active_workspace_and_configured_projects_block_folder_deletion(self):
        body = dict(id=self.job['id'], delete_folder=True, confirm_folder='repo')
        for workspace in (str(self.folder), str(self.folder / 'subfolder'), str(self.projects.root)):
            with patch('server.command', return_value=[{'cwd': workspace}]), self.assertRaises(HTTPError) as error:
                self.request(**body)
            self.assertEqual(error.exception.code, 400)
            self.assertTrue(self.folder.exists())
        for assignments in ({'profiles': [{'project': str(self.folder)}], 'organizations': []},
                            {'profiles': [], 'organizations': [{'project_dir': str(self.folder)}]}):
            self.organizations.snapshot.return_value = assignments
            with patch('server.command', return_value=[]), self.assertRaises(HTTPError) as error:
                self.request(**body)
            self.assertEqual(error.exception.code, 400)
        self.organizations.snapshot.return_value = {'profiles': [], 'organizations': []}
        with patch('server.command', return_value=[]), self.request(**body) as response:
            self.assertTrue(json.load(response)['folder_deleted'])
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.projects.snapshot()['jobs'], [])

    def test_unknown_workspace_paths_and_failed_usage_checks_preserve_the_folder(self):
        body = dict(id=self.job['id'], delete_folder=True, confirm_folder='repo')
        with patch('server.command', return_value=[{'id': 'w1'}]), self.assertRaises(HTTPError):
            self.request(**body)
        self.organizations.checkout_in_use.side_effect = ValueError('Cannot inspect active work')
        with patch('server.command', return_value=[]), self.assertRaises(HTTPError):
            self.request(**body)
        self.assertTrue((self.folder / 'notes').exists())
        self.assertEqual(len(self.projects.snapshot()['jobs']), 1)


if __name__ == '__main__':
    unittest.main()
