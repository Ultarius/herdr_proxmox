"""Exercise protected credential selection at the HTTP transport boundary."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from github_api import GitHub, GitHubError

UPSTREAM = 'SourceOwner/project'
FORK = 'ForkOwner/project'
SHA = 'a' * 40


class ProtectedTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.policy = self.root / 'policy.json'
        self.policy.write_text(json.dumps({'version': 1, 'protected': [dict(
            upstream=UPSTREAM, fork=FORK, mode='fork_only', arrangement='manual_adoption')]}), encoding='utf-8')
        self.path = self.root / 'github.json'
        self.api = GitHub(self.path, policy_path=self.policy)
        self.push = 'github_pat_' + 'p' * 40
        self.pr = 'github_pat_' + 'r' * 40
        self.calls = []
        self.call_lock = threading.Lock()
        self.reject_upstream = False
        self.opener = MagicMock()
        self.opener.open.side_effect = self.open
        self.patch = patch('github_api.build_opener', return_value=self.opener)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def body(self):
        return dict(token=self.push, pr_token=self.pr, repositories=[UPSTREAM],
                    publication={UPSTREAM: dict(mode='fork', publishing_repository=FORK)})

    def open(self, request, timeout=None):
        path = urlsplit(request.full_url).path
        token = request.get_header('Authorization').removeprefix('Bearer ')
        with self.call_lock:
            self.calls.append((path, token, threading.get_ident()))
        self.assertEqual(request.get_method(), 'GET', 'Protected setup performs no GitHub write')
        if path == '/user':
            self.assertIn(token, (self.push, self.pr))
            value = dict(login='fork-user' if token == self.push else 'upstream-user')
        elif path == '/repos/' + UPSTREAM:
            self.assertEqual(token, self.pr, 'Only the candidate upstream PAT may inspect upstream')
            if self.reject_upstream:
                raise HTTPError(request.full_url, 403, 'denied', {}, io.BytesIO(b'{}'))
            value = dict(full_name=UPSTREAM, default_branch='main', permissions=dict(push=False))
        elif path == '/repos/' + FORK:
            self.assertEqual(token, self.push, 'Only the candidate fork PAT may inspect its destination')
            value = dict(full_name=FORK, default_branch='main', fork=True,
                         source=dict(full_name=UPSTREAM), permissions=dict(push=True))
        elif path.startswith('/repos/' + UPSTREAM + '/branches/'):
            self.assertEqual(token, self.pr)
            value = dict(commit=dict(sha=SHA))
        elif path == '/repos/' + FORK + '/branches/main':
            self.assertEqual(token, self.pr)
            raise HTTPError(request.full_url, 404, 'not found', {}, io.BytesIO(b'{}'))
        else:
            raise AssertionError('Unexpected transport path: ' + path)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(value).encode()
        response.headers = {}
        return response

    def write_record(self, token, pr_token):
        self.path.write_text(json.dumps(dict(token=token, pr_token=pr_token,
            login='fork-user', pr_login='upstream-user', repositories=[UPSTREAM],
            publication={UPSTREAM: dict(mode='fork', publishing_repository=FORK)})), encoding='utf-8')
        if os.name != 'nt':
            self.path.chmod(0o600)

    def test_first_setup_uses_candidate_credentials_in_pool_workers(self):
        main_thread = threading.get_ident()
        snapshot = self.api.configure(self.body())
        self.assertTrue(snapshot['configured'])
        self.assertEqual(snapshot['publication'][UPSTREAM]['capabilities'][FORK], 'branch_unavailable')
        metadata = [entry for entry in self.calls if entry[0] in ('/repos/' + UPSTREAM, '/repos/' + FORK)]
        self.assertEqual(len(metadata), 2)
        self.assertTrue(all(entry[2] != main_thread for entry in metadata), 'Metadata reads exercise worker-local candidate propagation')
        self.assertIsNone(getattr(self.api._candidate_credentials, 'value', None))
        self.assertEqual(self.api.configuration()['token'], self.push)
        self.assertEqual(self.api.configuration()['pr_token'], self.pr)
        for secret in (self.push, self.pr):
            self.assertNotIn(secret, json.dumps(snapshot))
        self.assertTrue(all('/release-' not in entry[0] for entry in self.calls))

    def test_rotation_uses_new_candidates_not_old_saved_credentials(self):
        self.api.configure(self.body())
        old_push, old_pr = self.push, self.pr
        self.calls.clear()
        self.push = 'github_pat_' + 'n' * 40
        self.pr = 'github_pat_' + 's' * 40
        self.api.configure(self.body())
        self.assertTrue(self.calls)
        self.assertTrue(all(token not in (old_push, old_pr) for _, token, _ in self.calls))
        self.assertEqual(self.api.pr_credential(UPSTREAM).token, self.pr)
        count = len(self.calls)
        with self.assertRaisesRegex(ValueError, 'selected source'):
            self.api.request('/repos/' + UPSTREAM, token=old_pr, credential_role='pull request token')
        self.assertEqual(len(self.calls), count)

    def test_invalid_legacy_classic_record_can_be_replaced_with_fine_grained_pair(self):
        self.write_record('ghp_' + 'o' * 40, 'ghp_' + 'q' * 40)
        snapshot = self.api.snapshot()
        self.assertFalse(snapshot['configured'])
        self.assertIn(UPSTREAM.lower(), snapshot['protected_publication'])
        self.assertIn('fine-grained', snapshot['error'])
        configured = self.api.configure(self.body())
        self.assertTrue(configured['configured'])
        self.assertEqual(self.api.configuration()['token'], self.push)
        self.assertEqual(self.api.configuration()['pr_token'], self.pr)

    def test_failed_candidate_scope_is_cleared_and_saved_credentials_still_work(self):
        self.api.configure(self.body())
        saved = self.path.read_bytes()
        old_push, old_pr = self.push, self.pr
        self.push = 'github_pat_' + 'f' * 40
        self.pr = 'github_pat_' + 'g' * 40
        self.reject_upstream = True
        with self.assertRaises(GitHubError) as failed:
            self.api.configure(self.body())
        self.assertEqual(failed.exception.status, 403)
        self.assertEqual(self.path.read_bytes(), saved)
        self.assertIsNone(getattr(self.api._candidate_credentials, 'value', None))
        rejected_pr = self.pr
        self.push, self.pr = old_push, old_pr
        self.reject_upstream = False
        self.api.request('/repos/' + UPSTREAM, token=old_pr, credential_role='pull request token')
        count = len(self.calls)
        with self.assertRaisesRegex(ValueError, 'selected source'):
            self.api.request('/repos/' + UPSTREAM, token=rejected_pr, credential_role='pull request token')
        self.assertEqual(len(self.calls), count)

    def test_fragments_encoded_owner_and_encoded_route_are_rejected_before_transport(self):
        self.api.configure(self.body())
        paths = ['/repos/' + UPSTREAM + '#unprotected', '/repos/%55ltarius/bevy_game',
                 '/repos/Ultarius/bevy%5fgame', '/%72epos/' + UPSTREAM,
                 '//repos/' + UPSTREAM, '/repos/Ultarius%2fbevy_game']
        count = len(self.calls)
        for path in paths:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'Invalid GitHub API path'):
                self.api.request(path, 'PATCH', {}, token=self.pr, credential_role='pull request token')
        self.assertEqual(len(self.calls), count)
        self.api.request('/repos/' + UPSTREAM + '/branches/feature%2Fchecked',
                         token=self.pr, credential_role='pull request token')

    def test_raw_credential_reads_do_not_bypass_protected_fine_grained_transport_guard(self):
        legacy_push, legacy_pr = 'ghp_' + 'x' * 40, 'ghp_' + 'y' * 40
        self.write_record(legacy_push, legacy_pr)
        self.assertEqual(self.api.configuration()['token'], legacy_push)
        count = len(self.calls)
        with self.assertRaisesRegex(ValueError, 'fine-grained'):
            self.api.request('/repos/' + UPSTREAM + '/pulls/1', 'PATCH', {'state':'closed'},
                             token=legacy_pr, credential_role='pull request token')
        self.assertEqual(len(self.calls), count)

    def test_role_label_cannot_spoof_selected_credential(self):
        self.api.configure(self.body())
        attempts = [('/repos/' + UPSTREAM, self.push, 'pull request token'),
                    ('/repos/' + UPSTREAM, self.pr, 'push token'),
                    ('/repos/' + FORK, self.push, 'pull request token'),
                    ('/repos/' + UPSTREAM, 'github_pat_' + 'x' * 40, 'pull request token')]
        count = len(self.calls)
        for path, token, role in attempts:
            with self.subTest(path=path, role=role), self.assertRaises(ValueError):
                self.api.request(path, token=token, credential_role=role)
        self.assertEqual(len(self.calls), count)
        self.api.request('/repos/' + UPSTREAM, token=self.pr, credential_role='pull request token')


if __name__ == '__main__':
    unittest.main()
