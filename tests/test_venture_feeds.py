from datetime import datetime, timedelta, timezone
import ipaddress
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import venture_feeds


class FeedSafetyTests(unittest.TestCase):
    def test_private_loopback_and_link_local_addresses_are_refused(self):
        for value in ('127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1', '169.254.1.1', '::1', 'fd00::1'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'public address'):
                    venture_feeds.resolve_addresses(value, 443)

    def test_public_address_is_accepted(self):
        with patch.object(venture_feeds.socket, 'getaddrinfo', return_value=[
                (2, 1, 6, '', ('93.184.216.34', 443))]):
            self.assertEqual(str(venture_feeds.resolve_addresses('news.example', 443)[0]), '93.184.216.34')

    def test_source_url_must_be_https_without_credentials(self):
        for value in ('http://news.example/feed', 'ftp://news.example/feed',
                      'https://user:pass@news.example/feed', 'https:///feed'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    venture_feeds.validate_url(value, resolve=None)
        self.assertEqual(venture_feeds.validate_url('https://news.example/feed', resolve=None),
                         'https://news.example/feed')

    def test_redirects_are_revalidated(self):
        class Opener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 302, 'Found',
                                {'Location': 'https://private.example/feed'}, None)
        def resolve(host, port):
            if host == 'private.example':
                raise ValueError('Feed source must resolve to a public address only.')
            return [ipaddress.ip_address('93.184.216.34')]
        with patch.object(venture_feeds, 'build_opener', return_value=Opener()):
            with self.assertRaisesRegex(ValueError, 'public address'):
                venture_feeds.request('https://news.example/feed', {}, resolve=resolve)


class FeedParseTests(unittest.TestCase):
    def test_rss_items_are_bounded_and_entity_declarations_are_refused(self):
        body = b'''<?xml version="1.0"?><rss><channel><item>
            <title>Bank &amp; settlement update</title><link>https://news.example/a#fragment</link>
            <pubDate>Wed, 15 Nov 2026 10:00:00 GMT</pubDate><category>payments</category>
            <description><![CDATA[<p>An excerpt that is longer than it appears.</p>]]></description>
        </item></channel></rss>'''
        item = venture_feeds.parse(body, 'https://news.example/feed', venture_feeds.now())[0]
        self.assertEqual(item['title'], 'Bank & settlement update')
        self.assertEqual(item['link'], 'https://news.example/a')
        self.assertEqual(item['tags'], ['payments'])
        self.assertTrue(item['published'].startswith('2026-11-15T10:00:00'))
        self.assertLessEqual(len(item['excerpt']), venture_feeds.EXCERPT_LIMIT)
        with self.assertRaisesRegex(ValueError, 'entity'):
            venture_feeds.parse(b'<!DOCTYPE x [<!ENTITY y "z">]><rss/>', 'https://news.example/feed', venture_feeds.now())

    def test_atom_items_and_filtering(self):
        body = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
            <title>Settlement product launch</title><link rel="alternate" href="https://news.example/atom"/>
            <published>2026-11-15T19:00:00+09:00</published><category term="payments"/>
            <summary>A payments update.</summary></entry></feed>'''
        item = venture_feeds.parse(body, 'https://news.example/feed', venture_feeds.now())[0]
        self.assertEqual(item['link'], 'https://news.example/atom')
        self.assertTrue(item['published'].startswith('2026-11-15T10:00:00'))
        kept = venture_feeds.filter_items([item], dict(tags=['PAYMENTS'], keywords=['settlement']),
                                          '2026-11-15T00:00:00+00:00')
        self.assertEqual(len(kept), 1)
        self.assertEqual(venture_feeds.filter_items([item], dict(tags=['other']), ''), [])

    def test_tags_and_keywords_are_a_union(self):
        tagged = dict(title='Quarterly results', excerpt='', tags=['Economy'], published='2026-11-15T10:00:00+00:00')
        worded = dict(title='Port outage halts exports', excerpt='', tags=[], published='2026-11-15T09:00:00+00:00')
        sport = dict(title='Cup final', excerpt='', tags=['Sports'], published='2026-11-15T08:00:00+00:00')
        sources = dict(tags=['economy'], keywords=['outage'])
        # A feed without category tags is still covered by the keyword watchlist.
        self.assertEqual(venture_feeds.filter_items([tagged, worded, sport], sources, ''), [tagged, worded])
        self.assertEqual(venture_feeds.filter_items([tagged, worded, sport], {}, ''), [tagged, worded, sport])

    def test_offset_timestamps_are_normalised_before_filtering(self):
        body = b'''<rss><channel><item><title>Tokyo settlement rule</title>
            <link>https://news.example/jst</link><pubDate>Sun, 15 Nov 2026 08:00:00 +0900</pubDate>
        </item></channel></rss>'''
        item = venture_feeds.parse(body, 'https://news.example/feed', venture_feeds.now())[0]
        self.assertEqual(item['published'], '2026-11-14T23:00:00+00:00')
        # 23:00Z is after 22:30Z even though "2026-11-15T08..." would sort later as text.
        self.assertEqual(len(venture_feeds.filter_items([item], {}, '2026-11-14T22:30:00+00:00')), 1)
        # Published before the previous tick, but within the late-item grace: the
        # caller's cursor decides whether it was already offered.
        self.assertEqual(len(venture_feeds.filter_items([item], {}, '2026-11-15T00:00:00+00:00')), 1)
        self.assertEqual(venture_feeds.filter_items([item], {}, '2026-11-16T00:00:00+00:00'), [])

    def test_request_connects_to_the_checked_address(self):
        opened = []
        class Opener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 404, 'Not found', {}, None)
        def build(*handlers):
            opened.append(handlers)
            return Opener()
        with patch.object(venture_feeds, 'build_opener', side_effect=build):
            with self.assertRaisesRegex(ValueError, 'HTTP 404'):
                venture_feeds.request('https://news.example/feed', {},
                                      resolve=lambda host, port: [ipaddress.ip_address('93.184.216.34')])
        pinned = [handler for handler in opened[0] if isinstance(handler, venture_feeds._PinnedHTTPS)]
        self.assertEqual(pinned[0]._addresses, ('93.184.216.34',))  # No second DNS lookup at connect time.
        self.assertTrue(any(isinstance(handler, venture_feeds.ProxyHandler) and not handler.proxies
                            for handler in opened[0]))

    def test_pinned_connection_falls_back_to_the_next_checked_address(self):
        attempts = []
        def create_connection(target, timeout):
            attempts.append(target[0])
            if ':' in target[0]:
                raise OSError('Network is unreachable')  # An IPv4-only host.
            return MagicMock()
        context = MagicMock()
        connection = venture_feeds._PinnedConnection('news.example', addresses=['2606:2800::1', '93.184.216.34'],
                                                     context=context, timeout=5)
        with patch.object(venture_feeds.socket, 'create_connection', side_effect=create_connection):
            connection.connect()
        self.assertEqual(attempts, ['2606:2800::1', '93.184.216.34'])
        context.wrap_socket.assert_called_once()
        self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], 'news.example')
        failing = venture_feeds._PinnedConnection('news.example', addresses=['2606:2800::1'],
                                                  context=context, timeout=5)
        with patch.object(venture_feeds.socket, 'create_connection', side_effect=create_connection):
            with self.assertRaisesRegex(OSError, 'unreachable'):
                failing.connect()

    def test_fetch_304_returns_no_items(self):
        response = venture_feeds.Response(304, {'ETag': 'abc'}, b'', 'https://news.example/feed')
        value = venture_feeds.fetch('https://news.example/feed', etag='old', request_fn=lambda *a, **k: response)
        self.assertEqual(value['status'], 304)
        self.assertEqual(value['items'], [])
        self.assertEqual(value['etag'], 'abc')


if __name__ == '__main__':
    unittest.main()
