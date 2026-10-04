"""Allowlisted startup of Herdr through the gateway user's service manager."""
import os
import subprocess
import threading
import time


class HerdrServer:
    def __init__(self, command):
        self.command = command
        self.lock = threading.Lock()

    def status(self):
        try:
            self.command('workspace', 'list', timeout=2)
            return {'state': 'running'}
        except ValueError as error:
            if 'server_not_running' in str(error):
                return {'state': 'stopped'}
            raise

    def start(self, body):
        if not isinstance(body, dict) or body:
            raise ValueError('Start Herdr does not accept commands or arguments.')
        with self.lock:
            if self.status()['state'] == 'running':
                return {'state': 'running', 'already_running': True}
            environment = dict(os.environ)
            runtime = f'/run/user/{os.getuid()}'
            environment.update(XDG_RUNTIME_DIR=runtime, DBUS_SESSION_BUS_ADDRESS=f'unix:path={runtime}/bus')
            result = subprocess.run(['systemctl', '--user', 'start', 'herdr-session.service'], capture_output=True, text=True, timeout=5, env=environment)
            if result.returncode:
                raise ValueError('Could not start the Herdr user service. Check journalctl --user -u herdr-session over the Proxmox console.')
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if self.status()['state'] == 'running':
                    return {'state': 'running', 'already_running': False}
                time.sleep(.2)
            return {'state': 'starting'}
