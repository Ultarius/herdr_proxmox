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
        self.fail('validation run did not finish: ' + json.dumps(current) + '\n' + self.runs.log(run['id'])[1])

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

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_required_scripts_run_independently_and_retain_failure_evidence(self):
        (self.repository / 'scripts/validate.sh').write_text('#!/bin/sh\necho backend-failed\nexit 3\n')
        (self.repository / 'scripts/build-web.sh').write_text('#!/bin/sh\necho frontend-still-ran\n')
        self.target = commit(self.repository, 'independent checks')
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual(run['state'], 'failed', self.runs.log(run['id'])[1])
        self.assertEqual([c['status'] for c in run['checks']], ['failed', 'passed', 'passed', 'passed'])
        self.assertFalse(run['required_checks_verified'])
        self.assertIn('frontend-still-ran', self.runs.log(run['id'])[1])
        self.assertEqual(self.records[0][1]['checks'], run['checks'])

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_waived_subcheck_does_not_turn_a_zero_exit_into_verified_checks(self):
        (self.repository / 'scripts/build-web.sh').write_text(
            '#!/bin/sh\nprintf \'%s\\n\' \'{"id":"lint","status":"waived","exit_code":null,"duration_ms":0}\' > "$HERDR_CHECK_REPORT"\n')
        self.target = commit(self.repository, 'reported waiver')
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual(run['checks'][0]['status'], 'passed')
        self.assertEqual(run['checks'][1]['status'], 'waived')
        self.assertFalse(run['required_checks_verified'])
        self.assertEqual(run['state'], 'failed', self.runs.log(run['id'])[1])
        self.assertNotIn('artifact', run)

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

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_service_queue_executes_without_browser_and_publishes_results_once(self):
        from build_queue import BuildQueue
        from build_worker import execute_next
        queue = BuildQueue(Path(self.temp.name) / 'queue')
        facade = ValidationRuns(self.projects, self.lookup, root=self.runs.root, queue=queue,
            record=lambda event, summary: self.records.append((event, summary)))
        submitted = facade.submit({'id': 'a' * 64})
        self.assertEqual(submitted['state'], 'queued')
        worker = ValidationRuns(self.projects, self.lookup, root=self.runs.root, external=True)
        with patch.object(validation, 'BASH', BASH):
            self.assertTrue(execute_next(queue, worker))
        self.assertEqual(facade.snapshot()['runs'][0]['state'], 'complete')
        self.assertEqual(self.records[0][1]['run_id'], submitted['id'])
        facade.snapshot()
        self.assertEqual(len(self.records), 1)
        duplicate = facade.submit({'id': 'a' * 64})
        self.assertEqual(duplicate['id'], submitted['id'])
        self.assertFalse(execute_next(queue, worker))

    def test_logs_reject_unknown_or_invalid_runs(self):
        with self.assertRaisesRegex(ValueError, 'Invalid validation run ID'):
            self.runs.log('nope')
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.runs.log('d' * 32)

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_success_retains_exact_build_artifact_after_checkout_cleanup(self):
        import tarfile
        (self.repository / 'scripts/build-web.sh').write_text(
            '#!/bin/sh\nmkdir -p web/public\necho built > web/public/index.html\n')
        self.target = commit(self.repository, 'static output')
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual(run['state'], 'complete', self.runs.log(run['id'])[1])
        self.assertEqual(run['artifact']['target'], self.target)
        self.assertFalse(run['artifact']['deployment_authorized'])
        folder = self.runs.root / run['id']
        self.assertFalse((folder / 'tree').exists())
        with tarfile.open(folder / 'artifact.tar.gz') as archive:
            self.assertEqual(archive.extractfile('index.html').read(), b'built\n')
        self.assertEqual(json.loads((folder / 'manifest.json').read_text()), run['artifact'])
        archive, manifest = self.runs.artifact(run['id'])
        self.assertEqual(manifest['target'], self.target)
        archive.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'size mismatch'):
            self.runs.artifact(run['id'])

    def test_artifact_download_requires_retained_complete_build(self):
        with self.assertRaisesRegex(ValueError, 'Invalid'):
            self.runs.artifact('../escape')
        with self.assertRaisesRegex(ValueError, 'No retained'):
            self.runs.artifact('d' * 32)

    def test_artifact_size_limit_rejects_before_packaging(self):
        tree = Path(self.temp.name) / 'output'
        source = tree / 'web/public'
        source.mkdir(parents=True)
        (source / 'index.html').write_text('too large')
        with patch.object(validation, 'ARTIFACT_LIMIT', 1):
            with self.assertRaisesRegex(ValueError, 'retention limit'):
                self.runs._retain_artifact(tree, tree, {})

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_retention_failure_keeps_the_passing_build_distinct(self):
        (self.repository / 'scripts/build-web.sh').write_text(
            '#!/bin/sh\nmkdir -p web/public\necho built > web/public/index.html\n')
        self.target = commit(self.repository, 'static output')
        with patch.object(validation, 'BASH', BASH), patch.object(validation, 'ARTIFACT_LIMIT', 1):
            run = self.submit_and_wait()
        self.assertEqual((run['state'], run['exit_code']), ('complete', 0))
        self.assertNotIn('artifact', run)
        self.assertIn('retention limit', run['artifact_error'])
        _, content = self.runs.log(run['id'])
        self.assertIn('Artifact retention failed', content)

    @unittest.skipUnless(BASH and os.name != 'nt', 'a POSIX bash is unavailable')
    def test_artifact_symlinks_are_rejected_after_a_passing_build(self):
        (self.repository / 'scripts/build-web.sh').write_text(
            '#!/bin/sh\nmkdir -p web/public\nln -s /etc web/public/escape\necho built > web/public/index.html\n')
        self.target = commit(self.repository, 'linked output')
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual(run['state'], 'complete', self.runs.log(run['id'])[1])
        self.assertNotIn('artifact', run)
        self.assertIn('symlinks', run['artifact_error'])

    def test_service_restart_cleans_detached_worktree_and_requires_explicit_retry(self):
        from build_queue import BuildQueue
        from build_worker import recover_interrupted
        queue = BuildQueue(Path(self.temp.name) / 'queue')
        facade = ValidationRuns(self.projects, self.lookup, root=self.runs.root, queue=queue)
        submitted = facade.submit({'id': 'a' * 64})
        claimed = queue.claim()
        tree = self.runs.root / submitted['id'] / 'tree'
        self.runs._worktree(self.repository, tree, self.target)
        recover_interrupted(queue, self.runs)
        self.assertFalse(tree.exists())
        self.assertNotIn(str(tree).replace('\\', '/'), git(self.repository, 'worktree', 'list', '--porcelain').stdout.decode())
        self.assertEqual(queue.get(claimed['id'])['state'], 'interrupted')
        self.assertEqual(facade.submit({'id': 'a' * 64})['state'], 'interrupted')
        retried = facade.submit({'id': 'a' * 64, 'retry': True})
        self.assertEqual(retried['attempt'], 2)
        self.assertNotEqual(retried['id'], submitted['id'])

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_passing_script_cannot_package_modified_tracked_source(self):
        (self.repository / 'scripts/build-web.sh').write_text(
            '#!/bin/sh\necho changed >> scripts/build-web.sh\nmkdir -p web/public\necho built > web/public/index.html\n')
        self.target = commit(self.repository, 'self modifying build')
        with patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual(run['state'], 'failed', self.runs.log(run['id'])[1])
        checks = {check['id']: check for check in run['checks']}
        self.assertEqual(checks['runner:worktree-clean']['status'], 'failed')
        self.assertEqual(checks['runner:exact-commit']['status'], 'passed')
        self.assertFalse(run['required_checks_verified'])
        self.assertNotIn('artifact', run)

    def test_unexpected_worker_exception_is_terminal_not_a_restart_retry(self):
        from build_queue import BuildQueue
        from build_worker import execute_next
        queue = BuildQueue(Path(self.temp.name) / 'queue')
        facade = ValidationRuns(self.projects, self.lookup, root=self.runs.root, queue=queue)
        submitted = facade.submit({'id': 'a' * 64})
        with patch.object(self.runs, '_execute', side_effect=RuntimeError('poison request')), self.assertLogs(level='ERROR'):
            self.assertTrue(execute_next(queue, self.runs))
        self.assertEqual(queue.get(submitted['id'])['state'], 'error')
        self.assertIn('poison request', queue.get(submitted['id'])['note'])
        self.assertFalse(execute_next(queue, self.runs))

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_artifact_keeps_task_and_waiver_provenance_without_rewriting_event(self):
        (self.repository / 'scripts/build-web.sh').write_text(
            '#!/bin/sh\nmkdir -p web/public\necho built > web/public/index.html\n')
        self.target = commit(self.repository, 'waiver provenance')
        event = dict(self.lookup('a' * 64), state='validation_waived', job_id='task-job',
                     operator_approval={'actor': 'admin', 'reason': 'Earlier tools unavailable'}, reason='Validation waived')
        with patch.object(self.runs, 'lookup', return_value=event), patch.object(validation, 'BASH', BASH):
            run = self.submit_and_wait()
        self.assertEqual(run['state'], 'complete', self.runs.log(run['id'])[1])
        self.assertEqual(run['artifact']['task_id'], 'task-job')
        self.assertEqual(run['artifact']['validation_waiver']['approval']['actor'], 'admin')
        self.assertEqual(event['state'], 'validation_waived')

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
        self.assertEqual(run['state'], 'complete', self.runs.log(run['id'])[1])
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
