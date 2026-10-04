"""Persistent dashboard listener policy, managed without root privileges."""
import json
import os
from pathlib import Path
import tempfile
import threading

BINDS = {'lan': '0.0.0.0', 'ssh': '127.0.0.1'}


def configured_bind(path, fallback):
    if not Path(path).exists():
        return fallback
    mode = json.loads(Path(path).read_text()).get('mode')
    if mode not in BINDS:
        raise ValueError('Invalid dashboard access configuration.')
    return BINDS[mode]


class DashboardAccess:
    def __init__(self, path, ssh_access, bind, schedule):
        self.path = Path(path)
        self.ssh_access = ssh_access
        self.bind = bind
        self.schedule = schedule
        self.lock = threading.Lock()
        self.pending = None

    def snapshot(self):
        return {'mode': 'ssh' if self.bind == BINDS['ssh'] else 'lan', 'pending_mode': self.pending}

    def update(self, body):
        if not isinstance(body, dict) or body.get('mode') not in BINDS:
            raise ValueError('Select LAN or SSH-only access.')
        mode = body['mode']
        with self.lock:
            if self.pending:
                raise ValueError('An access change is already pending. Reconnect after it completes.')
            if BINDS[mode] == self.bind:
                return self.snapshot()
            if mode == 'ssh':
                if self.ssh_access.snapshot()['key_count'] < 1:
                    raise ValueError('Add an SSH public key before enabling SSH-only access.')
                if body.get('tunnel_ready') is not True:
                    raise ValueError('Test your SSH tunnel and confirm it works before switching.')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='access-')
            try:
                with os.fdopen(fd, 'w') as output:
                    json.dump({'mode': mode}, output)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self.pending = mode
            self.schedule()
            return {**self.snapshot(), 'apply_after_seconds': 5}
