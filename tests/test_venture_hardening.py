"""Regression tests for the venture module's reliability and retention rules."""
from contextlib import closing
from datetime import timedelta
import ipaddress
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import project_git
import test_ventures as fixtures
import venture_brief
import venture_engine as engine
import venture_feeds
import venture_fx
import venture_notebook
import venture_panel
import venture_studio
import ventures


class VentureHardeningTests(unittest.TestCase):
    def refuse_meetings(self):
        original = self.store.action.side_effect

        def refused(action, body):
            if action == 'discuss':
                raise ValueError('Meeting limit reached.')
            return original(action, body)
        self.store.action.side_effect = refused
        return original

    # --- The feed cursor -------------------------------------------------

    def test_a_budget_skipped_tick_offers_the_same_news_again(self):
        record = self.attach_group(self.create())
        original = self.refuse_meetings()
        with self.assertRaisesRegex(ValueError, 'Meeting limit'):
            self.run_with_news(record)
        self.assertFalse(self.ventures.get(record['id'], 'org').get('feed_cursor'))
        self.store.action.side_effect = original
        tick = self.run_with_news(record)
        self.assertEqual((tick['state'], tick['review_kind'], len(tick['evidence']['items'])), ('meeting', 'news', 1))
        self.assertTrue(self.ventures.get(record['id'], 'org').get('feed_cursor'))

    def test_a_group_that_is_not_ready_loses_no_news(self):
        record = self.create()
        with self.assertRaisesRegex(ValueError, 'group'):
            self.run_with_news(record)
        self.assertFalse(self.ventures.get(record['id'], 'org').get('feed_cursor'))
        tick = self.run_with_news(self.attach_group(record))
        self.assertEqual((tick['review_kind'], len(tick['evidence']['items'])), ('news', 1))

    def test_a_tick_that_met_keeps_its_cursor_when_it_needs_attention(self):
        record = self.attach_group(self.create())
        original = self.store.action.side_effect

        def broken(action, body):
            if action == 'discuss':
                raise ValueError('The group could not be reached.')
            return original(action, body)
        self.store.action.side_effect = broken
        with self.assertRaises(ValueError):
            self.run_with_news(record)
        # The tick holds the items and recovery re-sends them.
        self.assertTrue(self.ventures.get(record['id'], 'org').get('feed_cursor'))

    # --- The ledger ------------------------------------------------------

    def test_a_small_burn_still_costs_money_and_anchors_the_price(self):
        def configuration(*amounts):
            return dict(market=dict(currency='EUR', fx_pair='USD/EUR', fx_rate=1.0),
                        simulation=dict(burn=[dict(label='x', amount=amount, currency='EUR') for amount in amounts]))
        for monthly in (1, 15):
            self.assertEqual(engine.daily_burn(configuration(monthly)), 1)
            self.assertEqual(engine.reference_price(configuration(monthly)), 30)
        self.assertEqual(engine.daily_burn(configuration(3000)), 100)  # Normal burns are unchanged.
        self.assertEqual(engine.daily_burn(configuration()), 0)
        self.assertEqual(engine.bounded_price(10 ** 9, 40, engine.reference_price(configuration(15))), 60)

    def test_a_daily_price_step_never_exceeds_half(self):
        self.assertEqual(engine.bounded_price(1, 5), 3)
        self.assertEqual(engine.bounded_price(100, 5), 7)

    # --- Duplicates ------------------------------------------------------

    def duplicates(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(fixtures.candidate('Invoice Desk', 'N1'), fixtures.candidate('Invoice Desk Pro', 'N1'),
                        fixtures.candidate('Invoice Desk Plus', 'N1'))
        return {item['name']: item for item in self.stored_candidates()}

    def test_a_false_duplicate_can_be_edited_back_into_the_queue(self):
        stored = self.duplicates()
        self.assertEqual(stored['Invoice Desk Pro']['state'], 'excluded')
        with self.assertRaisesRegex(ValueError, 'looks like a duplicate'):
            self.studio(mode='candidate_decide', candidate_id=stored['Invoice Desk Pro']['id'], decision='approve',
                        confirmed=True)
        unchanged = self.studio(mode='candidate_update', candidate_id=stored['Invoice Desk Pro']['id'],
                                pricing='$700 per site per month')
        self.assertEqual(unchanged['state'], 'excluded')  # Still the same idea.
        edited = self.studio(mode='candidate_update', candidate_id=stored['Invoice Desk Pro']['id'],
                             name='Customs Claims',
                             thesis='Customs brokers miss refund deadlines because duty claims live in email.')
        self.assertEqual((edited['state'], edited['duplicate_of']), ('proposed', ''))

    def test_an_operator_can_create_a_likely_duplicate_on_purpose(self):
        stored = self.duplicates()
        created = self.studio(mode='candidate_decide', candidate_id=stored['Invoice Desk Plus']['id'],
                              decision='approve', confirmed=True, override_duplicate=True)
        self.assertEqual(created['state'], 'created')
        self.assertTrue(created['decision']['override_duplicate'])
        self.assertEqual(created['duplicate_override']['duplicate_of'], stored['Invoice Desk Plus']['duplicate_of'])

    def test_a_permanent_exclusion_cannot_be_overridden(self):
        stored = self.duplicates()['Invoice Desk Pro']
        stored['excluded_by'] = 'invoice'
        self.ventures.save_candidate(stored)
        with self.assertRaisesRegex(ValueError, 'rejected or excluded'):
            self.studio(mode='candidate_decide', candidate_id=stored['id'], decision='approve', confirmed=True,
                        override_duplicate=True)
        with self.assertRaisesRegex(ValueError, 'Only a proposed'):
            self.studio(mode='candidate_update', candidate_id=stored['id'], pricing='$1')

    # --- Limits ----------------------------------------------------------

    def test_plain_drafts_hold_no_place_but_scheduled_drafts_do(self):
        draft = self.attach_group(self.create(request_id='draft-one', name='Draft one'))
        self.assertEqual(self.ventures._places_taken('org'), 0)
        self.configure(draft, daily_ticks=True)
        self.assertEqual(self.ventures._places_taken('org'), 1)
        self.ventures.action(dict(mode='resume', organization_id='org', venture_id=draft['id']), 'admin', 'admin')
        self.assertEqual(self.ventures._places_taken('org'), 1)

    def test_two_drafts_resumed_at_once_cannot_both_take_the_last_place(self):
        self.ventures.action(dict(mode='limits', organization_id='org', max_active_ventures=1), 'admin', 'admin')
        drafts = [self.create(request_id='race-' + str(index), name='Race ' + str(index)) for index in range(2)]
        barrier, outcomes = threading.Barrier(2), []

        def resume(record):
            barrier.wait()
            try:
                self.ventures.action(dict(mode='resume', organization_id='org', venture_id=record['id']),
                                     'admin', 'admin')
                outcomes.append('resumed')
            except ValueError:
                outcomes.append('refused')
        threads = [threading.Thread(target=resume, args=(record,)) for record in drafts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(sorted(outcomes), ['refused', 'resumed'])
        self.assertEqual(len(self.ventures._active('org')), 1)

    def test_daily_reviews_cannot_start_without_a_team(self):
        record = self.create()
        with self.assertRaisesRegex(ValueError, 'need a team'):
            self.configure(record, daily_ticks=True)
        configuration = json.loads(json.dumps(self.configuration))
        configuration['simulation']['daily_ticks'] = True
        with self.assertRaisesRegex(ValueError, 'need a team'):
            self.create(request_id='scheduled-draft', name='Scheduled', configuration=configuration)
        self.assertTrue(self.configure(self.attach_group(record), daily_ticks=True)['next_at'])

    # --- Settlement and same-day records ---------------------------------

    def test_a_failed_pitch_settlement_saves_no_partial_reviews(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')], scout_consult=False)
        self.scout_with(fixtures.candidate('Carrier Desk', 'N1'))
        pitch, = self.pitch_runs()
        with closing(self.service.connect()) as db, db:
            db.execute("CREATE TRIGGER refuse_settled BEFORE INSERT ON venture_studio_runs "
                       "WHEN json_extract(NEW.data, '$.kind')='pitch' AND json_extract(NEW.data, '$.state')='ready' "
                       "BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        reviews = [self.review('Carrier Desk', role, 'back') for role in ('investor', 'customer')]
        failed = self.complete_pitch(pitch, reviews, ['investor', 'customer'])
        self.assertEqual(failed['state'], 'needs_attention')
        self.assertFalse(self.stored_candidates()[0].get('stakeholder_reviews'))
        with closing(self.service.connect()) as db, db:
            db.execute('DROP TRIGGER refuse_settled')
        settled = self.studio(mode='studio_recover', run_id=pitch['id'], inspected=True)
        self.assertEqual(settled['state'], 'ready')
        self.assertEqual(len(self.stored_candidates()[0]['stakeholder_reviews']), 2)

    def pitched(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')], scout_consult=False)
        self.scout_with(fixtures.candidate('Carrier Desk', 'N1'))
        pitch, = self.pitch_runs()
        return pitch, self.stored_candidates()[0], [self.review('Carrier Desk', role, 'back')
                                                    for role in ('investor', 'customer')]

    def holding(self, identity):
        """An operator's request in progress on one idea, on its own thread."""
        entered, leave = threading.Event(), threading.Event()

        def hold():
            with self.service.operation('candidate:' + identity):
                entered.set()
                leave.wait(10)
        thread = threading.Thread(target=hold)
        thread.start()
        self.assertTrue(entered.wait(5))
        self.addCleanup(thread.join, 5)
        self.addCleanup(leave.set)
        return leave, thread

    def test_a_pitch_waits_for_an_operator_and_keeps_their_edit(self):
        pitch, stored, reviews = self.pitched()
        leave, thread = self.holding(stored['id'])
        waiting = self.complete_pitch(pitch, reviews, ['investor', 'customer'])
        self.assertEqual(waiting['state'], 'meeting')  # Not a failure, and nothing written.
        self.assertFalse(self.stored_candidates()[0].get('stakeholder_reviews'))
        edited = self.ventures.get_candidate('org', stored['id'])
        edited['pricing'] = '$650 per site per month'
        self.ventures.save_candidate(edited)
        leave.set()
        thread.join(5)
        self.ventures.advance()
        settled = self.stored_candidates()[0]
        self.assertEqual(self.pitch_runs()[0]['state'], 'ready')
        self.assertEqual(settled['pricing'], '$650 per site per month')
        self.assertEqual(len(settled['stakeholder_reviews']), 2)

    def test_recovering_a_pitch_during_an_edit_does_not_lose_the_run(self):
        pitch, stored, reviews = self.pitched()
        job = next(item for item in self.jobs if item['id'] == pitch['job_id'])
        job.update(state='artifact_ready', result='```json\n' + json.dumps(dict(reviews=reviews, summary='s'))
                   + '\n```', contributions=[dict(profile_id=pitch['panel']['members'][role], name=role, round=1,
                                                  content='Mine.') for role in ('investor', 'customer')])
        saved = self.ventures._studio_run_record('org', pitch['id'])
        saved.update(state='needs_attention', error='Interrupted.')
        self.ventures.save_studio_run(saved)
        leave, thread = self.holding(stored['id'])
        with self.assertRaisesRegex(ValueError, 'Try again') as refused:
            self.studio(mode='studio_recover', run_id=pitch['id'], inspected=True)
        self.assertNotIn('does not validate', str(refused.exception))
        repaired = self.studio(mode='studio_repair', confirmed=True)
        self.assertFalse(repaired['healthy'])
        self.assertEqual(self.ventures._studio_run_record('org', pitch['id'])['state'], 'needs_attention')
        leave.set()
        thread.join(5)
        self.assertEqual(self.studio(mode='studio_recover', run_id=pitch['id'], inspected=True)['state'], 'ready')

    def autonomous_pitch(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.setup_panel(stakeholders=[dict(role='investor'), dict(role='customer')], scout_consult=False)
        self.studio(mode='studio_policy', studio_mode='autonomous', studio_auto_min_confidence=0)
        idea = fixtures.candidate('Carrier Desk', 'N1')
        idea['dissent'] = dict(verdict='advance', summary='It holds up.')
        self.scout_with(idea)
        pitch, = self.pitch_runs()
        return pitch, self.stored_candidates()[0], [self.review('Carrier Desk', role, 'back')
                                                    for role in ('investor', 'customer')]

    def test_votes_on_an_earlier_version_authorize_nothing(self):
        pitch, stored, reviews = self.autonomous_pitch()
        self.studio(mode='candidate_update', candidate_id=stored['id'], product='A different product entirely.')
        settled = self.complete_pitch(pitch, reviews, ['investor', 'customer'])
        self.assertEqual(settled['state'], 'ready')
        idea = self.ventures.get_candidate('org', stored['id'])
        self.assertEqual(idea['state'], 'proposed')
        self.assertFalse(idea.get('venture_id'))
        self.assertTrue(idea['panel']['outdated'])
        self.assertEqual(len(idea['stakeholder_reviews']), 2)  # Kept as feedback.
        self.assertIn('edited after the panel', idea['auto']['reason'])
        self.assertEqual(self.ventures._unpitched('org'), [idea['id']])  # Back in the queue.
        self.assertTrue(self.ventures._panel_feedback('org')[0]['on_earlier_version'])
        self.assertIn('earlier version', self.studio(mode='candidate_brief', candidate_id=idea['id'])['html'])

    def test_an_edit_after_the_review_outdates_it_until_undone(self):
        pitch, stored, reviews = self.pitched()
        self.complete_pitch(pitch, reviews, ['investor', 'customer'])
        identity = stored['id']
        self.assertTrue(venture_panel.panel_backs(self.ventures.get_candidate('org', identity)))
        same = self.studio(mode='candidate_update', candidate_id=identity, pricing=stored['pricing'])
        self.assertFalse(same['panel']['outdated'])  # Nothing of substance changed.
        edited = self.studio(mode='candidate_update', candidate_id=identity, pricing='$900 per site per month')
        self.assertTrue(edited['panel']['outdated'])
        self.assertFalse(venture_panel.panel_backs(edited))
        self.assertEqual(self.ventures._unpitched('org'), [identity])
        undone = self.studio(mode='candidate_update', candidate_id=identity, pricing=stored['pricing'])
        self.assertFalse(undone['panel']['outdated'])
        self.assertTrue(venture_panel.panel_backs(undone))
        self.assertEqual(self.ventures._unpitched('org'), [])

    def revision_round(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(fixtures.candidate('Carrier Desk', 'N1'))
        original = self.stored_candidates()[0]
        with self.news(fixtures.feed_item(venture_feeds.now().isoformat())):
            second = self.studio(mode='studio_run', kind='scout')
        revised = fixtures.candidate('Carrier Desk Pilot', 'N1')
        revised.update(revises='carrier desk', revision_note='Priced below the cheaper tool.')
        return original, second, revised

    def test_a_failed_revision_leaves_the_original_in_the_queue(self):
        original, second, revised = self.revision_round()
        with closing(self.service.connect()) as db, db:
            db.execute("CREATE TRIGGER refuse_revision BEFORE INSERT ON venture_candidates "
                       "WHEN json_extract(NEW.data, '$.name')='Carrier Desk Pilot' "
                       "BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        failed = self.complete_studio(second, {'candidates': [revised]})
        self.assertEqual(failed['state'], 'needs_attention')
        self.assertEqual(self.ventures.get_candidate('org', original['id'])['state'], 'proposed')
        with closing(self.service.connect()) as db, db:
            db.execute('DROP TRIGGER refuse_revision')
        self.assertEqual(self.studio(mode='studio_recover', run_id=second['id'], inspected=True)['state'], 'ready')
        states = {item['name']: item['state'] for item in self.stored_candidates()}
        self.assertEqual(states, {'Carrier Desk': 'superseded', 'Carrier Desk Pilot': 'proposed'})

    def test_a_revision_waits_while_an_operator_is_on_the_original(self):
        original, second, revised = self.revision_round()
        leave, thread = self.holding(original['id'])
        waiting = self.complete_studio(second, {'candidates': [revised]})
        self.assertEqual(waiting['state'], 'meeting')
        self.assertEqual([item['name'] for item in self.stored_candidates()], ['Carrier Desk'])
        leave.set()
        thread.join(5)
        self.ventures.advance()
        states = {item['name']: item['state'] for item in self.stored_candidates()}
        self.assertEqual(states, {'Carrier Desk': 'superseded', 'Carrier Desk Pilot': 'proposed'})

    def test_a_likely_duplicate_keeps_its_sources_through_compaction(self):
        runs = [self.ventures.save_studio_run(dict(
            id=uuid.uuid4().hex, organization_id='org', kind='scout', state='ready',
            evidence=dict(items=[dict(id='N1')]), prompt='frozen')) for _ in range(ventures.HISTORY_FULL_RUNS + 2)]
        self.ventures.save_candidate(dict(id=uuid.uuid4().hex, organization_id='org', run_id=runs[0]['id'],
                                          state='excluded', name='Likely duplicate', duplicate_of='other'))
        self.ventures.save_candidate(dict(id=uuid.uuid4().hex, organization_id='org', run_id=runs[1]['id'],
                                          state='excluded', name='Excluded', duplicate_of='other',
                                          excluded_by='crypto'))
        self.assertEqual(self.ventures._compact_history()['runs'], 1)
        self.assertIn('evidence', self.ventures._studio_run_record('org', runs[0]['id']))
        self.assertNotIn('evidence', self.ventures._studio_run_record('org', runs[1]['id']))

    def test_edit_history_stays_flat(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(fixtures.candidate('Carrier Desk', 'N1'))
        identity = self.stored_candidates()[0]['id']
        for price in range(25):
            self.studio(mode='candidate_update', candidate_id=identity, pricing='$' + str(price))
        stored = self.ventures.get_candidate('org', identity)
        self.assertEqual(len(stored['revisions']), 20)
        self.assertFalse(any('revisions' in entry['before'] for entry in stored['revisions']))
        self.assertEqual(stored['revisions'][-1]['before']['pricing'], '$23')
        self.assertLess(len(json.dumps(stored)), 20 * (len(json.dumps(stored['revisions'][0])) + 10) + 20_000)

    def test_a_history_that_already_nested_is_flattened(self):
        self.setup_studio_manual()
        self.add_venture_group(self.create())
        self.scout_with(fixtures.candidate('Carrier Desk', 'N1'))
        stored = self.stored_candidates()[0]
        nested = [dict(at='then', by='admin', before=dict(pricing='$1'))]
        for price in range(2, 6):
            nested.append(dict(at='then', by='admin', before=dict(pricing='$' + str(price),
                                                                  revisions=json.loads(json.dumps(nested)))))
        stored['revisions'] = nested
        self.ventures.save_candidate(stored)
        self.assertEqual(self.ventures._compact_history()['histories'], 1)
        flat = self.ventures.get_candidate('org', stored['id'])['revisions']
        self.assertEqual([entry['before'] for entry in flat], [dict(pricing='$' + str(price)) for price in range(1, 6)])
        self.assertEqual(self.ventures._compact_history()['histories'], 0)

    def test_a_scheduled_venture_keeps_its_team(self):
        record = self.attach_group(self.create())
        self.configure(record, daily_ticks=True)
        with self.assertRaisesRegex(ValueError, 'need a team'):
            self.ventures.action(dict(mode='configure', organization_id='org', venture_id=record['id'], group_id=''),
                                 'admin', 'admin')
        self.assertEqual(self.ventures.get(record['id'], 'org')['group_id'], 'g' * 32)
        configuration = json.loads(json.dumps(self.configuration))
        configuration['simulation']['daily_ticks'] = False
        cleared = self.ventures.action(dict(mode='configure', organization_id='org', venture_id=record['id'],
                                            group_id='', configuration=configuration), 'admin', 'admin')
        self.assertEqual((cleared['group_id'], cleared['configuration']['simulation']['daily_ticks']), ('', False))

    def test_a_repeated_run_returns_the_tick_its_review_points_at(self):
        record = self.attach_group(self.create())
        self.run_with_news(record)
        self.complete(record)
        settled = self.ventures.tick_for(record['id'], 'org', ventures.now().date().isoformat())
        self.ventures.save_tick(dict(settled, id=uuid.uuid4().hex, state='skipped'))
        returned = self.ventures.run(self.ventures.get(record['id'], 'org'), 'admin', fetch=False)
        self.assertEqual(returned['id'], settled['id'])

    def test_a_double_clicked_outcome_counts_once(self):
        record = self.attach_group(self.create())
        self.run_with_news(record)
        self.complete(record)
        with closing(self.service.connect()) as db:
            draft = json.loads(db.execute('SELECT data FROM venture_outreach').fetchone()[0])
        saved = self.ventures.get(record['id'], 'org')
        saved.update(state='qualified', qualified_at=ventures.now().isoformat())
        self.ventures.save(saved)

        def decide(**body):
            return self.ventures.action(dict(mode='outreach', organization_id='org', venture_id=record['id'],
                                             outreach_id=draft['id'], **body), 'admin', 'admin')
        decide(decision='approve')
        for _ in range(2):
            decide(decision='outcome', outcome='replied', note='Asked for a call.')
        self.assertEqual(self.ventures.get(record['id'], 'org')['signals']['replied'], 1)
        decide(decision='outcome', outcome='replied', note='A second reply.')
        self.assertEqual(self.ventures.get(record['id'], 'org')['signals']['replied'], 2)

    # --- Retention -------------------------------------------------------

    def test_old_history_is_compacted_without_losing_the_ledger(self):
        record = self.attach_group(self.create())
        old_day = (ventures.now() - timedelta(days=120)).date().isoformat()
        base = dict(venture_id=record['id'], organization_id='org', simulated_date=old_day, state='ready',
                    evidence=dict(items=[dict(id='N1')]), prompt='frozen', evidence_index=dict(N1='cited-item'),
                    delivered_evidence_ids=['N1'], evidence_item_ids=['cited-item'],
                    ledger=dict(capital_after=900), events=[dict(summary='kept')])
        old = self.ventures.save_tick(dict(base, id=uuid.uuid4().hex))
        recent = self.ventures.save_tick(dict(base, id=uuid.uuid4().hex, evidence_item_ids=['other-item'],
                                              simulated_date=ventures.now().date().isoformat()))
        stuck = self.ventures.save_tick(dict(base, id=uuid.uuid4().hex, state='needs_attention',
                                             evidence_item_ids=['other-item']))
        published = (ventures.now() - timedelta(days=200)).isoformat()
        with closing(self.service.connect()) as db, db:
            for identity in ('cited-item', 'newest-item'):
                db.execute('INSERT INTO venture_feed_items VALUES (?,?,?)',
                           ('org', identity, json.dumps(dict(id=identity, published=published))))
        def feed_items():
            with closing(self.service.connect()) as db:
                return {row[0] for row in db.execute('SELECT id FROM venture_feed_items')}
        self.ventures._prune('org')
        self.assertIn('cited-item', feed_items())  # A tick with its evidence still cites it.

        runs = [self.ventures.save_studio_run(dict(
            id=uuid.uuid4().hex, organization_id='org', kind='scout', state='ready', evidence=dict(items=[]),
            prompt='frozen', bundle={'a.md': 'x'})) for _ in range(ventures.HISTORY_FULL_RUNS + 2)]
        self.ventures.save_candidate(dict(id=uuid.uuid4().hex, organization_id='org', run_id=runs[0]['id'],
                                          state='proposed', name='Still open'))
        decided = dict(id=uuid.uuid4().hex, organization_id='org', state='rejected', name='Decided',
                       updated_at=(ventures.now() - timedelta(days=120)).isoformat(), revisions=[dict(before={})])
        with closing(self.service.connect()) as db, db:
            db.execute('INSERT INTO venture_candidates VALUES (?,?,?)',
                       (decided['id'], 'org', json.dumps(decided)))

        self.assertEqual(self.ventures._compact_history(), dict(ticks=1, runs=1, candidates=1, histories=0))
        self.assertEqual(self.ventures._compact_history(), dict(ticks=0, runs=0, candidates=0, histories=0))

        def tick(identity):
            with closing(self.service.connect()) as db:
                return json.loads(db.execute('SELECT data FROM venture_ticks WHERE id=?', (identity,)).fetchone()[0])
        compacted = tick(old['id'])
        for key in ('evidence', 'prompt', 'evidence_index', 'delivered_evidence_ids', 'evidence_item_ids'):
            self.assertNotIn(key, compacted)
        self.assertEqual((compacted['ledger'], compacted['events']), (base['ledger'], base['events']))
        self.assertIn('evidence', tick(recent['id']))
        self.assertIn('evidence', tick(stuck['id']))  # Recovery may still need it.
        self.assertIn('evidence', self.ventures._studio_run_record('org', runs[0]['id']))  # An open idea came from it.
        self.assertNotIn('evidence', self.ventures._studio_run_record('org', runs[1]['id']))
        self.assertIn('evidence', self.ventures._studio_run_record('org', runs[-1]['id']))
        self.assertNotIn('revisions', self.ventures.get_candidate('org', decided['id']))
        self.ventures._prune('org')
        self.assertNotIn('cited-item', feed_items())
        self.assertIn('newest-item', feed_items())  # Cursors rely on the highest row staying.

    def test_history_compaction_runs_in_the_background_on_its_interval(self):
        handed_off = []
        self.ventures.background = handed_off.append
        self.ventures._history_soon()
        self.assertEqual(handed_off, [])  # Not in the first minutes after a start.
        self.ventures._history_due = 0
        self.ventures._history_soon()
        self.ventures._history_soon()
        self.assertEqual(len(handed_off), 1)
        handed_off[0]()

    def test_the_dashboard_rebuilds_lessons_only_when_history_changed(self):
        record = self.create()
        with patch.object(self.ventures, '_studio_lessons', return_value=[]) as lessons:
            self.ventures._studio_lessons_summary('org')
            self.ventures._studio_lessons_summary('org')
            self.assertEqual(lessons.call_count, 1)
            self.ventures.save(self.ventures.get(record['id'], 'org'))
            self.ventures._studio_lessons_summary('org')
            self.assertEqual(lessons.call_count, 2)

    def test_poll_batches_rotate_so_later_rows_are_reached(self):
        for index in range(3):
            self.create(request_id='rotate-' + str(index), name='Rotate ' + str(index))
        query = 'SELECT rowid, data FROM ventures WHERE 1=1'
        first = {item['id'] for item in self.ventures._rotating('test', query, limit=2)}
        second = {item['id'] for item in self.ventures._rotating('test', query, limit=2)}
        self.assertEqual(len(first | second), 3)

    # --- Studio repair and reporting -------------------------------------

    def test_repair_rebuilds_a_studio_whose_group_and_agent_were_removed(self):
        self.ventures.herdr = fixtures.FakeHerdr('running')
        self.profiles[:] = [dict(id='z' * 32, name='Template', organization_id='org',
                                 project=str(self.projects), use_worktree=True, runtime='codex', model='m')]
        studio = self.studio(mode='studio_setup', confirmed=True)
        members = dict(studio['team']['members'])
        self.profiles[:] = [item for item in self.profiles if item['id'] != members['critic']]
        for group in self.groups:
            group['removed_at'] = ventures.now().isoformat()
        steps = self.studio(mode='studio_repair')['steps']
        self.assertEqual([step['step'] for step in steps], ['rebuild'])
        result = self.studio(mode='studio_repair', confirmed=True)
        self.assertTrue(result['healthy'], result['steps'])
        rebuilt = self.ventures._studio('org')
        self.assertNotEqual(rebuilt['team']['members']['critic'], members['critic'])
        self.assertEqual(rebuilt['team']['members']['scout'], members['scout'])
        self.assertEqual(rebuilt['team']['rebuild'], 1)
        live, = [group for group in self.groups if not group.get('removed_at')]
        self.assertEqual(live['id'], rebuilt['group_id'])
        self.assertIn(rebuilt['team']['members']['critic'], live['members'])

    def test_the_module_lists_every_notebook_it_created(self):
        self.setup_studio_manual()
        record = self.create()
        roots = self.ventures.notebook_roots()
        self.assertEqual(roots, {(self.projects / 'ventures' / 'studio-org').resolve(),
                                 (self.projects / record['notebook']).resolve()})
        # A stored path that leaves the projects folder is never listed.
        studio = self.ventures._studio('org')
        studio['notebook'] = '../outside'
        self.ventures.save_studio('org', studio)
        self.assertNotIn((self.projects / '..' / 'outside').resolve(), self.ventures.notebook_roots())

    def test_failure_categories_for_real_errors(self):
        for message, expected in (
                ('Herdr is stopped. Start Herdr, then run the review.', 'herdr'),
                ('5 hour usage limit reached. It will reset in 4 hours.', 'usage'),
                ('Provider rate limit exceeded; retry later.', 'usage'),
                ('You exceeded your current quota.', 'usage'),
                ('Meeting limit reached.', 'limits'),
                ('Shared session budget reached; waiting for sessions to finish', 'limits'),
                ('Selected OpenCode provider/model is unavailable: x/y.', 'model'),
                ('Codex is not signed in.', 'provider'),
                ('The agent needs a decision; no prompt input was sent.', 'meeting'),
                ('Skeptic: inspect the existing session before meeting preparation', 'team'),
                ('Venture Studio result is not valid JSON.', 'result'),
                ('Something nobody has seen.', 'meeting')):
            with self.subTest(message=message):
                self.assertEqual(venture_studio.classify(message), expected)

    def test_a_herdr_error_code_outranks_its_text(self):
        for code, expected in (('agent_blocked', 'permission'), ('agent_working', 'busy'),
                               ('server_not_running', 'herdr'), ('pane_not_found', 'team')):
            self.assertEqual(venture_studio.classify('Meeting budget text', code), expected)
        self.assertEqual(venture_studio.classify('Meeting limit reached.', 'unknown_code'), 'limits')

    def test_an_empty_panel_warns_about_nothing(self):
        view = self.ventures.panel_view('org', dict(panel=dict(enabled=False), team={}))
        self.assertEqual(view['warning'], '')

    def test_a_confidence_between_the_two_scales_is_refused(self):
        self.assertEqual((ventures._confidence(0.5), ventures._confidence(70.0), ventures._confidence(1.0)),
                         (50, 70, 100))
        with self.assertRaisesRegex(ValueError, 'whole number'):
            ventures._confidence(1.5)

    def test_a_manual_fetch_stops_at_its_shared_budget(self):
        clock = [0.0]
        deadlines = []

        def slow(url, **options):
            deadlines.append(options.get('deadline'))
            clock[0] += 10
            return dict(status=200, source=url, fetched_at=venture_feeds.now().isoformat(), items=[],
                        etag='', modified='')
        urls = ['https://news.example/' + name for name in ('a', 'b', 'c')]
        with patch.object(ventures.time, 'monotonic', lambda: clock[0]), \
                patch.object(venture_feeds, 'fetch', side_effect=slow):
            self.ventures._fetch_urls('org', urls, 20)
        self.assertEqual(deadlines, [20, 10])
        self.assertTrue(self.ventures._feed_state('org', urls[1]).get('last_fetch'))
        self.assertEqual(self.ventures._feed_state('org', urls[2]), {})  # Still due, with no error against it.


for _name, _value in vars(fixtures.VentureTests).items():
    if not _name.startswith(('test_', '__')):
        setattr(VentureHardeningTests, _name, _value)


class FeedLimitTests(unittest.TestCase):
    def test_a_slow_source_is_stopped_at_the_total_deadline(self):
        class Slow:
            status, headers = 200, {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _size):
                time.sleep(0.2)
                return b'<rss/>'

        class Opener:
            def open(self, _request, timeout):
                return Slow()
        with patch.object(venture_feeds, 'build_opener', return_value=Opener()):
            with self.assertRaisesRegex(ValueError, 'time limit'):
                venture_feeds.request('https://news.example/feed', {}, deadline=0.05,
                                      resolve=lambda host, port: [ipaddress.ip_address('93.184.216.34')])

    def test_the_deadline_stops_a_source_that_keeps_trickling_bytes(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        reader.settimeout(5)  # Each byte would reset a per-read timeout forever.
        stop = threading.Event()

        def trickle():
            while not stop.wait(0.01):
                try:
                    writer.send(b'x')
                except OSError:
                    return
        sender = threading.Thread(target=trickle, daemon=True)
        sender.start()
        self.addCleanup(sender.join, 2)
        self.addCleanup(stop.set)
        budget = venture_feeds._Deadline(0.1)
        budget.attach(reader)
        started = time.monotonic()
        try:
            while reader.recv(1):
                self.assertLess(time.monotonic() - started, 3)
        except OSError:
            pass
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(budget.expired)

    def test_a_slow_name_lookup_counts_against_the_deadline(self):
        def slow(host, port):
            time.sleep(0.7)
            return [ipaddress.ip_address('93.184.216.34')]
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'time limit'):
            venture_feeds.request('https://news.example/feed', {}, resolve=slow, deadline=0.05)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_a_failed_name_lookup_keeps_its_own_error(self):
        def missing(host, port):
            raise ValueError('Feed source could not be resolved.')
        with self.assertRaisesRegex(ValueError, 'could not be resolved'):
            venture_feeds.request('https://news.example/feed', {}, resolve=missing, deadline=1)

    def test_connection_attempts_share_the_deadline(self):
        attempts = []

        def refuse(target, timeout):
            attempts.append(timeout)
            time.sleep(min(timeout, 0.08))
            raise OSError('unreachable')
        budget = venture_feeds._Deadline(0.1)
        self.addCleanup(budget.cancel)
        connection = venture_feeds._PinnedConnection(
            'news.example', addresses=['2001:db8::' + str(n) for n in range(1, 9)], context=None, timeout=10,
            deadline=budget)
        started = time.monotonic()
        with patch.object(venture_feeds.socket, 'create_connection', refuse), self.assertRaises(OSError):
            connection.connect()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertLess(len(attempts), 8)  # The later addresses were not tried.
        self.assertTrue(all(timeout <= 0.1 for timeout in attempts))

    def test_keywords_match_whole_words_and_plurals(self):
        for text, word, expected in (('she said no', 'ai', False), ('new ai tools', 'ai', True),
                                     ('we generate leads', 'rate', False), ('the rate rose', 'rate', True),
                                     ('unpaid invoices', 'invoice', True), ('tax boxes', 'box', True),
                                     ('instant settlement rule', 'instant settlement', True)):
            with self.subTest(text=text, word=word):
                self.assertEqual(venture_feeds.has_keyword(text, word), expected)

    def test_fx_refuses_a_declaration_in_utf_16(self):
        body = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY y "z">]><root/>').encode('utf-16')
        with self.assertRaisesRegex(ValueError, 'FX source rejected'):
            venture_fx.parse_ecb(body)

    def test_the_brief_does_not_print_a_boolean_as_money(self):
        self.assertNotIn('1', venture_brief._money(True, 'EUR'))


@unittest.skipUnless(shutil.which('git'), 'Git required')
class NotebookCommitTests(unittest.TestCase):
    def notebook(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve() / 'projects'
        root.mkdir()
        return root, venture_notebook.provision(root, 'ventures/desk', dict(
            organization_id='org', name='Desk', thesis='A desk.', product='Desk',
            configuration=dict(simulation=dict(architecture='api'))))

    @staticmethod
    def tick(date='2026-11-15'):
        return dict(id='a' * 32, simulated_date=date,
                    ledger=dict(capital_before=100, burn=1, capital_after=99, score=None))

    def test_a_quiet_tick_leaves_a_local_note_uncommitted(self):
        root, target = self.notebook()
        note = '\nA local note from the operator.\n'
        with (target / 'product.md').open('a', encoding='utf-8', newline='') as stream:
            stream.write(note)
        self.assertTrue(venture_notebook.commit_tick(root, 'ventures/desk', self.tick(), [], {}))
        self.assertEqual(project_git.git(target, 'show', '--name-only', '--format=', 'HEAD').split(), ['ledger.csv'])
        self.assertNotIn('local note', project_git.git(target, 'show', 'HEAD:product.md'))
        self.assertIn('local note', (target / 'product.md').read_text(encoding='utf-8'))

    def test_a_tick_changing_an_edited_file_commits_only_its_own_lines(self):
        root, target = self.notebook()
        with (target / 'product.md').open('a', encoding='utf-8', newline='') as stream:
            stream.write('\nA local note from the operator.\n')
        changes = [dict(kind='add', title='Onboarding', reason='Cited change.', evidence=['N1'])]
        self.assertTrue(venture_notebook.commit_tick(root, 'ventures/desk', self.tick(), changes, {}))
        committed = project_git.git(target, 'show', 'HEAD:product.md')
        self.assertIn('Onboarding', committed)
        self.assertNotIn('local note', committed)
        working = (target / 'product.md').read_text(encoding='utf-8')
        self.assertIn('Onboarding', working)
        self.assertIn('local note', working)
        # Only the note is left as a local change, and nothing stays staged.
        self.assertEqual(project_git.git(target, 'diff', '--cached', '--name-only').strip(), '')
        self.assertEqual(project_git.git(target, 'diff', '--name-only').split(), ['product.md'])
        self.assertEqual(venture_notebook.commit_tick(root, 'ventures/desk', self.tick(), changes, {}), '')

    def test_a_tick_keeps_what_an_operator_staged(self):
        root, target = self.notebook()
        with (target / 'product.md').open('a', encoding='utf-8', newline='') as stream:
            stream.write('\nA staged note from the operator.\n')
        project_git.git(target, 'add', 'product.md')
        with (target / 'product.md').open('a', encoding='utf-8', newline='') as stream:
            stream.write('An unstaged note.\n')
        changes = [dict(kind='add', title='Onboarding', reason='Cited change.', evidence=['N1'])]
        self.assertTrue(venture_notebook.commit_tick(root, 'ventures/desk', self.tick(), changes, {}))
        committed = project_git.git(target, 'show', 'HEAD:product.md')
        self.assertIn('Onboarding', committed)
        self.assertNotIn('note', committed)
        staged = project_git.git(target, 'show', ':product.md')
        self.assertIn('staged note', staged)
        self.assertIn('Onboarding', staged)  # Committing it later does not undo the tick.
        self.assertNotIn('unstaged', staged)
        difference = project_git.git(target, 'diff', '--cached', '--', 'product.md')
        self.assertIn('+A staged note', difference)
        self.assertNotIn('-- ' + '2026', difference)
        self.assertFalse([line for line in difference.splitlines() if line.startswith('-') and 'Onboarding' in line])
        working = (target / 'product.md').read_text(encoding='utf-8')
        for text in ('staged note', 'unstaged note', 'Onboarding'):
            self.assertIn(text, working)

    def test_a_tick_leaves_a_staged_removal_alone(self):
        root, target = self.notebook()
        project_git.git(target, 'rm', '--cached', '-q', 'product.md')
        changes = [dict(kind='add', title='Onboarding', reason='Cited change.', evidence=['N1'])]
        self.assertTrue(venture_notebook.commit_tick(root, 'ventures/desk', self.tick(), changes, {}))
        self.assertIn('Onboarding', project_git.git(target, 'show', 'HEAD:product.md'))
        self.assertEqual(project_git.git(target, 'ls-files', '--', 'product.md').strip(), '')

    def test_a_retry_after_a_crash_still_commits_the_tick(self):
        root, target = self.notebook()
        ledger = target / 'ledger.csv'
        ledger.write_text(venture_notebook._csv_row(ledger.read_text(encoding='utf-8'), [
            '2026-11-15', 100, 1, 99, '', 'a' * 32]), encoding='utf-8', newline='')
        self.assertTrue(venture_notebook.commit_tick(root, 'ventures/desk', self.tick(), [], {}))
        self.assertIn('2026-11-15', project_git.git(target, 'show', 'HEAD:ledger.csv'))
        self.assertEqual(ledger.read_text(encoding='utf-8').count('2026-11-15'), 1)

    def test_only_gateway_files_enter_a_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / 'projects'
            root.mkdir()
            target = venture_notebook.provision(root, 'ventures/desk', dict(
                organization_id='org', name='Desk', thesis='A desk.', product='Desk',
                configuration=dict(simulation=dict(architecture='api'))))
            (target / 'stray.txt').write_text('left by an agent', encoding='utf-8')
            (target / 'staged.txt').write_text('staged by an agent', encoding='utf-8')
            project_git.git(target, 'add', 'staged.txt')
            tick = dict(id='a' * 32, simulated_date='2026-11-15',
                        ledger=dict(capital_before=100, burn=1, capital_after=99, score=None))
            self.assertTrue(venture_notebook.commit_tick(root, 'ventures/desk', tick, [], {}))
            self.assertEqual(project_git.git(target, 'show', '--name-only', '--format=', 'HEAD').split(),
                             ['ledger.csv'])
            # Nothing of the gateway's changed: no empty or agent-only commit.
            self.assertEqual(venture_notebook.commit_tick(root, 'ventures/desk', tick, [], {}), '')
            path = venture_notebook.write_outreach(root, 'ventures/desk', dict(
                id='b' * 32, date='2026-11-15', persona='cfo', subject='Hello', language='en', stage='survey',
                evidence=['N1'], score=1, body='Hello {{name}}'))
            self.assertEqual(project_git.git(target, 'show', '--name-only', '--format=', 'HEAD').split(), [path])


if __name__ == '__main__':
    unittest.main()
