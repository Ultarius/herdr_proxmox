import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from cli_setup import CliSetup, SetupSession
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

    def test_setup_endpoints_require_auth_and_same_origin(self):
        server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        server.token = 'a' * 48
        server.cli_setup = self.manager
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            for path, body in [('/api/cli-setup', None), ('/api/cli-setup/start', b'{"cli":"codex"}')]:
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
