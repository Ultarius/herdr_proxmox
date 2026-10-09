"""Credential-free smoke checks against installed binaries and the real gateway."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

HOME = Path('/home/herdr')
STATE = HOME / '.config/herdr-web/ci-smoke.json'
BASE = 'http://127.0.0.1:8787'


def cli(name, *args):
    result = subprocess.run([str(HOME / '.local/bin' / name), *args], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f'{name} {args[0]} failed with exit code {result.returncode}')
    return result.stdout


def api(path, body=None, token=True, origin=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + (HOME / '.config/herdr-web/token').read_text().strip()
    if origin:
        headers['Origin'] = origin
    request = Request(BASE + '/api/' + path, headers=headers,
                      data=None if body is None else json.dumps(body).encode())
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def rejected(status, *args, **kwargs):
    try:
        api(*args, **kwargs)
    except HTTPError as error:
        if error.code == status:
            return
        raise
    raise AssertionError(f'Expected HTTP {status}')


def wait_for(callback):
    deadline = time.monotonic() + 60
    while True:
        try:
            return callback()
        except (OSError, ValueError, RuntimeError, URLError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def envelope(value):
    data = json.loads(value)
    if 'error' in data:
        raise RuntimeError('Herdr returned an error')
    return data.get('result', data)


def before():
    if os.getuid() == 0 or Path.home() != HOME:
        raise AssertionError('Run the runtime smoke test as herdr with its own HOME')
    # systemctl is-active only proves the process started. Asserting a 401
    # before the gateway binds its socket fails on a slow first start, so wait
    # for the listener and treat refusal as "not ready yet" rather than a
    # rejected request.
    deadline = time.monotonic() + 60
    while True:
        try:
            rejected(401, 'herdr-server/start', {}, token=False)
            break
        except URLError as error:
            if isinstance(error.reason, ConnectionRefusedError) and time.monotonic() < deadline:
                time.sleep(1)
                continue
            raise
    wait_for(lambda: api('herdr-server'))
    rejected(403, 'herdr-server/start', {}, origin='https://invalid.example')
    api('herdr-server/start', {})
    def running_server():
        if api('herdr-server')['state'] != 'running':
            raise RuntimeError('Dashboard-started Herdr is not ready yet')
        return True
    wait_for(running_server)
    if api('herdr-server/start', {}).get('already_running') is not True:
        raise AssertionError('Repeat start did not reuse the running server')
    for name in ('herdr', 'codex', 'claude', 'opencode', 'agy'):
        binary = HOME / '.local/bin' / name
        if binary.stat().st_uid != os.getuid():
            raise AssertionError(f'{name} binary must remain herdr-owned')
        version = cli(name, '--version').strip()
        if not version:
            raise AssertionError(f'{name} returned an empty version')
        print(f'{name}: {version}', flush=True)
    prefix = subprocess.check_output(['npm', 'config', 'get', 'prefix'], text=True, timeout=10).strip()
    if prefix != str(HOME / '.local'):
        raise AssertionError('Codex npm prefix is not user-local')
    schema = envelope(cli('herdr', 'api', 'schema', '--json'))
    fields = schema.get('schemas', {}).get('success_response', {}).get('$defs', {}).get('AgentInfo', {}).get('properties', {})
    if not {'agent_status', 'pane_id', 'agent', 'name'}.issubset(fields):
        raise AssertionError('Installed Herdr schema is incompatible with organization launch')
    wait_for(lambda: envelope(cli('herdr', 'workspace', 'list')))
    project = HOME / 'projects/ci-smoke'
    project.mkdir(exist_ok=True)
    created = envelope(cli('herdr', 'workspace', 'create', '--cwd', str(project), '--label', 'CI smoke', '--no-focus'))
    workspace = created['workspace']['workspace_id']
    snapshot = wait_for(lambda: api('snapshot'))
    public_key = HOME / '.config/herdr-web/ci-key.pub'
    if public_key.exists():
        body = {'public_key': public_key.read_text().strip()}
        rejected(401, 'ssh-access/add', body, token=False)
        rejected(403, 'ssh-access/add', body, origin='https://invalid.example')
        added = api('ssh-access/add', body)
        if not added['added'] or added['key_count'] < 1:
            raise AssertionError('Dashboard did not add the deferred SSH public key')
        if api('ssh-access/add', body)['added']:
            raise AssertionError('Dashboard duplicated the SSH public key')
    if not any(w['workspace_id'] == workspace for w in snapshot['workspaces']):
        raise AssertionError('Created workspace is missing from the real dashboard snapshot')
    if not isinstance(snapshot['agents'], list):
        raise AssertionError('Agent listing is not available')
    pane = created['root_pane']['pane_id']
    saved = api('logs/save', {'pane': pane, 'label': 'CI terminal snapshot', 'source': 'visible'})
    preview = api('logs/preview', {'id': saved['id']})
    if preview['size_mb'] != preview['size_bytes'] / 1_000_000 or len(preview['text'].encode()) > 8192 + 4:
        raise AssertionError('Log size or preview limit is incorrect')
    if not any(item['id'] == saved['id'] for item in api('logs')['logs']):
        raise AssertionError('Saved terminal snapshot is missing')
    api('logs/delete', {'id': saved['id']})
    for path, minimum in (('/', 100), ('/main.dart.js', 1000), ('/dashboard/', 100), ('/dashboard/main.dart.js', 10000)):
        with urlopen(BASE + path, timeout=15) as response:
            if response.status != 200 or len(response.read()) < minimum:
                raise AssertionError(f'Compiled web asset did not load: {path}')
    rejected(401, 'organizations', token=False)
    setup = api('cli-setup')
    if {item['id'] for item in setup['clis']} != {'codex', 'claude', 'opencode', 'agy'} or not all(item['installed'] for item in setup['clis']):
        raise AssertionError('CLI configuration page does not detect all installed runtimes')
    rejected(401, 'cli-setup/start', {'cli': 'codex'}, token=False)
    rejected(403, 'cli-setup/start', {'cli': 'codex'}, origin='https://not-this-dashboard.example')
    rejected(403, 'organizations', origin='https://not-this-dashboard.example')
    payload = dict(request_id=uuid.uuid4().hex, name='CI Engineering', purpose='Verify persistent organization controls', instructions='Report test evidence.')
    organization = api('organizations/save', payload)['id']
    if api('organizations/save', payload)['id'] != organization:
        raise AssertionError('Organization request deduplication failed')
    profiles = []
    for name, persona in (('CI lead', 'Plan and review evidence.'), ('CI worker', 'Implement and verify changes.')):
        hire = dict(request_id=uuid.uuid4().hex, organization_id=organization, name=name, role='Tester',
                    persona=persona, runtime='codex', project=str(project), manager_id=profiles[0] if profiles else '')
        profile = api('organizations/hire', hire)['id']
        if api('organizations/hire', hire)['id'] != profile:
            raise AssertionError('Hire request deduplication failed')
        profiles.append(profile)
    rejected(400, 'organizations/hire', dict(request_id=uuid.uuid4().hex, organization_id=organization,
             name='Invalid project', role='Tester', persona='Test', runtime='codex', project='/etc', manager_id=''))
    STATE.write_text(json.dumps(dict(organization=organization, profiles=profiles)))
    print('Real Herdr workspace, compiled assets, authenticated gateway and organization controls verified.', flush=True)


def after():
    expected = json.loads(STATE.read_text())
    snapshot = wait_for(lambda: api('organizations'))
    matches = [o for o in snapshot['organizations'] if o['id'] == expected['organization']]
    if len(matches) != 1:
        raise AssertionError('Organization did not survive gateway restart, or was duplicated')
    profiles = [p for p in snapshot['profiles'] if p['organization_id'] == expected['organization']]
    if sorted(p['id'] for p in profiles) != sorted(expected['profiles']):
        raise AssertionError('Hires did not survive gateway restart, or were duplicated')
    if len({p['persona'] for p in profiles}) != 2:
        raise AssertionError('Distinct personas were not retained')
    print('Organization and two distinct hires persisted across gateway restart.', flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['before']:
        before()
    elif sys.argv[1:] == ['after']:
        after()
    else:
        raise SystemExit('Usage: runtime.py before|after')
