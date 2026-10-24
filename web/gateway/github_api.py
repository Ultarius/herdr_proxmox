"""Bounded GitHub contribution API. Credentials never appear in snapshots/errors.

The gateway and agents currently share a Unix user. File mode 0600 is protection
against other users, not a sandbox against a same-user agent. Publishing is an
administrator operation; a separate identity/broker is needed for OS isolation.
"""
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
MODES = ('direct', 'fork')
# A single fine-grained token is limited to one resource owner. Separate push
# and upstream PR tokens support repositories under different owners.
FINE_GRAINED = 'github_pat_'
CREDENTIAL_ROLES = ('push token', 'pull request token', 'provided token')


@dataclass(frozen=True)
class GitHubCredential:
    token: str | None = field(repr=False)
    login: str | None
    role: str = 'provided token'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward Authorization to another origin.


def pull_validation(error, *, token=None, body=None):
    """Classify a bounded response, retaining redacted unfamiliar messages."""
    def detail(value):
        from discovery import redact
        if not isinstance(value, str):
            return None
        # Remove exact request secrets/text before general redaction or clipping.
        private_values = [token]
        if isinstance(body, dict):
            private_values.extend(body.get(key) for key in ('title', 'body'))
        for private in private_values:
            if isinstance(private, str) and private:
                value = value.replace(private, '[request content omitted]')
        value = ' '.join(redact(value).split())
        if not value or value.lower().strip('. ') in ('validation failed', 'invalid request'):
            return None
        prefix = 'GitHub detail: '
        limit = 240 - len(prefix)
        if len(value) > limit:
            value = value[:limit - 3].rsplit(' ', 1)[0] + '...'
        return prefix + value
    try:
        raw = error.read(16385)
        if len(raw) > 16384:
            return ('GitHub validation response exceeded the 16 KiB diagnostic limit.',)
        data = json.loads(raw)
    except (OSError, ValueError, TypeError, RecursionError, HTTPException):
        return ('GitHub returned an unreadable or non-JSON validation response.',)
    if not isinstance(data, dict):
        return ('GitHub returned a validation response in an unexpected format.',)
    errors = data.get('errors')
    entries = errors[:10] if isinstance(errors, list) else []
    # Some GitHub validations supply only a top-level message.
    entries = entries + [data.get('message')]
    reasons = []
    fields = {'head', 'base', 'head_sha', 'base_sha', 'head_repo', 'title', 'body', 'draft'}
    codes = {'missing', 'missing_field', 'invalid', 'already_exists', 'unprocessable', 'custom'}
    for item in entries:
        original_message = item.get('message') if isinstance(item, dict) else item
        message = original_message
        message = message.lower() if isinstance(message, str) else ''
        field = item.get('field') if isinstance(item, dict) else None
        code = item.get('code') if isinstance(item, dict) else None
        field = field if isinstance(field, str) else None
        code = code if isinstance(code, str) else None
        message_fields = set(re.findall(r'\b(head|base|title|body|draft)(?:[_ ](?:sha|repo))?\b', message))
        normalized_field = 'head' if field in ('head', 'head_sha', 'head_repo') else 'base' if field in ('base', 'base_sha') else field
        if field in fields and message_fields and normalized_field not in message_fields:
            reason = 'GitHub returned conflicting validation details. Inspect the submitted PR fields.'
        elif 'no commits between' in message:
            reason = 'GitHub reports no commits between the requested base and head branches.'
        elif 'pull request already exists' in message:
            reason = 'GitHub reports an existing pull request for this branch. Reconcile it before retrying.'
        elif 'draft' in message and any(word in message for word in ('not supported', 'not enabled', 'not allowed', 'not permitted')):
            reason = 'GitHub does not allow draft pull requests for this repository or plan.'
        elif field in ('head', 'head_sha', 'head_repo') or 'head sha' in message or 'head_sha' in message:
            reason = 'GitHub could not accept the head branch or fork. Check the PR credential can access the published fork branch.'
        elif field in ('base', 'base_sha') or 'base sha' in message or 'base_sha' in message:
            reason = 'GitHub could not accept the base branch. Check it exists in the upstream repository.'
        elif field in fields and code in codes:
            reason = f'GitHub validation rejected {field} ({code}).'
        else:
            reason = detail(original_message)
            if reason is None:
                continue
        if reason not in reasons:
            reasons.append(reason)
    return tuple(reasons[:3]) or ('GitHub reports validation failure without usable field-level details.',)


class GitHubError(ValueError):
    def __init__(self, status, *, repository=None, operation=None, credential_role=None, validation=(), request_id=None):
        if (not isinstance(validation, (tuple, list)) or len(validation) > 3
                or any(not isinstance(item, str) or not item or len(item) > 240 for item in validation)):
            raise TypeError('GitHub validation must contain at most three short summary strings.')
        self.status = status
        self.request_id = request_id if isinstance(request_id, str) and re.fullmatch(r'[A-Fa-f0-9]{1,32}(?::[A-Fa-f0-9]{1,32}){1,7}', request_id) else None
        self.validation = validation = tuple(validation)
        self.repository = repository if isinstance(repository, str) and len(repository) <= 200 and REPOSITORY.fullmatch(repository) else None
        self.operation = operation = operation[:80] if isinstance(operation, str) else None
        self.credential_role = credential_role = credential_role if credential_role in CREDENTIAL_ROLES else None
        message = {401: 'GitHub credentials are invalid or expired.',
                          403: 'GitHub access was denied or rate limited.',
                          404: 'GitHub repository or pull request is unavailable.',
                          409: 'GitHub head changed; refresh before continuing.',
                          422: 'GitHub rejected the pull request (HTTP 422).'}.get(
                              status, 'GitHub request failed.')
        if validation:
            message += ' ' + ' '.join(validation)
        elif status == 422:
            message += ' GitHub did not provide a recognized validation reason; check the head/base branches and PR credential access.'
        if self.repository:
            message = f'{operation or "Repository access"} for {self.repository} using {credential_role or "the selected token"}. {message}'
            if status == 404:
                message += ' Check the repository name, token repository selection, resource owner, and organization approval.'
        if self.request_id:
            message += ' GitHub request ID: ' + self.request_id + '.'
        super().__init__(message)


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
        # Optional credential for upstream PR operations. Splitting credentials
        # narrows GitHub permissions, but does not isolate their local storage.
        if 'pr_token' in data or 'pr_login' in data:
            if (not isinstance(data.get('pr_token'), str)
                    or not re.fullmatch(r'[A-Za-z0-9_]{20,255}', data['pr_token'])
                    or not isinstance(data.get('pr_login'), str)
                    or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', data['pr_login'])):
                raise ValueError('Invalid GitHub pull request credential file.')
        return data

    def pr_credential(self):
        """The credential used for upstream pull request operations.

        Falls back to the push credential, so a configuration without the split
        behaves exactly as it did before one existed.
        """
        data = self.configuration()
        return GitHubCredential(data.get('pr_token') or data.get('token'),
                                data.get('pr_login') or data.get('login'),
                                'pull request token' if data.get('pr_token') else 'push token')

    def snapshot(self):
        data = self.configuration()
        return dict(configured=bool(data.get('token')), login=data.get('login'),
                    pr_configured=bool(data.get('pr_token')), pr_login=data.get('pr_login'),
                    repositories=data.get('repositories', []),
                    publication=data.get('publication', {}),
                    credential_isolation='shared_unix_user')

    def publication_settings(self):
        """Per-upstream publication defaults: direct, or through a named fork."""
        return self.configuration().get('publication', {})

    def publication_options(self):
        """Destinations an operator may select for each configured upstream."""
        options = {}
        for upstream, entry in self.publication_settings().items():
            values = [upstream]
            if entry.get('mode') == 'fork' and isinstance(entry.get('publishing_repository'), str):
                values.append(entry['publishing_repository'])
            options[upstream] = values
        return options

    def allow_publishing(self, upstream, publishing):
        """Authorize a write destination: the configured one for this upstream.

        A task records its destination, so without this check a modified task
        record could publish to an unrelated repository.
        """
        self.allow(upstream)
        if publishing == upstream:
            return
        entry = self.publication_settings().get(upstream) or {}
        if entry.get('mode') != 'fork' or entry.get('publishing_repository') != publishing:
            raise ValueError('Repository is not the configured fork for this project.')

    def request(self, path, method='GET', body=None, token=None, page_result=False, *, credential_role=None):
        """Return JSON, refusing partial lists unless the caller consumes page metadata.

        page_result requires an explicit token and returns {items, has_next}.
        Such results bypass the shared cache. Explicit tokens may carry a diagnostic
        credential_role; this labels their source without changing authentication.
        Callers construct subsequent URLs
        themselves; response Link URLs are never followed with credentials.
        """
        if page_result and (method != 'GET' or token is None):
            raise ValueError('Paged GitHub requests require an explicit credential and GET.')
        if not path.startswith('/') or '\\' in path or '..' in path:
            raise ValueError('Invalid GitHub API path.')
        # Explicit credentials carry provenance from the same configuration
        # snapshot that selected the secret; the endpoint cannot identify it.
        if credential_role is not None and (token is None or credential_role not in CREDENTIAL_ROLES):
            raise ValueError('An explicit credential role requires a token and a known role.')
        credential_role = credential_role or 'provided token'
        supplied_token = token is not None
        if token is None:
            config = self.configuration()
            # Keep creation, lookup, reviews and lost-response recovery on the
            # same credential. Explicit tokens still override this selection.
            match = re.match(r'^/repos/([^/]+/[^/?]+)(/[^?]*)?(?:[?]|$)', path)
            repository = match[1] if match else None
            suffix = (match[2] or '') if match else ''
            entry = config.get('publication', {}).get(repository, {})
            upstream_read = method == 'GET' and entry.get('mode') == 'fork' and entry.get('publishing_repository') != repository
            pr_operation = suffix == '/pulls' or suffix.startswith('/pulls/')
            if (config.get('pr_token') and repository in config.get('repositories', [])
                    and (pr_operation or upstream_read)):
                token = config['pr_token']
                supplied_token = True  # Do not mix identities in the GET cache.
                credential_role = 'pull request token'
            else:
                token = config.get('token')
                credential_role = 'push token'
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
                has_next = any('next' in (quoted or bare).split() for quoted, bare in
                    re.findall(r';\s*rel\s*=\s*(?:"([^"]*)"|([^;,\s]+))',
                               response.headers.get('Link', ''), re.IGNORECASE))
                # Ordinary check/review callers must still refuse partial lists.
                if not page_result and has_next:
                    raise ValueError('GitHub result exceeds the supported page size; inspect it on GitHub.')
                result = json.loads(content)
                if page_result:
                    return dict(items=result, has_next=has_next)
                if method == 'GET' and not supplied_token:
                    with self.lock:
                        if len(self.cache) >= 256:
                            self.cache.clear()
                        self.cache[path] = (response.headers.get('ETag'), result)
                return result
        except HTTPError as error:
            status = error.code
            is_pull = method == 'POST' and re.fullmatch(r'/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pulls', path)
            try:
                validation = pull_validation(error, token=token, body=body) if status == 422 and is_pull else ()
                request_id = error.headers.get('X-GitHub-Request-Id') if error.headers else None
            finally:
                error.close()
            if status == 304:
                # Re-read under the lock: a credential change may have cleared it.
                with self.lock:
                    current = self.cache.get(path)
                if current:
                    return current[1]
                raise GitHubError(status) from None
            match = re.match(r'^/repos/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:[/?]|$)', path)
            raise GitHubError(status, repository=match[1] if match else None,
                              operation='Create draft pull request' if is_pull and isinstance(body, dict) and body.get('draft') else method + ' repository API request',
                              credential_role=credential_role, validation=validation, request_id=request_id) from None
        except (OSError, ValueError) as error:
            if isinstance(error, GitHubError):
                raise
            raise ValueError('GitHub response is unavailable, oversized or incomplete.') from None

    @staticmethod
    def same_fork_network(upstream, publishing):
        """True when both repositories belong to one GitHub fork network."""
        if publishing.get('fork') is not True:
            return False
        source = publishing.get('source')
        if not isinstance(source, dict) or not isinstance(source.get('full_name'), str):
            return False
        root = upstream.get('source') if isinstance(upstream.get('source'), dict) else upstream
        name = root.get('full_name')
        return isinstance(name, str) and source['full_name'].lower() == name.lower()

    @staticmethod
    def pr_capability(token):
        """Report compatibility without inferring token permissions from account access.

        Repository push access does not prove a fine-grained token has the
        separate Pull requests write permission. Only publication tests that.
        """
        return 'not_exercised' if token.startswith(FINE_GRAINED) else 'supported'

    def repository_info(self, repository, token, *, credential_role='push token', operation='Read repository metadata'):
        try:
            info = self.request('/repos/' + repository, token=token)
        except GitHubError as error:
            raise GitHubError(error.status, repository=repository, operation=operation,
                              credential_role=credential_role, validation=error.validation, request_id=error.request_id) from None
        return info if isinstance(info, dict) else {}

    def fork_repositories(self, upstream, publishing, config):
        # An upstream-scoped fine-grained PAT need not see the private fork,
        # and a fork-scoped push PAT need not see the private upstream.
        upstream_token = config.get('pr_token') or config.get('token')
        role = 'pull request token' if config.get('pr_token') else 'push token'
        target = self.repository_info(upstream, upstream_token, credential_role=role,
                                      operation='Read upstream repository')
        destination = self.repository_info(publishing, config.get('token'), operation='Read fork destination')
        return target, destination

    def verify_fork(self, upstream, publishing):
        """Re-check a fork destination against the live API, for task overrides."""
        if not REPOSITORY.fullmatch(str(publishing)) or publishing == upstream:
            raise ValueError('Enter the fork as owner/repository.')
        self.allow_publishing(upstream, publishing)
        if not self.same_fork_network(*self.fork_repositories(upstream, publishing, self.configuration())):
            raise ValueError(publishing + ' is not a fork of ' + upstream + '.')
        return True

    def publication_preflight(self, upstream, publishing):
        """Refresh identity/access without claiming that a read proves token write."""
        self.allow_publishing(upstream, publishing)
        config = self.configuration()
        token = config.get('token')
        user = self.request('/user', token=token)  # Explicit token bypasses cached setup evidence.
        login = user.get('login') if isinstance(user, dict) else None
        if not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', login):
            raise ValueError('GitHub returned an invalid publishing identity.')
        if login.lower() != str(config.get('login', '')).lower():
            raise ValueError('GitHub publishing account changed; configure credentials again.')
        if config.get('pr_token'):
            self.credential_login(config['pr_token'], config['pr_login'])
        if upstream == publishing:
            target = destination = self.repository_info(upstream, token)
        else:
            target, destination = self.fork_repositories(upstream, publishing, config)
        if (str(target.get('full_name', '')).lower() != upstream.lower()
                or str(destination.get('full_name', '')).lower() != publishing.lower()):
            raise ValueError('GitHub repository moved or is unavailable; inspect the publishing destination.')
        permissions = destination.get('permissions')
        if not isinstance(permissions, dict) or permissions.get('push') is not True:
            raise ValueError('GitHub account no longer has push access to ' + publishing + '.')
        if upstream != publishing:
            if not self.same_fork_network(target, destination):
                raise ValueError(publishing + ' is no longer a fork of ' + upstream + '.')
        return dict(login=login, repository=publishing, upstream=upstream,
                    mode='direct' if upstream == publishing else 'fork',
                    account_write='verified', token_write='not_exercised',
                    upstream_pr_creation=self.pr_capability(config.get('pr_token') or token))

    def credential_login(self, token, expected=None):
        user = self.request('/user', token=token)
        login = user.get('login') if isinstance(user, dict) else None
        if not isinstance(login, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', login):
            raise ValueError('GitHub returned an invalid account identity.')
        if expected and login.lower() != expected.lower():
            raise ValueError('GitHub pull request account changed; configure credentials again.')
        return login

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
        clear_pr = body.get('clear_pr_credential', False)
        if type(clear_pr) is not bool:
            raise ValueError('Choose whether to remove the pull request credential.')
        pr_token, pr_login = body.get('pr_token'), body.get('pr_login')
        if pr_token is not None and not isinstance(pr_token, str):
            raise ValueError('Enter a valid pull request personal access token.')
        if pr_login is not None and not isinstance(pr_login, str):
            raise ValueError('Enter the pull request account name.')
        pr_token, pr_login = (pr_token or '').strip() or None, (pr_login or '').strip() or None
        if pr_login and not re.fullmatch(r'[A-Za-z0-9-]{1,39}', pr_login):
            raise ValueError('Enter the pull request account name.')
        if clear_pr and (pr_token or pr_login):
            raise ValueError('Remove or replace the pull request credential, not both.')
        if pr_token and not re.fullmatch(r'[A-Za-z0-9_]{20,255}', pr_token):
            raise ValueError('Enter a valid pull request personal access token.')
        previous = self.configuration()
        if not pr_token and not clear_pr:
            pr_token = previous.get('pr_token')
            pr_login = pr_login or previous.get('pr_login')
            if pr_login and not pr_token:
                raise ValueError('Provide a pull request token to verify its account.')
        if pr_token:
            if pr_token == previous.get('pr_token') and (not pr_login or pr_login.lower() == str(previous.get('pr_login') or '').lower()):
                pr_login = previous['pr_login']
            else:
                pr_login = self.credential_login(pr_token, pr_login)
        requested = body.get('publication') or {}
        if not isinstance(requested, dict):
            raise ValueError('Invalid publication settings.')
        publication = {}
        for upstream, entry in requested.items():
            if upstream not in repositories:
                raise ValueError('Publication settings must name an allowlisted repository.')
            if not isinstance(entry, dict) or entry.get('mode') not in MODES:
                raise ValueError('Choose direct or fork publication for each repository.')
            if entry['mode'] == 'direct':
                publication[upstream] = dict(mode='direct', publishing_repository=upstream)
                continue
            publishing = entry.get('publishing_repository')
            if (not isinstance(publishing, str) or not REPOSITORY.fullmatch(publishing)
                    or any(p in ('.', '..') for p in publishing.split('/'))):
                raise ValueError('Enter the fork as owner/repository.')
            if publishing == upstream:
                raise ValueError('A fork destination must differ from the upstream repository.')
            publication[upstream] = dict(mode='fork', publishing_repository=publishing)
        user = self.request('/user', token=token)
        if not isinstance(user, dict) or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', str(user.get('login', ''))):
            raise ValueError('GitHub returned an invalid account identity.')
        login = user['login']
        # Resolve every repository once, concurrently: the dashboard gives this
        # call a bounded timeout, and twenty sequential checks could exceed it.
        # Keep upstream and destination evidence separate even when a repository
        # is a destination for one entry and the upstream for another.
        needed = {(name, 'upstream' if publication.get(name, {}).get('mode') == 'fork' else 'destination')
                  for name in repositories}
        needed.update((entry['publishing_repository'], 'destination') for entry in publication.values() if entry['mode'] == 'fork')
        needed = sorted(needed)
        def resolve(item):
            name, purpose = item
            separate = purpose == 'upstream' and pr_token is not None
            return self.repository_info(name, pr_token if separate else token,
                                        credential_role='pull request token' if separate else 'push token',
                                        operation='Read upstream repository' if purpose == 'upstream' else 'Read branch destination')
        with ThreadPoolExecutor(max_workers=5) as pool:
            resolved = dict(zip(needed, pool.map(resolve, needed)))
        # A fork-mode upstream is read-only here. Requiring push on it would
        # contradict the purpose of contributing through a fork, and the account
        # permission GitHub reports does not prove the token may write anyway.
        upstreams = {name for name, entry in publication.items() if entry['mode'] == 'fork'}
        writers = [name for name in repositories if name not in upstreams]
        writers += [entry['publishing_repository'] for entry in publication.values() if entry['mode'] == 'fork']
        if not all(isinstance(resolved[(name, 'destination')].get('permissions'), dict) and resolved[(name, 'destination')]['permissions'].get('push')
                   for name in writers):
            raise ValueError('GitHub account needs push access to every repository Herdr publishes to.')
        for upstream in repositories:
            entry = publication.setdefault(upstream, dict(mode='direct', publishing_repository=upstream))
            if entry['mode'] == 'direct':
                entry['verification'] = dict(
                    fork_access='not_applicable', account_write_to_publishing='verified',
                    token_write_capability='not_exercised',
                    upstream_pr_creation=self.pr_capability(pr_token or token))
                continue
            publishing = entry['publishing_repository']
            if not self.same_fork_network(resolved[(upstream, 'upstream')], resolved[(publishing, 'destination')]):
                raise ValueError(publishing + ' is not a fork of ' + upstream + '.')
            # Setup never creates a branch or pull request to prove write access.
            entry['verification'] = dict(
                fork_access='verified', account_write_to_publishing='verified',
                token_write_capability='not_exercised',
                upstream_pr_creation=self.pr_capability(pr_token or token))
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_suffix('.tmp')
            # A crash between create and replace must not block configuration.
            temporary.unlink(missing_ok=True)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, 'w') as output:
                    record = dict(token=token, repositories=repositories, login=login,
                                  publication=publication)
                    # Omitted rather than written as null: the presence of the key
                    # is what marks the split as configured.
                    if pr_token is not None:
                        record.update(pr_token=pr_token, pr_login=pr_login)
                    json.dump(record, output)
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.cache.clear()
        return self.snapshot()

    def allow(self, repository):
        if repository not in self.configuration().get('repositories', []):
            raise ValueError('Repository is not allowlisted for GitHub publishing.')

    def find_pull(self, repository, branch, head_owner=None, base=None):
        self.allow(repository)
        # A fork pull request is discovered by its head owner, not the upstream
        # owner; matching only the branch name could attach another account's
        # pull request to this task. Constraining by base keeps a same-named
        # pull request into another branch out of the result set.
        owner = head_owner or repository.split('/')[0]
        query = dict(head=owner + ':' + branch, state='all', per_page=100)
        if base:
            query['base'] = base
        return self.request('/repos/' + repository + '/pulls?' + urlencode(query))
