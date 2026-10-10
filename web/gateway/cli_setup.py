"""Allowlisted CLI setup PTYs, with bounded, ephemeral terminal output."""
import codecs
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
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
# Herdr names these integrations differently from our CLI aliases, and the
# plugin location is where `herdr integration status` reports it. Without the
# integration an agent never reports a native resume reference, so session
# archives can only be continued with saved context.
INTEGRATIONS = {'codex': ('codex', '.codex/herdr-agent-state.sh'),
                'claude': ('claude', '.claude/hooks/herdr-agent-state.sh'),
                'opencode': ('opencode', '.config/opencode/plugins/herdr-agent-state.js'),
                'agy': ('antigravity-cli', '.gemini/config/hooks/herdr-agent-state.sh')}


def parse_integration_status(text):
    """Read `herdr integration status` lines.

    Herdr prints `<name>: <state> (<path>)`, where the state is one of
    `not installed`, `current (v13)`, `legacy`, `outdated (v3 < v5)` or
    `needs repair (v5)`. A qualifier such as `(experimental)` decorates the
    name. Only the state and versions are kept; nothing else is echoed.
    """
    entries = {}
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        name, _, rest = line.partition(':')
        name = name.split('(')[0].strip().lower()
        if not name:
            continue
        rest = rest.strip()
        # The last parenthesised group is normally the plugin path. A state
        # that omits it still carries its version in the only group present.
        groups = re.findall(r'\(([^()]*)\)', rest)
        head = (rest[:rest.rfind('(')] if groups else rest).strip().lower()
        if head.startswith('not installed'):
            detail = ''
        elif len(groups) > 1:
            detail = groups[0].strip()
        elif groups and head.startswith(('current', 'outdated', 'needs repair')):
            detail = groups[0].strip()
        else:
            detail = ''
        if head.startswith('not installed'):
            entries[name] = dict(state='not_installed', version='', expected='')
        elif head.startswith('needs repair'):
            entries[name] = dict(state='needs_repair', version=detail[:40], expected='')
        elif head.startswith('outdated'):
            installed, _, expected = detail.partition('<')
            entries[name] = dict(state='outdated', version=installed.strip()[:40],
                                 expected=expected.strip()[:40])
        elif head.startswith('current'):
            entries[name] = dict(state='current', version=detail[:40], expected='')
        else:
            entries[name] = dict(state='unknown', version='', expected='')
    return entries


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
        self.integration_checked = 0
        self.integration_state = {}
        threading.Thread(target=self._reap, daemon=True).start()

    def binary(self, name):
        return self.home / '.local/bin' / name

    def herdr(self):
        return self.home / '.local/bin' / 'herdr'

    def ready(self, name):
        """Why this CLI cannot run agents yet, or None when it is ready.

        An agent whose Herdr integration is absent or outdated still starts,
        but it never reports working/blocked state and never hands back a
        resume reference, so the session becomes unrecoverable without any
        visible failure. That misconfiguration is cheaper to prevent here than
        to diagnose from a lost conversation later.
        """
        if name not in COMMANDS:
            return None  # Runtimes outside this allowlist are not ours to judge.
        if not os.access(self.binary(name), os.X_OK):
            return NAMES[name] + ' is not installed. Install the CLI before adding an agent.'
        integration = self.integration(name)
        if not integration.get('installed'):
            return (NAMES[name] + ' has no Herdr integration, so its sessions cannot report '
                    'state or be resumed. Install it on the CLI accounts page first.')
        if not integration.get('current'):
            if integration['state'] == 'unverified':
                return (NAMES[name] + ' has a Herdr integration whose version could not be verified. '
                        'Run `herdr integration status` before adding an agent.')
            shown = integration['version'] or 'legacy'
            return (NAMES[name] + ' has a Herdr integration that is '
                    + ('outdated (' + shown + ' < ' + (integration.get('expected') or 'current') + ')'
                       if integration['state'] == 'outdated' else 'in need of repair')
                    + '. Update it on the CLI accounts page before adding an agent.')
        return None

    def integration_status(self, refresh=False):
        """Parse `herdr integration status` once and cache it briefly.

        One command covers every agent, so the four CLI cards share a single
        bounded call instead of spawning one each.
        """
        with self.lock:
            if not refresh and time.monotonic() - self.integration_checked < 10:
                return self.integration_state
            state = {}
            try:
                probe = subprocess.run([str(self.herdr()), 'integration', 'status'],
                                       capture_output=True, text=True, timeout=15,
                                       env={**os.environ, 'HOME': str(self.home), 'TERM': 'dumb',
                                            'NO_COLOR': '1',
                                            'PATH': str(self.home / '.local/bin') + ':/usr/local/bin:/usr/bin:/bin'})
                if probe.returncode == 0:
                    state = parse_integration_status(probe.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                state = {}
            # Cache the outcome either way: a failed probe must not re-run on
            # every card render.
            self.integration_state, self.integration_checked = state, time.monotonic()
            return state

    def integration(self, name):
        """Report whether this CLI's Herdr integration is connected.

        An agent only reports working/blocked state and a native resume
        reference through its official integration, so a missing one silently
        disables session resume.
        """
        herdr_name, relative = INTEGRATIONS[name]
        result = {'installed': False, 'current': False, 'integration': herdr_name,
                  'version': '', 'expected': '', 'state': '',
                  'detail': 'Install the Herdr integration so this agent can report its state and resume sessions.'}
        if not os.access(self.binary(name), os.X_OK):
            return result
        entry = self.integration_status().get(herdr_name)
        if entry is None:
            # `herdr integration status` is the authority; fall back to the
            # plugin path it prints when the command itself is unavailable.
            try:
                installed = (self.home / relative).exists()
            except OSError:
                return result
            return {**result, 'installed': installed,
                    'state': 'unverified',
                    'detail': ('Herdr integration is present but its version could not be verified. '
                               'Run `herdr integration status` before adding agents.' if installed
                               else result['detail'])}
        state = entry['state']
        installed = state != 'not_installed'
        current = state == 'current'
        version = entry['version']
        detail = result['detail']
        if current:
            detail = 'Herdr integration connected' + ((' (' + version + ')') if version else '') + '.'
        elif state == 'outdated':
            detail = ('Herdr integration is outdated (' + (version or 'legacy') +
                      ' < ' + (entry['expected'] or 'current') +
                      '); update it to restore current behavior.')
        elif state == 'needs_repair':
            detail = ('Herdr integration needs repair' + ((' (' + version + ')') if version else '') +
                      '; reinstall it to restore reporting.')
        elif installed:
            detail = 'Herdr integration reported an unknown state; inspect it in the setup terminal.'
        return {**result, 'installed': installed, 'current': current, 'version': version,
                'expected': entry['expected'], 'state': state, 'detail': detail}

    def install_integration(self, name):
        """Install this CLI's Herdr integration. Bounded, allowlisted, no secrets."""
        if name not in INTEGRATIONS:
            raise ValueError('Unsupported CLI.')
        if not os.access(self.binary(name), os.X_OK):
            raise ValueError('CLI is not installed.')
        env = {**os.environ, 'HOME': str(self.home), 'TERM': 'dumb', 'NO_COLOR': '1',
               'PATH': str(self.home / '.local/bin') + ':/usr/local/bin:/usr/bin:/bin'}
        try:
            probe = subprocess.run([str(self.herdr()), 'integration', 'install', INTEGRATIONS[name][0]],
                                   capture_output=True, text=True, timeout=120, env=env,
                                   cwd=str(self.home))
        except subprocess.TimeoutExpired:
            raise ValueError('Herdr did not finish installing the integration in 120 seconds; '
                             'run it in the setup terminal to see why.') from None
        except OSError:
            raise ValueError('Herdr CLI is unavailable; install the LXC agent runtimes first.') from None
        # Re-read the authoritative status rather than trusting the exit code.
        state = self.integration_status(refresh=True).get(INTEGRATIONS[name][0])
        if not state or state['state'] in ('not_installed', 'unknown'):
            detail = ' '.join((probe.stdout + probe.stderr).split())[:300]
            raise ValueError('Herdr did not install the integration' + (': ' + detail if detail else '') + '.')
        result = self.integration(name)
        if not result['installed']:
            raise ValueError('Herdr reports this integration as not installed; inspect the setup terminal.')
        if not result['current']:
            # Installed but still outdated is not a working integration, and
            # reporting it as success would leave agents silently unverified.
            raise ValueError('Herdr installed the integration but it is still '
                             + ('outdated' if result['state'] == 'outdated' else 'not current')
                             + '. Update Herdr and retry; agents stay blocked until it is current.')
        return result

    def status(self, name):
        result = {'id': name, 'name': NAMES[name], 'installed': os.access(self.binary(name), os.X_OK),
                  'status': 'unknown', 'detail': 'Check authentication in the setup terminal.',
                  'integration': self.integration(name)}
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
        # One `herdr integration status` call backs every card; the per-CLI
        # reports below only read its cached result.
        self.integration_status()
        with ThreadPoolExecutor(max_workers=4) as pool:
            clis = list(pool.map(self.status, COMMANDS))
        connected = [c['name'] for c in clis
                     if c.get('installed') and (c.get('integration') or {}).get('current')]
        outdated = [c['name'] for c in clis
                    if c.get('installed') and (c.get('integration') or {}).get('state') in ('outdated', 'needs_repair')]
        missing = [c['name'] for c in clis
                   if c.get('installed') and not (c.get('integration') or {}).get('installed')]
        # Disjoint buckets: current, outdated/needs-repair, or absent.
        return {'clis': clis, 'integrations': {'connected': len(connected), 'outdated': len(outdated),
                                               'missing': len(missing), 'total': len(clis),
                                               'names': connected, 'outdated_names': outdated,
                                               'missing_names': missing}}

    def action(self, action, body):
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object.')
        if action == 'verify':
            name = body.get('cli')
            if not isinstance(name, str) or name not in COMMANDS:
                raise ValueError('Unsupported CLI.')
            return self.verify(name)
        if action == 'install_integration':
            name = body.get('cli')
            if not isinstance(name, str) or name not in COMMANDS:
                raise ValueError('Unsupported CLI.')
            if sys.platform != 'linux':
                raise ValueError('Herdr integrations are installed on the Linux LXC gateway.')
            return {'cli': name, **self.install_integration(name)}
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
