"""Loopback-only dashboard preview with real storage/jobs and simulated agents.

Run after building web/dashboard. No container, credentials, or real agents used.
"""
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import server
from organizations import OrganizationStore


class MockHerdr:
    def __init__(self):
        self.agents = {}
        self.workspaces = []
        self.outputs = {}

    def command(self, *args, **kwargs):
        if args == ('api', 'schema', '--json'):
            return {'schemas': {'success_response': {'$defs': {'AgentInfo': {'properties': {k: {} for k in ('name', 'pane_id', 'agent', 'agent_status')}}}}}}
        if args[:2] == ('workspace', 'create'):
            number = len(self.workspaces) + 1
            self.workspaces.append({'workspace_id': f'w{number}', 'label': args[args.index('--label') + 1]})
            return {'root_pane': {'pane_id': f'w{number}:p1'}}
        if args[:2] == ('workspace', 'list'):
            return {'workspaces': self.workspaces}
        if args[:2] == ('agent', 'list'):
            return {'agents': list(self.agents.values())}
        if args[:2] == ('pane', 'process-info'):
            return {'process_info': {'shell_pid': 42, 'foreground_process_group_id': 42}}
        if args[:2] == ('agent', 'start'):
            self.agents[args[2]] = dict(name=args[2], pane_id=args[6], agent=args[4], agent_status='idle', agent_session={'value': uuid.uuid4().hex})
            return {'agent': self.agents[args[2]]}
        if args[:2] == ('agent', 'get'):
            return {'agent': self.agents[args[2]]}
        if args[:2] == ('agent', 'read'):
            return {'output': self.outputs.get(args[2], 'Mock agent ready.')}
        if args[:2] == ('agent', 'send-keys'):
            self.agents[args[2]]['agent_status'] = 'idle'
            self.outputs[args[2]] = f'Mock terminal received {args[3]}.'
            return {}
        if args[:2] == ('agent', 'prompt'):
            alias, prompt = args[2:4]
            self.agents[alias]['agent_status'] = 'working'
            time.sleep(0.5)
            path = re.search(r'exactly (?:this new file: )?(.+?\.md)', prompt)
            if path:
                final = 'Synthesize the discussion' in prompt or 'You are the group conversation' in prompt
                content = ('# Action brief (mock)\n\n1. Compare deployment options.\n2. Prototype the recommended option.\n3. Review evidence before rollout.\n\n## Risks\nCapacity and recovery have not been verified.\n\n## Acceptance criteria\nA successful backup and restore rehearsal.\n' if final else '# Proposal (mock)\n\nUse a staged deployment with a tested rollback. Review the other proposals and verify capacity before choosing.\n')
                Path(path[1]).write_text(content, encoding='utf-8')
                self.outputs[alias] = content
                transcript = re.search(r'Also write (.+?discussion\.json)', prompt)
                if transcript:
                    ids = json.loads(re.search(r'Profile IDs: (.+?)\. Record', prompt)[1])
                    parts = []
                    for round_number in (1, 2):
                        for member_alias, profile_id in ids.items():
                            self.agents[member_alias]['agent_status'] = 'working'
                            self.outputs[alias] = f'Mock facilitator: asking a member for round {round_number}.'
                            self.outputs[member_alias] = 'Mock member: comparing deployment options…'
                            time.sleep(1.5)
                            reply = 'Mock member response: use a staged deployment and rehearse recovery.'
                            parts.append(dict(profile_id=profile_id, name='mock', round=round_number, content=reply))
                            Path(transcript[1]).write_text(json.dumps({'contributions': parts}), encoding='utf-8')
                            self.outputs[member_alias] = reply
                            self.agents[member_alias]['agent_status'] = 'idle'
                    self.outputs[alias] = content
            elif 'mock:block' in prompt:
                self.agents[alias]['agent_status'] = 'blocked'
                self.outputs[alias] = 'Mock permission request: Allow this action? Use Enter to accept or Escape to dismiss.'
                return {}
            else:
                self.outputs[alias] = f'You: {prompt[-300:]}\n\nMax (mock): I can help review the design. Start by defining the goal, constraints, and acceptance criteria.'
            self.agents[alias]['agent_status'] = 'idle'
            return {}
        raise ValueError('This operation is not supported by the local mock preview.')


class PreviewHandler(server.Handler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(302)
            self.send_header('Location', '/dashboard/')
            self.end_headers()
            return
        if self.path == '/api/cli-setup':
            if self.authenticated():
                self.reply(200, {'clis': [dict(id=i, name=n, installed=True,
                    status='not_configured', detail='Connect an account using the setup terminal (local demo).')
                    for i, n in [('codex', 'Codex'), ('claude', 'Claude Code'), ('opencode', 'OpenCode'), ('agy', 'Antigravity')]]})
            return
        if self.path.startswith('/api/') and self.path not in ('/api/snapshot', '/api/organizations', '/api/models'):
            if self.authenticated():
                self.reply(503, {'error': 'This local preview simulates agent chat and groups only. Container setup and updates are disabled.'})
            return
        if self.path.startswith('/dashboard/'):
            self.path = self.path[len('/dashboard'):]
        super().do_GET()

    def do_POST(self):
        if self.path == '/api/models':
            if self.authenticated():
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.reply(200, {'supports_variants': True, 'models': [
                    {'provider': 'opencode', 'id': 'big-pickle', 'name': 'Big Pickle', 'reasoning': []},
                    {'provider': 'openai', 'id': 'gpt-5', 'name': 'GPT-5 (mock)', 'reasoning': ['low', 'medium', 'high']},
                    {'provider': 'openai', 'id': 'fast', 'name': 'Fast (mock)', 'reasoning': []}]})
            return
        if self.path.startswith('/api/cli-setup/'):
            if not self.authenticated():
                return
            size = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(size))
            operation = self.path.rsplit('/', 1)[-1]
            if operation == 'start':
                self.reply(200, {'id': 'demo-setup'})
            elif operation == 'poll':
                output = '' if body.get('cursor') else '\x1b[36mConnect your provider (mock)\x1b[0m\r\n\r\n  > OpenCode Zen\r\n    OpenAI\r\n    Anthropic\r\n    Google\r\n\r\nUse arrow keys and Enter to select. Demo only; do not enter real credentials.\r\n'
                self.reply(200, {'output': output, 'cursor': 1, 'running': True})
            else:
                self.reply(200, {})
            return
        if self.path.startswith('/api/') and not self.path.startswith('/api/organizations/'):
            if self.authenticated():
                self.reply(503, {'error': 'Container configuration changes are disabled in the local mock preview.'})
            return
        super().do_POST()


def main():
    root = Path(__file__).resolve().parents[1]
    server.ROOT = root / 'web/dashboard/build/web'
    if not (server.ROOT / 'index.html').exists():
        raise SystemExit('Build the Flutter dashboard first.')
    with tempfile.TemporaryDirectory(prefix='herdr-mock-') as directory:
        home = Path(directory)
        projects = home / 'projects'
        projects.mkdir()
        mock = MockHerdr()
        server.command = mock.command
        server.PROJECTS = projects
        store = OrganizationStore(home / 'organizations.sqlite3', projects, mock.command)
        def action(operation, **values):
            return store.action(operation, dict(request_id=uuid.uuid4().hex, **values))['id']
        org = action('save', name='Local preview', purpose='Exercise chat and group discussion', instructions='Use simulated data only.')
        for name, role in [('Max', 'Developer'), ('Iris', 'Reviewer')]:
            profile = action('hire', organization_id=org, name=name, role=role, persona='Discuss options and provide evidence.', runtime='opencode', manager_id='', project=str(projects))
            action('launch', organization_id=org, profile_id=profile)
        store.worker.submit(lambda: None).result()
        members = [p['id'] for p in store.snapshot()['profiles']]
        group = action('group', organization_id=org, name='Deployment review', description='Compare deployment options and recommend next actions.', members=members)
        store.worker.submit(lambda: None).result()
        action('discuss', organization_id=org, group_id=group, prompt='Review the deployment approach.')
        store.worker.submit(lambda: None).result()
        http = ThreadingHTTPServer(('127.0.0.1', 8789), PreviewHandler)
        http.token = 'local-preview-token'
        http.organizations = store
        print('Mock dashboard: http://127.0.0.1:8789/\nLogin token: local-preview-token\nType mock:block to simulate a permission request.', flush=True)
        try:
            http.serve_forever()
        finally:
            http.server_close()
            store.close()


if __name__ == '__main__':
    main()

