"""Owner notification delivery without contacting Telegram."""
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import telegram_notifications as telegram
from telegram_alerts import AlertBatch

TOKEN = '123456789:' + 'A' * 35
CHAT = '123456789'
ALERT = dict(key='task:one:review_ready', title='Review needed', message='Task one is awaiting review.')


class TelegramNotificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'telegram.json'
        self.collect = Mock(return_value=[dict(ALERT)])
        self.send = Mock()
        self.service = telegram.TelegramNotifications(self.path, self.collect, send=self.send)
        self.clock_patch = patch.object(telegram.time, 'time', return_value=1000)
        self.clock = self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.service.close)

    def configure(self):
        return self.service.configure(dict(token=TOKEN, chat_id=CHAT, enabled=True))

    def advance(self):
        self.clock.return_value += 2

    def test_unconfigured_service_is_idle(self):
        self.assertEqual(self.service.snapshot(), dict(enabled=False, token_configured=False,
                         chat_id='', last_error='', last_sent_at=0))
        self.service.reconcile()
        self.collect.assert_not_called()
        self.send.assert_not_called()
        self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(ValueError, 'Save a bot token'):
            self.service.test()

    def test_settings_are_private_and_blank_token_preserves_credentials(self):
        status = self.configure()
        self.assertNotIn(TOKEN, json.dumps(status))
        self.assertNotIn('token', status)
        self.assertEqual(self.service.load()['token'], TOKEN)
        if os.name == 'posix':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        status = self.service.configure(dict(token='  ', chat_id=CHAT, enabled=False))
        self.assertFalse(status['enabled'])
        self.assertTrue(status['token_configured'])
        self.assertEqual(self.service.load()['token'], TOKEN)
        self.service.reconcile()
        self.send.assert_not_called()
        self.service.configure(dict(chat_id=CHAT, enabled=True))
        self.service.reconcile()
        self.assertEqual(self.send.call_args.args[:2], (TOKEN, CHAT))

    def test_invalid_configuration_does_not_write_supplied_values(self):
        valid = dict(token=TOKEN, chat_id=CHAT, enabled=True)
        invalid = [None, [], {}, dict(valid, enabled='yes'), dict(valid, chat_id=12),
                   dict(valid, chat_id='-100123'), dict(valid, chat_id='@owner'),
                   dict(valid, chat_id='0'), dict(valid, token='secret-invalid-token'),
                   dict(valid, token=None), dict(valid, unexpected='secret'),
                   dict(valid, token='', chat_id='')]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValueError) as error:
                self.service.configure(body)
            self.assertNotIn('secret', str(error.exception))
            self.assertFalse(self.path.exists())

    def test_disconnect_removes_credentials_and_stops_delivery(self):
        self.configure()
        self.service.reconcile()
        self.advance()
        result = self.service.disconnect()
        self.assertFalse(result['enabled'])
        self.assertFalse(result['token_configured'])
        self.assertEqual(result['chat_id'], '')
        self.assertNotIn(TOKEN, self.path.read_text())
        self.assertEqual(self.service.load()['delivered'], [])
        self.service.reconcile()
        self.assertEqual(self.send.call_count, 1)

    def test_receipts_survive_restart_and_resolution_allows_recurrence(self):
        self.configure()
        self.service.reconcile()
        self.assertIn(ALERT['key'], self.service.load()['delivered'])
        self.advance()
        restarted = telegram.TelegramNotifications(self.path, self.collect, send=self.send)
        restarted.reconcile()
        self.assertEqual(self.send.call_count, 1)
        self.collect.return_value = []
        restarted.reconcile()
        self.assertEqual(restarted.load()['delivered'], [])
        self.collect.return_value = [dict(ALERT)]
        restarted.reconcile()
        self.assertEqual(self.send.call_count, 2)

    def test_changed_recipient_resets_receipts(self):
        self.configure()
        self.service.reconcile()
        self.service.configure(dict(chat_id='987654321', enabled=True))
        self.assertEqual(self.service.load()['delivered'], [])
        self.service.reconcile()
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.send.call_args.args[1], '987654321')

    def test_digest_records_acknowledged_issues_and_limits_backlog(self):
        self.collect.return_value = [dict(ALERT, key='task:' + str(i)) for i in range(12)]
        self.configure()
        self.service.reconcile()
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(len(self.service.load()['delivered']), 10)
        self.advance()
        self.service.reconcile()
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(len(self.service.load()['delivered']), 12)
        self.advance()
        self.service.reconcile()
        self.assertEqual(self.send.call_count, 2)

    def test_partial_runtime_outage_does_not_repeat_unresolved_blocker(self):
        self.configure()
        self.service.reconcile()
        self.advance()
        offline = AlertBatch()
        offline.preserve_prefixes = (ALERT['key'],)
        self.collect.return_value = offline
        self.service.reconcile()
        self.assertIn(ALERT['key'], self.service.load()['delivered'])
        self.collect.return_value = [dict(ALERT)]
        self.service.reconcile()
        self.assertEqual(self.send.call_count, 1)

    def test_failed_delivery_has_no_receipt_and_honors_retry_delay(self):
        self.configure()
        self.send.side_effect = telegram.TelegramError('Telegram is unavailable.', retry_after=90)
        with self.assertRaises(telegram.TelegramError):
            self.service.reconcile()
        self.assertEqual(self.service.load()['delivered'], [])
        self.assertEqual(self.service.snapshot()['last_error'], 'Telegram is unavailable.')
        self.service.reconcile()
        self.assertEqual(self.send.call_count, 1)
        self.clock.return_value += 91
        self.send.side_effect = None
        self.service.reconcile()
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.service.snapshot()['last_error'], '')

    def test_manual_test_works_when_disabled_and_obeys_backoff(self):
        self.service.configure(dict(token=TOKEN, chat_id=CHAT, enabled=False))
        status = self.service.test()
        self.assertFalse(status['enabled'])
        self.assertEqual(status['last_sent_at'], 1000)
        self.assertEqual(self.service.load()['delivered'], [])
        self.send.side_effect = telegram.TelegramError('Retry later.', retry_after=30)
        with self.assertRaises(telegram.TelegramError):
            self.service.test()
        with self.assertRaisesRegex(telegram.TelegramError, 'waiting'):
            self.service.test()
        self.assertEqual(self.send.call_count, 2)

    def test_configuration_or_shutdown_during_collection_prevents_send(self):
        self.configure()
        def disabled():
            self.service.configure(dict(chat_id=CHAT, enabled=False))
            return [dict(ALERT)]
        self.collect.side_effect = disabled
        self.service.reconcile()
        self.send.assert_not_called()
        self.service.configure(dict(chat_id=CHAT, enabled=True))
        def stopped():
            self.service.close()
            return [dict(ALERT)]
        self.collect.side_effect = stopped
        self.service.reconcile()
        self.send.assert_not_called()

    def test_backoff_established_during_collection_prevents_send(self):
        self.configure()
        def delayed():
            self.service.retry_at = 1100
            return [dict(ALERT)]
        self.collect.side_effect = delayed
        self.service.reconcile()
        self.send.assert_not_called()

    def test_collection_outage_keeps_receipts_and_sanitizes_error(self):
        self.configure()
        self.service.reconcile()
        self.advance()
        self.collect.side_effect = RuntimeError('terminal output contains ' + TOKEN)
        wake = Mock()
        wake.wait.side_effect = lambda _: self.service.stopped.set()
        self.service.wake = wake
        self.service.run()
        self.assertIn(ALERT['key'], self.service.load()['delivered'])
        self.assertNotIn(TOKEN, json.dumps(self.service.snapshot()))
        self.assertIn('retry automatically', self.service.snapshot()['last_error'])


class TelegramTransportTests(unittest.TestCase):
    def test_posts_bounded_plain_text_to_fixed_https_endpoint(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok":true,"result":{}}'
        with patch.object(telegram, 'urlopen', return_value=response) as request:
            telegram.send_message(TOKEN, CHAT, '<b>' + 'x' * 5000)
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, 'https://api.telegram.org/bot' + TOKEN + '/sendMessage')
        self.assertEqual(request.call_args.kwargs['timeout'], 8)
        data = json.loads(sent.data)
        self.assertEqual(data['chat_id'], CHAT)
        self.assertEqual(len(data['text']), 4000)
        self.assertNotIn('parse_mode', data)
        self.assertTrue(data['link_preview_options']['is_disabled'])

    def test_errors_never_expose_token_or_upstream_description(self):
        for code, expected in ((401, 'bot token'), (403, 'chat'), (400, 'chat'), (500, 'retry')):
            with self.subTest(code=code):
                error = HTTPError('https://api.telegram.org/bot' + TOKEN, code, TOKEN, {},
                                  BytesIO(json.dumps(dict(ok=False, description=TOKEN)).encode()))
                with patch.object(telegram, 'urlopen', side_effect=error), self.assertRaises(telegram.TelegramError) as result:
                    telegram.send_message(TOKEN, CHAT, 'hello')
                self.assertNotIn(TOKEN, str(result.exception))
                self.assertIn(expected, str(result.exception))
        with patch.object(telegram, 'urlopen', side_effect=URLError(TOKEN)), self.assertRaises(telegram.TelegramError) as result:
            telegram.send_message(TOKEN, CHAT, 'hello')
        self.assertNotIn(TOKEN, str(result.exception))

    def test_rate_limit_respects_bounded_telegram_retry_after(self):
        for delay, expected in ((120, 120), ('bad', 60), (0, 1), (9999999, 86400)):
            with self.subTest(delay=delay):
                error = HTTPError('https://api.telegram.org/bot' + TOKEN, 429, TOKEN, {},
                                  BytesIO(json.dumps(dict(parameters=dict(retry_after=delay))).encode()))
                with patch.object(telegram, 'urlopen', side_effect=error), self.assertRaises(telegram.TelegramError) as result:
                    telegram.send_message(TOKEN, CHAT, 'hello')
                self.assertEqual(result.exception.retry_after, expected)
                self.assertNotIn(TOKEN, str(result.exception))
