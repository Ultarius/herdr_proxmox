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


class Updates:
    def __init__(self, root='/opt/herdr-web', queue='/var/lib/herdr-update-requests', state='/var/lib/herdr-updater/status.json'):
        self.root, self.queue, self.state = Path(root), Path(queue), Path(state)
        self.lock = threading.Lock()
        self.cached = None
        self.checked = 0

    def snapshot(self):
        with self.lock:
            if self.cached is None or time.monotonic() - self.checked > 3600:
                self.cached = latest_release()
                self.checked = time.monotonic()
            latest = self.cached['tag_name']
            version = self.root / 'VERSION'
            installed = version.read_text().strip() if version.exists() else 'unknown'
            status = json.loads(self.state.read_text()) if self.state.exists() else {'state': 'idle'}
            if (self.queue / 'request.json').exists():
                status = {'state': 'queued'}
            newer = installed == 'unknown' or (bool(re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', installed))
                and tuple(map(int, latest[1:].split('.'))) > tuple(map(int, installed[1:].split('.'))))
            return dict(installed=installed, latest=latest, available=newer,
                        supported=self.queue.is_dir(), release_url=self.cached['html_url'],
                        notes=self.cached.get('body', '')[:12000], **status)

    def install(self, body):
        info = self.snapshot()
        with self.lock:
            if not isinstance(body, dict) or body.get('version') != info['latest']:
                raise ValueError('Check updates again before installing.')
            if not info['supported']:
                raise ValueError('Install the updater service as root first.')
            if not info['available'] or info['state'] in ('queued', 'running'):
                raise ValueError('No update available, or an update is already running.')
            request = self.queue / 'request.json'
            temporary = self.queue / 'request.tmp'
            with temporary.open('w') as file:
                json.dump({'version': info['latest']}, file)
            temporary.replace(request)
            return {'state': 'queued', 'version': info['latest']}
