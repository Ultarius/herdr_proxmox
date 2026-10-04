import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from run_logs import RunLogs, PAGE, MAX_FILE
import server as gateway


class RunLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.logs = RunLogs(self.temp.name, 'herdr', lambda *args: b'hello\n')

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        return self.logs.action('save', {'pane': 'w1:p1', 'label': 'Run snapshot', 'source': 'visible'})

    def test_persistence_metadata_size_and_bounded_preview(self):
        self.logs.reader = lambda *args: b'x' * 1_000_000
        item = self.save()
        self.assertEqual(item['size_mb'], 1.0)
        self.assertEqual(item['size_bytes'], 1_000_000)
        self.assertNotIn('text', self.logs.snapshot()['logs'][0])
        restored = RunLogs(self.temp.name, 'herdr')
        self.assertEqual(restored.snapshot()['logs'][0]['id'], item['id'])
        preview = restored.action('preview', {'id': item['id']})
        self.assertEqual(len(preview['text']), 8192)
        self.assertTrue(preview['truncated'])

    def test_utf8_page_boundaries_and_expired_scoped_links(self):
        content = b'a' * (PAGE - 1) + '€🙂'.encode() + b'z' * PAGE
        self.logs.reader = lambda *args: content
        item = self.save()
        token = self.logs.action('ticket', {'id': item['id'], 'mode': 'view'})['ticket']
        output, offset = b'', 0
        while offset is not None:
            _, chunk, offset = self.logs.page(token, offset)
            chunk.decode('utf-8')
            self.assertLessEqual(len(chunk), PAGE + 3)
            output += chunk
        self.assertEqual(output, content)
        with patch('run_logs.time.monotonic', return_value=10 ** 15):
            with self.assertRaises(ValueError):
                self.logs.page(token)
        with self.assertRaises(ValueError):
            self.logs.page('invalid-ticket')

    def test_download_single_use_size_limit_delete_and_validation(self):
        item = self.save()
        ticket = self.logs.action('ticket', {'id': item['id'], 'mode': 'download'})['ticket']
        _, data, offset = self.logs.page(ticket)
        self.assertEqual(data, b'hello\n')
        self.assertIsNone(offset)
        with self.assertRaises(ValueError):
            self.logs.page(ticket)
        token = self.logs.action('ticket', {'id': item['id']})['ticket']
        self.logs.action('delete', {'id': item['id']})
        self.assertEqual(self.logs.snapshot()['logs'], [])
        with self.assertRaises(ValueError):
            self.logs.page(token)
        for body in ({'pane': '../token'}, {'pane': 'w1:p1', 'source': 'unknown'}, {'pane': 'w1:p1', 'label': '\n'}):
            with self.assertRaises(ValueError):
                self.logs.action('save', body)
        self.logs.reader = lambda *args: b'x' * (MAX_FILE + 1)
        with self.assertRaises(ValueError):
            self.save()
        self.assertFalse(list(Path(self.temp.name).glob('*.txt')))

    def test_http_auth_text_only_and_fragment_ticket(self):
        self.logs.reader = lambda *args: b'<script>alert("not executable")</script>'
        item = self.save()
        server = gateway.ThreadingHTTPServer(('127.0.0.1', 0), gateway.Handler)
        server.token = 'a' * 48
        server.run_logs = self.logs
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        headers = {'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json'}
        try:
            for endpoint in ('/api/logs', '/api/logs/preview'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + endpoint, data=b'{}' if endpoint.endswith('preview') else None))
                self.assertEqual(error.exception.code, 401)
            ticket = json.load(urlopen(Request(base + '/api/logs/ticket', data=json.dumps({'id': item['id']}).encode(), headers=headers)))['ticket']
            body = json.dumps({'ticket': ticket, 'offset': 0}).encode()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + '/log-data', data=body, headers={'Origin': 'https://foreign.example'}))
            self.assertEqual(error.exception.code, 403)
            response = urlopen(Request(base + '/log-data', data=body))
            self.assertEqual(response.headers['Content-Type'], 'text/plain; charset=utf-8')
            self.assertIn('sandbox', response.headers['Content-Security-Policy'])
            self.assertEqual(response.read(), b'<script>alert("not executable")</script>')
            page = urlopen(base + '/logs/view').read().decode()
            self.assertIn('text.textContent=', page)
            self.assertNotIn('flutter', page.lower())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
