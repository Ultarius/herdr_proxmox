import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('gateway', Path(__file__).parents[1] / 'web/gateway/server.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class GatewayTests(unittest.TestCase):
    def test_access_mode_rebinds_real_http_listener(self):
        listeners = []
        factory = gateway.ThreadingHTTPServer
        def create(*args):
            listener = factory(*args)
            listeners.append(listener)
            return listener
        keys = type('Keys', (), {'snapshot': lambda _: {'key_count': 1}})()
        with tempfile.TemporaryDirectory() as directory, patch.object(gateway, 'ThreadingHTTPServer', create):
            policy = Path(directory) / 'access.json'
            thread = threading.Thread(target=gateway.serve_gateway, args=('0.0.0.0', 0, policy, 'a' * 48, {'ssh_access': keys}), daemon=True)
            thread.start()
            def wait_for_listener(count):
                deadline = time.monotonic() + 12
                while len(listeners) < count or not hasattr(listeners[-1], 'dashboard_access'):
                    if time.monotonic() >= deadline:
                        self.fail('Listener did not rebind')
                    time.sleep(.02)
            try:
                wait_for_listener(1)
                base = f'http://127.0.0.1:{listeners[0].server_port}/api/dashboard-access'
                headers = {'Authorization': 'Bearer ' + 'a' * 48}
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base, data=b'{"mode":"ssh","tunnel_ready":true}'))
                self.assertEqual(error.exception.code, 401)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base, data=b'{"mode":"ssh","tunnel_ready":true}', headers={**headers, 'Origin':'https://invalid.example'}))
                self.assertEqual(error.exception.code, 403)
                data = json.load(urlopen(Request(base, data=b'{"mode":"ssh","tunnel_ready":true}', headers=headers)))
                self.assertEqual(data['pending_mode'], 'ssh')
                wait_for_listener(2)
                self.assertEqual(listeners[-1].server_address[0], '127.0.0.1')
                self.assertEqual(json.load(urlopen(Request(base, headers=headers)))['mode'], 'ssh')
                json.load(urlopen(Request(base, data=b'{"mode":"lan"}', headers=headers)))
                wait_for_listener(3)
                self.assertEqual(listeners[-1].server_address[0], '0.0.0.0')
                self.assertEqual(json.load(urlopen(Request(base, headers=headers)))['mode'], 'lan')
            finally:
                if listeners:
                    listeners[-1].shutdown()
                thread.join(15)
                self.assertFalse(thread.is_alive())

    def test_native_list_envelopes(self):
        self.assertEqual(gateway.listing({'agents': [{'state': 'working'}]}, 'agents'), [{'state': 'working'}])
        with self.assertRaises(ValueError):
            gateway.listing({'changed_protocol': []}, 'agents')

    def test_workspace_validation_and_arguments(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gateway, 'PROJECTS', Path(directory).resolve()), patch.object(gateway, 'command') as command:
            gateway.workspace_action('create', {'cwd': directory, 'label': 'My project; echo hello'})
            args = command.call_args.args
            self.assertEqual(args, ('workspace', 'create', '--cwd', str(Path(directory).resolve()), '--label', 'My project; echo hello', '--no-focus'))
            with self.assertRaises(ValueError):
                gateway.workspace_action('create', {'cwd': str(Path(directory).parent), 'label': 'escape'})
            with self.assertRaises(ValueError):
                gateway.workspace_action('rename', {'id': '--help', 'label': 'x'})
            with self.assertRaises(ValueError):
                gateway.workspace_action('rename', {'id': 'w1', 'label': '--help'})
            with self.assertRaises(ValueError):
                gateway.workspace_action('close', {'id': 'w1'})

    def test_subprocess_uses_no_shell_and_unwraps_json(self):
        with patch.object(gateway.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{"result":{"workspaces":[]}}'
            self.assertEqual(gateway.command('workspace', 'list'), {'workspaces': []})
            self.assertEqual(run.call_args.args[0], [gateway.BIN, 'workspace', 'list'])
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_http_auth_origin_static_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gateway, 'ROOT', Path(directory).resolve()), patch.object(gateway, 'command') as command:
            Path(directory, 'index.html').write_text('dashboard')
            # Match commands explicitly so snapshot request ordering is irrelevant.
            responses = {('workspace', 'list'): {'workspaces': [{'workspace_id': 'w1'}]},
                         ('agent', 'list'): {'agents': [{'name': 'external-agent', 'pane_id': 'w1:p1'}]}}
            command.side_effect = lambda *args: responses[args]
            server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
            server.token = 'a' * 48
            server.ssh_access = gateway.SshAccess(directory)
            server.herdr_server = gateway.HerdrServer(command)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                self.assertEqual(urlopen(base + '/').read(), b'dashboard')
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + '/api/snapshot')
                self.assertEqual(error.exception.code, 401)
                command.assert_not_called()
                headers = {'Authorization': 'Bearer ' + server.token}
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/herdr-server/start', data=b'{}'))
                self.assertEqual(error.exception.code, 401)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/herdr-server/start', data=b'{}', headers={**headers, 'Origin':'https://invalid.example'}))
                self.assertEqual(error.exception.code, 403)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/ssh-access/add', data=b'{"public_key":"invalid"}', headers={'Content-Type': 'application/json'}))
                self.assertEqual(error.exception.code, 401)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/ssh-access/add', data=b'{"public_key":"invalid"}', headers={**headers, 'Origin': 'https://evil.example'}))
                self.assertEqual(error.exception.code, 403)
                self.assertEqual(json.load(urlopen(Request(base + '/api/ssh-access', headers=headers))), {'key_count': 0})
                response = json.load(urlopen(Request(base + '/api/snapshot', headers=headers)))
                self.assertEqual(response['workspaces'][0]['workspace_id'], 'w1')
                self.assertEqual(response['agents'][0]['name'], 'external-agent')
                command.side_effect = ValueError('server_not_running')
                stopped = json.load(urlopen(Request(base + '/api/snapshot', headers=headers)))
                self.assertEqual(stopped, {'herdr_server':'stopped', 'workspaces':[], 'agents':[]})
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/snapshot', headers={**headers, 'Origin': 'https://evil.example'}))
                self.assertEqual(error.exception.code, 403)
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + '/%2e%2e/server.py')
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
