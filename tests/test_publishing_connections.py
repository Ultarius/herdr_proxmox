import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from github_api import GitHub, GitHubError
from repository_connections import RepositoryConnections
import server


class PublishingConnectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='herdr-publishing-source-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.github = GitHub(self.root / 'github.json')
        self.connections = RepositoryConnections(self.root / 'connections', self.root / 'projects', self.github)
        self.secret = 'github_pat_' + 's' * 40
        self.record = self.connections.create(dict(kind='token', label='Saved PAT', repository='owner/repo', token=self.secret))
        self.request = Mock(side_effect=self.response)
        self.github.request = self.request

    def response(self, path, method='GET', body=None, token=None):
        self.assertEqual(method, 'GET', 'Publishing setup must only read GitHub metadata')
        self.assertIsNone(body)
        if path == '/user':
            return {'login': 'reviewer' if token == 'github_pat_' + 'r' * 40 else 'owner'}
        if path.startswith('/repos/'):
            name = path.removeprefix('/repos/')
            return {'full_name': name, 'permissions': {'push': True},
                    'fork': name == 'contributor/repo', 'source': {'full_name': 'owner/repo'}}
        self.fail('Unexpected GitHub request: ' + path)

    def configure(self, **options):
        return self.connections.configure_publishing(dict(connection_id=self.record['id'], repositories=['owner/repo'], **options))

    def test_saved_pat_is_copied_and_never_returned_or_exercised_by_a_push(self):
        result = self.configure()
        self.assertTrue(result['configured'])
        self.assertEqual(self.github.configuration()['token'], self.secret)
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertNotIn(self.secret, json.dumps(self.connections.snapshot()))
        self.assertEqual(self.connections.get(self.record['id']), self.record)
        verification = result['publication']['owner/repo']['verification']
        self.assertEqual(verification['token_write_capability'], 'not_exercised')
        self.assertEqual(verification['upstream_pr_creation'], 'not_exercised')
        self.assertEqual([call.args[0] for call in self.request.call_args_list], ['/user', '/repos/owner/repo'])

    def test_rotation_and_removal_do_not_change_the_publishing_copy(self):
        self.configure()
        self.connections.rotate(self.record['id'], 'github_pat_' + 'n' * 40)
        self.assertEqual(self.github.configuration()['token'], self.secret)
        self.connections.remove(self.record['id'])
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.configure()
        self.assertEqual(self.github.configuration()['token'], self.secret)

    def test_invalid_ambiguous_and_cross_repository_sources_are_refused_before_network(self):
        for body in (None, {}, {'connection_id': '../outside', 'repositories': ['owner/repo']},
                     {'connection_id': self.record['id'], 'repositories': ['owner/repo'], 'token': self.secret},
                     {'connection_id': self.record['id'], 'repositories': ['owner/repo'], 'path': '/private/token'},
                     {'connection_id': self.record['id'], 'repositories': []},
                     {'connection_id': self.record['id'], 'repositories': ['other/repo']}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.connections.configure_publishing(body)
        with self.assertRaisesRegex(ValueError, 'owner/repo has branch destination other/fork; .* permits only owner/repo'):
            self.configure(publication={'owner/repo': {'mode': 'fork', 'publishing_repository': 'other/fork'}})
        self.request.assert_not_called()
        self.assertFalse(self.github.path.exists())

    def test_extra_direct_allowlist_entry_identifies_the_mismatched_destination(self):
        with self.assertRaisesRegex(ValueError, 'other/repo has branch destination other/repo; .* permits only owner/repo'):
            self.connections.configure_publishing(dict(connection_id=self.record['id'], repositories=['owner/repo', 'other/repo']))
        self.request.assert_not_called()

    def test_ssh_and_account_connections_are_not_pat_sources(self):
        for kind in ('ssh', 'github'):
            data = self.connections.load()
            data['connections'][self.record['id']]['kind'] = kind
            self.connections.save(data)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'personal access token'):
                self.configure()
        self.request.assert_not_called()

    def test_fork_mode_writes_only_the_selected_fork(self):
        record = self.connections.create(dict(kind='token', label='Fork', repository='contributor/repo', token=self.secret))
        result = self.connections.configure_publishing(dict(connection_id=record['id'], repositories=['owner/repo'],
            publication={'owner/repo': {'mode': 'fork', 'publishing_repository': 'contributor/repo'}}))
        self.assertEqual(result['publication']['owner/repo']['publishing_repository'], 'contributor/repo')
        self.assertEqual(self.github.configuration()['token'], self.secret)

    def test_failed_validation_preserves_configuration_and_releases_the_source(self):
        self.configure()
        before = self.github.path.read_bytes()
        self.request.side_effect = GitHubError(403)
        with self.assertRaises(GitHubError):
            self.configure()
        self.assertEqual(self.github.path.read_bytes(), before)
        self.assertEqual(self.connections.active_credentials, {})
        self.connections.rotate(self.record['id'], 'github_pat_' + 'n' * 40)

    def test_account_without_push_access_is_rejected(self):
        self.request.side_effect = lambda path, **kwargs: {'login': 'owner'} if path == '/user' else {'permissions': {'push': False}}
        with self.assertRaisesRegex(ValueError, 'push access'):
            self.configure()
        self.assertFalse(self.github.path.exists())

    def test_source_is_pinned_during_validation(self):
        def request(path, **kwargs):
            self.assertEqual(self.connections.active_credentials[self.record['id']], 1)
            with self.assertRaisesRegex(ValueError, 'in use'):
                self.connections.rotate(self.record['id'], 'github_pat_' + 'n' * 40)
            with self.assertRaisesRegex(ValueError, 'in use'):
                self.connections.remove(self.record['id'])
            return self.response(path, **kwargs)
        self.request.side_effect = request
        self.configure()
        self.assertEqual(self.connections.active_credentials, {})
        self.connections.remove(self.record['id'])

    def test_separate_pr_credential_is_preserved_and_explicit_clear_still_works(self):
        pr_secret = 'github_pat_' + 'r' * 40
        self.configure(pr_token=pr_secret)
        self.configure()
        self.assertEqual(self.github.configuration()['pr_token'], pr_secret)
        result = self.configure(clear_pr_credential=True)
        self.assertFalse(result['pr_configured'])
        self.assertNotIn('pr_token', self.github.configuration())

    def test_missing_or_linked_token_file_is_not_read(self):
        credential = self.connections.directory / self.record['id'] / 'token.json'
        credential.unlink()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.configure()
        external = self.root / 'external.json'
        external.write_text(json.dumps({'token': self.secret}))
        try:
            credential.symlink_to(external)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaisesRegex(ValueError, 'regular files'):
            self.configure()
        self.request.assert_not_called()

    def test_http_route_requires_admin_and_accepts_only_the_source_id_from_the_browser(self):
        listener = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        listener.token = 'admin'
        listener.operators = Mock()
        listener.operators.identify.side_effect = lambda value: {'name': 'reader', 'role': 'operator'} if value == 'reader' else None
        listener.github = self.github
        listener.repository_connections = self.connections
        worker = threading.Thread(target=listener.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(listener.server_close)
        self.addCleanup(worker.join, 3)
        self.addCleanup(listener.shutdown)
        body = json.dumps({'connection_id': self.record['id'], 'repositories': ['owner/repo']}).encode()
        self.assertNotIn(self.secret.encode(), body)
        endpoint = f'http://127.0.0.1:{listener.server_port}/api/github/configure'
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(endpoint, data=body, headers={'Authorization': 'Bearer reader'}))
        self.assertEqual(error.exception.code, 400)
        self.request.assert_not_called()
        with urlopen(Request(endpoint, data=body, headers={'Authorization': 'Bearer admin'})) as response:
            result = json.load(response)
        self.assertTrue(result['configured'])
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertEqual(self.github.configuration()['token'], self.secret)


if __name__ == '__main__':
    unittest.main()
