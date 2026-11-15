from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import venture_feeds
import venture_fx

ECB = b'''<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube><Cube time="2026-11-13">
    <Cube currency="USD" rate="1.0800"/><Cube currency="JPY" rate="162.00"/><Cube currency="bad" rate="x"/>
  </Cube></Cube>
</gesmes:Envelope>'''


class FxTests(unittest.TestCase):
    def test_parse_reads_date_and_rates_and_skips_malformed_entries(self):
        value = venture_fx.parse_ecb(ECB)
        self.assertEqual(value['date'], '2026-11-13')
        self.assertEqual(value['rates'], {'EUR': 1.0, 'USD': 1.08, 'JPY': 162.0})

    def test_cross_rates_follow_left_buys_right(self):
        rates = venture_fx.parse_ecb(ECB)['rates']
        self.assertEqual(venture_fx.cross(rates, 'USD/JPY'), 150.0)
        self.assertAlmostEqual(venture_fx.cross(rates, 'JPY/USD'), 1 / 150, places=6)
        self.assertEqual(venture_fx.cross(rates, 'JPY/JPY'), 1.0)
        with self.assertRaisesRegex(ValueError, 'KRW'):
            venture_fx.cross(rates, 'USD/KRW')

    def test_entity_declarations_and_empty_documents_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'entity'):
            venture_fx.parse_ecb(b'<!DOCTYPE x [<!ENTITY y "z">]><x/>')
        with self.assertRaisesRegex(ValueError, 'no reference rates'):
            venture_fx.parse_ecb(b'<x/>')

    def test_fetch_uses_the_fixed_source_through_the_safe_fetcher(self):
        calls = []
        def request(url, headers, **options):
            calls.append((url, options))
            return venture_feeds.Response(200, {}, ECB, url)
        value = venture_fx.fetch(request_fn=request)
        self.assertEqual(calls, [(venture_fx.ECB_URL, {'limit': venture_fx.FETCH_LIMIT})])
        self.assertIn('fetched_at', value)


if __name__ == '__main__':
    unittest.main()
