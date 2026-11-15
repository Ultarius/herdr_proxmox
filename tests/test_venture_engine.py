from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import venture_engine as engine


def configuration(**simulation):
    return dict(
        market=dict(currency='JPY', fx_pair='USD/JPY', fx_rate=150, sales_cycle=1, legacy_bias=1),
        simulation=dict(dict(burn=[dict(label='hosting', amount=30000, currency='JPY')], red_team=5,
                             survival_threshold=60, consecutive_days=2), **simulation),
        outreach=dict(stage='survey', tone='formal', language='ja+en', persona='cfo'))


class LedgerTests(unittest.TestCase):
    def test_fx_converts_both_directions_and_refuses_unrelated_currencies(self):
        market = dict(currency='JPY', fx_pair='USD/JPY', fx_rate=150)
        self.assertEqual(engine.convert(100, 'USD', market), 15000)
        self.assertEqual(engine.convert(15000, 'JPY', dict(market, currency='USD')), 100)
        with self.assertRaisesRegex(ValueError, 'FX pair'):
            engine.convert(100, 'EUR', market)

    def test_burn_lines_are_monthly(self):
        value = configuration(burn=[dict(label='api', amount=30000, currency='JPY'),
                                    dict(label='cloud', amount=100, currency='USD')])
        self.assertEqual(engine.daily_burn(value), 1500)

    def test_sales_cycle_friction_delays_demand(self):
        value = configuration()
        value['market']['sales_cycle'] = 3
        state, first = engine.step(None, value, 100000, dict(demand=2), [], None)
        self.assertEqual(first['scores']['demand'], 50)
        state, second = engine.step(state, value, first['capital_after'], {}, [], None)
        self.assertEqual(second['scores']['demand'], 50)
        state, third = engine.step(state, value, second['capital_after'], {}, [], None)
        self.assertEqual(third['scores']['demand'], 60)
        self.assertEqual(third['landed_demand'], 2)

    def test_price_moves_at_most_half_per_day_and_drives_revenue(self):
        self.assertEqual(engine.bounded_price(100000, 10000), 15000)
        self.assertEqual(engine.bounded_price(None, 10000), 10000)
        self.assertEqual(engine.bounded_price(20000, None), 20000)
        state = dict(engine.initial_state(), scores=dict(demand=80, competition=50, regulatory=50, delivery_risk=50),
                     monthly_price=30000)
        _, ledger = engine.step(state, configuration(), 100000, {}, [], None)
        self.assertEqual(ledger['customers'], 3)
        self.assertEqual(ledger['revenue'], 3000)
        self.assertEqual(ledger['capital_after'], 100000 - 1000 + 3000)
        self.assertIsNone(ledger['runway_days'])

    def test_red_team_penalty_scales_with_strictness(self):
        scores = dict(demand=50, competition=50, regulatory=50, delivery_risk=50)
        lenient = engine.survival(scores, 180, [dict(severity=5)], configuration(red_team=1))
        strict = engine.survival(scores, 180, [dict(severity=5)], configuration(red_team=10))
        # Same base score (63.2); penalty 5 x 1/10 = 0.5 versus 5 x 10/10 = 5.
        self.assertEqual((lenient, strict), (63, 58))

    def test_breakdown_explains_the_score(self):
        _, ledger = engine.step(None, configuration(), 100000, {}, [dict(severity=2)], None)
        parts = ledger['score_breakdown']
        self.assertEqual(set(parts), {'runway', 'demand', 'competition', 'regulatory', 'delivery_risk',
                                      'red_team_penalty'})
        self.assertEqual(parts['red_team_penalty'], -1.0)
        self.assertLessEqual(abs(sum(parts.values()) - ledger['score']), 0.6)

    def test_qualification_needs_consecutive_days_and_capital_failure_wins(self):
        value = configuration(survival_threshold=0, consecutive_days=2)
        buying = dict(engine.initial_state(), scores=dict(demand=70, competition=50, regulatory=50, delivery_risk=50),
                      monthly_price=30000)
        state, first = engine.step(buying, value, 100000, {}, [], None)
        self.assertIsNone(engine.outcome(first, value))
        state, second = engine.step(state, value, first['capital_after'], {}, [], None)
        self.assertEqual(engine.outcome(second, value), 'qualified')
        _, broke = engine.step(None, value, 500, {}, [], None)
        self.assertEqual(engine.outcome(broke, value), 'failed')

    def test_an_untouched_venture_never_qualifies(self):
        # Baseline scores with a long runway sit above the default threshold;
        # that alone must not qualify, however many days pass.
        value = configuration(survival_threshold=60, consecutive_days=2, burn=[])
        state = None
        for _ in range(5):
            state, ledger = engine.step(state, value, 100000, {}, [], None)
            self.assertGreaterEqual(ledger['score'], 60)
            self.assertEqual(ledger['customers'], 0)
            self.assertIsNone(engine.outcome(ledger, value))

    def test_an_unpriced_venture_has_no_paying_customers_and_never_qualifies(self):
        value = configuration(survival_threshold=0, consecutive_days=1)
        keen = dict(engine.initial_state(), scores=dict(demand=90, competition=50, regulatory=50, delivery_risk=50))
        for price in (None, 0):
            with self.subTest(price=price):
                _, ledger = engine.step(dict(keen, monthly_price=price), value, 100000, {}, [], None)
                self.assertEqual((ledger['customers'], ledger['revenue'], ledger['qualifying_days']), (0, 0, 0))
                self.assertIsNone(engine.outcome(ledger, value))
        _, priced = engine.step(keen, value, 100000, {}, [], 30000)
        self.assertEqual(engine.outcome(priced, value), 'qualified')

    def test_quiet_days_neither_count_nor_reset_qualification(self):
        value = configuration(survival_threshold=0, consecutive_days=2)
        buying = dict(engine.initial_state(), scores=dict(demand=70, competition=50, regulatory=50, delivery_risk=50),
                      monthly_price=30000)
        state, judged = engine.step(buying, value, 100000, {}, [], None)
        self.assertEqual(judged['qualifying_days'], 1)
        state, quiet = engine.step(state, value, judged['capital_after'], {}, [], None, judged=False)
        self.assertEqual(quiet['qualifying_days'], 1)
        self.assertFalse(quiet['judged'])
        self.assertIsNone(engine.outcome(quiet, value))
        state, again = engine.step(state, value, quiet['capital_after'], {}, [], None)
        self.assertEqual(engine.outcome(again, value), 'qualified')

    def test_price_is_anchored_to_a_reference_and_costs_customers(self):
        value = configuration()  # Monthly burn 30000 is the reference price.
        self.assertEqual(engine.reference_price(value), 30000)
        self.assertEqual(engine.reference_price(configuration(reference_price=12000)), 12000)
        # The first price has no previous value; the ceiling still bounds it.
        self.assertEqual(engine.bounded_price(1_000_000_000, None, 30000), 120000)
        self.assertEqual(engine.price_drag(30000, 30000), 0)
        self.assertEqual(engine.price_drag(60000, 30000), 10)
        self.assertEqual(engine.price_drag(120000, 30000), 20)
        keen = dict(engine.initial_state(), scores=dict(demand=70, competition=50, regulatory=50, delivery_risk=50))
        _, fair = engine.step(dict(keen, monthly_price=30000), value, 100000, {}, [], None)
        _, greedy = engine.step(dict(keen, monthly_price=120000), value, 100000, {}, [], None)
        self.assertEqual((fair['customers'], greedy['customers']), (2, 0))
        self.assertEqual(greedy['price_drag'], 20)
        self.assertEqual(greedy['revenue'], 0)

    def test_change_limit_tightens_with_strictness(self):
        self.assertEqual([engine.change_limit(value) for value in (1, 3, 4, 7, 8, 10)], [3, 3, 2, 2, 1, 1])

    def test_next_slot_is_the_next_occurrence_of_the_hour(self):
        at = datetime(2026, 11, 15, 7, 30, tzinfo=timezone.utc)
        self.assertEqual(engine.next_slot(6, at), datetime(2026, 11, 16, 6, tzinfo=timezone.utc))
        self.assertEqual(engine.next_slot(9, at), datetime(2026, 11, 15, 9, tzinfo=timezone.utc))


class RoleTests(unittest.TestCase):
    def test_roles_follow_overrides_then_names_then_load(self):
        people = [('f', 'Facilitator'), ('a', 'Skeptic'), ('b', 'Sales writer')]
        roles = engine.assign_roles(people)
        self.assertEqual(roles['red_team'], ['a'])
        self.assertEqual(roles['outreach'], ['b'])
        self.assertEqual(roles['analyst'], ['f'])
        self.assertTrue(all(roles[role] for role in engine.ROLES))
        self.assertEqual(engine.assign_roles(people, {'b': 'red_team'})['red_team'], ['b'])

    def test_two_member_group_covers_every_role(self):
        roles = engine.assign_roles([('f', 'One'), ('a', 'Two')])
        self.assertTrue(all(len(roles[role]) == 1 for role in engine.ROLES))
        self.assertEqual(roles['analyst'], ['f'])

    def test_brief_carries_etiquette_persona_stage_and_strictness(self):
        value = configuration(red_team=8)
        text = engine.brief(value, engine.assign_roles([('f', 'Analyst')]), {'f': 'Analyst'}, '{}')
        self.assertIn('keigo', text)
        self.assertIn('Bilingual', text)
        self.assertIn('CFO', text)
        self.assertIn('discovery conversation', text)
        self.assertIn('Strictness 8/10', text)
        self.assertIn('Challenge every product change', text)
        self.assertIn('at most 1 product change', text)
        self.assertIn('tone formal', text)
        self.assertNotIn('Translate', text)

    def test_brief_tells_the_analyst_to_translate_and_keep_terms(self):
        value = dict(configuration(), sources=dict(translation=True, keep_terms=['稟議書', 'APPI']))
        text = engine.brief(value, engine.assign_roles([('f', 'Analyst')]), {'f': 'Analyst'}, '{}')
        self.assertIn('Translate the selected items', text)
        self.assertIn('稟議書, APPI', text)


if __name__ == '__main__':
    unittest.main()
