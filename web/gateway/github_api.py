"""Bounded GitHub contribution API. Credentials never appear in snapshots/errors.

The gateway and agents currently share a Unix user. File mode 0600 is protection
against other users, not a sandbox against a same-user agent. Publishing is an
administrator operation; a separate identity/broker is needed for OS isolation.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener

REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward Authorization to another origin.


class GitHubError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__({401: 'GitHub credentials are invalid or expired.',
                          403: 'GitHub access was denied or rate limited.',
                          404: 'GitHub repository or pull request is unavailable.',
                          409: 'GitHub head changed; refresh before continuing.',
                          422: 'GitHub rejected the pull request; refresh before retrying.'}.get(
                              status, 'GitHub request failed.'))


class GitHub:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.cache = {}

    def configuration(self):
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or self.path.stat().st_size > 16384:
            raise ValueError('Invalid GitHub credential file.')
        if os.name != 'nt' and self.path.stat().st_mode & 0o077:
            raise ValueError('GitHub credential file must have mode 0600.')
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raise ValueError('GitHub configuration is unreadable.') from None
        if not isinstance(data, dict):
            raise ValueError('Invalid GitHub configuration.')
        if (not isinstance(data.get('token'), str)
                or not re.fullmatch(r'[A-Za-z0-9_]{20,255}', data['token'])):
            raise ValueError('Invalid GitHub credential file.')
        return data

    def snapshot(self):
        data = self.configuration()
        return dict(configured=bool(data.get('token')), login=data.get('login'),
                    repositories=data.get('repositories', []),
                    credential_isolation='shared_unix_user')

    def request(self, path, method='GET', body=None, token=None):
        if not path.startswith('/') or '\\' in path or '..' in path:
            raise ValueError('Invalid GitHub API path.')
        supplied_token = token is not None
        token = token or self.configuration().get('token')
        if not token:
            raise ValueError('Configure GitHub publishing credentials first.')
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                   'User-Agent': 'herdr-dashboard', 'X-GitHub-Api-Version': '2022-11-28'}
        with self.lock:
            cached = self.cache.get(path) if method == 'GET' and not supplied_token else None
        if cached and cached[0]:
            headers['If-None-Match'] = cached[0]
        payload = json.dumps(body).encode() if body is not None else None
        if payload:
            headers['Content-Type'] = 'application/json'
        try:
            with build_opener(NoRedirect()).open(Request('https://api.github.com' + path,
                    data=payload, headers=headers, method=method), timeout=15) as response:
                content = response.read(1_000_001)
                if len(content) > 1_000_000:
                    raise ValueError('GitHub response exceeds the size limit.')
                # Do not silently label an incomplete check/review list complete.
                if 'rel="next"' in response.headers.get('Link', ''):
                    raise ValueError('GitHub result exceeds the supported page size; inspect it on GitHub.')
                result = json.loads(content)
                if method == 'GET' and not supplied_token:
                    with self.lock:
                        if len(self.cache) >= 256:
                            self.cache.clear()
                        self.cache[path] = (response.headers.get('ETag'), result)
                return result
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 304:
                # Re-read under the lock: a credential change may have cleared it.
                with self.lock:
                    current = self.cache.get(path)
                if current:
                    return current[1]
                raise GitHubError(status) from None
            raise GitHubError(status) from None
        except (OSError, ValueError) as error:
            if isinstance(error, GitHubError):
                raise
            raise ValueError('GitHub response is unavailable, oversized or incomplete.') from None

    def configure(self, body):
        if not isinstance(body, dict):
            raise ValueError('Expected GitHub configuration fields.')
        token, repositories = body.get('token'), body.get('repositories')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_]{20,255}', token):
            raise ValueError('Enter a GitHub personal access token.')
        if (not isinstance(repositories, list) or not 1 <= len(repositories) <= 20
                or any(not isinstance(r, str) or not REPOSITORY.fullmatch(r)
                       or any(p in ('.', '..') for p in r.split('/')) for r in repositories)):
            raise ValueError('Allowlist 1–20 repositories as owner/repository.')
        user = self.request('/user', token=token)
        if not isinstance(user, dict) or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', str(user.get('login', ''))):
            raise ValueError('GitHub returned an invalid account identity.')
        # Validate the allowlist concurrently. The dashboard gives this call a
        # bounded timeout; twenty sequential repository checks could exceed it
        # and report a failure for credentials that were in fact accepted.
        def permitted(repository):
            info = self.request('/repos/' + repository, token=token)
            return isinstance(info, dict) and isinstance(info.get('permissions'), dict) and bool(info['permissions'].get('push'))

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(permitted, repositories))
        if not all(results):
            raise ValueError('GitHub account needs push access to each selected repository.')
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_suffix('.tmp')
            # A crash between create and replace must not block configuration.
            temporary.unlink(missing_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, 'w') as output:
                    json.dump(dict(token=token, repositories=repositories, login=user['login']), output)
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.cache.clear()
        return self.snapshot()

    def allow(self, repository):
        if repository not in self.configuration().get('repositories', []):
            raise ValueError('Repository is not allowlisted for GitHub publishing.')

    def find_pull(self, repository, branch):
        self.allow(repository)
        query = urlencode(dict(head=repository.split('/')[0] + ':' + branch, state='all', per_page=100))
        return self.request('/repos/' + repository + '/pulls?' + query)
