import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from herdr_ids import is_pane_id, is_workspace_id


class HerdrIdTests(unittest.TestCase):
    def test_accepts_numeric_and_letter_counters(self):
        for value in ('wA', 'w12'):
            self.assertIs(is_workspace_id(value), True)
        self.assertIs(is_pane_id('wA:pB'), True)
        self.assertIs(is_pane_id('wA:pB', 'wA'), True)

    def test_rejects_malformed_and_option_like_ids(self):
        for value in (None, 1, '', '--help', 'w', 'wA/p1', 'wA\n', 'wA;echo', 'wA:p1'):
            with self.subTest(value=value):
                self.assertFalse(is_workspace_id(value))
        for value in (None, '', '--help', 'wA', 'wA:p', 'wA:p1\n', 'wA:p1;echo'):
            with self.subTest(value=value):
                self.assertFalse(is_pane_id(value))
        self.assertFalse(is_pane_id('wB:pA', 'wA'))
