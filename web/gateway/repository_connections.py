"""Repository-scoped GitHub read connections; private credentials never enter responses.

Gateway and agents share a Unix user. Mode 0600 is not isolation from agents.
No global SSH/Git configuration is edited; managed network operations get an
explicit identity, strict host verification and a credential-scoped askpass.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import uuid

# Published GitHub Ed25519 host fingerprint, verified against GitHub Docs.
HOST_FINGERPRINT = 'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU'
IDENTIFIER = re.compile(r'[a-f0-9]{32}')
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}')
# This form stores personal access tokens, not OAuth/App access or refresh
# credentials. Prefixes catch paste mistakes; only the access test verifies one.
PERSONAL_TOKEN_PREFIXES = ('github_pat_', 'ghp_')


def personal_token(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_]{20,255}', value)
            or not value.startswith(PERSONAL_TOKEN_PREFIXES)):
        raise ValueError('Enter a GitHub personal access token beginning with github_pat_ or ghp_.')
    return value


def repository_url(url):
    if not isinstance(url, str) or len(url) > 2000 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError('Enter a GitHub HTTPS or git@github.com repository URL.')
    if url.startswith('git@github.com:'):
        repository, kind = url[len('git@github.com:'):], 'ssh'
    else:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('Managed connections support github.com only, without embedded credentials.')
        repository, kind = parsed.path.removeprefix('/'), 'github'
    repository = repository.rstrip('/').removesuffix('.git')
    if not REPOSITORY.fullmatch(repository) or any(part in ('.', '..') for part in repository.split('/')):
        raise ValueError('Enter a GitHub owner/repository URL.')
    return repository, kind


def now():
    return datetime.now(timezone.utc).isoformat()


def write_private(path, content):
    """Publish complete private bytes without exposing a partial/truncated file."""
    temporary = path.with_name(path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class RepositoryConnections:
    def __init__(self, directory, projects, github):
        self.directory = Path(directory).resolve()
        self.projects = Path(projects).resolve()
        self.github = github
        self.lock = threading.RLock()
        self.active_credentials = {}
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.path = self.directory / 'connections.json'

    def load(self):
        if not self.path.exists():
            return {'connections': {}, 'bindings': {}}
        if self.path.is_symlink() or self.path.stat().st_size > 256000:
            raise ValueError('Invalid repository connection store.')
        return json.loads(self.path.read_text(encoding='utf-8'))

    def save(self, data):
        write_private(self.path, json.dumps(data))

    def _remove_key_directory(self, identifier):
        # Only a validated direct child and the generated credential files can be
        # removed. Never recursively follow a directory planted in this store.
        if not IDENTIFIER.fullmatch(identifier):
            raise ValueError('Invalid repository connection identity.')
        folder = self.directory / identifier
        if folder.is_symlink() or folder.resolve().parent != self.directory:
            raise ValueError('Repository key directory must be a regular managed directory.')
        if not folder.exists():
            return
        if not folder.is_dir():
            raise ValueError('Repository key directory must be a regular managed directory.')
        files = list(folder.iterdir())
        if any(path.name not in ('identity', 'identity.pub', 'known_hosts', 'token.json') or
               (not path.is_symlink() and not path.is_file()) for path in files):
            raise ValueError('Repository key directory contains unexpected files; inspect it before removal.')
        for path in files:
            path.unlink()
        folder.rmdir()

    def get(self, identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise ValueError('Select a repository connection.')
        with self.lock:
            record = self.load()['connections'].get(identifier)
        if record is None:
            raise ValueError('Repository connection is unavailable.')
        return dict(record)

    def snapshot(self):
        with self.lock:
            data = self.load()
        return dict(connections=list(data['connections'].values()),
                    bindings=[dict(path=str(Path(path).parent), connection_id=value) for path, value in data['bindings'].items()],
                    github_host_fingerprint=HOST_FINGERPRINT,
                    credential_isolation='shared_unix_user')

    def create(self, body):
        label, repository, kind = body.get('label'), body.get('repository'), body.get('kind')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or any(ord(c) < 32 for c in label):
            raise ValueError('Enter a connection name of at most 80 characters.')
        if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository) or any(p in ('.', '..') for p in repository.split('/')):
            raise ValueError('Enter owner/repository, for example Ultarius/bevy_game.')
        if kind not in ('ssh', 'github', 'token'):
            raise ValueError('Choose SSH, a GitHub token, or the configured GitHub account.')
        token = body.get('token')
        if kind == 'token':
            personal_token(token)
        elif token is not None:
            raise ValueError('A token can only be supplied for a GitHub token connection.')
        identifier = uuid.uuid4().hex
        record = dict(id=identifier, label=label.strip(), repository=repository, kind=kind,
                      created_at=now(), host_verified=False, access_state='not_tested')
        with self.lock:
            data = self.load()
            if len(data['connections']) >= 64:
                raise ValueError('Repository connection limit reached (64).')
            if kind == 'github':
                if not self.github.configuration().get('token'):
                    raise ValueError('Configure the dashboard GitHub account first.')
            elif kind == 'token':
                folder = self.directory / identifier
                folder.mkdir(mode=0o700)
                try:
                    write_private(folder / 'token.json', json.dumps(dict(token=token)))
                except (OSError, ValueError):
                    self._remove_key_directory(identifier)
                    raise ValueError('Could not save the repository token on this gateway.') from None
            else:
                folder = self.directory / identifier
                folder.mkdir(mode=0o700)
                try:
                    result = subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                                             '-C', 'herdr-' + identifier, '-f', str(folder / 'identity')],
                                            capture_output=True, timeout=15)
                    if result.returncode:
                        raise ValueError('Could not generate the repository SSH key.')
                    (folder / 'identity').chmod(0o600)
                    record['public_key'] = (folder / 'identity.pub').read_text(encoding='utf-8').strip()
                except (OSError, subprocess.TimeoutExpired, ValueError):
                    self._remove_key_directory(identifier)
                    raise ValueError('SSH key generation is unavailable on this gateway.') from None
            data['connections'][identifier] = record
            try:
                self.save(data)
            except (OSError, ValueError):
                if kind in ('ssh', 'token'):
                    self._remove_key_directory(identifier)
                raise
        return record

    def discover_repositories(self, token, page=1):
        """One bounded page of metadata; discovery never saves the supplied PAT."""
        token = personal_token(token)
        if type(page) is not int or not 1 <= page <= 1000:
            raise ValueError('Invalid repository discovery page.')
        response = self.github.request(
            f'/user/repos?per_page=100&page={page}&sort=full_name&direction=asc',
            token=token, page_result=True)
        results, has_next = response['items'], response['has_next']
        if not isinstance(results, list) or len(results) > 100:
            raise ValueError('Invalid repository discovery response from GitHub.')
        repositories = []
        for item in results:
            name = item.get('full_name') if isinstance(item, dict) else None
            if (not isinstance(name, str) or not REPOSITORY.fullmatch(name)
                    or any(part in ('.', '..') for part in name.split('/'))):
                raise ValueError('Invalid repository discovery response from GitHub.')
            repositories.append(dict(repository=name, private=item.get('private') is True,
                                     archived=item.get('archived') is True))
        return dict(repositories=repositories,
                    limit_reached=page == 1000 and has_next,
                    next_page=page + 1 if has_next and page < 1000 else None)

    def rotate(self, identifier, token):
        token = personal_token(token)
        with self.lock:
            record = self.get(identifier)
            if record['kind'] != 'token':
                raise ValueError('Only a connection-specific GitHub token can be rotated here.')
            if self.active_credentials.get(identifier):
                raise ValueError('This connection is in use. Wait for its Git operation to finish before rotating it.')
            credential = self._token_file(record)
            data = self.load()
            updated = data['connections'][identifier]
            updated.update(access_state='not_tested', tested_at=None, error=None)
            # Invalidate the old access verdict first. If atomic credential
            # publication fails, the old token and bindings remain usable, but
            # no stale verification is claimed for a possibly changed token.
            self.save(data)
            write_private(credential, json.dumps(dict(token=token)))
            return dict(updated)

    def _token_file(self, record):
        credential = self._credential_path(record)
        try:
            parent, info = credential.parent.lstat(), credential.lstat()
        except OSError:
            raise ValueError('The repository token file is unavailable.') from None
        if (not stat.S_ISDIR(parent.st_mode) or not stat.S_ISREG(info.st_mode)
                or any(getattr(value, 'st_file_attributes', 0) & 0x400 for value in (parent, info))
                or (os.name != 'nt' and info.st_mode & 0o077)):
            raise ValueError('Repository token files must be regular files with mode 0600 in their managed directory.')
        return credential

    def verify_host(self, identifier):
        record = self.get(identifier)
        if record['kind'] != 'ssh':
            raise ValueError('Host verification is only needed for SSH connections.')
        try:
            result = subprocess.run(['ssh-keyscan', '-T', '5', '-t', 'ed25519', 'github.com'],
                                    capture_output=True, timeout=8)
            lines = result.stdout.decode('utf-8', errors='replace').splitlines()
            key = None
            for line in lines:
                fields = line.split()
                if len(fields) != 3 or fields[:2] != ['github.com', 'ssh-ed25519']:
                    continue
                raw = base64.b64decode(fields[2], validate=True)
                fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
                if fingerprint == HOST_FINGERPRINT:
                    key = 'github.com ssh-ed25519 ' + fields[2] + '\n'
            if key is None:
                raise ValueError('GitHub host key did not match its published fingerprint. No host was trusted.')
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise ValueError('Could not verify GitHub host key. Retry or check gateway connectivity. No host was trusted.') from error
        with self.lock:
            self.get(identifier)  # A concurrent retirement may have removed it.
            write_private(self.directory / identifier / 'known_hosts', key)
            data = self.load()
            data['connections'][identifier].update(host_verified=True, host_fingerprint=HOST_FINGERPRINT)
            self.save(data)
        return self.get(identifier)

    @contextmanager
    def credentials(self, identifier, url):
        with self.lock:
            record = self.get(identifier)
            self.active_credentials[identifier] = self.active_credentials.get(identifier, 0) + 1
        try:
            with self._credentials(record, url) as environment:
                yield environment
        finally:
            with self.lock:
                self.active_credentials[identifier] -= 1
                if not self.active_credentials[identifier]:
                    del self.active_credentials[identifier]

    @contextmanager
    def _credentials(self, record, url):
        identifier = record['id']
        repository, kind = repository_url(url)
        protocol = 'ssh' if record['kind'] == 'ssh' else 'github'
        if repository.lower() != record['repository'].lower() or kind != protocol:
            raise ValueError('The selected connection does not match this repository and URL protocol.')
        environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and not k.startswith('HERDR_GITHUB_')}
        environment.update(GIT_TERMINAL_PROMPT='0', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        with tempfile.TemporaryDirectory(prefix='herdr-repository-') as temporary:
            options = [('credential.helper', ''), ('credential.useHttpPath', 'true'),
                       ('http.followRedirects', 'false'), ('core.hooksPath', temporary)]
            environment['GIT_CONFIG_COUNT'] = str(len(options))
            for index, (name, value) in enumerate(options):
                environment['GIT_CONFIG_KEY_' + str(index)] = name
                environment['GIT_CONFIG_VALUE_' + str(index)] = value
            if kind == 'ssh':
                if not record['host_verified']:
                    raise ValueError('Verify the GitHub host key for this connection first.')
                folder = self.directory / identifier
                identity = folder / 'identity'
                hosts = folder / 'known_hosts'
                for path in (identity, hosts):
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and info.st_mode & 0o077):
                        raise ValueError('Repository SSH files must be regular files with mode 0600.')
                environment['GIT_SSH_COMMAND'] = self._binding_settings(record)[0][1]
            else:
                from github_api import GitHub
                credential = self._token_file(record) if record['kind'] == 'token' else self._credential_path(record)
                if not GitHub(credential).configuration().get('token'):
                    raise ValueError('The configured GitHub credential is unavailable.')
                helper = Path(temporary) / 'askpass'
                helper.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable,
                    str(Path(__file__).with_name('repository_askpass.py'))]) + ' "$@"\n', encoding='utf-8')
                helper.chmod(0o700)
                environment.update(GIT_ASKPASS=str(helper), HERDR_GITHUB_CONFIG=str(credential),
                                   HERDR_REPOSITORY=record['repository'])
            yield environment

    def test(self, identifier, url):
        record = self.get(identifier)
        try:
            with self.credentials(identifier, url) as environment:
                result = subprocess.run(['git', '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never',
                                         'ls-remote', '--exit-code', '--', url, 'HEAD'],
                                        capture_output=True, timeout=20, env=environment)
            if result.returncode not in (0, 2):
                raise ValueError('Repository access failed. Check the deploy key or GitHub token permissions, expiry, organization approval and gateway connectivity.')
            state, error = 'verified', None
        except (ValueError, OSError, subprocess.TimeoutExpired) as failure:
            state = 'failed'
            error = str(failure) if isinstance(failure, ValueError) else 'Repository access test timed out or Git is unavailable.'
        with self.lock:
            data = self.load()
            self.get(identifier)
            data['connections'][identifier].update(access_state=state, tested_at=now(), error=error)
            self.save(data)
        return self.get(identifier)

    def bind(self, identifier, path):
        from repository_lock import repository_lock
        folder = Path(path).resolve()
        if folder.parent != self.projects or not folder.is_dir() or (folder / '.git').is_symlink():
            raise ValueError('Select a project directly inside the projects directory.')
        with repository_lock(folder), self.lock:
            return self._bind(identifier, folder)

    def _bind(self, identifier, path):
        from repository_lock import common_directory
        folder = Path(path).resolve()
        if folder.parent != self.projects or not folder.is_dir():
            raise ValueError('Select a project directly inside the projects directory.')
        result = subprocess.run(['git', '-C', str(folder), 'remote', 'get-url', 'origin'],
                                capture_output=True, timeout=10)
        if result.returncode:
            raise ValueError('The project has no readable origin repository.')
        url = result.stdout.decode('utf-8', errors='replace').strip()
        record = self.get(identifier)
        with self.credentials(identifier, url):
            settings = self._binding_settings(record)
        common = common_directory(folder)
        if common != (folder / '.git').resolve():
            raise ValueError('Assign the project checkout, not a linked worktree.')
        # A protocol switch must not retain the previous managed identity. Only
        # remove exact values we generated; preserve subsequent operator edits.
        with self.lock:
            data = self.load()
            previous = data['connections'].get(data['bindings'].get(str(common)))
        if previous and previous['kind'] != record['kind']:
            self._clear_settings(folder, previous, keep={key for key, _ in settings})
        # Persist only identity selection, never key bytes/tokens. This also
        # supports ordinary Git fetch/pull in agents and linked worktrees.
        for name, value in settings:
            result = subprocess.run(['git', '-C', str(folder), 'config', '--local', '--replace-all', name, value],
                                    capture_output=True, timeout=10)
            if result.returncode:
                raise ValueError('Could not configure the project connection. Inspect the retained checkout.')
        with self.lock:
            data = self.load()
            if len(data['bindings']) >= 128 and str(common) not in data['bindings']:
                raise ValueError('Repository assignment limit reached (128).')
            data['bindings'][str(common)] = identifier
            self.save(data)
        return dict(path=str(folder), connection_id=identifier)

    def _clear_settings(self, folder, record, keep=()):
        for name, value in self._binding_settings(record):
            if name in keep:
                continue
            result = subprocess.run(['git', '-C', str(folder), 'config', '--local', '--get-all', name],
                                    capture_output=True, timeout=10)
            if result.returncode not in (0, 1):
                raise ValueError('Could not inspect the managed Git settings; no binding was removed.')
            if result.returncode == 0 and value in result.stdout.decode().splitlines():
                removed = subprocess.run(['git', '-C', str(folder), 'config', '--local', '--fixed-value', '--unset-all', name, value],
                                         capture_output=True, timeout=10)
                if removed.returncode not in (0, 5):
                    raise ValueError('Could not remove the managed Git setting; inspect the retained checkout.')

    def unbind(self, identifier, path):
        from repository_lock import repository_lock, common_directory
        folder = Path(path).resolve()
        if folder.parent != self.projects or not folder.is_dir() or (folder / '.git').is_symlink():
            raise ValueError('Select a project directly inside the projects directory.')
        # Match bind's repository -> store lock order. A stale dashboard cannot
        # unbind a newer replacement connection at the same checkout.
        with repository_lock(folder), self.lock:
            common = common_directory(folder)
            if common != (folder / '.git').resolve():
                raise ValueError('Unassign the project checkout, not a linked worktree.')
            data = self.load()
            record = self.get(identifier)
            if data['bindings'].get(str(common)) != identifier:
                raise ValueError('The project connection changed. Refresh before unassigning it.')
            self._clear_settings(folder, record)
            del data['bindings'][str(common)]
            self.save(data)
        return dict(path=str(folder), connection_id=None)

    def remove(self, identifier):
        with self.lock:
            self.get(identifier)
            data = self.load()
            if identifier in data['bindings'].values():
                raise ValueError('Unassign every project using this connection before removing it.')
            if self.active_credentials.get(identifier):
                raise ValueError('This connection is in use. Wait for its Git operation to finish before removing it.')
            self._remove_key_directory(identifier)
            del data['connections'][identifier]
            self.save(data)
        return dict(id=identifier, removed=True)

    def _credential_path(self, record):
        if record['kind'] == 'token':
            return self.directory / record['id'] / 'token.json'
        return self.github.path

    def _binding_settings(self, record):
        """Exact non-secret configuration generated by this release."""
        if record['kind'] == 'ssh':
            folder = self.directory / record['id']
            command = shlex.join(['ssh', '-F', os.devnull, '-i', str(folder / 'identity'),
                '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'UserKnownHostsFile=' + str(folder / 'known_hosts'), '-o', 'GlobalKnownHostsFile=' + os.devnull,
                '-o', 'ConnectTimeout=10'])
            return [('core.sshCommand', command)]
        helper = shlex.join(['env', 'HERDR_GITHUB_CONFIG=' + str(self._credential_path(record)),
            'HERDR_REPOSITORY=' + record['repository'], sys.executable,
            str(Path(__file__).with_name('repository_askpass.py'))])
        return [('credential.helper', ''), ('credential.useHttpPath', 'true'),
                ('http.followRedirects', 'false'), ('core.askPass', helper)]

    @contextmanager
    def checkout_credentials(self, path):
        from repository_lock import common_directory
        common = common_directory(path)
        with self.lock:
            identifier = self.load()['bindings'].get(str(common))
        if identifier is None:
            yield None
            return
        result = subprocess.run(['git', '-C', str(path), 'remote', 'get-url', 'origin'],
                                capture_output=True, timeout=10)
        if result.returncode:
            raise ValueError('Cannot read the assigned repository origin.')
        with self.credentials(identifier, result.stdout.decode().strip()) as environment:
            yield environment

    def action(self, body, role):
        if role != 'admin':
            raise ValueError('Only an administrator can configure repository connections.')
        if not isinstance(body, dict):
            raise ValueError('Expected a repository connection request.')
        action = body.get('action')
        allowed = {'create': {'action', 'label', 'repository', 'kind', 'token'},
                   'discover': {'action', 'token', 'page'},
                   'rotate': {'action', 'id', 'token'},
                   'verify_host': {'action', 'id'}, 'test': {'action', 'id', 'url'},
                   'bind': {'action', 'id', 'folder'}, 'unbind': {'action', 'id', 'folder'},
                   'remove': {'action', 'id'}}
        if not isinstance(action, str) or action not in allowed or set(body) - allowed[action]:
            raise ValueError('Invalid repository connection options.')
        if action in ('bind', 'unbind') and (not isinstance(body.get('folder'), str) or len(body['folder']) > 255):
            raise ValueError('Enter an existing project folder name.')
        if action == 'create':
            return self.create(body)
        if action == 'discover':
            return self.discover_repositories(body.get('token'), body.get('page', 1))
        if action == 'rotate':
            return self.rotate(body.get('id'), body.get('token'))
        if action == 'verify_host':
            return self.verify_host(body.get('id'))
        if action == 'test':
            return self.test(body.get('id'), body.get('url'))
        if action == 'bind':
            return self.bind(body.get('id'), self.projects / str(body.get('folder', '')))
        if action == 'unbind':
            return self.unbind(body.get('id'), self.projects / str(body.get('folder', '')))
        if action == 'remove':
            return self.remove(body.get('id'))
        raise ValueError('Unknown repository connection action.')
