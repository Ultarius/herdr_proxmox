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
from urllib.parse import parse_qs, urlsplit

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
from operators import Operators
from sdk_install import SdkInstall
from updates import Updates
from validation import ValidationRuns
from github_api import GitHub, GitHubError
from contributions import Contributions

ROOT = Path(os.environ.get('HERDR_WEB_ROOT', '/opt/herdr-web/public')).resolve()
PROJECTS = Path(os.environ.get('HERDR_PROJECTS', '/home/herdr/projects')).resolve()
BIN = os.environ.get('HERDR_BIN', '/home/herdr/.local/bin/herdr')
TOKEN_FILE = Path(os.environ.get('HERDR_WEB_TOKEN_FILE', '/home/herdr/.config/herdr-web/token'))
DATABASE = Path(os.environ.get('HERDR_ORGANIZATION_DB', '/home/herdr/.config/herdr-web/organizations.sqlite3'))
BUILD_SERVICE_CONFIG = Path('/etc/herdr/build-service.json')


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

    def operator(self, key, resolver):
        """Resolve the session's identity at request time, so revocation takes effect."""
        with self.lock:
            entry = self.entries.get(key)
            token = entry[1] if entry and entry[0] > time.time() else None
        return resolver(token) if token else None

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

    def validation_log(self, run_id):
        try:
            run, content = self.server.validation.log(run_id)
        except ValueError as exc:
            self.reply(400, {'error': str(exc)[:500]})
            return
        payload = content.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="validation-{run["id"]}.log"')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
        self.send_header('X-Validation-State', str(run.get('state')))
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def validation_artifact(self, run_id, kind='static'):
        try:
            archive, manifest = self.server.validation.artifact(run_id, kind)
            stream = archive.open('rb')
        except (ValueError, OSError) as exc:
            self.reply(400, {'error': str(exc)[:500]})
            return
        with stream:
            self.send_response(200)
            self.send_header('Content-Type', 'application/gzip')
            self.send_header('Content-Disposition', f'attachment; filename="{kind}-{run_id}.tar.gz"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(manifest['bytes']))
            self.end_headers()
            while chunk := stream.read(65536):
                self.wfile.write(chunk)

    def organization_snapshot(self, directory=False, state=False):
        data = (self.server.organizations.state_snapshot() if state
                else self.server.organizations.snapshot(directory=directory))
        watcher = getattr(self.server, 'integration', None)
        # Minimal gateways used for setup can serve organization storage before
        # the background integration watcher is attached.
        return watcher.annotate_profiles(data) if watcher else data

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

    def resolve_operator(self, token):
        """Map a bearer token to a named operator, or to the shared administrator."""
        if not isinstance(token, str) or not token:
            return None
        operators = getattr(self.server, 'operators', None)
        identity = operators.identify(token) if operators is not None else None
        if identity is not None:
            return identity
        if hmac.compare_digest(token.encode(), self.server.token.encode()):
            return {'name': 'dashboard', 'role': 'admin', 'master': True}
        return None

    def authenticated(self):
        supplied = self.headers.get('Authorization', '')
        token = supplied[len('Bearer '):] if supplied.startswith('Bearer ') else ''
        identity = self.resolve_operator(token)
        bearer = identity is not None
        if not bearer:
            sessions = getattr(self.server, 'sessions', None)
            identity = sessions.operator(self.session_key(), self.resolve_operator) if sessions else None
        if identity is None:
            self.reply(401, {'error': 'Invalid dashboard token. Disconnect and enter the correct token.'})
            return False
        self.operator = identity
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
            identity = sessions.operator(self.session_key(), self.resolve_operator) if sessions else None
            self.reply(200, {'authenticated': identity is not None,
                             'operator': identity['name'] if identity else None,
                             'role': identity['role'] if identity else None})
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
            if self.path.startswith('/api/validation/log?'):
                query = parse_qs(urlsplit(self.path).query)
                self.validation_log((query.get('id') or [''])[0])
                return
            if self.path.startswith('/api/tasks/patch?'):
                query = parse_qs(urlsplit(self.path).query)
                try:
                    content, filename = self.server.contributions.patch((query.get('id') or [''])[0])
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
                    self.send_header('Content-Length', str(len(content)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    self.wfile.write(content)
                except (ValueError, OSError) as error:
                    self.reply(400, {'error': str(error)[:500]})
                return
            if self.path.startswith('/api/tasks/detail?'):
                query = parse_qs(urlsplit(self.path).query)
                try:
                    self.reply(200, self.server.contributions.detail((query.get('id') or [''])[0]))
                except sqlite3.Error:
                    self.reply(503, {'error': 'Task storage is unavailable. Retry after checking the gateway service.'})
                except (ValueError, OSError) as error:
                    self.reply(400, {'error': str(error)[:500]})
                return
            if self.path.startswith('/api/validation/artifact?'):
                query = parse_qs(urlsplit(self.path).query)
                self.validation_artifact((query.get('id') or [''])[0], (query.get('kind') or ['static'])[0])
                return
            if self.path == '/api/build':
                from build_identity import snapshot as build_snapshot
                self.reply(200, build_snapshot())
                return
            if self.path == '/api/resources':
                self.reply(200, resources.snapshot())
                return
            if self.path == '/api/organizations/export':
                self.reply(200, export_configuration(self.server.organizations))
                return
            if self.path not in ('/api/snapshot', '/api/organizations', '/api/organizations/directory', '/api/organizations/state', '/api/projects/jobs', '/api/cli-setup', '/api/logs', '/api/ssh-access', '/api/dashboard-access', '/api/herdr-server', '/api/updates', '/api/models', '/api/integration', '/api/sdk', '/api/validation', '/api/tasks', '/api/github'):
                self.reply(404, {'error': 'Unknown endpoint.'})
                return
            try:
                if self.path == '/api/tasks':
                    # Task read access intentionally matches organization and
                    # repository read access. It grants no publication rights.
                    self.reply(200, self.server.contributions.snapshot(role=self.operator['role']))
                    return
                if self.path == '/api/github':
                    if self.operator['role'] != 'admin':
                        self.reply(403, {'error': 'Only an administrator can view GitHub publishing configuration.'})
                        return
                    self.reply(200, self.server.github.snapshot())
                    return
                if self.path == '/api/projects/jobs':
                    self.reply(200, self.server.projects.snapshot())
                    return
                if self.path == '/api/models':
                    self.reply(200, model_catalog.snapshot())
                    return
                if self.path == '/api/sdk':
                    self.reply(200, self.server.sdk_install.snapshot())
                    return
                if self.path == '/api/validation':
                    self.reply(200, self.server.validation.snapshot())
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
                    self.reply(200, self.organization_snapshot(state=True))
                    return
                if self.path == '/api/organizations/directory':
                    self.reply(200, self.organization_snapshot(directory=True))
                    return
                if self.path == '/api/organizations':
                    self.reply(200, self.organization_snapshot())
                    return
                agents = listing(command('agent', 'list'), 'agents')
                if getattr(self.server, 'organizations', None) is not None:
                    agents = self.server.organizations.label_agents(agents)
                self.reply(200, {
                    'herdr_server': 'running',
                    'workspaces': listing(command('workspace', 'list'), 'workspaces'),
                    'agents': agents,
                })
            except sqlite3.Error:
                self.reply(503, {'error': 'Dashboard storage is unavailable. Retry after checking the gateway service.'})
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                if self.path == '/api/snapshot' and isinstance(exc, ValueError) and 'server_not_running' in str(exc):
                    self.reply(200, {'herdr_server': 'stopped', 'workspaces': [], 'agents': []})
                    return
                self.reply(502, {'error': str(exc)[:500]})
            except sqlite3.Error:
                self.reply(503, {'error': 'Organization storage is unavailable. Check the gateway service logs and available disk space.'})
            return
        from urllib.parse import unquote
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
            supplied = self.headers.get('Authorization', '')
            token = supplied[len('Bearer '):] if supplied.startswith('Bearer ') else ''
            identity = self.resolve_operator(token)
            if identity is None:
                self.reply(401, {'error': 'Invalid dashboard token.'})
                return
            sessions.revoke(self.session_key())
            key = sessions.create(token)
            self.reply(200, {'authenticated': True, 'operator': identity['name'], 'role': identity['role']},
                       self.session_cookie(key, sessions.lifetime))
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
        actor = getattr(self, 'operator', {}).get('name', 'dashboard_operator')
        role = getattr(self, 'operator', {}).get('role', 'operator')
        actions = {f'/api/workspaces/{name}': name for name in ('create', 'focus', 'rename')}
        organization_actions = {f'/api/organizations/{name}': name for name in ('save', 'hire', 'launch', 'delegate', 'release', 'report', 'group', 'discuss', 'chat', 'inspect', 'input', 'recover', 'transcript', 'remove_agent', 'remove_group')}
        setup_actions = {f'/api/cli-setup/{name}': name for name in ('start', 'poll', 'input', 'resize', 'close', 'verify')}
        log_actions = {f'/api/logs/{name}': name for name in ('save', 'preview', 'ticket', 'delete')}
        if self.path not in actions and self.path not in organization_actions and self.path not in setup_actions and self.path not in log_actions and self.path not in ('/api/organizations/import', '/api/ssh-access/add', '/api/dashboard-access', '/api/herdr-server/start', '/api/updates/install', '/api/updates/check', '/api/updates/promote', '/api/updates/rollback', '/api/updates/provenance', '/api/models', '/api/sdk/install', '/api/validation/run', '/api/projects/clone', '/api/projects/browse', '/api/projects/git', '/api/integration/configure', '/api/integration/retry', '/api/integration/blockers', '/api/integration/repair', '/api/integration/guidance', '/api/integration/recover', '/api/organizations/history', '/api/organizations/activity', '/api/github/configure', '/api/tasks/create', '/api/tasks/launch', '/api/tasks/candidate', '/api/tasks/publish', '/api/tasks/pull', '/api/tasks/refresh', '/api/tasks/build'):
            self.reply(404, {'error': 'Unknown endpoint.'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= (2_000_000 if self.path == '/api/organizations/import' else 65536):
                raise ValueError('Invalid request size.')
            body = json.loads(self.rfile.read(size))
            if self.path == '/api/github/configure':
                if role != 'admin':
                    raise ValueError('Only an administrator can configure GitHub publishing.')
                self.reply(200, self.server.github.configure(body))
            elif self.path.startswith('/api/tasks/'):
                self.reply(200, self.server.contributions.action(self.path.rsplit('/', 1)[1], body, actor, role))
            elif self.path == '/api/organizations/import':
                self.reply(200, import_configuration(self.server.organizations, body))
            elif self.path == '/api/organizations/activity':
                self.reply(200, self.server.organizations.activity(body))
            elif self.path == '/api/organizations/history':
                self.reply(200, self.server.organizations.history(body))
            elif self.path == '/api/sdk/install':
                if role != 'admin':
                    raise ValueError('Only an administrator can install the development SDK.')
                self.reply(200, self.server.sdk_install.install(body))
            elif self.path == '/api/validation/run':
                self.reply(200, self.server.validation.submit(body, actor=actor))
            elif self.path == '/api/integration/configure':
                if 'auto_sdk' in body and role != 'admin':
                    raise ValueError('Only an administrator can change automatic SDK installation.')
                self.reply(200, self.server.coordinator.configure(body, actor=actor, role=role))
                self.server.integration.wake()
            elif self.path == '/api/integration/retry':
                self.reply(200, self.server.coordinator.retry(body.get('id'), actor=actor))
                self.server.integration.wake()
            elif self.path == '/api/integration/blockers':
                self.reply(200, self.server.coordinator.review_blockers(body, actor=actor, role=role))
                self.server.integration.wake()
            elif self.path == '/api/integration/recover':
                self.reply(200, self.server.coordinator.recover_worker(body, actor=actor))
                self.server.integration.wake()
            elif self.path == '/api/integration/guidance':
                if role != 'admin':
                    raise ValueError('Only an administrator can repair worker guidance.')
                self.reply(200, self.server.coordinator.repair_guidance(body, actor=actor))
            elif self.path == '/api/integration/repair':
                self.reply(200, self.server.coordinator.repair_report(body, actor=actor))
                self.server.integration.wake()
            elif self.path == '/api/projects/git':
                if body.get('action') == 'update_base':
                    from base_updates import update
                    self.reply(200, update(PROJECTS, body, actor, role, self.server.organizations.checkout_in_use))
                else:
                    self.reply(200, self.server.integration.annotate_worktrees(project_git.inspect(PROJECTS, body)))
                if body.get('action') in ('fetch', 'update_base'):
                    self.server.integration.wake()
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
            elif self.path == '/api/updates/promote':
                if role != 'admin':
                    raise ValueError('Only an administrator can deploy a build.')
                run_id = body.get('run_id') if isinstance(body, dict) else None
                archive, manifest = self.server.validation.artifact(run_id, 'deployment')
                self.reply(200, self.server.updates.promote(manifest, archive, actor))
            elif self.path == '/api/updates/rollback':
                if role != 'admin':
                    raise ValueError('Only an administrator can roll back a deployment.')
                self.reply(200, self.server.updates.rollback(actor))
            elif self.path == '/api/updates/provenance':
                if role != 'admin':
                    raise ValueError('Only an administrator can repair deployment provenance.')
                self.reply(200, self.server.updates.provenance(actor))
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
            elif self.path == '/api/organizations/recover' and body.get('chat') is True:
                self.reply(200, self.server.organizations.recover_chat(body, actor))
            elif self.path in organization_actions:
                self.reply(200, self.server.organizations.action(organization_actions[self.path], body))
            else:
                self.reply(200, workspace_action(actions[self.path], body))
        except GitHubError as exc:
            # HTTP 401 is reserved for dashboard authentication. An upstream
            # 401 must not revoke a valid browser session or erase task state.
            self.reply(502, {'error': str(exc)[:500], 'source': 'github', 'github_status': exc.status})
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
    operators = Operators(Path('/etc/herdr/operators.json'))
    sdk_install = SdkInstall()
    cli_setup = CliSetup()
    organizations = OrganizationStore(DATABASE, PROJECTS, command, runtime_status=cli_setup.status, model_validator=lambda profile: model_catalog.validate_selection(cli_setup, PROJECTS, profile))
    github = GitHub(DATABASE.parent / 'github.json')
    contributions = Contributions(DATABASE.with_name('tasks.sqlite3'), PROJECTS, organizations, github)
    run_logs = RunLogs(DATABASE.parent / 'run-logs', BIN)
    ssh_access = SshAccess()
    projects = ProjectJobs(PROJECTS, DATABASE.with_name('projects.sqlite3'))
    coordinator = IntegrationCoordinator(DATABASE.with_name('integration.sqlite3'), organizations,
                                         sdk_request=lambda: sdk_install.install({}))
    build_queue = None
    minimum_build_space = 0
    build_config = BUILD_SERVICE_CONFIG
    if build_config.is_file():
        from build_queue import BuildQueue
        configuration = json.loads(build_config.read_text())
        if Path(configuration['projects']).resolve() != Path(PROJECTS).resolve():
            raise ValueError('Build service projects do not match the gateway.')
        build_queue = BuildQueue(configuration['queue'])
        minimum_build_space = configuration.get('minimum_free_bytes', 2 * 1024**3)
    coordinator.build_service = build_queue is not None
    def build_event(event_id):
        return contributions.build_event(event_id) or coordinator.event(event_id)

    def record_build(event_id, summary):
        if not contributions.record_build(event_id, summary):
            coordinator.record_validation_run(event_id, summary)

    validation = ValidationRuns(PROJECTS, build_event, record=record_build, queue=build_queue, minimum_free_bytes=minimum_build_space)
    contributions.validation = validation
    integration = IntegrationWatcher(PROJECTS, organizations.active_checkouts, coordinator=coordinator,
                                     wake_event=organizations.jobs_changed)
    # Durable queue results reach their integration events even without a browser.
    integration.build_results = validation.snapshot
    integration.schedule_builds = lambda: coordinator.schedule_builds(validation)
    integration.wake()
    contributions.start()
    try:
        serve_gateway(bind, 8787, policy, token, {'github': github, 'contributions': contributions, 'projects': projects, 'organizations': organizations, 'cli_setup': cli_setup, 'run_logs': run_logs, 'ssh_access': ssh_access, 'herdr_server': HerdrServer(command), 'integration': integration, 'coordinator': coordinator, 'operators': operators, 'sdk_install': sdk_install, 'validation': validation}, cookie_secure=secure_setting == '1')
    finally:
        contributions.close()
        coordinator.close()
        integration.close()
        projects.close()
        organizations.close()
        cli_setup.close()


if __name__ == '__main__':
    main()
