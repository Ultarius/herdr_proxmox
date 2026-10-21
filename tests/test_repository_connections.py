import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from github_api import GitHub
from project_files import ProjectJobs, clone_repository
import project_git
from repository_askpass import permitted
from repository_connections import RepositoryConnections, repository_url, HOST_FINGERPRINT

HOST_KEY = 'AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'


class RepositoryConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.projects = self.root / 'projects'
        self.projects.mkdir()
        self.github = GitHub(self.root / 'github.json')
        self.service = RepositoryConnections(self.root / 'connections', self.projects, self.github)

    def github_connection(self):
        self.github.path.write_text(json.dumps({'token': 'secret_' + 'a' * 30}), encoding='utf-8')
        self.github.path.chmod(0o600)
        return self.service.create(dict(label='Private project', repository='owner/private', kind='github'))

    def ssh_connection(self):
        def generate(arguments, **kwargs):
            key = Path(arguments[-1])
            key.write_text('PRIVATE KEY CONTENT', encoding='utf-8')
            key.with_suffix('.pub').write_text('ssh-ed25519 PUBLIC test', encoding='utf-8')
            return subprocess.CompletedProcess(arguments, 0)
        with patch('repository_connections.subprocess.run', side_effect=generate):
            return self.service.create(dict(label='Private project', repository='owner/private', kind='ssh'))

    def verify(self, identifier):
        with patch('repository_connections.subprocess.run', return_value=subprocess.CompletedProcess([], 0,
                   stdout=('github.com ssh-ed25519 ' + HOST_KEY + '\n').encode())):
            return self.service.verify_host(identifier)

    def test_urls_are_exact_github_repository_identities(self):
        self.assertEqual(repository_url('git@github.com:owner/private.git'), ('owner/private', 'ssh'))
        self.assertEqual(repository_url('https://github.com/owner/private'), ('owner/private', 'github'))
        self.assertEqual(repository_url('https://GITHUB.com:443/owner/private.git/'), ('owner/private', 'github'))
        self.assertEqual(repository_url('git@github.com:owner/private.git/'), ('owner/private', 'ssh'))
        for url in ['https://github.com.evil/owner/private', 'https://user:token@github.com/owner/private',
                    'https://github.com/owner/private?token=secret', 'https://github.com/../private',
                    'git@other.test:owner/private', 'file:///tmp/repo', 'https://github.com:8443/owner/private']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                repository_url(url)

    def test_only_admin_can_create_and_private_bytes_never_enter_snapshot(self):
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.service.action({'action': 'create'}, 'operator')
        record = self.ssh_connection()
        snapshot = json.dumps(self.service.snapshot())
        self.assertNotIn('PRIVATE KEY CONTENT', snapshot)
        self.assertIn('ssh-ed25519 PUBLIC', snapshot)
        self.assertEqual(RepositoryConnections(self.service.directory, self.projects, self.github).get(record['id']), record)
        self.assertFalse((self.root / '.ssh/config').exists())
        if os.name != 'nt':
            self.assertEqual((self.service.directory / record['id'] / 'identity').stat().st_mode & 0o777, 0o600)

    def test_host_key_is_pinned_and_unknown_key_never_gets_trusted(self):
        record = self.ssh_connection()
        with patch('repository_connections.subprocess.run', return_value=subprocess.CompletedProcess([], 0,
                   stdout=b'github.com ssh-ed25519 YWJj\n')):
            with self.assertRaisesRegex(ValueError, 'No host was trusted'):
                self.service.verify_host(record['id'])
        self.assertFalse(self.service.get(record['id'])['host_verified'])
        verified = self.verify(record['id'])
        self.assertEqual(verified['host_fingerprint'], HOST_FINGERPRINT)
        with self.service.credentials(record['id'], 'git@github.com:owner/private.git') as environment:
            self.assertIn('StrictHostKeyChecking=yes', environment['GIT_SSH_COMMAND'])
            self.assertIn('IdentitiesOnly=yes', environment['GIT_SSH_COMMAND'])
            self.assertIn(record['id'], environment['GIT_SSH_COMMAND'])
            self.assertIn('GlobalKnownHostsFile=', environment['GIT_SSH_COMMAND'])

    def test_credentials_refuse_other_repositories_and_require_host_verification(self):
        record = self.ssh_connection()
        for url in ['git@github.com:owner/other.git', 'https://github.com/owner/private.git', 'git@evil.test:owner/private.git']:
            with self.subTest(url=url), self.assertRaises(ValueError), self.service.credentials(record['id'], url):
                pass
        with self.assertRaisesRegex(ValueError, 'Verify'), self.service.credentials(record['id'], 'git@github.com:owner/private.git'):
            pass

    def test_https_askpass_is_prompt_scoped_and_does_not_expose_token_in_environment(self):
        record = self.github_connection()
        with self.service.credentials(record['id'], 'https://github.com/owner/private.git') as env:
            self.assertNotIn('secret_', json.dumps(env))
            helper = Path(env['GIT_ASKPASS'])
            self.assertTrue(helper.exists())
            script = Path(__file__).parents[1] / 'web/gateway/repository_askpass.py'
            allowed = subprocess.run([sys.executable, str(script), "Password for 'https://x-access-token@github.com/owner/private.git':"], env=env, capture_output=True)
            self.assertEqual(allowed.stdout.strip(), ('secret_' + 'a' * 30).encode())
            denied = subprocess.run([sys.executable, str(script), "Password for 'https://github.com.evil/owner/private.git':"], env=env, capture_output=True)
            self.assertEqual(denied.returncode, 1)
            username = subprocess.run([sys.executable, str(script), "Username for 'https://github.com:443/OWNER/PRIVATE.git':"], env=env, capture_output=True)
            self.assertEqual(username.stdout.strip(), b'x-access-token')
            self.assertEqual(denied.stdout, b'')
        self.assertFalse(helper.exists())
        for prompt in ["Password for 'https://github.com/owner/other.git':", "Password for 'https://github.com:8443/owner/private.git':", "Password for 'https://github.com/':", "Not a prompt https://github.com/owner/private.git"]:
            self.assertFalse(permitted(prompt, 'owner/private'))
        for suffix in ('?token=value', '#fragment', ':invalid'):
            url = 'https://github.com/owner/private.git' + suffix if suffix != ':invalid' else 'https://github.com:invalid/owner/private.git'
            self.assertFalse(permitted("Password for '" + url + "':", 'owner/private'))
        self.assertFalse(permitted("Password for 'https://github.com/owner/private.git' then 'https://evil.test/owner/private.git':", 'owner/private'))
        self.assertTrue(permitted("Username for 'https://github.com:443/OWNER/PRIVATE.git':", 'owner/private'))

    def test_credentials_scrub_inherited_git_and_github_overrides_before_clone(self):
        record = self.github_connection()
        overrides = dict(GIT_ASKPASS='hostile', GIT_CONFIG_GLOBAL='hostile', GIT_CONFIG_COUNT='999',
                         GIT_CONFIG_KEY_88='hostile', GIT_SSH_COMMAND='hostile', HERDR_GITHUB_TOKEN='hostile',
                         HERDR_GITHUB_CONFIG='hostile')
        def clone(arguments, **kwargs):
            env = kwargs['env']
            self.assertFalse(any(value == 'hostile' for value in env.values()))
            self.assertNotIn('GIT_CONFIG_KEY_88', env)
            self.assertNotIn('HERDR_GITHUB_TOKEN', env)
            self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
            self.assertEqual(env['HERDR_GITHUB_CONFIG'], str(self.github.path))
            return subprocess.CompletedProcess(arguments, 0, stderr='')
        with patch.dict(os.environ, overrides), patch('project_files.subprocess.run', side_effect=clone), \
                patch.object(self.service, 'bind'):
            clone_repository(self.projects, dict(folder='private', url='https://github.com/owner/private.git',
                             connection_id=record['id']), connections=self.service)

    def test_save_uses_unique_durable_temporary_and_cleans_up_failed_replace(self):
        record = self.github_connection()
        original = self.service.path.read_bytes()
        with patch('repository_connections.os.fsync') as fsync, \
                patch.object(Path, 'replace', side_effect=OSError('disk full')) as replace:
            with self.assertRaises(OSError):
                self.service.save(self.service.load())
            fsync.assert_called_once()
            replace.assert_called_once_with(self.service.path)
        self.assertEqual(self.service.path.read_bytes(), original)
        self.assertEqual(list(self.service.directory.glob('*.tmp')), [])
        self.assertEqual(self.service.get(record['id']), record)

    def test_access_result_is_durable_bounded_and_ignores_remote_stderr(self):
        record = self.github_connection()
        with patch('repository_connections.subprocess.run', return_value=subprocess.CompletedProcess([], 128, stdout=b'', stderr=b'secret_token PRIVATE KEY')):
            result = self.service.test(record['id'], 'https://github.com/owner/private.git')
        self.assertEqual(result['access_state'], 'failed')
        self.assertNotIn('secret_token', json.dumps(self.service.snapshot()))
        with patch('repository_connections.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=b'a'*40 + b' HEAD')):
            result = self.service.test(record['id'], 'https://github.com/owner/private.git')
        self.assertEqual(result['access_state'], 'verified')
        self.assertIn('tested_at', result)

    def test_bad_connection_is_rejected_before_destination_reservation(self):
        record = self.github_connection()
        jobs = ProjectJobs(self.projects, self.root / 'jobs.sqlite3', self.service)
        self.addCleanup(jobs.close)
        with self.assertRaises(ValueError):
            jobs.start(dict(folder='private', url='https://github.com/owner/other.git', connection_id=record['id']))
        self.assertFalse((self.projects / 'private').exists())

    def test_empty_repository_has_verified_access_without_a_head(self):
        record = self.github_connection()
        with patch('repository_connections.subprocess.run', return_value=subprocess.CompletedProcess([], 2, stdout=b'')):
            result = self.service.test(record['id'], 'https://github.com:443/owner/private.git/')
        self.assertEqual(result['access_state'], 'verified')
        self.assertIsNone(result['error'])

    def test_failed_key_generation_and_failed_metadata_save_leave_no_key_directory(self):
        for failure in ('nonzero', 'timeout', 'missing'):
            def generate(arguments, **kwargs):
                key = Path(arguments[-1])
                key.write_text('partial private key')
                if failure == 'timeout':
                    raise subprocess.TimeoutExpired(arguments, 15)
                if failure == 'missing':
                    raise OSError('ssh-keygen missing')
                return subprocess.CompletedProcess(arguments, 1)
            with self.subTest(failure=failure), patch('repository_connections.subprocess.run', side_effect=generate):
                with self.assertRaisesRegex(ValueError, 'key generation'):
                    self.service.create(dict(label='SSH', repository='owner/private', kind='ssh'))
            self.assertEqual(list(self.service.directory.iterdir()), [])
        with patch.object(self.service, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.ssh_connection()
        self.assertEqual(list(self.service.directory.iterdir()), [])

    def test_interrupted_host_file_publication_preserves_complete_existing_trust(self):
        record = self.ssh_connection()
        self.verify(record['id'])
        hosts = self.service.directory / record['id'] / 'known_hosts'
        original = hosts.read_bytes()
        replace = Path.replace
        def publish(source, target):
            if Path(target) == hosts:
                raise OSError('interrupted host-file publication')
            return replace(source, target)
        with patch.object(Path, 'replace', autospec=True, side_effect=publish):
            with self.assertRaises(OSError):
                self.verify(record['id'])
        self.assertEqual(hosts.read_bytes(), original)
        self.assertEqual(list(hosts.parent.glob('*.tmp')), [])
        if os.name != 'nt':
            self.assertEqual(hosts.stat().st_mode & 0o777, 0o600)

    def test_binding_follows_common_directory_and_fetch_uses_assigned_credentials(self):
        record = self.github_connection()
        checkout = self.projects / 'private'
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        subprocess.run(['git', '-C', str(checkout), 'remote', 'add', 'origin', 'https://github.com/owner/private.git'], check=True)
        subprocess.run(['git', '-C', str(checkout), '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '--allow-empty', '-qm', 'initial'], check=True)
        self.service.bind(record['id'], checkout)
        configuration = (checkout / '.git/config').read_text(encoding='utf-8')
        self.assertNotIn('secret_', configuration)
        self.assertIn('HERDR_REPOSITORY=owner/private', configuration)
        linked = self.projects / 'linked'
        subprocess.run(['git', '-C', str(checkout), 'worktree', 'add', '--detach', str(linked)], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, 'not a linked worktree'):
            self.service.bind(record['id'], linked)
        with self.service.checkout_credentials(linked) as env:
            self.assertEqual(env['HERDR_REPOSITORY'], 'owner/private')
        real_run = subprocess.run
        def execute(arguments, **kwargs):
            if 'fetch' in arguments:
                self.assertEqual(kwargs['env']['HERDR_REPOSITORY'], 'owner/private')
                self.assertIn('GIT_ASKPASS', kwargs['env'])
                return subprocess.CompletedProcess(arguments, 0, stdout=b'')
            return real_run(arguments, **kwargs)
        with patch.object(project_git, 'REPOSITORY_CONNECTIONS', self.service), patch('project_git.subprocess.run', side_effect=execute):
            project_git.fetch(checkout)
        subprocess.run(['git', '-C', str(checkout), 'remote', 'set-url', 'origin', 'https://github.com/owner/other.git'], check=True)
        with self.assertRaisesRegex(ValueError, 'does not match'), self.service.checkout_credentials(checkout):
            pass

    def test_clone_uses_scoped_identity_and_persists_assignment(self):
        record = self.github_connection()
        bound = []
        def clone(arguments, **kwargs):
            self.assertEqual(kwargs['env']['HERDR_REPOSITORY'], 'owner/private')
            self.assertNotIn('secret_', repr(arguments))
            return subprocess.CompletedProcess(arguments, 0, stderr='')
        with patch('project_files.subprocess.run', side_effect=clone), patch.object(self.service, 'bind', side_effect=lambda identifier, target: bound.append((identifier, target))):
            clone_repository(self.projects, dict(folder='private', url='https://github.com/owner/private.git', connection_id=record['id']), connections=self.service)
        self.assertEqual(bound, [(record['id'], self.projects / 'private')])

    def test_ssh_assignment_persists_identity_without_global_configuration(self):
        record = self.ssh_connection()
        self.verify(record['id'])
        checkout = self.projects / 'private'
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        subprocess.run(['git', '-C', str(checkout), 'remote', 'add', 'origin', 'git@github.com:owner/private.git'], check=True)
        self.service.bind(record['id'], checkout)
        selected = subprocess.run(['git', '-C', str(checkout), 'config', '--local', '--get', 'core.sshCommand'], check=True, capture_output=True).stdout.decode()
        self.assertIn(record['id'], selected)
        self.assertIn('StrictHostKeyChecking=yes', selected)
        self.assertNotIn('PRIVATE KEY CONTENT', selected)
        self.assertFalse((self.root / '.ssh/config').exists())
        self.assertEqual(self.service.snapshot()['bindings'], [{'path': str(checkout.resolve()), 'connection_id': record['id']}])
        with self.assertRaises(ValueError):
            self.service.bind(record['id'], self.root)
        identity = self.service.directory / record['id'] / 'identity'
        identity.unlink()
        identity.mkdir()
        with self.assertRaisesRegex(ValueError, 'regular files'), self.service.credentials(record['id'], 'git@github.com:owner/private.git'):
            pass

    def test_protocol_rebinding_removes_only_previous_managed_settings(self):
        https, ssh = self.github_connection(), self.ssh_connection()
        self.verify(ssh['id'])
        checkout = self.projects / 'private'
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        def git(*args):
            return subprocess.run(['git', '-C', str(checkout), *args], capture_output=True, check=True).stdout.decode().strip()
        git('remote', 'add', 'origin', 'https://github.com/owner/private.git')
        self.service.bind(https['id'], checkout)
        git('remote', 'set-url', 'origin', 'git@github.com:owner/private.git')
        self.service.bind(ssh['id'], checkout)
        self.assertNotIn('askpass', git('config', '--local', '--list').lower())
        self.assertIn(ssh['id'], git('config', '--get', 'core.sshCommand'))
        git('remote', 'set-url', 'origin', 'https://github.com/owner/private.git')
        self.service.bind(https['id'], checkout)
        self.assertNotIn('sshcommand', git('config', '--local', '--list').lower())
        # An operator's later override must survive a subsequent switch.
        git('config', '--local', 'core.askPass', 'operator-helper')
        git('remote', 'set-url', 'origin', 'git@github.com:owner/private.git')
        self.service.bind(ssh['id'], checkout)
        self.assertEqual(git('config', '--get', 'core.askPass'), 'operator-helper')


    def test_unbind_cleans_only_exact_settings_then_remove_deletes_only_its_key_files(self):
        record = self.ssh_connection()
        self.verify(record['id'])
        other = self.github_connection()
        checkout = self.projects / 'private'
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        def git(*args):
            return subprocess.run(['git', '-C', str(checkout), *args], check=True, capture_output=True).stdout.decode().strip()
        git('remote', 'add', 'origin', 'git@github.com:owner/private.git')
        self.service.action(dict(action='bind', id=record['id'], folder='private'), 'admin')
        with self.assertRaisesRegex(ValueError, 'Unassign every project'):
            self.service.action(dict(action='remove', id=record['id']), 'admin')
        git('config', '--local', '--add', 'core.sshCommand', 'ssh operator-override')
        git('config', '--local', 'user.name', 'Operator')
        with self.assertRaisesRegex(ValueError, 'connection changed'):
            self.service.action(dict(action='unbind', id=other['id'], folder='private'), 'admin')
        self.service.action(dict(action='unbind', id=record['id'], folder='private'), 'admin')
        self.assertEqual(git('config', '--get-all', 'core.sshCommand'), 'ssh operator-override')
        self.assertEqual(git('config', '--get', 'user.name'), 'Operator')
        self.assertEqual(self.service.snapshot()['bindings'], [])
        self.service.action(dict(action='remove', id=record['id']), 'admin')
        self.assertFalse((self.service.directory / record['id']).exists())
        self.assertEqual(self.service.get(other['id']), other)
        self.assertTrue((checkout / '.git').is_dir())

    def test_unbind_https_removes_generated_settings_but_preserves_operator_edits(self):
        record = self.github_connection()
        checkout = self.projects / 'private'
        subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
        def git(*args):
            return subprocess.run(['git', '-C', str(checkout), *args], check=True, capture_output=True).stdout.decode().strip()
        git('remote', 'add', 'origin', 'https://github.com/owner/private.git')
        self.service.bind(record['id'], checkout)
        git('config', '--local', 'http.followRedirects', 'initial')
        self.service.unbind(record['id'], checkout)
        config = git('config', '--local', '--list').lower()
        self.assertNotIn('core.askpass', config)
        self.assertNotIn('credential.helper', config)
        self.assertEqual(git('config', '--get', 'http.followRedirects'), 'initial')

    def test_remove_refuses_unexpected_key_directory_contents_without_deleting_anything(self):
        record = self.ssh_connection()
        folder = self.service.directory / record['id']
        (folder / 'operator-note').write_text('preserve')
        with self.assertRaisesRegex(ValueError, 'unexpected files'):
            self.service.remove(record['id'])
        self.assertTrue((folder / 'identity').is_file())
        self.assertEqual(self.service.get(record['id']), record)

    def test_remove_cannot_delete_credentials_during_a_git_operation(self):
        record = self.github_connection()
        with self.service.credentials(record['id'], 'https://github.com/owner/private.git'):
            with self.assertRaisesRegex(ValueError, 'connection is in use'):
                self.service.remove(record['id'])
            self.assertEqual(self.service.get(record['id']), record)
        self.service.remove(record['id'])
        self.assertEqual(self.service.snapshot()['connections'], [])


class RepositoryConnectionHttpTests(unittest.TestCase):
    def test_routes_require_admin_and_keep_secrets_out_of_snapshots(self):
        import threading
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        import server
        class Operators:
            def identify(self, token):
                return {'name': 'reader', 'role': 'operator'} if token == 'reader' else None
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        projects = root / 'projects'
        projects.mkdir()
        github = GitHub(root / 'github.json')
        github.path.write_text(json.dumps(dict(token='secret_' + 'a' * 30)))
        github.path.chmod(0o600)
        service = RepositoryConnections(root / 'connections', projects, github)
        listener = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        listener.token = 'administrator'
        listener.operators = Operators()
        listener.sessions = server.BrowserSessions()
        listener.repository_connections = service
        worker = threading.Thread(target=listener.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(listener.server_close)
        self.addCleanup(worker.join, 3)
        self.addCleanup(listener.shutdown)
        url = 'http://127.0.0.1:' + str(listener.server_port)
        def request(endpoint, token, body=None, origin=None):
            headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            return urlopen(Request(url + endpoint, data=None if body is None else json.dumps(body).encode(), headers=headers))
        record = json.load(request('/api/projects/connections', 'administrator',
                           dict(action='create', label='Private repository', repository='owner/private', kind='github')))
        snapshot = json.load(request('/api/projects/connections', 'administrator'))
        self.assertEqual(snapshot['connections'], [record])
        self.assertNotIn('secret_', json.dumps(snapshot))
        with self.assertRaises(HTTPError) as error:
            request('/api/projects/connections', 'reader')
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            request('/api/projects/connections', 'reader', {'action': 'create'})
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(len(service.snapshot()['connections']), 1)
        with self.assertRaises(HTTPError) as error:
            request('/api/projects/connections', 'administrator', {'action': 'create'}, 'https://evil.test')
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            request('/api/projects/clone', 'reader', {'connection_id':'managed'})
        self.assertEqual(error.exception.code, 403)
        with patch('repository_connections.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=b'a' * 40 + b' HEAD')) as git:
            result = json.load(request('/api/projects/connections', 'administrator',
                               dict(action='test', id=record['id'], url='https://github.com/owner/private.git')))
        self.assertEqual(result['access_state'], 'verified')
        self.assertEqual(service.get(record['id'])['tested_at'], result['tested_at'])
        self.assertEqual(git.call_args.kwargs['env']['HERDR_REPOSITORY'], 'owner/private')
        with self.assertRaises(HTTPError) as error:
            request('/api/projects/connections', 'reader', dict(action='remove', id=record['id']))
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(json.load(request('/api/projects/connections', 'administrator', dict(action='remove', id=record['id']))),
                         dict(id=record['id'], removed=True))
        self.assertEqual(service.snapshot()['connections'], [])


if __name__ == '__main__':
    unittest.main()
