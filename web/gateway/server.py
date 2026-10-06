"""Local, authenticated dashboard gateway to the official Herdr CLI."""
import hmac
import secrets
import time
from http.cookies import SimpleCookie
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from organizations import OrganizationStore
from integration import IntegrationWatcher
from integration_coordinator import IntegrationCoordinator
from project_files import ProjectJobs, clone_repository, project_directory
from cli_setup import CliSetup
import model_catalog
import project_explorer
import project_git
from run_logs import RunLogs
from ssh_access import SshAccess
from dashboard_access import DashboardAccess, configured_bind
from herdr_server import HerdrServer
from herdr_ids import is_workspace_id
from resource_usage import resources
from organization_transfer import export_configuration, import_configuration
from updates import Updates

ROOT = Path(os.environ.get('HERDR_WEB_ROOT', '/opt/herdr-web/public')).resolve()
PROJECTS = Path(os.environ.get('HERDR_PROJECTS', '/home/herdr/projects')).resolve()
BIN = os.environ.get('HERDR_BIN', '/home/herdr/.local/bin/herdr')
TOKEN_FILE = Path(os.environ.get('HERDR_WEB_TOKEN_FILE', '/home/herdr/.config/herdr-web/token'))
DATABASE = Path(os.environ.get('HERDR_ORGANIZATION_DB', '/home/herdr/.config/herdr-web/organizations.sqlite3'))


def command(*args, timeout=10):
    result = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError(result.stderr.strip()[:500] or 'Herdr command failed; start Herdr over SSH first.')
    if args[:2] == ('agent', 'read'):
        return {'output': result.stdout[-40000:]}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError('Unexpected Herdr response; check the installed CLI version.') from exc
    if isinstance(data, dict) and 'error' in data:
        raise ValueError(str(data['error'])[:500])
    return data.get('result', data) if isinstance(data, dict) else data


def listing(data, field):
    # Preserve Herdr's native item fields, but normalize its response envelope.
    items = data if isinstance(data, list) else data.get(field)
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(f'Unexpected {field} response shape from Herdr.')
    return items


def clone_project(body):
    return clone_repository(PROJECTS, body)


def workspace_action(name, body):
    if not isinstance(body, dict):
        raise ValueError('Expected a JSON object.')
    if name == 'create':
        label = body.get('label', '')
        path_value = body.get('cwd', '')
        if not isinstance(path_value, str) or not path_value:
            raise ValueError('A project directory is required.')
        path = project_directory(PROJECTS, path_value)
        validate_label(label)
        return command('workspace', 'create', '--cwd', str(path), '--label', label, '--no-focus')
    workspace_id = body.get('id', '')
    if not is_workspace_id(workspace_id):
        raise ValueError('Invalid workspace ID.')
    if name == 'focus':
        return command('workspace', 'focus', workspace_id)
    if name == 'rename':
        label = body.get('label', '')
        validate_label(label)
        return command('workspace', 'rename', workspace_id, label)
    raise ValueError('Unsupported workspace action.')


def validate_label(label):
    if not isinstance(label, str) or not label.strip() or len(label) > 120 or label.startswith('-') or any(ord(c) < 32 for c in label):
        raise ValueError('Use a workspace name of 1–120 characters, without control characters or a leading dash.')


class BrowserSessions:
    lifetime = 7 * 24 * 60 * 60

    def __init__(self):
        self.lock = threading.Lock()
        self.entries = {}

    def create(self, token):
        key = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.entries = {k: v for k, v in self.entries.items() if v[0] > now}
            if len(self.entries) >= 256:
                del self.entries[min(self.entries, key=lambda k: self.entries[k][0])]
            self.entries[key] = (now + self.lifetime, token)
        return key

    def valid(self, key, token):
        with self.lock:
            entry = self.entries.get(key)
            return bool(entry and entry[0] > time.time() and hmac.compare_digest(entry[1], token))

    def revoke(self, key):
        with self.lock:
            self.entries.pop(key, None)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, data, cookie=None):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def session_key(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
            return cookies['herdr_session'].value if 'herdr_session' in cookies else ''
        except Exception:
            return ''

    def session_cookie(self, key, age):
        secure = '; Secure' if getattr(self.server, 'cookie_secure', False) else ''
        return f'herdr_session={key}; Path=/api/; Max-Age={age}; HttpOnly; SameSite=Strict{secure}'

    def authenticated(self):
        supplied = self.headers.get('Authorization', '')
        expected = 'Bearer ' + self.server.token
        bearer = hmac.compare_digest(supplied.encode(), expected.encode())
        if not bearer and not (
                getattr(self.server, 'sessions', None) and
                self.server.sessions.valid(self.session_key(), self.server.token)):
            self.reply(401, {'error': 'Invalid dashboard token. Disconnect and enter the correct token.'})
            return False
        if not bearer and self.command == 'POST' and not self.headers.get('Origin'):
            self.reply(403, {'error': 'A same-origin browser request is required.'})
            return False
        return self.same_origin()

    def same_origin(self):
        # No CORS: only the same-origin browser application may use this API.
        origin = self.headers.get('Origin')
        if origin and origin not in ('http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')):
            self.reply(403, {'error': 'Origin rejected.'})
            return False
        return True

    def do_GET(self):
        if self.path == '/api/session':
            if not self.same_origin():
                return
            sessions = getattr(self.server, 'sessions', None)
            self.reply(200, {'authenticated': bool(sessions and sessions.valid(self.session_key(), self.server.token))})
            return
        if self.path == '/logs/view':
            content = Path(__file__).with_name('log_view.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'")
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if self.path.startswith('/api/'):
            if not self.authenticated():
                return
            if self.path == '/api/resources':
                self.reply(200, resources.snapshot())
                return
            if self.path == '/api/organizations/export':
                self.reply(200, export_configuration(self.server.organizations))
                return
            if self.path not in ('/api/snapshot', '/api/organizations', '/api/organizations/directory', '/api/organizations/state', '/api/projects/jobs', '/api/cli-setup', '/api/logs', '/api/ssh-access', '/api/dashboard-access', '/api/herdr-server', '/api/updates', '/api/models', '/api/integration'):
                self.reply(404, {'error': 'Unknown endpoint.'})
                return
            try:
                if self.path == '/api/projects/jobs':
                    self.reply(200, self.server.projects.snapshot())
                    return
                if self.path == '/api/models':
                    self.reply(200, model_catalog.snapshot())
                    return
                if self.path == '/api/integration':
                    # Served from memory; the watcher owns the Git work.
                    self.reply(200, self.server.integration.snapshot())
                    return
                if self.path == '/api/updates':
                    self.reply(200, self.server.updates.snapshot())
                    return
                if self.path == '/api/herdr-server':
                    self.reply(200, self.server.herdr_server.status())
                    return
                if self.path == '/api/dashboard-access':
                    self.reply(200, self.server.dashboard_access.snapshot())
                    return
                if self.path == '/api/ssh-access':
                    self.reply(200, self.server.ssh_access.snapshot())
                    return
                if self.path == '/api/logs':
                    self.reply(200, self.server.run_logs.snapshot())
                    return
                if self.path == '/api/cli-setup':
                    self.reply(200, self.server.cli_setup.snapshot())
                    return
                if self.path == '/api/organizations/state':
                    self.reply(200, self.server.organizations.state_snapshot())
                    return
                if self.path == '/api/organizations/directory':
                    self.reply(200, self.server.organizations.snapshot(directory=True))
                    return
                if self.path == '/api/organizations':
                    self.reply(200, self.server.organizations.snapshot())
                    return
                agents = listing(command('agent', 'list'), 'agents')
                if getattr(self.server, 'organizations', None) is not None:
                    agents = self.server.organizations.label_agents(agents)
                self.reply(200, {
                    'herdr_server': 'running',
                    'workspaces': listing(command('workspace', 'list'), 'workspaces'),
                    'agents': agents,
                })
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                if self.path == '/api/snapshot' and isinstance(exc, ValueError) and 'server_not_running' in str(exc):
                    self.reply(200, {'herdr_server': 'stopped', 'workspaces': [], 'agents': []})
                    return
                self.reply(502, {'error': str(exc)[:500]})
            except sqlite3.Error:
                self.reply(503, {'error': 'Organization storage is unavailable. Check the gateway service logs and available disk space.'})
            return
        from urllib.parse import unquote, urlsplit
        path = (ROOT / unquote(urlsplit(self.path).path).lstrip('/')).resolve()
        if not path.is_relative_to(ROOT):
            self.reply(404, {'error': 'Not found.'})
            return
        if path.is_dir():
            path = path / 'index.html'
        if not path.is_file():
            self.reply(404, {'error': 'Not found. Build the web assets first.'})
            return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        if self.path in ('/api/session', '/api/session/logout'):
            if not self.same_origin():
                return
            if not self.headers.get('Origin'):
                self.reply(403, {'error': 'A same-origin browser request is required.'})
                return
            sessions = getattr(self.server, 'sessions', None)
            if sessions is None:
                self.reply(503, {'error': 'Browser sessions are unavailable.'})
                return
            if self.path.endswith('/logout'):
                sessions.revoke(self.session_key())
                self.reply(200, {'authenticated': False}, self.session_cookie('', 0))
                return
            expected = 'Bearer ' + self.server.token
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode()):
                self.reply(401, {'error': 'Invalid dashboard token.'})
                return
            sessions.revoke(self.session_key())
            key = sessions.create(self.server.token)
            self.reply(200, {'authenticated': True}, self.session_cookie(key, sessions.lifetime))
            return
        if self.path == '/log-data':
            if not self.same_origin():
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096:
                    raise ValueError('Invalid request size.')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict) or not isinstance(body.get('ticket'), str):
                    raise ValueError('Invalid log link.')
                item, content, next_offset = self.server.run_logs.page(body['ticket'], body.get('offset', 0))
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
                self.send_header('X-Log-Size-Bytes', str(item['size_bytes']))
                self.send_header('X-Log-Id', item['id'])
                self.send_header('X-Log-Mode', item['delivery_mode'])
                if next_offset is not None:
                    self.send_header('X-Log-Next-Offset', str(next_offset))
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except (ValueError, OSError) as exc:
                self.reply(400, {'error': str(exc)[:500]})
            return
        if not self.authenticated():
            return
        actions = {f'/api/workspaces/{name}': name for name in ('create', 'focus', 'rename')}
        organization_actions = {f'/api/organizations/{name}': name for name in ('save', 'hire', 'launch', 'delegate', 'release', 'report', 'group', 'discuss', 'chat', 'inspect', 'input', 'recover', 'transcript', 'remove_agent', 'remove_group')}
        setup_actions = {f'/api/cli-setup/{name}': name for name in ('start', 'poll', 'input', 'resize', 'close')}
        log_actions = {f'/api/logs/{name}': name for name in ('save', 'preview', 'ticket', 'delete')}
        if self.path not in actions and self.path not in organization_actions and self.path not in setup_actions and self.path not in log_actions and self.path not in ('/api/organizations/import', '/api/ssh-access/add', '/api/dashboard-access', '/api/herdr-server/start', '/api/updates/install', '/api/updates/check', '/api/models', '/api/projects/clone', '/api/projects/browse', '/api/projects/git', '/api/integration/configure', '/api/integration/retry', '/api/organizations/history', '/api/organizations/activity'):
            self.reply(404, {'error': 'Unknown endpoint.'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= (2_000_000 if self.path == '/api/organizations/import' else 65536):
                raise ValueError('Invalid request size.')
            body = json.loads(self.rfile.read(size))
            if self.path == '/api/organizations/import':
                self.reply(200, import_configuration(self.server.organizations, body))
            elif self.path == '/api/organizations/activity':
                self.reply(200, self.server.organizations.activity(body))
            elif self.path == '/api/organizations/history':
                self.reply(200, self.server.organizations.history(body))
            elif self.path == '/api/integration/configure':
                self.reply(200, self.server.coordinator.configure(body))
            elif self.path == '/api/integration/retry':
                self.reply(200, self.server.coordinator.retry(body.get('id')))
            elif self.path == '/api/projects/git':
                self.reply(200, project_git.inspect(PROJECTS, body))
            elif self.path == '/api/projects/browse':
                self.reply(200, project_explorer.browse(PROJECTS, body))
            elif self.path == '/api/projects/clone':
                self.reply(200, self.server.projects.start(body))
            elif self.path == '/api/models':
                self.reply(200, model_catalog.discover_models(self.server.cli_setup, PROJECTS, body))
            elif self.path == '/api/updates/check':
                self.reply(200, self.server.updates.snapshot(force=True))
            elif self.path == '/api/updates/install':
                self.reply(200, self.server.updates.install(body))
            elif self.path == '/api/herdr-server/start':
                self.reply(200, self.server.herdr_server.start(body))
            elif self.path == '/api/dashboard-access':
                self.reply(200, self.server.dashboard_access.update(body))
            elif self.path == '/api/ssh-access/add':
                self.reply(200, self.server.ssh_access.add(body))
            elif self.path in log_actions:
                self.reply(200, self.server.run_logs.action(log_actions[self.path], body))
            elif self.path in setup_actions:
                self.reply(200, self.server.cli_setup.action(setup_actions[self.path], body))
            elif self.path in organization_actions:
                self.reply(200, self.server.organizations.action(organization_actions[self.path], body))
            else:
                self.reply(200, workspace_action(actions[self.path], body))
        except (ValueError, UnicodeError) as exc:
            self.reply(400, {'error': str(exc)[:500]})
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.reply(502, {'error': str(exc)[:500]})
        except sqlite3.Error:
            self.reply(503, {'error': 'Organization storage is unavailable. Check the gateway service logs and available disk space.'})


def serve_gateway(bind, port, policy, token, services, cookie_secure=False):
    sessions = BrowserSessions()
    while True:
        active_bind = configured_bind(policy, bind)
        server = ThreadingHTTPServer((active_bind, port), Handler)
        port = server.server_port
        server.token = token
        server.sessions = sessions
        server.cookie_secure = cookie_secure
        server.updates = Updates()
        for name, service in services.items():
            setattr(server, name, service)
        def schedule(listener=server):
            timer = threading.Timer(5, listener.shutdown)
            timer.daemon = True
            timer.start()
        server.dashboard_access = DashboardAccess(policy, services['ssh_access'], active_bind, schedule)
        try:
            server.serve_forever()
        finally:
            server.server_close()
        if server.dashboard_access.pending is None:
            break


def main():
    token = TOKEN_FILE.read_text().strip()
    if len(token) < 32:
        raise SystemExit('Dashboard token must contain at least 32 characters.')
    secure_setting = os.environ.get('HERDR_WEB_COOKIE_SECURE', '0')
    if secure_setting not in ('0', '1'):
        raise SystemExit('HERDR_WEB_COOKIE_SECURE must be 0 or 1.')
    bind = os.environ.get('HERDR_WEB_BIND', '127.0.0.1')
    if bind not in ('127.0.0.1', '0.0.0.0'):
        raise SystemExit('Invalid HERDR_WEB_BIND.')
    policy = TOKEN_FILE.parent / 'dashboard-access.json'
    cli_setup = CliSetup()
    organizations = OrganizationStore(DATABASE, PROJECTS, command, runtime_status=cli_setup.status, model_validator=lambda profile: model_catalog.validate_selection(cli_setup, PROJECTS, profile))
    run_logs = RunLogs(DATABASE.parent / 'run-logs', BIN)
    ssh_access = SshAccess()
    projects = ProjectJobs(PROJECTS, DATABASE.with_name('projects.sqlite3'))
    coordinator = IntegrationCoordinator(DATABASE.with_name('integration.sqlite3'), organizations)
    integration = IntegrationWatcher(PROJECTS, organizations.active_checkouts, coordinator=coordinator)
    try:
        serve_gateway(bind, 8787, policy, token, {'projects': projects, 'organizations': organizations, 'cli_setup': cli_setup, 'run_logs': run_logs, 'ssh_access': ssh_access, 'herdr_server': HerdrServer(command), 'integration': integration, 'coordinator': coordinator}, cookie_secure=secure_setting == '1')
    finally:
        coordinator.close()
        integration.close()
        projects.close()
        organizations.close()
        cli_setup.close()


if __name__ == '__main__':
    main()
