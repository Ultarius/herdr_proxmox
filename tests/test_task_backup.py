from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import server
from task_backup import BackupBusy, export_database, export_info


class TaskBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='herdr-backup-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'tasks.sqlite3'
        self.writer = sqlite3.connect(self.path)
        self.addCleanup(self.writer.close)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.execute('PRAGMA wal_autocheckpoint=0')
        self.writer.executescript('''
            CREATE TABLE tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE audit (task_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE assignments (task_id TEXT PRIMARY KEY, position INTEGER, data TEXT);
            CREATE TABLE policies (id TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE future_metadata (id INTEGER PRIMARY KEY, payload BLOB);
            CREATE INDEX task_audit ON audit(task_id);
        ''')
        self.writer.executemany('INSERT INTO tasks VALUES (?, ?)', [
            (str(i), json.dumps(dict(id=str(i), state='closed' if i % 2 else 'draft',
                                    diff='full diff', outcome_request={'retained': True},
                                    custom_metadata={'nested': [i, 'unchanged']}))) for i in range(125)])
        self.writer.executemany('INSERT INTO audit VALUES (?, ?)', [('0', json.dumps({'sequence': i})) for i in range(301)])
        self.writer.execute('INSERT INTO assignments VALUES (?, ?, ?)', ('0', 3, '{"state":"queued"}'))
        self.writer.execute('INSERT INTO policies VALUES (?, ?)', ('org', '{"paused":true}'))
        self.writer.execute('INSERT INTO future_metadata VALUES (?, ?)', (1, b'future\x00metadata'))
        self.writer.commit()
        self.connect = Mock(side_effect=lambda: sqlite3.connect(self.path))

    def assert_complete(self, path):
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 125)
            record = json.loads(db.execute('SELECT data FROM tasks WHERE id="0"').fetchone()[0])
            self.assertEqual(record['outcome_request'], {'retained': True})
            self.assertEqual(record['custom_metadata'], {'nested': [0, 'unchanged']})
            self.assertEqual(record['diff'], 'full diff')
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], 301)
            self.assertEqual(db.execute('SELECT position FROM assignments').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT data FROM policies').fetchone()[0], '{"paused":true}')
            self.assertEqual(db.execute('SELECT payload FROM future_metadata').fetchone()[0], b'future\x00metadata')
            self.assertIsNotNone(db.execute('SELECT name FROM sqlite_master WHERE name="task_audit"').fetchone())

    def test_complete_wal_backup_is_restorable_and_temporary_file_is_removed(self):
        self.assertTrue(Path(str(self.path) + '-wal').is_file())
        with export_database(self.connect) as backup:
            saved = backup
            # The copy is a standalone database, without any WAL sidecar.
            self.assertFalse(Path(str(backup) + '-wal').exists())
            restored = self.root / 'restored.sqlite3'
            restored.write_bytes(backup.read_bytes())
            self.assert_complete(restored)
            self.writer.execute('DELETE FROM tasks WHERE id="0"')
            self.writer.commit()
            self.assert_complete(restored)
        self.assertFalse(saved.exists())
        self.assertEqual(self.writer.execute('SELECT count(*) FROM tasks').fetchone()[0], 124)

    def test_slow_copy_deadline_preserves_live_data_and_cleans_scratch(self):
        created = []
        temporary_directory = tempfile.TemporaryDirectory
        def scratch(**kwargs):
            temporary = temporary_directory(**kwargs)
            created.append(Path(temporary.name))
            return temporary
        with patch('task_backup.tempfile.TemporaryDirectory', side_effect=scratch), \
                patch('task_backup.time.monotonic', side_effect=[0, 301]):
            with self.assertRaisesRegex(ValueError, 'copy exceeded its time limit'):
                with export_database(self.connect):
                    self.fail('An expired backup must not be returned')
        self.assertTrue(created)
        self.assertTrue(all(not path.exists() for path in created))
        self.assert_complete(self.path)

    def test_config_volume_and_configured_backup_directory_are_used(self):
        with export_database(self.connect) as backup:
            self.assertEqual(backup.parent.parent, self.path.parent / 'task-backups')
        directory = self.root / 'larger-volume'
        with patch.dict('os.environ', {'HERDR_TASK_BACKUP_DIR': str(directory)}):
            with export_database(self.connect) as backup:
                self.assertEqual(backup.parent.parent, directory)
                self.assert_complete(backup)
        self.assertEqual(list(directory.iterdir()), [])

    def test_insufficient_backup_space_fails_before_allocating_a_copy(self):
        with patch('task_backup.build_capacity.storage_status', return_value=dict(available=True, free_bytes=1, free_inodes=100)), \
                patch('task_backup.tempfile.TemporaryDirectory') as scratch:
            with self.assertRaisesRegex(ValueError, 'backup needs .* MiB free on the backup volume'):
                with export_database(self.connect):
                    self.fail('Low space must reject the export')
            scratch.assert_not_called()
        self.assert_complete(self.path)

    def test_concurrent_exports_are_refused_through_download_completion(self):
        with export_database(self.connect) as first:
            self.assertTrue(first.is_file())
            with self.assertRaisesRegex(BackupBusy, 'Another task export'):
                with export_database(self.connect):
                    self.fail('An overlapping export must not allocate a second copy')
            endpoint = self.start_http()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(endpoint, headers={'Authorization': 'Bearer admin'}))
            self.assertEqual(error.exception.code, 409)
            self.assertEqual(error.exception.headers['Retry-After'], '2')
        with export_database(self.connect) as retry:
            self.assert_complete(retry)

    def test_real_exclusive_database_lock_returns_bounded_http_error(self):
        # An independent, genuinely locked SQLite source, not a mocked clock.
        locked = self.root / 'locked.sqlite3'
        holder = sqlite3.connect(locked)
        self.addCleanup(holder.close)
        holder.execute('CREATE TABLE tasks (id TEXT)')
        holder.commit()
        holder.execute('BEGIN EXCLUSIVE')
        self.connect.side_effect = lambda: sqlite3.connect(locked)
        endpoint = self.start_http()
        started = time.monotonic()
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(endpoint, headers={'Authorization': 'Bearer admin'}), timeout=5)
            self.assertEqual(error.exception.code, 409)
            self.assertIn('storage is locked', json.load(error.exception)['error'])
            self.assertLess(time.monotonic() - started, 4)
        finally:
            holder.rollback()

    def test_browser_preflight_and_cache_busting_queries_are_supported(self):
        endpoint = self.start_http()
        with urlopen(Request(endpoint + '/info?cache=1', headers={'Authorization': 'Bearer admin'})) as response:
            info = json.load(response)
        self.assertGreater(info['estimated_bytes'], 0)
        self.assertGreater(info['required_free_bytes'], info['estimated_bytes'])
        self.assertGreaterEqual(info['free_bytes'], info['required_free_bytes'])
        with urlopen(Request(endpoint + '?cache=2', headers={'Authorization': 'Bearer admin'})) as response:
            self.assertTrue(response.read().startswith(b'SQLite format 3\x00'))

    def test_cancelled_download_releases_file_and_export_lease(self):
        handler = server.Handler.__new__(server.Handler)
        handler.server = SimpleNamespace(contributions=SimpleNamespace(connect=self.connect))
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        for failure in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            handler.wfile = Mock(spec=io.BytesIO)
            handler.wfile.write.side_effect = failure('Browser cancelled')
            handler.task_backup()
            self.assertEqual(list((self.root / 'task-backups').iterdir()), [])
            with export_database(self.connect) as retry:
                self.assert_complete(retry)

    def start_http(self):
        listener = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        listener.token = 'admin'
        listener.operators = SimpleNamespace(identify=lambda token: {'name': 'reader', 'role': 'operator'} if token == 'reader' else None)
        listener.contributions = SimpleNamespace(connect=self.connect)
        worker = threading.Thread(target=listener.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(listener.server_close)
        self.addCleanup(worker.join, 3)
        self.addCleanup(listener.shutdown)
        return f'http://127.0.0.1:{listener.server_port}/api/tasks/export'

    def test_http_admin_download_is_a_complete_database_and_not_cached(self):
        endpoint = self.start_http()
        for token, status in (('', 401), ('reader', 403)):
            with self.subTest(token=token), self.assertRaises(HTTPError) as error:
                urlopen(Request(endpoint, headers={'Authorization': 'Bearer ' + token}))
            self.assertEqual(error.exception.code, status)
        self.connect.assert_not_called()
        with urlopen(Request(endpoint, headers={'Authorization': 'Bearer admin'})) as response:
            content = response.read()
            self.assertEqual(response.headers['Content-Type'], 'application/vnd.sqlite3')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(int(response.headers['Content-Length']), len(content))
            self.assertRegex(response.headers['Content-Disposition'], r'attachment; filename="herdr-tasks-\d{8}T\d{6}Z\.sqlite3"')
        restored = self.root / 'download.sqlite3'
        restored.write_bytes(content)
        self.assert_complete(restored)
        self.assert_complete(self.path)

    def test_http_reports_storage_failure_without_returning_a_partial_backup(self):
        endpoint = self.start_http()
        self.connect.side_effect = sqlite3.OperationalError('private storage details')
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(endpoint, headers={'Authorization': 'Bearer admin'}))
        self.assertEqual(error.exception.code, 503)
        message = json.load(error.exception)['error']
        self.assertIn('Check gateway storage', message)
        self.assertNotIn('private storage details', message)


if __name__ == '__main__':
    unittest.main()
