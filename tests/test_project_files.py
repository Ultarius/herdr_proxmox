import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from project_files import ProjectJobs, redact


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
