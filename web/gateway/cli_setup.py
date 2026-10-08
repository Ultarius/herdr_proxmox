"""Allowlisted CLI setup PTYs, with bounded, ephemeral terminal output."""
import codecs
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import select
import struct
import subprocess
import sys
import threading
import time
import uuid

COMMANDS = {'codex': ('login', '--device-auth'), 'claude': ('auth', 'login'),
            'opencode': ('auth', 'login'), 'agy': ()}
NAMES = {'codex': 'Codex', 'claude': 'Claude Code', 'opencode': 'OpenCode', 'agy': 'Antigravity'}


class SetupSession:
    def __init__(self, argv, cwd, env):
        import fcntl
        import termios
        master, slave = os.openpty()
        self.master = master
        self.lock = threading.RLock()
        self.output = deque(maxlen=64)
        self.sequence = 0
        self.last_seen = time.monotonic()
        self.closed = False
        self.done = False
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('pty_exec.py')), *argv],
                                            stdin=slave, stdout=slave, stderr=slave, cwd=cwd,
                                            env=env, start_new_session=True, close_fds=True)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                if self.closed:
                    break
                if not select.select([self.master], [], [], 1)[0]:
                    continue
                try:
                    chunk = os.read(self.master, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                with self.lock:
                    if self.closed:
                        break
                    self.sequence += 1
                    self.output.append((self.sequence, decoder.decode(chunk)))
        except (OSError, ValueError):
            pass
        finally:
            self.process.wait()
            with self.lock:
                self.done = True

    def poll(self, cursor):
        with self.lock:
            self.last_seen = time.monotonic()
            return {'output': ''.join(text for seq, text in self.output if seq > cursor),
                    'cursor': self.sequence, 'truncated': bool(self.output and cursor < self.output[0][0] - 1),
                    'running': not self.done, 'exit_code': self.process.poll() if self.done else None}

    def input(self, data):
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise ValueError('Setup session has ended. Start a new session.')
            self.last_seen = time.monotonic()
            pending = data.encode()
            deadline = time.monotonic() + 2
            while pending:
                if time.monotonic() >= deadline:
                    raise ValueError('Terminal is not accepting input. Some input may have been delivered; inspect the terminal before retrying.')
                if select.select([], [self.master], [], 0.1)[1]:
                    try:
                        pending = pending[os.write(self.master, pending):]
                    except BlockingIOError:
                        pass

    def resize(self, cols, rows):
        import fcntl
        import termios
        with self.lock:
            if not self.closed:
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                if not self.done or self.process.poll() is None:
                    os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
            except ProcessLookupError:
                pass
            os.close(self.master)
            self.output.clear()


class CliSetup:
    def __init__(self, home=None):
        self.home = Path(home or Path.home())
        self.sessions = {}
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        threading.Thread(target=self._reap, daemon=True).start()

    def binary(self, name):
        return self.home / '.local/bin' / name

    def status(self, name):
        result = {'id': name, 'name': NAMES[name], 'installed': os.access(self.binary(name), os.X_OK),
                  'status': 'unknown', 'detail': 'Check authentication in the setup terminal.'}
        if not result['installed']:
            return {**result, 'status': 'missing', 'detail': 'Run the LXC installer to install this CLI.'}
        try:
            if name in ('codex', 'claude'):
                args = ('login', 'status') if name == 'codex' else ('auth', 'status')
                probe = subprocess.run([str(self.binary(name)), *args], capture_output=True, text=True, timeout=5)
                if name == 'claude':
                    # `claude auth status` prints JSON with loggedIn and the
                    # documented authMethod. Either field can be absent by
                    # version, so accept both and never echo the payload.
                    payload = json.loads(probe.stdout)
                    if not isinstance(payload, dict):
                        raise ValueError('Unexpected Claude status payload.')
                    method = payload.get('authMethod')
                    logged_in = payload.get('loggedIn')
                    if not isinstance(logged_in, bool):
                        logged_in = method not in (None, 'none') if isinstance(method, str) else None
                    if logged_in and isinstance(method, str) and method and method != 'none':
                        result['auth_method'] = method
                else:
                    text = (probe.stdout + probe.stderr).lower()
                    logged_in = True if 'logged in' in text and 'not logged in' not in text else False if 'not logged in' in text else None
                if isinstance(logged_in, bool):
                    detail = 'Sign in using the setup terminal.'
                    if logged_in:
                        detail = 'CLI reports signed in; run Test account to confirm a real reply.'
                        if result.get('auth_method'):
                            detail = f"Signed in via {result['auth_method']}; run Test account to confirm a real reply."
                    result.update(status='configured' if logged_in else 'not_configured', detail=detail)
            elif name == 'opencode':
                auth = self.home / '.local/share/opencode/auth.json'
                providers = json.loads(auth.read_text()) if auth.exists() else {}
                if isinstance(providers, dict):
                    detected = any(isinstance(v, dict) and any(v.get(k) for k in ('key', 'access', 'refresh')) for v in providers.values())
                    result.update(status='credentials_detected' if detected else 'not_configured',
                                  detail='Stored provider credentials detected; validity has not been tested.' if detected else 'No stored provider credentials. Configure a provider in the terminal; environment-based providers may also be available.')
            # AGY's secure keyring cannot be inferred from a settings file.
        except (OSError, ValueError, subprocess.TimeoutExpired):
            result['detail'] = 'Authentication status could not be determined. Inspect the setup terminal.'
        return result

    def verify(self, name):
        """Confirm the CLI answers a real prompt; a stored credential is not proof."""
        if name not in COMMANDS:
            raise ValueError('Unsupported CLI.')
        if not os.access(self.binary(name), os.X_OK):
            raise ValueError('CLI is not installed.')
        if name != 'claude':
            raise ValueError('Account verification is currently available for Claude Code only.')
        env = {**os.environ, 'HOME': str(self.home), 'TERM': 'dumb', 'NO_COLOR': '1',
               'PATH': str(self.home / '.local/bin') + ':/usr/local/bin:/usr/bin:/bin',
               'BROWSER': '/bin/false'}
        cwd = self.home / '.config/herdr-web/setup'
        cwd.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            probe = subprocess.run([str(self.binary(name)), '--print', 'Respond with hello.',
                                    '--output-format', 'json'], capture_output=True, text=True,
                                   timeout=90, env=env, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise ValueError('Claude Code did not answer within 90 seconds; inspect the setup terminal and account access.') from None
        try:
            payload = json.loads(probe.stdout)
        except ValueError:
            raise ValueError('Claude Code did not return a JSON result; inspect the setup terminal.') from None
        reply = payload.get('result') if isinstance(payload, dict) else None
        if probe.returncode != 0 or not isinstance(reply, str) or not reply.strip():
            message = ' '.join(str(reply).split())[:300] if reply is not None else ''
            raise ValueError('Claude Code could not answer' + (': ' + message if message else '') +
                             '. Inspect the setup terminal and account access.')
        return {'cli': name, 'verified': True,
                'detail': 'Claude Code answered: ' + ' '.join(reply.split())[:200]}

    def snapshot(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            return {'clis': list(pool.map(self.status, COMMANDS))}

    def action(self, action, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        if action == 'verify':
            name = body.get('cli')
            if not isinstance(name, str) or name not in COMMANDS:
                raise ValueError('Unsupported CLI.')
            return self.verify(name)
        with self.lock:
            if action == 'start':
                name = body.get('cli')
                if not isinstance(name, str) or name not in COMMANDS:
                    raise ValueError('Unsupported CLI.')
                if sys.platform != 'linux':
                    raise ValueError('Interactive setup requires the Linux LXC gateway.')
                if not os.access(self.binary(name), os.X_OK):
                    raise ValueError('CLI is not installed.')
                if len(self.sessions) >= 4:
                    raise ValueError('Close an existing setup terminal first.')
                if any(cli == name for cli, session in self.sessions.values()):
                    raise ValueError('This CLI already has an open setup terminal.')
                env = {**os.environ, 'HOME': str(self.home), 'TERM': 'xterm-256color', 'COLORTERM': 'truecolor',
                       'PATH': str(self.home / '.local/bin') + ':/usr/local/bin:/usr/bin:/bin',
                       'BROWSER': '/bin/false', 'SSH_CONNECTION': '127.0.0.1 0 127.0.0.1 0', 'SSH_TTY': '/dev/tty'}
                cwd = self.home / '.config/herdr-web/setup'
                cwd.mkdir(parents=True, exist_ok=True, mode=0o700)
                session = SetupSession([str(self.binary(name)), *COMMANDS[name]], cwd, env)
                key = uuid.uuid4().hex
                self.sessions[key] = (name, session)
                return {'id': key}
            key = body.get('id')
            if not isinstance(key, str) or key not in self.sessions:
                raise ValueError('Setup session expired or is unavailable.')
            _, session = self.sessions[key]
            if action == 'close':
                session.close()
                del self.sessions[key]
                return {'closed': True}
            if action == 'poll':
                cursor = body.get('cursor', 0)
                if type(cursor) is not int or not 0 <= cursor <= session.sequence:
                    raise ValueError('Invalid terminal cursor.')
                return session.poll(cursor)
            if action == 'input':
                data = body.get('data')
                if not isinstance(data, str) or not 0 < len(data.encode()) <= 4096:
                    raise ValueError('Terminal input must contain 1–4096 bytes.')
                session.input(data)
                return {'accepted': True}
            if action == 'resize':
                cols, rows = body.get('cols'), body.get('rows')
                if type(cols) is not int or type(rows) is not int or not (10 <= cols <= 400 and 5 <= rows <= 150):
                    raise ValueError('Invalid terminal dimensions.')
                session.resize(cols, rows)
                return {'resized': True}
            raise ValueError('Unsupported setup action.')

    def _reap(self):
        while not self.stopped.wait(30):
            with self.lock:
                for key, (_, session) in list(self.sessions.items()):
                    if time.monotonic() - session.last_seen > 600:
                        session.close()
                        del self.sessions[key]

    def close(self):
        self.stopped.set()
        with self.lock:
            for _, session in self.sessions.values():
                session.close()
            self.sessions.clear()
