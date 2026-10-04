"""Local, authenticated dashboard gateway to the official Herdr CLI."""
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from organizations import OrganizationStore
from cli_setup import CliSetup
from run_logs import RunLogs
from ssh_access import SshAccess
from dashboard_access import DashboardAccess, configured_bind

ROOT = Path(os.environ.get('HERDR_WEB_ROOT', '/opt/herdr-web/public')).resolve()
PROJECTS = Path(os.environ.get('HERDR_PROJECTS', '/home/herdr/projects')).resolve()
BIN = os.environ.get('HERDR_BIN', '/home/herdr/.local/bin/herdr')
TOKEN_FILE = Path(os.environ.get('HERDR_WEB_TOKEN_FILE', '/home/herdr/.config/herdr-web/token'))
DATABASE = Path(os.environ.get('HERDR_ORGANIZATION_DB', '/home/herdr/.config/herdr-web/organizations.sqlite3'))


def command(*args, timeout=10):
    result = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError(result.stderr.strip()[:500] or 'Herdr command failed; start Herdr over SSH first.')
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


def workspace_action(name, body):
    if not isinstance(body, dict):
        raise ValueError('Expected a JSON object.')
    if name == 'create':
        label = body.get('label', '')
        path_value = body.get('cwd', '')
        if not isinstance(path_value, str) or not path_value:
            raise ValueError('A project directory is required.')
        path = Path(path_value).expanduser().resolve()
        if not path.is_relative_to(PROJECTS) or not path.is_dir():
            raise ValueError('Select an existing directory inside /home/herdr/projects.')
        validate_label(label)
        return command('workspace', 'create', '--cwd', str(path), '--label', label, '--no-focus')
    workspace_id = body.get('id', '')
    if not isinstance(workspace_id, str) or not re.fullmatch(r'w[0-9]+', workspace_id):
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


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, data):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authenticated(self):
        supplied = self.headers.get('Authorization', '')
        expected = 'Bearer ' + self.server.token
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            self.reply(401, {'error': 'Invalid dashboard token. Disconnect and enter the correct token.'})
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
            if self.path not in ('/api/snapshot', '/api/organizations', '/api/cli-setup', '/api/logs', '/api/ssh-access', '/api/dashboard-access'):
                self.reply(404, {'error': 'Unknown endpoint.'})
                return
            try:
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
                if self.path == '/api/organizations':
                    self.reply(200, self.server.organizations.snapshot())
                    return
                self.reply(200, {
                    'workspaces': listing(command('workspace', 'list'), 'workspaces'),
                    'agents': listing(command('agent', 'list'), 'agents'),
                })
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
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
        organization_actions = {f'/api/organizations/{name}': name for name in ('save', 'hire', 'launch', 'delegate', 'release', 'report')}
        setup_actions = {f'/api/cli-setup/{name}': name for name in ('start', 'poll', 'input', 'resize', 'close')}
        log_actions = {f'/api/logs/{name}': name for name in ('save', 'preview', 'ticket', 'delete')}
        if self.path not in actions and self.path not in organization_actions and self.path not in setup_actions and self.path not in log_actions and self.path not in ('/api/ssh-access/add', '/api/dashboard-access'):
            self.reply(404, {'error': 'Unknown endpoint.'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                raise ValueError('Invalid request size.')
            body = json.loads(self.rfile.read(size))
            if self.path == '/api/dashboard-access':
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


def serve_gateway(bind, port, policy, token, services):
    while True:
        active_bind = configured_bind(policy, bind)
        server = ThreadingHTTPServer((active_bind, port), Handler)
        port = server.server_port
        server.token = token
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
    bind = os.environ.get('HERDR_WEB_BIND', '127.0.0.1')
    if bind not in ('127.0.0.1', '0.0.0.0'):
        raise SystemExit('Invalid HERDR_WEB_BIND.')
    policy = TOKEN_FILE.parent / 'dashboard-access.json'
    organizations = OrganizationStore(DATABASE, PROJECTS, command)
    cli_setup = CliSetup()
    run_logs = RunLogs(DATABASE.parent / 'run-logs', BIN)
    ssh_access = SshAccess()
    try:
        serve_gateway(bind, 8787, policy, token, {'organizations': organizations, 'cli_setup': cli_setup, 'run_logs': run_logs, 'ssh_access': ssh_access})
    finally:
        organizations.close()
        cli_setup.close()


if __name__ == '__main__':
    main()
