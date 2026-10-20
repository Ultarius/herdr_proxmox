"""Real Git questions without checkouts; mutable evidence never becomes cached truth."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import threading

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from repository_queries import RepositoryQueries, ReadOnlyTasks
from operation_metrics import Metrics, measured, query_measurement
import project_git
from repository_lock import repository_lock
import server


class RepositoryQueryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.test')
        (self.repo / 'file.txt').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.repo / 'file.txt').write_text('changed\n')
        self.git('commit', '-am', 'change')
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.task = dict(id='a' * 32, repository='repo', state='review_ready', head_sha=self.head,
                         builds={self.head: dict(target=self.head, state='complete', policy_digest='policy',
                             required_checks_verified=True, checks=[dict(id='test', status='passed', required=True)])})
        self.release = dict(id='b' * 32, repository='repo', state='ready', members=[],
            revision=dict(digest='manifest', members=[dict(task_id=self.task['id'], candidate_sha=self.head)]),
            results={self.task['id']: dict(state='incorporated', integration_sha=self.head)}, resulting_sha=self.head)
        tasks = SimpleNamespace(get=lambda _: copy.deepcopy(self.task),
            releases=SimpleNamespace(get=lambda _: copy.deepcopy(self.release)))
        self.queries = RepositoryQueries(self.root, tasks=tasks)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout

    def query(self, kind, **extra):
        return self.queries.query(dict(repository='repo', kind=kind, **extra))

    def test_immutable_queries_ignore_working_edits_and_warm_cache_skips_git_diff(self):
        (self.repo / 'file.txt').write_text('uncommitted secret\n')
        before = self.git('worktree', 'list', '--porcelain')
        cold = self.query('diff', revision=self.head, base=self.base)
        self.assertIn('+changed', cold['result']['patch'])
        self.assertNotIn('uncommitted', cold['result']['patch'])
        warm = self.query('diff', revision=self.head, base=self.base)
        self.assertEqual(cold['result'], warm['result'])
        self.assertTrue(warm['cached'])
        self.assertLess(warm['measurement']['git_calls'], cold['measurement']['git_calls'])
        warm['result']['patch'] = 'caller mutation'
        self.assertNotEqual(self.query('diff', revision=self.head, base=self.base)['result']['patch'], 'caller mutation')
        self.assertEqual(before, self.git('worktree', 'list', '--porcelain'))

    def test_ancestry_and_bounded_history_report_exact_commits(self):
        result = self.query('ancestry', revision=self.head, base=self.base)['result']
        self.assertTrue(result['base_is_ancestor'])
        self.assertFalse(result['revision_is_ancestor'])
        self.assertEqual(result['revision_only_commits'], 1)
        history = self.query('history', revision=self.head, limit=1)['result']
        self.assertEqual([row['sha'] for row in history['commits']], [self.head])
        self.assertEqual(history['commits'][0]['parents'], [self.base])
        all_history = self.query('history', revision=self.head)['result']['commits']
        self.assertEqual([row['sha'] for row in all_history], [self.head, self.base])

    def test_oversized_answers_are_refused_and_never_cached(self):
        self.queries.analysis = SimpleNamespace(report=lambda *args, **kwargs: {'payload': 'x' * 1_000_001})
        with self.assertRaisesRegex(ValueError, 'exceeds 1 MB'):
            self.query('requirements', revision=self.head)
        self.assertEqual(len(self.queries.cache), 0)

    def test_cache_cannot_survive_repository_replacement_without_its_commits(self):
        self.query('history', revision=self.head)
        self.repo.rename(self.root / 'old-repository')
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        with self.assertRaises(ValueError):
            self.query('history', revision=self.head)

    def test_large_patch_reports_truncation(self):
        (self.repo / 'large.txt').write_text('new line\n' * 9000)
        self.git('add', '.')
        self.git('commit', '-m', 'large change')
        revision = self.git('rev-parse', 'HEAD').strip()
        result = self.query('diff', revision=revision, base=self.base)['result']
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['patch']), 65536)

    def test_paths_revisions_and_limits_are_refused_before_execution(self):
        for extra in (dict(revision='HEAD'), dict(revision='--all'), dict(revision=self.head[:8]),
                      dict(revision=self.head, limit=51), dict(revision=self.head, file='../file'),
                      dict(revision=self.head, file='/outside')):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.query('history', **extra)
        for name in ('../outside', str(self.repo), 'repo/../repo'):
            with self.assertRaises(ValueError):
                self.queries.query(dict(repository=name, kind='history', revision=self.head))
        with self.assertRaises(ValueError):
            self.query('fetch', revision=self.head)
        self.assertEqual(self.query('diff', revision=self.head, base=self.base, file='*')['result']['patch'], '')

    def test_recorded_evidence_is_never_cached_or_promoted_to_current_approval(self):
        first = self.query('evidence', task_id=self.task['id'])
        self.assertTrue(first['result']['recorded_checks_passed'])
        self.assertEqual(first['result']['current_policy'], 'not_evaluated')
        self.task['head_sha'] = self.base
        current = self.query('evidence', task_id=self.task['id'])
        self.assertFalse(current['result']['recorded_checks_passed'])
        historical = self.query('evidence', task_id=self.task['id'], revision=self.head)
        self.assertTrue(historical['result']['recorded_checks_passed'])
        self.assertFalse(historical['result']['current_candidate_matches'])
        self.assertFalse(historical['cached'])
        self.task['builds'][self.head]['required_checks_verified'] = False
        self.assertFalse(self.query('evidence', task_id=self.task['id'], revision=self.head)['result']['recorded_checks_passed'])
        self.task['repository'] = 'elsewhere'
        with self.assertRaisesRegex(ValueError, 'another repository'):
            self.query('status', task_id=self.task['id'])

    def test_incorporation_keeps_selected_and_current_candidates_distinct(self):
        self.task['head_sha'] = self.base
        result = self.query('incorporation', task_id=self.task['id'], release_id=self.release['id'])['result']
        self.assertEqual(result['incorporation'], 'incorporated')
        self.assertEqual(result['revision'], self.head)
        self.assertFalse(result['current_candidate_matches'])
        self.release['results'] = {}
        self.assertEqual(self.query('incorporation', task_id=self.task['id'], release_id=self.release['id'])['result']['incorporation'], 'pending')

    def test_requirements_use_committed_report_without_host_assessment(self):
        calls = []
        self.queries.analysis = SimpleNamespace(report=lambda path, revision: calls.append((path, revision)) or
                                               dict(source=dict(scope='committed', revision=revision)))
        self.query('requirements', revision=self.head)
        self.query('requirements', revision=self.head)
        self.assertEqual(calls, [('repo', self.head)])

    def test_cli_reads_sqlite_without_initializing_or_writing_it(self):
        database = self.root / 'tasks.sqlite3'
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TABLE tasks (id TEXT, data TEXT)')
            db.execute('INSERT INTO tasks VALUES (?,?)', (self.task['id'], json.dumps(self.task)))
        original = database.read_bytes()
        records = ReadOnlyTasks(database)
        self.assertEqual(records.get(self.task['id']), self.task)
        output = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'web/gateway/repository_queries.py'),
            '--projects', str(self.root), '--repository', 'repo', '--kind', 'evidence',
            '--tasks-db', str(database), '--task-id', self.task['id']], capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(output.stdout)['result']['recorded_checks_passed'])
        self.assertEqual(database.read_bytes(), original)

    def test_http_authentication_query_bounds_and_diagnostics_role(self):
        gateway = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        gateway.token = 'a' * 48
        gateway.repository_queries = self.queries
        gateway.operators = SimpleNamespace(identify=lambda token: dict(name='admin', role='admin')
                                            if token == gateway.token else dict(name='reader', role='operator'))
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(gateway.server_close)
        self.addCleanup(gateway.shutdown)
        endpoint = 'http://127.0.0.1:' + str(gateway.server_port)
        # No operators service for an empty credential: auth must reject it.
        with self.assertRaises(HTTPError) as denied:
            urlopen(endpoint + '/api/projects/query?repository=repo&kind=history&revision=' + self.head)
        self.assertEqual(denied.exception.code, 401)
        headers = {'Authorization': 'Bearer ' + gateway.token}
        with urlopen(Request(endpoint + '/api/projects/query?repository=repo&kind=history&revision=' + self.head, headers=headers)) as response:
            result = json.load(response)
            self.assertGreater(int(response.headers['X-Herdr-Git-Calls']), 0)
            self.assertEqual(int(response.headers['X-Herdr-Git-Calls']), result['measurement']['git_calls'])
        self.assertEqual(result['result']['commits'][0]['sha'], self.head)
        with self.assertRaises(HTTPError) as invalid:
            urlopen(Request(endpoint + '/api/projects/query?repository=repo&kind=history&kind=diff&revision=' + self.head, headers=headers))
        self.assertEqual(invalid.exception.code, 400)
        with self.assertRaises(HTTPError) as denied:
            urlopen(Request(endpoint + '/api/diagnostics/performance', headers={'Authorization': 'Bearer viewer'}))
        self.assertEqual(denied.exception.code, 403)
        self.assertEqual(json.load(urlopen(Request(endpoint + '/api/diagnostics/performance', headers=headers)))['scope'], 'process')


class OperationMetricTests(unittest.TestCase):
    def test_nested_query_metrics_count_once_in_each_request_scope(self):
        metrics = Metrics()
        with patch('operation_metrics.METRICS', metrics):
            with query_measurement('http.read') as outer:
                with query_measurement() as inner:
                    with measured('git.command'):
                        pass
            self.assertEqual(outer['git_calls'], 1)
            self.assertEqual(inner['git_calls'], 1)
            with query_measurement() as other:
                pass
            self.assertEqual(other['git_calls'], 0)
    def test_samples_are_bounded_failures_counted_and_payloads_cannot_enter_series(self):
        metrics = Metrics()
        for index in range(300):
            metrics.observe('git.command', index / 1000, failed=index == 299)
        row = metrics.snapshot()['operations']['git.command']
        self.assertEqual(row['count'], 300)
        self.assertEqual(row['samples'], 256)
        self.assertEqual(row['failed'], 1)
        self.assertGreaterEqual(row['p95_ms'], row['p50_ms'])
        with self.assertRaises(ValueError):
            metrics.observe('secret arbitrary path', 1)

    def test_reentrant_locks_count_once_and_failure_releases_the_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(['git', 'init', temporary], capture_output=True, check=True)
            metrics = Metrics()
            with patch('operation_metrics.METRICS', metrics):
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    with repository_lock(temporary):
                        with repository_lock(temporary):
                            raise RuntimeError('interrupted')
                with repository_lock(temporary):
                    pass
            rows = metrics.snapshot()['operations']
            self.assertEqual(rows['repository.lock_hold']['count'], 2)
            self.assertEqual(rows['repository.lock_hold']['failed'], 1)
            self.assertEqual(rows['repository.lock_wait']['count'], 2)
