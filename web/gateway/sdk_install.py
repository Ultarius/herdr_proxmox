"""Request the fixed-purpose SDK install service; the root worker installs it."""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading


class SdkInstall:
    def __init__(self, queue='/var/lib/herdr-sdk-requests',
                 state='/var/lib/herdr-sdk/status.json',
                 flutter='/home/herdr/.local/bin/flutter'):
        self.queue, self.state, self.flutter = Path(queue), Path(state), Path(flutter)
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            try:
                status = json.loads(self.state.read_text()) if self.state.is_file() else {'state': 'idle'}
            except (OSError, ValueError):
                status = {'state': 'failed', 'error': 'SDK status is unreadable; inspect the installation service.'}
            if not isinstance(status, dict):
                status = {'state': 'idle'}
            if status.get('state') == 'running':
                try:
                    started = datetime.fromisoformat(status['started_at'])
                    stale = (datetime.now(timezone.utc) - started).total_seconds() > 1860
                except (KeyError, ValueError, TypeError):
                    stale = True
                if stale:
                    status = dict(status, state='failed', error='SDK installation did not finish within its service deadline; inspect the service before retrying.')
            installed = self.flutter.is_file()
            if (self.queue / 'request.json').exists():
                status = dict(status, state='queued')
            return dict(status, installed=installed, supported=self.queue.is_dir())

    def install(self, body):
        if body not in (None, {}):
            raise ValueError('SDK installation does not accept options.')
        with self.lock:
            info = self.snapshot()
            if not info['supported']:
                raise ValueError('Install the SDK service as root first.')
            if info['state'] in ('queued', 'running'):
                raise ValueError('An SDK installation is already running.')
            if info['installed'] and info['state'] != 'failed':
                raise ValueError('The development SDK is already installed.')
            request = self.queue / 'request.json'
            temporary = self.queue / 'request.tmp'
            temporary.write_text(json.dumps({'action': 'install'}))
            temporary.replace(request)
            return {'state': 'queued'}
