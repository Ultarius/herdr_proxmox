"""Real Git managed revisions, including interrupted ref/index publication."""
import base64
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
sys.path.insert(0, str(Path(__file__).parent))
import test_contributions as fixtures
import task_commits
import task_commit_client
import project_access
import server


def file_change(name='file', data=b'implementation\n', mode='100644'):
    return dict(path=name, mode=mode, content=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest())


class ManagedCommitTests(unittest.TestCase):
    git = fixtures.ContributionTests.git

    def setUp(self):
        fixtures.ContributionTests.setUp(self)
        self.tree = self.root / 'tree'
        self.git('worktree', 'add', '-b', self.task['branch'], str(self.tree), self.base)
        self.task.update(worktree=str(self.tree), run_id='run', state='implementing')
        self.service.save(self.task, 'launch', 'admin')
        self.run = dict(id='run', state='persona_sent', task_id=self.task['id'], profile_id='worker',
                        completion_token='session-token', toolchain_policy=dict(digest='frozen-policy'))
        self.store.snapshot.return_value['jobs'] = [self.run]
        self.body = dict(task_id=self.task['id'], run_id='run', token='session-token', request_id='revision-1',
                         parent=self.base, message='Implement task', files=[file_change()])

    def submit(self, **changes):
        return self.service.commits.submit(dict(self.body, **changes))

    def head(self):
        return self.git('rev-parse', 'HEAD', path=self.tree).strip()

    def test_git_failure_preserves_bounded_redacted_diagnostics(self):
        failure = subprocess.CompletedProcess([], 128, stdout=b'', stderr=(
            b'fatal: No space left on device; token=private-token https://private.example/repo ' + b'x' * 1400))
        with patch('task_commits.subprocess.run', return_value=failure):
            with self.assertRaisesRegex(ValueError, 'No space left on device') as caught:
                task_commits.command(self.tree, 'write-tree')
        message = str(caught.exception)
        self.assertLess(len(message), 1100)
        self.assertNotIn('private-token', message)
        self.assertNotIn('private.example', message)

    def test_git_diagnostics_use_strong_redaction_before_truncation(self):
        samples = [
            ('Authorization: Bearer alphabeticsecret', 'alphabeticsecret'),
            ('Authorization: Basic abc', 'abc'),
            ('ghp_privateCredential123', 'ghp_privateCredential123'),
            ('github_pat_privateCredential123', 'github_pat_privateCredential123'),
            ('sk-privateCredential123456789', 'sk-privateCredential123456789'),
            ('eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature', 'eyJhbGciOiJIUzI1NiJ9'),
            ('-----BEGIN PRIVATE KEY-----\nprivatekeydata\n-----END PRIVATE KEY-----', 'privatekeydata'),
            ('api_key: |\n  blocksecret\nend', 'blocksecret'),
            ('-----BEGIN PRIVATE KEY-----\n' + 'x' * 1200 + 'privatekeytail\n-----END PRIVATE KEY-----', 'x' * 20),
        ]
        for diagnostic, secret in samples:
            failure = subprocess.CompletedProcess([], 128, stdout=b'',
                stderr=('fatal: object write failed\n' + diagnostic).encode())
            with self.subTest(diagnostic=diagnostic[:40]), patch('task_commits.subprocess.run', return_value=failure):
                with self.assertRaisesRegex(ValueError, 'object write failed') as caught:
                    task_commits.command(self.tree, 'write-tree')
            self.assertNotIn(secret, str(caught.exception))
            self.assertLess(len(str(caught.exception)), 1100)

    def test_fresh_capture_refuses_wrong_checkout_before_reading_or_sending(self):
        context = dict(task_id=self.task['id'], run_id='run', token='session-token', checkout_root=str(self.tree))
        request_file = self.root / 'new-request.json'
        with patch('task_commit_client.read_json', return_value=context), \
                patch('task_commit_client.capture') as capture, patch('task_commit_client.urlopen') as send, \
                patch('task_commit_client.Path.cwd', return_value=self.tree / 'subdirectory'), \
                patch('sys.stderr', new_callable=io.StringIO) as output:
            result = task_commit_client.main(['--context', 'context.json', '--request-file', str(request_file),
                '--parent', self.base, '--message', 'change', '--file', 'file'])
        self.assertEqual(result, 1)
        self.assertIn('assigned checkout root', output.getvalue())
        capture.assert_not_called()
        send.assert_not_called()
        self.assertFalse(request_file.exists())

    def test_fresh_capture_uses_launch_checkout_and_persists_no_token(self):
        context = dict(task_id=self.task['id'], run_id='run', token='session-token', checkout_root=str(self.tree))
        request_file = self.root / 'new-request.json'
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"state":"complete"}'
        with patch('task_commit_client.read_json', return_value=context), \
                patch('task_commit_client.capture', return_value=[file_change()]) as capture, \
                patch('task_commit_client.urlopen', return_value=response), \
                patch('task_commit_client.Path.cwd', return_value=self.tree), \
                patch('sys.stdout', new_callable=io.StringIO):
            result = task_commit_client.main(['--context', 'context.json', '--request-file', str(request_file),
                '--parent', self.base, '--message', 'change', '--file', 'file'])
        self.assertEqual(result, 0)
        capture.assert_called_once_with(self.tree.resolve(), ['file'])
        self.assertNotIn('token', json.loads(request_file.read_text()))

    def test_exact_binary_bytes_deletions_modes_metadata_and_candidate_link(self):
        (self.tree / 'file').unlink()
        binary = b'\x00\xff\nraw\r\n'
        (self.tree / 'binary').write_bytes(binary)
        (self.tree / 'binary').chmod(0o755)
        self.body['files'] = [dict(path='file', mode='deleted'), file_change('binary', binary, '100755')]
        result = self.submit()
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.head(), result['sha'])
        self.assertEqual(self.git('rev-parse', result['sha'] + '^').strip(), self.base)
        blob = task_commits.command(self.tree, 'show', result['sha'] + ':binary')
        self.assertEqual(blob, binary)
        self.assertTrue(self.git('ls-tree', result['sha'], 'binary').startswith('100755 blob '))
        self.assertEqual(self.git('ls-tree', result['sha'], 'file'), '')
        metadata = self.git('show', '-s', '--format=%an|%cn|%ae|%ce', result['sha'])
        self.assertIn('Herdr agent worker|Herdr|agent@herdr.local|commits@herdr.local', metadata)
        self.assertEqual(result['policy_digest'], 'frozen-policy')
        self.assertNotIn('token', json.dumps(result))
        self.assertNotIn('payload', result)
        # Git on Windows cannot represent POSIX executable bits in the index
        # checkout; core.filemode=false still reports these bytes as clean.
        self.assertEqual(self.git('status', '--porcelain', path=self.tree).strip(), '')
        captured = self.service.action('candidate', dict(task_id=self.task['id'], request_id='capture-managed'), 'admin', 'admin')
        self.assertEqual(captured['candidate_revision'], result['id'])
        self.assertEqual(captured['head_sha'], result['sha'])
        self.assertFalse(captured.get('checks'))
        self.service.validation = MagicMock()
        self.service.validation.submit.return_value = dict(id='managed-check', state='queued')
        with patch('toolchain_policy.resolve_committed', return_value=dict(toolchains=[], declarations={}, lockfiles={}, checks=[])):
            self.service.action('build', dict(task_id=self.task['id'], request_id='managed-build', target=result['sha']), 'admin', 'admin')
        with closing(self.service.connect()) as db:
            event = json.loads(db.execute('SELECT data FROM build_events').fetchone()[0])
        self.assertEqual(event['task_revision_id'], result['id'])
        self.assertEqual(event['target'], result['sha'])

    def test_only_submitted_bytes_are_committed_and_live_edits_are_preserved(self):
        (self.tree / 'file').write_bytes(b'newer concurrent edit\n')
        (self.tree / 'unrelated').write_bytes(b'leave me\n')
        result = self.submit()
        self.assertEqual(task_commits.command(self.tree, 'show', result['sha'] + ':file'), b'implementation\n')
        self.assertEqual((self.tree / 'file').read_bytes(), b'newer concurrent edit\n')
        self.assertEqual((self.tree / 'unrelated').read_bytes(), b'leave me\n')
        self.assertIn('file', self.git('status', '--porcelain', path=self.tree))
        with self.assertRaisesRegex(ValueError, 'Commit task changes'):
            self.service.action('candidate', dict(task_id=self.task['id'], request_id='dirty-capture'), 'admin', 'admin')

    def test_replay_returns_same_revision_after_branch_advance_and_rejects_id_reuse(self):
        first = self.submit()
        second = self.submit(request_id='revision-2', parent=first['sha'], files=[file_change(data=b'second\n')])
        replay = self.submit()
        self.assertEqual(first, replay)
        self.assertEqual(self.head(), second['sha'])
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.submit(message='different')
        revisions = self.service.commits.revisions(self.task['id'])['revisions']
        self.assertEqual([item['sha'] for item in revisions], [second['sha'], first['sha']])
        for item in revisions:
            self.assertEqual(self.git('rev-parse', 'refs/herdr/revisions/' + item['id']).strip(), item['sha'])

    def test_stale_parent_staged_changes_and_existing_lock_are_refused_without_discard(self):
        first = self.submit()
        with self.assertRaisesRegex(ValueError, 'parent changed'):
            self.submit(request_id='stale')
        (self.tree / 'file').write_bytes(b'staged\n')
        self.git('add', 'file', path=self.tree)
        before = self.git('diff', '--cached', path=self.tree)
        with self.assertRaisesRegex(ValueError, 'Unstage'):
            self.submit(request_id='staged', parent=first['sha'])
        self.assertEqual(before, self.git('diff', '--cached', path=self.tree))
        self.git('reset', '--mixed', 'HEAD', path=self.tree)
        gitdir = Path(self.git('rev-parse', '--absolute-git-dir', path=self.tree).strip())
        (gitdir / 'index.lock').write_bytes(b'another operation')
        with self.assertRaisesRegex(ValueError, 'index is locked'):
            self.submit(request_id='locked', parent=first['sha'])
        self.assertEqual((gitdir / 'index.lock').read_bytes(), b'another operation')

    def test_bad_session_terminal_task_and_wrong_identity_cannot_commit_or_replay(self):
        for changes in (dict(token='wrong'), dict(run_id='other'), dict(token='\u2603')):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'current task session'):
                self.submit(**changes)
        self.run['profile_id'] = 'another'
        with self.assertRaisesRegex(ValueError, 'current task session'):
            self.submit()
        self.run['profile_id'] = 'worker'
        result = self.submit()
        self.task.update(state='completed')
        self.service.save(self.task, 'closed', 'admin')
        with self.assertRaisesRegex(ValueError, 'terminal'):
            self.submit()
        self.assertEqual(self.head(), result['sha'])

    def test_recovery_after_branch_move_before_index_replace_uses_same_commit(self):
        replace = os.replace
        def interrupted(source, target):
            if Path(target).name == 'index':
                raise OSError('simulated process interruption')
            return replace(source, target)
        with patch('task_commits.os.replace', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interruption'):
                self.submit()
        advanced = self.head()
        self.assertNotEqual(advanced, self.base)
        # Reconstruct the service to prove recovery does not use Python state.
        self.service.commits = task_commits.TaskCommits(self.service)
        recovered = self.submit()
        self.assertEqual(recovered['sha'], advanced)
        self.assertEqual(self.git('write-tree', path=self.tree).strip(), recovered['tree'])
        self.assertEqual(recovered['state'], 'complete')

    def test_recovery_after_index_replace_before_completion_checkpoint(self):
        save = self.service.commits.store
        def interrupted(record, fingerprint):
            if record['state'] == 'complete':
                raise OSError('lost completion checkpoint')
            return save(record, fingerprint)
        with patch.object(self.service.commits, 'store', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'checkpoint'):
                self.submit()
        head = self.head()
        self.assertEqual(self.submit()['sha'], head)

    def test_recovery_after_retained_ref_before_construction_checkpoint_is_deterministic(self):
        save = self.service.commits.store
        def interrupted(record, fingerprint):
            if record['state'] == 'constructed':
                raise OSError('lost construction checkpoint')
            return save(record, fingerprint)
        with patch.object(self.service.commits, 'store', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'construction'):
                self.submit()
        revision_id = self.service.commits.revisions(self.task['id'])['revisions'][0]['id']
        pinned = self.git('rev-parse', 'refs/herdr/revisions/' + revision_id).strip()
        self.assertEqual(self.head(), self.base)
        self.assertEqual(self.submit()['sha'], pinned)

    def test_external_branch_advance_between_construction_and_cas_is_preserved(self):
        original = task_commits.command
        def race(path, *args, **kwargs):
            if args[:2] == ('update-ref', 'refs/heads/' + self.task['branch']):
                self.git('update-ref', args[1], self.other, self.base)
            return original(path, *args, **kwargs)
        self.other = self.git('commit-tree', self.git('rev-parse', self.base + '^{tree}').strip(), '-p', self.base,
                              '-m', 'external advance').strip()
        with patch('task_commits.command', side_effect=race), self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.head(), self.other)
        with self.assertRaisesRegex(ValueError, 'advanced during submission'):
            self.submit()

    def test_index_edit_during_pending_submission_is_not_overwritten(self):
        with patch('task_commits.os.replace', side_effect=OSError('interrupt')):
            with self.assertRaises(OSError):
                self.submit()
        (self.tree / 'file').write_bytes(b'another staged edit\n')
        self.git('add', 'file', path=self.tree)
        before = self.git('diff', '--cached', path=self.tree)
        with self.assertRaisesRegex(ValueError, 'index changed'):
            self.submit()
        self.assertEqual(self.git('diff', '--cached', path=self.tree), before)

    def test_branch_switch_during_construction_is_refused_before_index_or_ref_change(self):
        before = self.git('write-tree', path=self.tree).strip()
        save = self.service.commits.store
        def switch(record, fingerprint):
            save(record, fingerprint)
            if record['state'] == 'constructed':
                self.git('symbolic-ref', 'HEAD', 'refs/heads/main', path=self.tree)
        with patch.object(self.service.commits, 'store', side_effect=switch), self.assertRaisesRegex(ValueError, 'branch changed'):
            self.submit()
        self.assertEqual(self.git('write-tree', path=self.tree).strip(), before)
        self.assertEqual(self.git('rev-parse', 'refs/heads/' + self.task['branch']).strip(), self.base)

    def killed_submission(self, stage):
        # os._exit bypasses finally: the kernel must release the repository
        # lock, and the next process must recognize only its own index marker.
        script = '''
import json,os,sqlite3,sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import task_commits
spec=json.load(sys.stdin)
task=spec['task']
service=SimpleNamespace(connect=lambda:sqlite3.connect(spec['database']),
    operation=lambda key:nullcontext(), get=lambda key:task,
    store=SimpleNamespace(snapshot=lambda **kwargs:dict(jobs=[spec['run']])),
    tree_for=lambda task:Path(task['worktree']), path_for=lambda task:Path(spec['repository']),
    save=lambda *args:None)
commits=task_commits.TaskCommits(service)
original=task_commits.command
def command(path,*args,**kwargs):
    result=original(path,*args,**kwargs)
    if spec['stage']=='ref' and args[:2]==('update-ref','refs/heads/'+task['branch']): os._exit(73)
    return result
task_commits.command=command
save=commits.store
def store(record,fingerprint):
    save(record,fingerprint)
    if spec['stage']=='complete' and record['state']=='complete': os._exit(73)
commits.store=store
commits.submit(spec['body'])
'''
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / 'web/gateway'))
        child = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=30, env=environment,
            input=json.dumps(dict(task=self.task, run=self.run, body=self.body, stage=stage,
                                 database=str(self.service.path), repository=str(self.repo))).encode())
        self.assertEqual(child.returncode, 73, child.stderr.decode())
        committed = self.head()
        gitdir = Path(self.git('rev-parse', '--absolute-git-dir', path=self.tree).strip())
        self.assertTrue((gitdir / 'index.lock').exists())
        recovered = self.submit()
        self.assertEqual(recovered['sha'], committed)
        self.assertEqual(recovered['state'], 'complete')
        self.assertFalse((gitdir / 'index.lock').exists())
        self.assertEqual(self.git('write-tree', path=self.tree).strip(), recovered['tree'])
        self.assertEqual(self.service.get(self.task['id'])['managed_revision']['id'], recovered['id'])

    def test_real_process_death_after_ref_update_recovers_its_own_lock_and_index(self):
        self.killed_submission('ref')

    def test_real_process_death_after_completion_recovers_lock_and_task_summary(self):
        self.killed_submission('complete')

    def test_concurrent_identical_requests_create_one_revision(self):
        answers, errors = [], []
        def submit():
            try:
                answers.append(self.submit())
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(answers), 2)
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(len(self.service.commits.revisions(self.task['id'])['revisions']), 1)

    def test_repository_hooks_and_signing_configuration_do_not_control_managed_commits(self):
        hooks = self.root / 'hooks'
        hooks.mkdir()
        hook = hooks / 'reference-transaction'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        self.git('config', 'core.hooksPath', str(hooks))
        self.git('config', 'commit.gpgSign', 'true')
        result = self.submit()
        self.assertEqual(self.head(), result['sha'])

    def test_invalid_paths_hashes_modes_and_payload_fields_never_move_head(self):
        for name in ('../file', '/absolute', '.git/config', 'a/.GIT/config', 'a//b', 'a\\b', 'a/./b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.submit(files=[file_change(name)])
        for files in ([file_change(mode='120000')], [file_change(), file_change()],
                      [dict(file_change(), sha256='a' * 64)], [dict(path='file', mode='deleted', content='')],
                      [file_change('file'), file_change('file/child')], [], [file_change(data=b'x' * (2 * 1024 * 1024 + 1))]):
            with self.subTest(files=str(files)[:60]), self.assertRaises(ValueError):
                self.submit(files=files)
        with self.assertRaises(ValueError):
            self.submit(author='operator')
        self.assertEqual(self.head(), self.base)

    def test_noop_and_nonregular_parent_entries_are_not_silently_replaced(self):
        with self.assertRaisesRegex(ValueError, 'no changes'):
            self.submit(files=[file_change(data=b'base\n')])
        with self.assertRaisesRegex(ValueError, 'absent'):
            self.submit(request_id='delete-missing', files=[dict(path='missing', mode='deleted')])
        with self.assertRaisesRegex(ValueError, 'non-directory ancestor'):
            self.submit(request_id='nested-file', files=[file_change('file/child')])

    def test_protected_input_commit_records_frozen_policy_without_approving_it(self):
        result = self.submit(files=[file_change('pyproject.toml', b'[project]\nrequires-python=">=3.13"\n')])
        self.assertEqual(result['policy_digest'], 'frozen-policy')
        self.assertIn('separate', result['policy_review'])
        task = self.service.get(self.task['id'])
        self.assertEqual(task['state'], 'implementing')
        self.assertNotIn('head_sha', task)
        self.assertFalse(task.get('builds'))
        with closing(self.service.connect()) as db:
            persisted = db.execute('SELECT data FROM task_revisions').fetchone()[0]
        self.assertNotIn('session-token', persisted)
        self.assertNotIn('content', persisted)

    def test_http_submission_uses_session_token_and_history_uses_dashboard_auth(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.token = 'dashboard-secret'
        httpd.contributions = self.service
        self.addCleanup(httpd.server_close)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.shutdown)
        url = 'http://127.0.0.1:' + str(httpd.server_port)
        def post(body):
            return Request(url + '/api/tasks/submit', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as denied:
            urlopen(post(dict(self.body, token='wrong')), timeout=5)
        self.assertEqual(denied.exception.code, 400)
        denied.exception.close()
        with urlopen(post(self.body), timeout=5) as response:
            result = json.load(response)
        history = url + '/api/tasks/revisions?id=' + self.task['id']
        with self.assertRaises(HTTPError) as denied:
            urlopen(history, timeout=5)
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        with urlopen(Request(history, headers={'Authorization': 'Bearer dashboard-secret'}), timeout=5) as response:
            self.assertEqual(json.load(response)['revisions'][0]['sha'], result['sha'])
        with self.assertRaises(HTTPError) as denied:
            urlopen(Request(url + '/api/tasks/publish', data=b'{}', headers={'Authorization': 'Bearer session-token'}), timeout=5)
        self.assertEqual(denied.exception.code, 401)
        denied.exception.close()
        # A stored request is replayed over the real HTTP path, without reading
        # live source bytes or putting the token into the request artifact.
        request_file = self.root / 'client-request.json'
        task_commit_client.write_private(request_file, {key: value for key, value in self.body.items() if key != 'token'}, exclusive=True)
        def read(path):
            if str(path) == 'context.json':
                return {key: self.body[key] for key in ('task_id', 'run_id', 'token')}
            return json.loads(Path(path).read_text())
        with patch('task_commit_client.read_json', side_effect=read), patch('task_commit_client.capture', side_effect=AssertionError('recapture')), patch('sys.stdout', new_callable=io.StringIO) as output:
            code = task_commit_client.main(['--context', 'context.json', '--request-file', str(request_file), '--gateway', url])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['sha'], result['sha'])
        self.assertNotIn('session-token', request_file.read_text())

    def test_history_pagination_is_bounded_and_hides_pending_captured_bytes(self):
        with closing(self.service.connect()) as db, db:
            for number in range(56):
                record = dict(id=str(number), task_id=self.task['id'], state='intent',
                              payload=dict(files=[dict(content='private-captured-bytes')]), index_before='private-index')
                db.execute('INSERT INTO task_revisions VALUES (?,?,?,?)',
                           (str(number), self.task['id'], 'fingerprint', json.dumps(record)))
        first = self.service.commits.revisions(self.task['id'])
        second = self.service.commits.revisions(self.task['id'], first['next_before'])
        self.assertEqual(len(first['revisions']), 50)
        self.assertEqual(len(second['revisions']), 6)
        self.assertIsNone(second['next_before'])
        self.assertEqual(first['revisions'][0]['id'], '55')
        self.assertNotIn('private', json.dumps(first))
        compact = self.service.commits.revisions(self.task['id'], limit=10)
        following = self.service.commits.revisions(self.task['id'], compact['next_before'], limit=10)
        self.assertEqual(len(compact['revisions']), 10)
        self.assertEqual(following['revisions'][0]['id'], '45')
        for invalid in (0, 51, True):
            with self.subTest(limit=invalid), self.assertRaisesRegex(ValueError, 'limit'):
                self.service.commits.revisions(self.task['id'], limit=invalid)

    def test_journal_identity_cannot_be_replaced_by_another_payload(self):
        result = self.submit()
        with self.assertRaisesRegex(ValueError, 'different content'):
            self.service.commits.store(dict(result, message='overwritten'), 'different-fingerprint')
        self.assertEqual(self.service.commits.revisions(self.task['id'])['revisions'][0], result)


class ClientCaptureTests(unittest.TestCase):
    def test_unsupported_capture_fails_cleanly_without_posix_flags(self):
        with patch('task_commit_client.ANCHORED_ACCESS', False):
            with self.assertRaisesRegex(ValueError, 'Linux gateway'):
                task_commit_client.capture(Path('.'), ['file'])

    @unittest.skipUnless(project_access.ANCHORED_ACCESS, 'Descriptor-anchored capture requires Linux.')
    def test_anchored_capture_binary_delete_symlink_and_immutable_request(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'file').write_bytes(b'\x00\xff')
            (root / 'link').symlink_to('file')
            files = task_commit_client.capture(root, ['file', 'missing', 'removed/file'])
            self.assertEqual(base64.b64decode(files[0]['content']), b'\x00\xff')
            self.assertEqual(files[1], dict(path='missing', mode='deleted'))
            self.assertEqual(files[2], dict(path='removed/file', mode='deleted'))
            with self.assertRaisesRegex(ValueError, 'contains a symbolic link'):
                task_commit_client.capture(root, ['link'])
            request = root / 'request.json'
            task_commit_client.write_private(request, {'files': files}, exclusive=True)
            self.assertEqual(task_commit_client.read_json(request)['files'], files)
            with self.assertRaises(FileExistsError):
                task_commit_client.write_private(request, {'files': []}, exclusive=True)
