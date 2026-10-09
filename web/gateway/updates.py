"""Release discovery and a fixed-purpose systemd update request."""
import json
from pathlib import Path
import re
import threading
import time
from urllib.request import Request, urlopen

REPOSITORY = 'Ultarius/herdr_proxmox'
ASSETS = {'herdr-proxmox.tar.gz', 'herdr-proxmox.tar.gz.sha256'}


def latest_release():
    request = Request(f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100',
                      headers={'User-Agent': 'herdr-proxmox-updater', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=10) as response:
        releases = json.loads(response.read(2_000_001))
    available = [r for r in releases if not r.get('draft') and not r.get('prerelease')
                 and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', r.get('tag_name', ''))
                 and ASSETS.issubset({a.get('name') for a in r.get('assets', [])})]
    if not available:
        raise ValueError('No installable stable release is available.')
    return max(available, key=lambda r: tuple(map(int, r['tag_name'][1:].split('.'))))


ALPHA = re.compile(r'alpha-[a-z0-9-]+-[a-f0-9]{12}-b[1-9][0-9]*')


def alpha_releases():
    request = Request(f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100',
                      headers={'User-Agent': 'herdr-proxmox-updater', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=10) as response:
        releases = json.loads(response.read(2_000_001))
    return sorted((r for r in releases if not r.get('draft') and r.get('prerelease')
                   and ALPHA.fullmatch(str(r.get('tag_name', '')))
                   and ASSETS.issubset({a.get('name') for a in r.get('assets', [])})),
                  key=lambda r: r.get('published_at') or '', reverse=True)


class Updates:
    def __init__(self, root='/opt/herdr-web', queue='/var/lib/herdr-update-requests', state='/var/lib/herdr-updater/status.json'):
        self.root, self.queue, self.state = Path(root), Path(queue), Path(state)
        self.lock = threading.Lock()
        self.cached = None
        self.checked = 0
        self.refreshing = False
        self.retry_after = 0
        self.check_error = None

    def _status(self):
        try:
            status = json.loads(self.state.read_text()) if self.state.exists() else {'state': 'idle'}
        except (OSError, ValueError):
            status = {'state': 'idle'}
        return status if isinstance(status, dict) else {'state': 'idle'}

    def _busy(self, status):
        return (self.queue / 'request.json').exists() or status.get('state') in ('queued', 'running')

    def _request(self, payload):
        temporary = self.queue / 'request.tmp'
        temporary.write_text(json.dumps(payload))
        temporary.replace(self.queue / 'request.json')

    def promote(self, manifest, archive, actor='dashboard'):
        """Queue an approved local build; downloads alone never authorize this."""
        with self.lock:
            if not self.queue.is_dir():
                raise ValueError('Install the updater service as root first.')
            if self._busy(self._status()):
                raise ValueError('A deployment is already running.')
            if (not isinstance(manifest, dict) or manifest.get('deployment_mode') != 'local'
                    or not re.fullmatch(r'[a-f0-9]{32}', str(manifest.get('build_id', '')))
                    or not re.fullmatch(r'[a-f0-9]{64}', str(manifest.get('sha256', '')))):
                raise ValueError('Select a retained local deployment package.')
            self._request(dict(mode='local', build_id=manifest['build_id'],
                               sha256=manifest['sha256'], path=str(archive),
                               actor=str(actor)[:80]))
            return {'state': 'queued', 'mode': 'local', 'build_id': manifest['build_id']}

    def rollback(self, actor='dashboard'):
        with self.lock:
            if not self.queue.is_dir():
                raise ValueError('Install the updater service as root first.')
            status = self._status()
            if self._busy(status):
                raise ValueError('A deployment is already running.')
            if not status.get('backup'):
                raise ValueError('No completed deployment backup is available to restore.')
            self._request(dict(mode='rollback', actor=str(actor)[:80]))
            return {'state': 'queued', 'mode': 'rollback'}

    def provenance(self, actor='dashboard'):
        """Queue a best-effort GitHub tag-to-commit repair for a release install."""
        with self.lock:
            if not self.queue.is_dir():
                raise ValueError('Install the updater service as root first.')
            if self._busy(self._status()):
                raise ValueError('A deployment is already running.')
            identity_path = self.root / 'BUILD.json'
            try:
                identity = json.loads(identity_path.read_text())
            except (OSError, ValueError) as error:
                raise ValueError('Deployment identity is missing or unreadable.') from error
            if (not isinstance(identity, dict) or identity.get('deployment_mode') == 'local'
                    or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', str(identity.get('build_id', '')))
                    or re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', str(identity.get('source_sha', '')))):
                raise ValueError('Only release deployments with unresolved source provenance can be repaired.')
            self._request(dict(mode='provenance', actor=str(actor)[:80]))
            return {'state': 'queued', 'mode': 'provenance'}

    def snapshot(self, force=False):
        with self.lock:
            now = time.monotonic()
            refresh = (force or self.cached is None or now - self.checked > 3600)
            refresh = refresh and not self.refreshing and now >= self.retry_after
            if refresh:
                self.refreshing = True
            cached = self.cached
            error = self.check_error
        if refresh:
            try:
                release = latest_release()
            except Exception as exception:
                with self.lock:
                    self.check_error = str(exception)
                    self.retry_after = time.monotonic() + 60
                    cached, error = self.cached, self.check_error
            else:
                with self.lock:
                    self.cached = cached = release
                    self.checked = time.monotonic()
                    self.retry_after = 0
                    self.check_error = error = None
            finally:
                with self.lock:
                    self.refreshing = False
        with self.lock:
            latest = cached['tag_name'] if cached else None
            version = self.root / 'VERSION'
            installed = version.read_text().strip() if version.exists() else 'unknown'
            raw = self._status()
            status = dict(raw)
            if (self.queue / 'request.json').exists():
                status = {'state': 'queued', 'backup': raw.get('backup')}
            newer = bool(latest) and (installed == 'unknown' or bool(ALPHA.fullmatch(installed)) or (bool(re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', installed))
                and tuple(map(int, latest[1:].split('.'))) > tuple(map(int, installed[1:].split('.'))))
            )
            identity_path = self.root / 'BUILD.json'
            identity = None
            if identity_path.exists():
                try:
                    identity = json.loads(identity_path.read_text())
                except (OSError, ValueError):
                    identity = None
            local_mode = identity_path.exists() and (
                not isinstance(identity, dict) or identity.get('deployment_mode') == 'local')
            source_sha = identity.get('source_sha') if isinstance(identity, dict) else None
            provenance_repairable = bool(
                isinstance(identity, dict) and identity.get('deployment_mode') != 'local'
                and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', str(identity.get('build_id', '')))
                and not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', str(source_sha or '')))
            return dict(status, installed=installed, latest=latest, available=newer,
                        supported=self.queue.is_dir() and not local_mode,
                        local_supported=self.queue.is_dir(),
                        source_sha=source_sha if isinstance(source_sha, str) else None,
                        provenance_repairable=provenance_repairable,
                        rollback_available=bool(raw.get('backup')),
                        deployment_mode='local' if local_mode else 'release',
                        release_url=cached['html_url'] if cached else None,
                        check_error=error,
                        notes=cached.get('body', '')[:12000] if cached else '')

    def alphas(self):
        return {'releases': [{'version': r['tag_name'], 'notes': r.get('body', '')[:12000]}
                             for r in alpha_releases()]}

    def install_alpha(self, body):
        if body.get('confirm_alpha') is not True:
            raise ValueError('Explicit alpha installation confirmation is required.')
        version = body.get('version')
        if not isinstance(version, str) or not ALPHA.fullmatch(version):
            raise ValueError('Invalid alpha version.')
        if not any(r['tag_name'] == version for r in alpha_releases()):
            raise ValueError('This alpha no longer has installable release assets. Refresh the list.')
        with self.lock:
            if not self.queue.is_dir():
                raise ValueError('Install the updater service as root first.')
            try:
                identity = json.loads((self.root / 'BUILD.json').read_text())
            except FileNotFoundError:
                identity = {}
            if not isinstance(identity, dict) or identity.get('deployment_mode') == 'local':
                raise ValueError('Release installation is disabled for a local deployment.')
            if self._busy(self._status()):
                raise ValueError('A deployment is already running.')
            self._request({'mode': 'alpha', 'version': version})
            return {'state': 'queued', 'mode': 'alpha', 'version': version}

    def install(self, body):
        if isinstance(body, dict) and body.get('channel') == 'alpha':
            return self.install_alpha(body)
        info = self.snapshot()
        with self.lock:
            if not isinstance(body, dict) or body.get('version') != info['latest']:
                raise ValueError('Check updates again before installing.')
            if not info['supported']:
                if info.get('deployment_mode') == 'local':
                    raise ValueError('Release installation is disabled for a local deployment.')
                raise ValueError('Install the updater service as root first.')
            if not info['available'] or info['state'] in ('queued', 'running'):
                raise ValueError('No update available, or an update is already running.')
            request = self.queue / 'request.json'
            temporary = self.queue / 'request.tmp'
            with temporary.open('w') as file:
                json.dump({'version': info['latest']}, file)
            temporary.replace(request)
            return {'state': 'queued', 'version': info['latest']}
