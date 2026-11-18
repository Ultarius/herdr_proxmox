from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from usage_limits import detect, observe

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc)
UNKNOWN_LIMIT = timedelta(hours=6)


class UsageLimitTests(unittest.TestCase):
    def test_opencode_go_limit_uses_its_retry_countdown(self):
        screen = ('Go limit reached\n5 hour usage limit reached. It will reset in 4 hours 31 minutes. '
                  'To continue using this model now, enable usage from your available balance\n'
                  '5 hour usage limit reached. It will reset in 4 hours 31 minutes. (click to expand) '
                  '[retrying in 4h 28m attempt #1]')
        hit = observe(detect(screen), None, NOW, UNKNOWN_LIMIT)
        self.assertEqual(hit['reset_at'], NOW + timedelta(hours=4, minutes=31))
        self.assertEqual(hit['retry_at'], NOW + timedelta(hours=4, minutes=28))
        self.assertEqual(hit['until'], hit['reset_at'])
        self.assertIn('usage limit reached', hit['message'])

    def test_codex_limit_with_a_duration_and_with_a_clock_time(self):
        hit = detect("You've hit your usage limit. Upgrade to Pro or try again in 2 days 3 hours 5 minutes.")
        self.assertEqual(hit['reset_after'], int(timedelta(days=2, hours=3, minutes=5).total_seconds()))
        for screen in ("You've hit your usage limit. Try again at 3:05 PM.",
                       'Claude usage limit reached. Your limit will reset at 3pm.'):
            hit = observe(detect(screen), None, NOW, UNKNOWN_LIMIT)
            self.assertIsNone(hit['reset_at'])
            self.assertEqual(hit['until'], NOW + UNKNOWN_LIMIT)

    def test_ordinary_work_and_old_messages_are_not_limits(self):
        for screen in ('The buyer has a rate limit today. Reset the price assumptions.',
                       'Rate limit reached in the example.\nTry again in 5 seconds to re-run the tests.',
                       'Waiting for input', '', None,
                       'usage limit reached, resets in 2h\n' + '\n'.join('line %d' % n for n in range(60))):
            with self.subTest(screen=(screen or '')[:40]):
                self.assertIsNone(detect(screen))

    def test_only_the_provider_status_block_contributes_timing(self):
        hit = detect('\x1b[31mGo usage limit reached. It will reset in 4 hours\x1b[0m\n'
                     '31 minutes.\n[retrying in 3h 59m attempt #1]\nTry again in 5 seconds to re-run the tests.')
        self.assertEqual(hit['reset_after'], 16260)
        self.assertEqual(hit['retry_after'], 14340)

    def test_unchanged_countdowns_keep_their_deadlines_even_after_expiry(self):
        hit = detect('Go usage limit reached. It will reset in 2 hours. [retrying in 1h 59m attempt #1]')
        first = observe(hit, None, NOW, UNKNOWN_LIMIT)
        later = observe(hit, first, NOW + timedelta(hours=3), UNKNOWN_LIMIT)
        self.assertEqual(later['reset_at'], first['reset_at'])
        self.assertEqual(later['retry_at'], first['retry_at'])
        self.assertEqual(later['until'], first['until'])

    def test_a_new_cli_retry_does_not_move_the_provider_reset(self):
        screen = 'Go usage limit reached. It will reset in 2 hours. [retrying in 5m attempt #'
        first = observe(detect(screen + '1]'), None, NOW, UNKNOWN_LIMIT)
        later = observe(detect(screen + '2]'), first, NOW + timedelta(minutes=5), UNKNOWN_LIMIT)
        self.assertEqual(later['reset_at'], first['reset_at'])
        self.assertEqual(later['retry_at'], NOW + timedelta(minutes=10))


if __name__ == '__main__':
    unittest.main()
