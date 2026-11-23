"""Telegram settings authentication and request validation."""
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import server as gateway

STATUS = dict(enabled=True, token_configured=True, chat_id='123456789', last_error='', last_sent_at=0)


class TelegramHttpTests(unittest.TestCase):
    def setUp(self):
        self.server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        self.server.token = 'a' * 48
        self.server.operators = Mock()
        self.server.operators.identify.side_effect = lambda token: (
            dict(name='reader', role='operator') if token == 'reader' else None)
        self.server.sessions = gateway.BrowserSessions()
        self.server.telegram = Mock()
        for name in ('snapshot', 'configure', 'test', 'disconnect'):
            getattr(self.server.telegram, name).return_value = dict(STATUS)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def request(self, path='/api/telegram', body=None, token=None, headers=None, raw=None):
        credentials = {'Authorization': 'Bearer ' + (self.server.token if token is None else token)}
        credentials.update(headers or {})
        data = raw if raw is not None else None if body is None else json.dumps(body).encode()
        try:
            with urlopen(Request(self.base + path, data=data, headers=credentials), timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_authentication_and_role_checked_before_service_access(self):
        for token, expected in (('', 401), ('reader', 403)):
            self.assertEqual(self.request(token=token)[0], expected)
            for action in ('configure', 'test', 'disconnect'):
                self.assertEqual(self.request('/api/telegram/' + action, {}, token=token)[0], expected)
        for name in ('snapshot', 'configure', 'test', 'disconnect'):
            getattr(self.server.telegram, name).assert_not_called()

    def test_admin_can_read_configure_test_and_disconnect(self):
        self.assertEqual(self.request(), (200, STATUS))
        body = dict(enabled=True, chat_id='123456789', token='write-only-token')
        self.assertEqual(self.request('/api/telegram/configure', body), (200, STATUS))
        self.server.telegram.configure.assert_called_once_with(body)
        for action in ('test', 'disconnect'):
            self.assertEqual(self.request('/api/telegram/' + action, {}), (200, STATUS))
            getattr(self.server.telegram, action).assert_called_once_with()

    def test_cookie_session_requires_same_origin_for_mutation(self):
        cookies = {'Cookie': 'herdr_session=' + self.server.sessions.create(self.server.token)}
        self.assertEqual(self.request(token='', headers=cookies)[0], 200)
        self.assertEqual(self.request('/api/telegram/test', {}, token='', headers=cookies)[0], 403)
        self.assertEqual(self.request('/api/telegram/test', {}, token='',
                         headers=dict(cookies, Origin='https://untrusted.example'))[0], 403)
        self.server.telegram.test.assert_not_called()
        self.assertEqual(self.request('/api/telegram/test', {}, token='',
                         headers=dict(cookies, Origin=self.base))[0], 200)
        self.server.telegram.test.assert_called_once_with()

    def test_bad_or_oversized_requests_do_not_reach_service(self):
        for action in ('configure', 'test', 'disconnect'):
            for raw in (b'[]', b'{broken', b'{}' + b' ' * 4096):
                self.assertEqual(self.request('/api/telegram/' + action, raw=raw)[0], 400)
            getattr(self.server.telegram, action).assert_not_called()
        for action in ('test', 'disconnect'):
            self.assertEqual(self.request('/api/telegram/' + action, {'unexpected': True})[0], 400)
            getattr(self.server.telegram, action).assert_not_called()

    def test_failures_preserve_dashboard_session_and_hide_storage_paths(self):
        self.server.telegram.test.side_effect = gateway.TelegramError('Telegram rejected the bot token.')
        status, body = self.request('/api/telegram/test', {})
        self.assertEqual(status, 502)
        self.assertIn('bot token', body['error'])
        self.assertEqual(self.request()[0], 200)
        self.server.telegram.snapshot.side_effect = OSError('private-token-path')
        self.assertEqual(self.request(), (503, {'error': 'Telegram settings storage is unavailable.'}))
        self.server.telegram.configure.side_effect = OSError('private-token-path')
        self.assertEqual(self.request('/api/telegram/configure', {}),
                         (503, {'error': 'Telegram settings storage is unavailable.'}))
