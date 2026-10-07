import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uuid
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('organization_gateway', Path(__file__).parents[1] / 'web/gateway/server.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = self.root / 'projects'
        self.projects.mkdir()
        self.calls = []
        self.agents = {}
        self.store = gateway.OrganizationStore(self.root / 'data/org.sqlite3', self.projects, self.command)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def command(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if args == ('api', 'schema', '--json'):
            return {'schemas': {'success_response': {'$defs': {'AgentInfo': {'properties': {key: {} for key in ('name', 'pane_id', 'agent', 'agent_status')}}}}}}
        if args[:2] == ('pane', 'process-info'):
            self.assertEqual(len(args), 4)
            self.assertEqual(args[2], '--pane')
            return {'process_info': {'shell_pid': 42, 'foreground_process_group_id': 42}}
        if args[:2] == ('workspace', 'create'):
            return {'root_pane': {'pane_id': f'w{len(self.calls)}:p1'}}
        if args[:2] == ('agent', 'start'):
            self.agents[args[2]] = {'name': args[2], 'pane_id': args[6], 'agent': args[4], 'agent_status': 'idle', 'interactive_ready': True, 'launch_pending': False, 'agent_session': {'value': uuid.uuid4().hex}}
            return {'agent': self.agents[args[2]]}
        if args[:2] == ('agent', 'list'):
            return {'agents': list(self.agents.values())}
        if args[:2] == ('agent', 'get'):
            return {'agent': self.agents[args[2]]}
        return {}

    def test_created_pane_recovers_from_authoritative_checkout_records(self):
        def command(*args, **kwargs):
            if args == ('workspace', 'list'):
                return {'workspaces': [dict(workspace_id='w42', worktree=dict(checkout_path=str(self.projects)))]}
            if args == ('pane', 'list', '--workspace', 'w42'):
                return {'panes': [dict(pane_id='w42:p1')]}
            self.fail(f'Unexpected command: {args}')
        self.store.command = command
        self.assertEqual(self.store.created_pane({}, str(self.projects)), ('w42:p1', 'w42'))

    def test_created_pane_accepts_letter_counters_in_both_response_paths(self):
        self.assertEqual(self.store.created_pane(
            {'root_pane': {'pane_id': 'wA:pB'}, 'workspace': {'workspace_id': 'wA'}},
            str(self.projects)), ('wA:pB', 'wA'))
        self.store.command = lambda *args, **kwargs: (
            {'workspaces': [dict(workspace_id='wB', cwd=str(self.projects))]}
            if args == ('workspace', 'list') else {'panes': [dict(pane_id='wB:pA')]})
        self.assertEqual(self.store.created_pane({}, str(self.projects)), ('wB:pA', 'wB'))

    def test_direct_root_pane_normalizes_unusable_workspace_metadata(self):
        for metadata in (None, 'unexpected', {}, {'workspace_id': None},
                         {'workspace_id': '--help'}, {'workspace_id': 'wB'}):
            with self.subTest(metadata=metadata):
                self.assertEqual(self.store.created_pane(
                    {'root_pane': {'pane_id': 'wA:pB'}, 'workspace': metadata},
                    str(self.projects)), ('wA:pB', None))
        self.assertEqual(self.store.created_pane(
            {'root_pane': {'pane_id': 'wA:pB'}}, str(self.projects)), ('wA:pB', None))

    def test_created_pane_rejects_ambiguous_checkout_or_wrong_workspace_pane(self):
        self.store.command = lambda *args, **kwargs: {'workspaces': [
            dict(workspace_id='w42', worktree=dict(checkout_path=str(self.projects))),
            dict(workspace_id='w43', cwd=str(self.projects))]}
        with self.assertRaisesRegex(ValueError, 'uniquely'):
            self.store.created_pane({}, str(self.projects))
        self.store.command = lambda *args, **kwargs: ({'workspaces': [dict(workspace_id='w42', worktree=dict(checkout_path=str(self.projects)))]}
            if args == ('workspace', 'list') else {'panes': [dict(pane_id='w99:p1')]})
        with self.assertRaisesRegex(ValueError, 'valid root pane'):
            self.store.created_pane({}, str(self.projects))

    def test_launch_starts_agent_after_missing_root_pane_is_resolved(self):
        original = self.store.command
        def command(*args, **kwargs):
            if args[:2] == ('workspace', 'create'):
                return {'type': 'workspace_created'}
            if args == ('workspace', 'list'):
                return {'workspaces': [dict(workspace_id='wB', worktree=dict(checkout_path=str(self.projects)))]}
            if args == ('pane', 'list', '--workspace', 'wB'):
                return {'panes': [dict(pane_id='wB:pA')]}
            return original(*args, **kwargs)
        self.store.command = command
        org = self.organization()
        profile = self.hire(org)
        run = self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == run)
        self.assertEqual(job['state'], 'persona_sent', job.get('error'))
        self.assertEqual(job['pane_id'], 'wB:pA')
        starts = [args for args, _ in self.calls if args[:2] == ('agent', 'start')]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0][6], 'wB:pA')

    def test_ambiguous_launch_never_sends_agent_input(self):
        original = self.store.command
        def command(*args, **kwargs):
            if args[:2] == ('workspace', 'create'):
                return {'root_pane': None}
            if args == ('workspace', 'list'):
                return {'workspaces': [dict(workspace_id='w42', worktree=dict(checkout_path=str(self.projects))),
                                       dict(workspace_id='w43', worktree=dict(checkout_path=str(self.projects)))]}
            return original(*args, **kwargs)
        self.store.command = command
        org = self.organization()
        profile = self.hire(org)
        run = self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == run)
        self.assertEqual(job['state'], 'needs_attention')
        self.assertFalse(any(args[:2] in (('agent', 'start'), ('agent', 'prompt')) for args, _ in self.calls))

    def action(self, action, **body):
        return self.store.action(action, {'request_id': uuid.uuid4().hex, **body})['id']

    def organization(self, name='Engineering'):
        return self.action('save', name=name, purpose='Build useful software', instructions='Respect project owner instructions.')

    def hire(self, org, name='Maya', **changes):
        body = dict(organization_id=org, name=name, role='Lead', runtime='codex', persona='Plan carefully; report evidence.', project=str(self.projects), manager_id='')
        body.update(changes)
        return self.action('hire', **body)

    def drain(self):
        self.store.wait_idle(timeout=5)

    def test_launch_rejects_unconfigured_runtime_before_creating_workspace(self):
        org = self.organization()
        profile = self.hire(org)
        self.store.runtime_status = lambda runtime: dict(installed=True, status='not_configured')
        job_id = self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == job_id)
        self.assertEqual(job['state'], 'needs_attention')
        self.assertIn('uses codex', job['error'])
        self.assertFalse(any(args[:2] == ('workspace', 'create') for args, _ in self.calls))

    def test_task_launch_pins_recorded_base_before_agent_starts(self):
        def git(*args):
            return subprocess.run(['git', '-C', str(self.projects), *args], check=True,
                                  capture_output=True, text=True, timeout=20).stdout
        git('init')
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.test')
        (self.projects / '.gitignore').write_text('.ci-cache/\n')
        (self.projects / 'file').write_text('base')
        git('add', '.')
        git('commit', '-m', 'base')
        base = git('rev-parse', 'HEAD').strip()
        (self.projects / 'file').write_text('newer shared checkout')
        git('commit', '-am', 'newer')
        shared = git('rev-parse', 'HEAD').strip()
        original = self.store.command
        checkout = None
        def command(*args, **kwargs):
            nonlocal checkout
            if args[:2] == ('worktree', 'create'):
                checkout = Path(args[args.index('--path') + 1])
                git('worktree', 'add', '-b', args[args.index('--branch') + 1], str(checkout))
                return {'root_pane': {'pane_id': 'w999:p1'}}
            if args[:2] == ('agent', 'start'):
                self.assertEqual(subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'],
                    capture_output=True, text=True, check=True).stdout.strip(), base)
            return original(*args, **kwargs)
        self.store.command = command
        org = self.organization()
        profile = self.hire(org)
        deployed = (Path(gateway.__file__).parent / 'skills/herdr-worktree-integration').resolve().as_posix()
        job_id = self.action('launch', organization_id=org, profile_id=profile,
                            task_id='a' * 32, worktree_branch='herdr/task-' + 'a' * 12,
                            start_sha=base,
                            task_prompt='Implement this task. Read ' + deployed + '/references/tools.md. Never push.')
        self.drain()
        job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == job_id)
        self.assertEqual(job['state'], 'persona_sent', job.get('error'))
        self.assertEqual(job['worktree_branch'], 'herdr/task-' + 'a' * 12)
        self.assertEqual(git('rev-parse', 'HEAD').strip(), shared)
        prompts = [args[3] for args, _ in self.calls if args[:2] == ('agent', 'prompt')]
        assigned = next(prompt for prompt in prompts if 'Assigned task (start now' in prompt)
        # The delivered prompt reads the checkout-local copy, never the deployed
        # bundle, so no external-directory approval is required.
        self.assertIn('.ci-cache/herdr-guidance/', assigned)
        self.assertIn('/references/tools.md', assigned)
        self.assertNotIn(deployed, assigned)

    def test_git_launch_defaults_to_separate_committed_worktree(self):
        def git(*args):
            subprocess.run(['git', *args], check=True, capture_output=True, timeout=20)
        git('init', str(self.projects))
        (self.projects / 'tracked.txt').write_text('Committed content', encoding='utf-8')
        git('-C', str(self.projects), 'add', 'tracked.txt')
        git('-C', str(self.projects), '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'Initial')
        (self.projects / 'tracked.txt').write_text('Local uncommitted content', encoding='utf-8')
        original = self.store.command
        def command(*args, **kwargs):
            if args[:2] == ('worktree', 'create'):
                self.calls.append((args, kwargs))
                git('-C', args[args.index('--cwd') + 1], 'worktree', 'add', '-b',
                    args[args.index('--branch') + 1], args[args.index('--path') + 1])
                return {'root_pane': {'pane_id': 'w999:p1'}, 'workspace': {'workspace_id': 'w999'}}
            return original(*args, **kwargs)
        self.store.command = command
        org = self.organization()
        profile = self.hire(org)
        first = self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == first)
        self.assertEqual(job['state'], 'persona_sent', job['error'])
        self.assertEqual(job['workspace_mode'], 'worktree')
        checkout = Path(job['worktree_path'])
        self.assertEqual((checkout / 'tracked.txt').read_text(encoding='utf-8'), 'Committed content')
        (checkout / 'tracked.txt').write_text('Agent change', encoding='utf-8')
        self.assertEqual((self.projects / 'tracked.txt').read_text(encoding='utf-8'), 'Local uncommitted content')
        self.assertFalse(any(args[:2] == ('workspace', 'create') for args, _ in self.calls))
        self.action('release', organization_id=org, job_id=first)
        second = self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        next_job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == second)
        self.assertEqual(next_job['state'], 'persona_sent', next_job['error'])
        self.assertNotEqual(next_job['worktree_path'], job['worktree_path'])
        self.assertTrue(checkout.exists())  # Release never deletes agent work.

    def test_worktree_failure_does_not_launch_in_shared_directory(self):
        subprocess.run(['git', 'init', str(self.projects)], check=True, capture_output=True)
        original = self.store.command
        def command(*args, **kwargs):
            if args[:2] == ('worktree', 'create'):
                raise ValueError('Worktree unavailable')
            return original(*args, **kwargs)
        self.store.command = command
        org = self.organization()
        profile = self.hire(org)
        job_id = self.action('launch', organization_id=org, profile_id=profile)
        # Real metadata exercises the configured-base and locking preflight;
        # the Herdr command itself still fails before any agent can start.
        self.drain()
        job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == job_id)
        self.assertEqual(job['state'], 'needs_attention')
        self.assertIn('Worktree unavailable', job['error'])
        self.assertFalse(any(args[:2] in (('workspace', 'create'), ('agent', 'start')) for args, _ in self.calls))

    def test_shared_checkout_gate_does_not_block_independent_worker_jobs(self):
        from unittest.mock import patch
        run = dict(id='launch', kind='launch', profile_id='worker', state='persona_sent',
                   source_project=str(self.projects), worktree_path=str(self.projects / 'worker'))
        chat = dict(id='chat', kind='chat', profile_id='worker', state='running', runs=[run])
        data = dict(profiles=[dict(id='worker', project=str(self.projects))], jobs=[run, chat],
                    member_states={'worker': {'status': 'working'}})
        with patch.object(self.store, 'snapshot', return_value=data):
            self.assertFalse(self.store.checkout_in_use(self.projects))
            self.assertTrue(self.store.checkout_in_use(self.projects / 'worker'))
            run.pop('worktree_path')
            self.assertTrue(self.store.checkout_in_use(self.projects))

    def test_worktree_can_be_explicitly_disabled(self):
        (self.projects / '.git').mkdir()
        org = self.organization()
        profile = self.hire(org, use_worktree=False)
        job_id = self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == job_id)
        self.assertEqual(job['state'], 'persona_sent', job['error'])
        self.assertEqual(job['workspace_mode'], 'workspace')
        self.assertTrue(any(args[:2] == ('workspace', 'create') for args, _ in self.calls))

    def test_model_settings_persist_and_reach_runtime_arguments(self):
        org = self.organization()
        settings = [
            ('opencode', dict(provider='opencode-go', model='deepseek-v4.1-flash'), ['--model', 'opencode-go/deepseek-v4.1-flash']),
            ('opencode', dict(provider='opencode', model='deepseek-v4.1-flash'), ['--model', 'opencode/deepseek-v4.1-flash']),
            ('opencode', dict(provider='openai', model='test-model', reasoning='high'), ['--model', 'openai/test-model#high']),
            ('codex', dict(provider='openai', model='test-model', reasoning='high'), ['--model', 'test-model', '--config', 'model_provider="openai"', '--config', 'model_reasoning_effort="high"']),
            ('claude', dict(provider='anthropic', model='sonnet', reasoning='high'), ['--model', 'sonnet', '--effort', 'high']),
        ]
        for runtime, config, expected in settings:
            profile_id = self.hire(org, name=runtime, runtime=runtime, **config)
            profile = next(p for p in self.store.snapshot()['profiles'] if p['id'] == profile_id)
            for key, value in config.items():
                self.assertEqual(profile[key], value)
            job_id = self.action('launch', organization_id=org, profile_id=profile_id)
            self.drain()
            run = next(j for j in self.store.snapshot()['jobs'] if j['id'] == job_id)
            self.assertEqual(run['state'], 'persona_sent', run['error'])
            call = next(args for args, _ in self.calls if args[:3] == ('agent', 'start', run['alias']))
            self.assertEqual(list(call[call.index('--') + 1:]), expected)
            self.assertEqual(run['launch_arguments'], expected)
        for config in [dict(provider='openai'), dict(model='test-model'), dict(provider='-bad', model='model')]:
            with self.assertRaises(ValueError):
                self.hire(org, runtime='opencode', **config)

    def test_persistence_idempotency_and_payload_conflict(self):
        body = {'request_id': 'unique_request_1', 'name': 'Engineering', 'purpose': 'Build', 'instructions': ''}
        first = self.store.action('save', body)
        self.assertEqual(self.store.action('save', body), first)
        with self.assertRaises(ValueError):
            self.store.action('save', {**body, 'name': 'Changed'})
        self.assertEqual(len(self.store.snapshot()['organizations']), 1)
        self.store.close()
        self.store = gateway.OrganizationStore(self.store.path, self.projects, self.command)
        self.assertEqual(self.store.snapshot()['organizations'][0]['id'], first['id'])

    def test_scope_cycles_and_project_boundary(self):
        org = self.organization()
        other = self.organization('Other')
        lead = self.hire(org)
        worker = self.hire(org, 'Noah', manager_id=lead)
        with self.assertRaisesRegex(ValueError, 'cycles'):
            self.hire(org, id=lead, manager_id=worker)
        with self.assertRaisesRegex(ValueError, 'another organization'):
            self.hire(other, manager_id=lead)
        with self.assertRaises(ValueError):
            self.hire(org, project=str(self.root))
        with self.assertRaises(ValueError):
            self.hire(org, runtime='bash')
        self.hire(org, id=lead, persona='New persona')
        profile = next(p for p in self.store.snapshot()['profiles'] if p['id'] == lead)
        self.assertEqual(profile['version'], 2)

    def test_two_personas_launch_delegation_report_and_duplicate_launch(self):
        org = self.organization()
        maya = self.hire(org, persona='Lead with a plan.')
        noah = self.hire(org, 'Noah', persona='Implement and test.')
        for profile in (maya, noah):
            body = {'request_id': uuid.uuid4().hex, 'organization_id': org, 'profile_id': profile}
            first = self.store.action('launch', body)
            self.assertEqual(self.store.action('launch', body), first)
        self.drain()
        prompts = [args[3] for args, _ in self.calls if args[:2] == ('agent', 'prompt')]
        self.assertEqual(len(prompts), 2)
        self.assertIn('Lead with a plan.', prompts[0])
        self.assertIn('Implement and test.', prompts[1])
        with self.assertRaisesRegex(ValueError, 'previous run'):
            self.action('launch', organization_id=org, profile_id=maya)
        delegation = self.action('delegate', organization_id=org, sender_id=maya, profile_id=noah, task='Implement the feature')
        self.drain()
        job = next(j for j in self.store.snapshot()['jobs'] if j['id'] == delegation)
        self.assertEqual(job['state'], 'delivered')
        self.assertIn('Work delegated by Maya', [args for args, _ in self.calls if args[:2] == ('agent', 'prompt')][-1][3])
        self.action('report', organization_id=org, job_id=delegation, result='Reviewed the reply and tests over SSH.')
        self.assertEqual(self.store.snapshot()['jobs'][-1]['state'], 'reported_complete')
        self.assertEqual(len([args for args, _ in self.calls if args[:2] == ('agent', 'start')]), 2)

    def test_replaced_session_rejects_delivery(self):
        org = self.organization()
        a, b = self.hire(org), self.hire(org, 'Noah')
        for profile in (a, b):
            self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        recipient = self.store.snapshot()['jobs'][-1]
        self.agents[recipient['alias']]['agent_session'] = {'value': 'replacement'}
        before = len([args for args, _ in self.calls if args[:2] == ('agent', 'prompt')])
        self.action('delegate', organization_id=org, sender_id=a, profile_id=b, task='Do work')
        self.drain()
        self.assertEqual(self.store.snapshot()['jobs'][-1]['state'], 'needs_attention')
        self.assertEqual(len([args for args, _ in self.calls if args[:2] == ('agent', 'prompt')]), before)

    def test_start_failure_and_restart_do_not_replay(self):
        org = self.organization()
        profile = self.hire(org)
        real_command = self.store.command
        def blocked(*args, **kwargs):
            if args[:2] == ('agent', 'start'):
                raise ValueError('agent_not_ready: authentication required')
            return real_command(*args, **kwargs)
        self.store.command = blocked
        job_id = self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        job = self.store.snapshot()['jobs'][0]
        self.assertEqual(job['state'], 'needs_attention')
        self.assertIn('authentication', job['error'])
        self.assertTrue(job['pane_id'])
        self.assertFalse(any(args[:2] == ('agent', 'prompt') for args, _ in self.calls))
        self.store.update_job(job_id, state='running')
        self.store.close()
        before = len(self.calls)
        self.store = gateway.OrganizationStore(self.store.path, self.projects, self.command)
        self.assertEqual(self.store.snapshot()['jobs'][0]['state'], 'uncertain')
        self.assertEqual(len(self.calls), before)

    def test_incompatible_schema_fails_before_layout_creation(self):
        org = self.organization()
        profile = self.hire(org)
        self.store.command = lambda *args, **kwargs: {}
        self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        job = self.store.snapshot()['jobs'][0]
        self.assertEqual(job['state'], 'needs_attention')
        self.assertIn('Unsupported Herdr agent schema', job['error'])
        self.assertNotIn('pane_id', job)

    def test_busy_or_blocked_agent_rejects_task_without_terminal_input(self):
        org = self.organization()
        a, b = self.hire(org), self.hire(org, 'Noah')
        for profile in (a, b):
            self.action('launch', organization_id=org, profile_id=profile)
        self.drain()
        recipient = self.store.snapshot()['jobs'][-1]
        self.agents[recipient['alias']].update(agent_status='blocked', interactive_ready=False)
        before = len([args for args, _ in self.calls if args[:2] == ('agent', 'prompt')])
        self.action('delegate', organization_id=org, sender_id=a, profile_id=b, task='Do work')
        self.drain()
        job = self.store.snapshot()['jobs'][-1]
        self.assertEqual(job['state'], 'needs_attention')
        self.assertIn('not ready for input', job['error'])
        self.assertEqual(len([args for args, _ in self.calls if args[:2] == ('agent', 'prompt')]), before)

    def test_http_create_hire_and_reconnect_without_herdr(self):
        server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        server.token = 'a' * 48
        server.organizations = self.store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}/api/organizations'
        headers = {'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json'}
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(base)
            self.assertEqual(error.exception.code, 401)
            body = {'request_id': uuid.uuid4().hex, 'name': 'Engineering', 'purpose': 'Build', 'instructions': ''}
            org = json.load(urlopen(Request(base + '/save', headers=headers, data=json.dumps(body).encode())))['id']
            body = {'request_id': uuid.uuid4().hex, 'organization_id': org, 'name': 'Maya', 'role': 'Lead', 'persona': 'Be precise', 'runtime': 'claude', 'project': str(self.projects), 'manager_id': ''}
            json.load(urlopen(Request(base + '/hire', headers=headers, data=json.dumps(body).encode())))
            snapshot = json.load(urlopen(Request(base, headers=headers)))
            self.assertEqual(snapshot['profiles'][0]['name'], 'Maya')
            self.assertEqual(self.calls, [])
            from integration import IntegrationWatcher
            watcher = object.__new__(IntegrationWatcher)
            watcher.coordinator = SimpleNamespace(configuration_records=lambda: [
                {'profile_id': snapshot['profiles'][0]['id'], 'repository': 'repo'}])
            server.integration = watcher
            # The roster polls the compact state endpoint, not the full snapshot.
            # Both must expose the same binding without embedding coordination.
            for endpoint in (base, base + '/state'):
                labelled = json.load(urlopen(Request(endpoint, headers=headers)))
                self.assertEqual(labelled['profiles'][0]['coordination_binding'], 'used')
                self.assertEqual(labelled['profiles'][0]['coordination_repositories'], ['repo'])
                self.assertNotIn('integration', labelled)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + '/save', headers={**headers, 'Origin': 'https://other.example'}, data=json.dumps(body).encode()))
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
