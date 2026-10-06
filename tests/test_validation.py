"""Exact-commit validation runs against real throwaway Git repositories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import validation
from validation import ValidationRuns

ENV = dict(os.environ, GIT_AUTHOR_NAME='tester', GIT_AUTHOR_EMAIL='tester@example.test',
           GIT_COMMITTER_NAME='tester', GIT_COMMITTER_EMAIL='tester@example.test')


def git(path, *arguments):
    return subprocess.run(['git', '-C', str(path), *arguments], check=True, capture_output=True, env=ENV)


def commit(path, message):
    git(path, 'add', '-A')
    git(path, 'commit', '-m', message)
    return git(path, 'rev-parse', 'HEAD').stdout.decode().strip()


def usable_bash():
    if os.name != 'nt':
        return 'bash'
    found = shutil.which('git')
    candidate = Path(found).parent.parent / 'bin/bash.exe' if found else None
    return str(candidate) if candidate and candidate.exists() else None


BASH = usable_bash()


class ValidationRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects = Path(self.temp.name) / 'projects'
        self.repository = self.projects / 'source'
        script = self.repository / 'scripts/build-web.sh'
        script.parent.mkdir(parents=True)
        script.write_text('#!/bin/sh\necho validating\n')
        git(self.repository, 'init', '-b', 'main')
        self.target = commit(self.repository, 'initial')
        self.records = []
        self.runs = ValidationRuns(self.projects, self.lookup,
                                   record=lambda event_id, summary: self.records.append((event_id, summary)),
                                   root=Path(self.temp.name) / 'runs')

    def lookup(self, event_id):
        if event_id == 'b' * 64:
            return None
        return dict(id=event_id, repository='source', path='source', target=self.target)

    def submit_and_wait(self, event_id='a' * 64):
        run = self.runs.submit({'id': event_id}, actor='damien')
        self.assertEqual(run['state'], 'running')
        deadline = time.time() + 60
        while time.time() < deadline:
            current = next(item for item in self.runs.snapshot()['runs'] if item['id'] == run['id'])
            if current['state'] != 'running':
                return current
            time.sleep(0.05)
        self.fail('validation run did not finish')

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_exact_commit_is_validated_with_a_downloadable_log(self):
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual((run['state'], run['exit_code'], run['command']), ('complete', 0, 'scripts/build-web.sh'))
        self.assertEqual(run['target'], self.target)
        self.assertEqual(run['actor'], 'damien')
        saved, content = self.runs.log(run['id'])
        self.assertEqual(saved['id'], run['id'])
        self.assertIn('validating', content)
        self.assertIn('status 0', content)
        self.assertFalse((self.runs.root / run['id'] / 'tree').exists())
        event_id, summary = self.records[0]
        self.assertEqual((event_id, summary['run_id'], summary['state']), ('a' * 64, run['id'], 'complete'))
        self.assertEqual(self.runs.snapshot()['running'], 0)

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_a_failing_script_reports_its_status_and_log(self):
        (self.repository / 'scripts/build-web.sh').write_text('#!/bin/sh\necho broken >&2\nexit 3\n')
        self.target = commit(self.repository, 'break the build')
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual((run['state'], run['exit_code']), ('failed', 3))
        _, content = self.runs.log(run['id'])
        self.assertIn('broken', content)
        self.assertIn('status 3', content)

    def test_a_missing_script_is_an_error_not_a_failed_validation(self):
        (self.repository / 'scripts').rename(self.repository / 'scripts-away')
        self.target = commit(self.repository, 'remove the script')
        run = self.submit_and_wait()
        self.assertEqual(run['state'], 'error')
        self.assertIsNone(run['command'])
        _, content = self.runs.log(run['id'])
        self.assertIn('No validation script found', content)
        self.assertFalse((self.runs.root / run['id'] / 'tree').exists())

    def test_submit_rejects_bad_requests_unknown_events_and_running_duplicates(self):
        for body in ({}, {'id': 'nope'}, {'id': 'b' * 64}):
            with self.assertRaises(ValueError):
                self.runs.submit(body)
        folder = self.runs.root / ('c' * 32)
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps({'id': 'c' * 32, 'event_id': 'a' * 64,
                                                     'target': self.target, 'state': 'running',
                                                     'started_at': '2026-01-01T00:00:00+00:00'}))
        with self.assertRaisesRegex(ValueError, 'already running'):
            self.runs.submit({'id': 'a' * 64})

    def test_logs_reject_unknown_or_invalid_runs(self):
        with self.assertRaisesRegex(ValueError, 'Invalid validation run ID'):
            self.runs.log('nope')
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.runs.log('d' * 32)

    def test_restart_marks_incomplete_runs_interrupted(self):
        folder = self.runs.root / ('c' * 32)
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps({'id': 'c' * 32, 'state': 'running'}))
        restarted = ValidationRuns(self.projects, self.lookup, root=self.runs.root)
        self.assertEqual(restarted.snapshot()['runs'][0]['state'], 'interrupted')
        self.assertEqual(restarted.running(), [])

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_both_suites_run_and_logs_remain_bounded(self):
        (self.repository / 'scripts/validate.sh').write_text('#!/bin/sh\necho backend-suite\n')
        (self.repository / 'scripts/build-web.sh').write_text('#!/bin/sh\nprintf %0200010d 1\necho frontend-suite\n')
        self.target = commit(self.repository, 'both suites')
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual(run['state'], 'complete')
        self.assertEqual(run['command'], 'scripts/validate.sh + scripts/build-web.sh')
        _, content = self.runs.log(run['id'])
        self.assertLessEqual(len(content), validation.OUTPUT_LIMIT)
        self.assertIn('frontend-suite', content)

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_timeout_stops_validation_and_records_error(self):
        (self.repository / 'scripts/build-web.sh').write_text('#!/bin/sh\nsleep 10\n')
        self.target = commit(self.repository, 'slow script')
        self.runs.timeout = 0.1
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual(run['state'], 'error')
        self.assertIn('exceeded', self.runs.log(run['id'])[1])

    def test_symlinked_scripts_are_not_validation_entrypoints(self):
        with patch.object(Path, 'is_symlink', return_value=True):
            self.assertEqual(self.runs._commands(self.repository), [])


if __name__ == '__main__':
    unittest.main()
