import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location('session_gateway', Path(__file__).parents[1] / 'web/gateway/server.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class BrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        self.server.token = 'secret'
        self.server.sessions = gateway.BrowserSessions()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method='GET', **headers):
        return urlopen(Request(self.base + path, method=method, headers=headers))

    def test_cookie_authentication_restore_logout_and_origin(self):
        response = self.request('/api/session', 'POST', Authorization='Bearer secret', Origin=self.base)
        cookie = response.headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Max-Age=604800', cookie)
        self.assertNotIn('secret', cookie)
        cookie = cookie.split(';')[0]
        self.assertTrue(json.load(self.request('/api/session', Cookie=cookie))['authenticated'])
        with patch.object(gateway, 'command', return_value=[]):
            self.assertEqual(self.request('/api/snapshot', Cookie=cookie).status, 200)
        for headers in [dict(Cookie=cookie, Origin='https://other.example'), dict(Cookie=cookie)]:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/session/logout', 'POST', **headers)
            self.assertEqual(error.exception.code, 403)
        response = self.request('/api/session/logout', 'POST', Cookie=cookie, Origin=self.base)
        self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
        self.assertFalse(json.load(self.request('/api/session', Cookie=cookie))['authenticated'])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/snapshot', Cookie=cookie)
        self.assertEqual(error.exception.code, 401)

    def test_expiry_rotation_and_secure_cookie(self):
        response = self.request('/api/session', 'POST', Authorization='Bearer secret', Origin=self.base.replace('http:', 'https:'))
        self.assertNotIn('; Secure', response.headers['Set-Cookie'])
        self.server.cookie_secure = True
        response = self.request('/api/session', 'POST', Authorization='Bearer secret', Origin=self.base)
        self.assertIn('; Secure', response.headers['Set-Cookie'])
        key = response.headers['Set-Cookie'].split(';')[0].split('=', 1)[1]
        self.assertFalse(self.server.sessions.valid(key, 'rotated'))
        with patch.object(gateway.time, 'time', return_value=gateway.time.time() + 604801):
            self.assertFalse(self.server.sessions.valid(key, 'secret'))
        with self.assertRaises(HTTPError) as error:
            self.request('/api/session', 'POST', Authorization='Bearer wrong', Origin=self.base)
        self.assertEqual(error.exception.code, 401)
