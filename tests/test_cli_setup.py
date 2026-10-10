import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from cli_setup import CliSetup, SetupSession, parse_integration_status
import server as gateway
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import threading


class CliSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.manager = CliSetup(self.home)

    def tearDown(self):
        self.manager.close()
        self.directory.cleanup()

    def test_status_does_not_return_credentials_and_distinguishes_unknown(self):
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.return_value = Mock(stdout='{"loggedIn":true,"token":"SECRET"}', stderr='', returncode=0)
            self.assertEqual(self.manager.status('claude')['status'], 'configured')
            run.return_value = Mock(stdout='', stderr='Not logged in', returncode=1)
            self.assertEqual(self.manager.status('codex')['status'], 'not_configured')
            run.return_value = Mock(stdout='unsupported command SECRET', stderr='', returncode=2)
            self.assertEqual(self.manager.status('codex')['status'], 'unknown')
            self.assertEqual(self.manager.status('agy')['status'], 'unknown')
            auth = self.home / '.local/share/opencode/auth.json'
            auth.parent.mkdir(parents=True)
            auth.write_text('{"provider":{"type":"api","key":"SECRET"}}')
            result = self.manager.status('opencode')
            self.assertEqual(result['status'], 'credentials_detected')
            self.assertNotIn('SECRET', json.dumps(result))
            auth.write_text('{broken')
            self.assertEqual(self.manager.status('opencode')['status'], 'unknown')
        with patch('cli_setup.os.access', return_value=False):
            self.assertEqual(self.manager.status('claude')['status'], 'missing')

    def test_allowlist_duplicate_validation_and_cleanup(self):
        with self.assertRaises(ValueError):
            self.manager.action('start', {'cli': '/bin/sh'})
        with self.assertRaises(ValueError):
            self.manager.action('start', {'cli': ['codex']})
        with patch('cli_setup.sys.platform', 'linux'), patch('cli_setup.os.access', return_value=True), patch('cli_setup.SetupSession') as factory:
            factory.return_value.sequence = 0
            result = self.manager.action('start', {'cli': 'codex', 'command': 'rm -rf /'})
            args = factory.call_args.args[0]
            self.assertEqual(args, [str(self.home / '.local/bin/codex'), 'login', '--device-auth'])
            key = result['id']
            with self.assertRaises(ValueError):
                self.manager.action('start', {'cli': 'codex'})
            for action, body in [('input', {'data': 'x' * 4097}), ('poll', {'cursor': -1}), ('resize', {'cols': 0, 'rows': 24})]:
                with self.assertRaises(ValueError):
                    self.manager.action(action, {'id': key, **body})
            self.manager.action('close', {'id': key})
            factory.return_value.close.assert_called_once()
            self.assertEqual(self.manager.sessions, {})
            with self.assertRaises(ValueError):
                self.manager.action('input', {'id': key, 'data': 'hello'})

    def test_missing_herdr_integration_is_reported_and_installable(self):
        # An agent reports working/blocked state and a resume reference only
        # through its official integration, so a missing one is surfaced.
        plugin = self.home / '.config/opencode/plugins/herdr-agent-state.js'
        with patch('cli_setup.os.access', return_value=True):
            self.assertFalse(self.manager.status('opencode')['integration']['installed'])
            self.assertEqual(self.manager.status('opencode')['integration']['integration'], 'opencode')
            # Antigravity is named antigravity-cli by Herdr, not agy.
            self.assertEqual(self.manager.status('agy')['integration']['integration'], 'antigravity-cli')
            with patch('cli_setup.subprocess.run') as run:
                # Installing is verified by re-reading the status command, not
                # by trusting the install command's exit code.
                install_ok = Mock(stdout='installed', stderr='', returncode=0)
                still_missing = Mock(stdout='claude: not installed (/c)\n', stderr='', returncode=0)
                now_current = Mock(stdout='opencode: current (v13) (/p)\n', stderr='', returncode=0)
                run.side_effect = [install_ok, still_missing, install_ok, now_current]
                with patch('cli_setup.sys.platform', 'linux'):
                    # A status that still reports the integration missing is a
                    # failure even though the install command exited cleanly.
                    with self.assertRaises(ValueError):
                        self.manager.action('install_integration', {'cli': 'claude'})
                    result = self.manager.action('install_integration', {'cli': 'opencode'})
                self.assertTrue(result['installed'])
                self.assertEqual(result['version'], 'v13')
                self.assertEqual(run.call_args_list[-1].args[0][1:], ['integration', 'status'])
                self.assertEqual(run.call_args_list[-2].args[0][1:], ['integration', 'install', 'opencode'])
                self.assertEqual(self.manager.integration('opencode')['installed'], True)
        with patch('cli_setup.os.access', return_value=False):
            self.assertFalse(self.manager.status('codex')['integration']['installed'])
            with self.assertRaises(ValueError):
                self.manager.install_integration('codex')

    def test_integration_install_failure_reports_bounded_output(self):
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.return_value = Mock(stdout='x' * 5000, stderr='', returncode=1)
            with patch('cli_setup.sys.platform', 'linux'), self.assertRaises(ValueError) as error:
                self.manager.action('install_integration', {'cli': 'opencode'})
            self.assertLessEqual(str(error.exception).count('x'), 300)
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.side_effect = subprocess.TimeoutExpired('herdr', 120)
            with patch('cli_setup.sys.platform', 'linux'), self.assertRaisesRegex(ValueError, '120 seconds'):
                self.manager.action('install_integration', {'cli': 'opencode'})

    def test_integration_status_output_is_parsed_from_the_herdr_command(self):
        # Real output shapes, including a decorated name and every state that
        # `describe_integration_state` can print.
        output = ('pi: not installed (/home/herdr/.pi/agent/extensions/herdr-agent-state.ts)\n'
                  'opencode: current (v13) (/home/herdr/.config/opencode/plugins/x.js)\n'
                  'letta (experimental): not installed (/home/herdr/.letta/hooks/x.sh)\n'
                  'claude: needs repair (v5) (/home/herdr/.claude/hooks/x.sh)\n'
                  'codex: outdated (v3 < v5) (/home/herdr/.codex/x.sh)\n'
                  'grok: current (legacy) (/home/herdr/.grok/x.sh)\n')
        parsed = parse_integration_status(output)
        self.assertEqual(parsed['pi']['state'], 'not_installed')
        self.assertEqual(parsed['pi']['version'], '')
        self.assertEqual(parsed['opencode'], dict(state='current', version='v13', expected=''))
        self.assertEqual(parsed['letta']['state'], 'not_installed')
        self.assertEqual(parsed['claude'], dict(state='needs_repair', version='v5', expected=''))
        # `outdated (v3 < v5)` must not be mistaken for a path or a version.
        self.assertEqual(parsed['codex'], dict(state='outdated', version='v3', expected='v5'))
        self.assertEqual(parsed['grok']['version'], 'legacy')
        self.assertEqual(parse_integration_status(''), {})

    def test_only_a_current_integration_counts_as_connected(self):
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.return_value = Mock(returncode=0, stderr='', stdout=(
                'opencode: current (v13) (/p)\n'
                'claude: outdated (v3 < v6) (/c)\n'
                'codex: needs repair (v5) (/x)\n'
                'antigravity-cli: not installed (/q)\n'))
            data = self.manager.snapshot()
            summary = data['integrations']
            self.assertEqual(summary['connected'], 1)
            self.assertEqual(summary['outdated'], 2)
            self.assertEqual(summary['missing'], 1)
            self.assertEqual(summary['total'], 4)
            # An outdated integration is installed but must not read as connected.
            claude = next(c for c in data['clis'] if c['id'] == 'claude')['integration']
            self.assertTrue(claude['installed'])
            self.assertFalse(claude['current'])
            self.assertIn('outdated', claude['detail'])

    def test_status_reports_integration_connection_from_one_command(self):
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.return_value = Mock(returncode=0, stderr='', stdout=(
                'opencode: current (v13) (/p)\n'
                'antigravity-cli: not installed (/q)\n'), )
            state = self.manager.status('opencode')['integration']
            self.assertTrue(state['installed'])
            self.assertEqual(state['version'], 'v13')
            self.assertIn('v13', state['detail'])
            self.assertFalse(self.manager.status('agy')['integration']['installed'])
            # One status command backs every card; the cache prevents a re-run.
            self.manager.status('claude')
            status_calls = [c for c in run.call_args_list
                         if c.args[0][1:3] == ['integration', 'status']]
            self.assertEqual(len(status_calls), 1)

    def test_snapshot_summarizes_integration_connections(self):
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.return_value = Mock(returncode=0, stderr='', stdout='opencode: current (v13) (/p)\n')
            data = self.manager.snapshot()
            self.assertEqual(data['integrations']['connected'], 1)
            self.assertEqual(data['integrations']['total'], 4)
            self.assertIn('OpenCode', data['integrations']['names'])
            self.assertIn('Antigravity', data['integrations']['missing_names'])

    def test_agents_cannot_be_added_until_the_cli_and_integration_are_ready(self):
        # A CLI without a current integration starts agents that never report
        # state and never resume, so hiring must stop at the precondition.
        with patch('cli_setup.os.access', return_value=False):
            self.assertIn('not installed', self.manager.ready('opencode'))
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.return_value = Mock(returncode=0, stderr='', stdout=(
                'opencode: not installed (/p)\nclaude: outdated (v3 < v6) (/c)\n'
                'codex: needs repair (v5) (/x)\nantigravity-cli: current (v2) (/a)\n'))
            self.assertIn('no Herdr integration', self.manager.ready('opencode'))
            self.assertIn('outdated', self.manager.ready('claude'))
            self.assertIn('repair', self.manager.ready('codex'))
            self.assertIsNone(self.manager.ready('agy'))
            # A runtime outside the allowlist is not ours to judge.
            self.assertIsNone(self.manager.ready('some-other-agent'))

    def test_installing_an_outdated_integration_is_reported_as_a_failure(self):
        # Installed but still outdated is not a working integration; reporting
        # success would leave agents silently unable to report or resume.
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.side_effect = [
                Mock(stdout='ok', stderr='', returncode=0),
                Mock(stdout='codex: outdated (v3 < v5) (/x)\n', stderr='', returncode=0),
            ]
            with patch('cli_setup.sys.platform', 'linux'), self.assertRaisesRegex(ValueError, 'still outdated'):
                self.manager.action('install_integration', {'cli': 'codex'})
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.side_effect = [
                Mock(stdout='ok', stderr='', returncode=0),
                Mock(stdout='codex: needs repair (v5) (/x)\n', stderr='', returncode=0),
            ]
            with patch('cli_setup.sys.platform', 'linux'), self.assertRaisesRegex(ValueError, 'not current'):
                self.manager.action('install_integration', {'cli': 'codex'})

    def test_an_unverified_integration_never_claims_a_fault_it_cannot_see(self):
        # With `herdr integration status` unavailable only the plugin path can
        # be checked, which proves presence but not health.
        def fresh():
            self.manager.integration_checked = 0

        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            fresh()
            run.return_value = Mock(returncode=1, stdout='', stderr='boom')
            (self.home / '.codex').mkdir(parents=True)
            (self.home / '.codex/herdr-agent-state.sh').write_text('// present')
            state = self.manager.integration('codex')
            self.assertTrue(state['installed'])
            self.assertEqual(state['state'], 'unverified')
            self.assertFalse(state['current'])
            self.assertIn('could not be verified', state['detail'])
            self.assertIn('could not be verified', self.manager.ready('codex'))
        # An empty version must not render as a bare comparison.
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            fresh()
            run.return_value = Mock(returncode=0, stderr='', stdout='claude: outdated (v3 < v6) (/c)\n')
            self.assertIn('v3 < v6', self.manager.ready('claude'))
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            fresh()
            run.return_value = Mock(returncode=0, stderr='', stdout='claude: current (v6) (/c)\n')
            self.assertIsNone(self.manager.ready('claude'))

    def test_a_version_without_a_trailing_path_is_still_parsed(self):
        # Documented output always prints a path, but dropping the version
        # would silently downgrade a current integration to unknown.
        parsed = parse_integration_status('opencode: current (v13)\n')
        self.assertEqual(parsed['opencode'], dict(state='current', version='v13', expected=''))

    def test_claude_status_accepts_documented_auth_method(self):
        # `claude auth status` documents authMethod; loggedIn is not guaranteed.
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.return_value = Mock(stdout='{"authMethod":"claude.ai"}', stderr='', returncode=0)
            result = self.manager.status('claude')
            self.assertEqual(result['status'], 'configured')
            self.assertEqual(result['auth_method'], 'claude.ai')
            self.assertIn('claude.ai', result['detail'])
            run.return_value = Mock(stdout='{"authMethod":"none"}', stderr='', returncode=1)
            self.assertEqual(self.manager.status('claude')['status'], 'not_configured')
            run.return_value = Mock(stdout='{"loggedIn":true,"authMethod":"api_key"}', stderr='', returncode=0)
            self.assertEqual(self.manager.status('claude')['status'], 'configured')
            run.return_value = Mock(stdout='not json', stderr='', returncode=0)
            self.assertEqual(self.manager.status('claude')['status'], 'unknown')

    def test_claude_verification_runs_a_bounded_hello_probe(self):
        # A stored credential is not proof: confirm a real answer.
        with patch('cli_setup.os.access', return_value=True), patch('cli_setup.subprocess.run') as run:
            run.return_value = Mock(
                stdout='{"type":"result","subtype":"success","is_error":false,"result":"hello"}',
                stderr='', returncode=0)
            verified = self.manager.action('verify', {'cli': 'claude'})
            self.assertTrue(verified['verified'])
            self.assertIn('hello', verified['detail'])
            command = run.call_args.args[0]
            self.assertEqual(command[1:4], ['--print', 'Respond with hello.', '--output-format'])
            self.assertEqual(run.call_args.kwargs['timeout'], 90)
            run.return_value = Mock(stdout='{"is_error":true,"result":"Invalid API key"}', stderr='', returncode=1)
            with self.assertRaisesRegex(ValueError, 'Invalid API key'):
                self.manager.verify('claude')
            run.side_effect = subprocess.TimeoutExpired('claude', 90)
            with self.assertRaisesRegex(ValueError, 'did not answer'):
                self.manager.verify('claude')
            run.side_effect = None
            run.return_value = Mock(stdout='not json', stderr='', returncode=0)
            with self.assertRaisesRegex(ValueError, 'JSON result'):
                self.manager.verify('claude')
            with self.assertRaisesRegex(ValueError, 'Claude Code only'):
                self.manager.verify('codex')
            with self.assertRaises(ValueError):
                self.manager.action('verify', {'cli': '/bin/sh'})

    def test_setup_endpoints_require_auth_and_same_origin(self):
        server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        server.token = 'a' * 48
        server.cli_setup = self.manager
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            for path, body in [('/api/cli-setup', None), ('/api/cli-setup/start', b'{"cli":"codex"}'),
                               ('/api/cli-setup/verify', b'{"cli":"claude"}')]:
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + path, data=body))
                self.assertEqual(error.exception.code, 401)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + path, data=body, headers={'Authorization': 'Bearer ' + server.token, 'Origin': 'https://foreign.example'}))
                self.assertEqual(error.exception.code, 403)
            with patch.object(self.manager, 'snapshot', return_value={'clis': []}):
                data = json.load(urlopen(Request(base + '/api/cli-setup', headers={'Authorization': 'Bearer ' + server.token})))
                self.assertEqual(data, {'clis': []})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    @unittest.skipUnless(sys.platform == 'linux', 'Real PTY integration runs in Linux CI')
    def test_real_pty_prompt_input_resize_and_close(self):
        code = 'import os; print("TTY=" + str(os.isatty(0)), flush=True); value=input("Enter code: "); print("Received " + value, flush=True)'
        session = SetupSession([sys.executable, '-u', '-c', code], self.home, dict(os.environ, TERM='xterm-256color'))
        try:
            session.resize(80, 30)
            deadline = time.monotonic() + 5
            while 'Enter code:' not in session.poll(0)['output']:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            self.assertIn('TTY=True', session.poll(0)['output'])
            session.input('test-code\r')
            while session.poll(0)['running']:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            result = session.poll(0)
            self.assertIn('Received test-code', result['output'])
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(session.poll(result['cursor'])['output'], '')
        finally:
            session.close()
        self.assertEqual(list(session.output), [])
        with self.assertRaises(ValueError):
            session.input('another code')

    @unittest.skipUnless(sys.platform == 'linux', 'Real PTY integration runs in Linux CI')
    def test_close_stops_live_process(self):
        session = SetupSession([sys.executable, '-u', '-c', 'import time; time.sleep(60)'], self.home, dict(os.environ))
        session.close()
        self.assertIsNotNone(session.process.poll())
        session.close()


if __name__ == '__main__':
    unittest.main()
