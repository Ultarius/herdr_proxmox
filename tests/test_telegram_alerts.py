"""Authoritative alert collection excludes prompts and terminal output."""
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from telegram_alerts import MAX_ALERTS, collect_alerts, key


class TelegramAlertTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(profiles=[dict(id='p1', name='Worker')], jobs=[], member_states={})
        self.tasks = []
        self.org = Mock()
        self.org.snapshot.side_effect = lambda **kwargs: self.data
        self.contributions = Mock()
        def connect():
            db = sqlite3.connect(':memory:')
            db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.executemany('INSERT INTO tasks VALUES (?,?)', [(t['id'], json.dumps(t)) for t in self.tasks])
            db.commit()
            return db
        self.contributions.connect.side_effect = connect
        self.coordinator = Mock()
        self.coordinator.snapshot.return_value = dict(events=[])

    def collect(self):
        return collect_alerts(self.org, self.contributions, self.coordinator)

    def test_agent_blocks_and_runtime_outages_preserve_receipts(self):
        self.data['member_states']['p1'] = dict(status='blocked', run_id='run1')
        alerts = self.collect()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['key'], key('agent', 'run1', 'blocked'))
        self.org.snapshot.assert_called_once_with(live_status=True)
        for status in ('unknown', 'unavailable'):
            self.data['member_states']['p1']['status'] = status
            self.data['jobs'] = [dict(id='job1', state='waiting_for_input')]
            offline = self.collect()
            self.assertEqual(len(offline), 1)
            self.assertIn(alerts[0]['key'], offline.preserve_prefixes)
        self.data['member_states']['p1']['status'] = 'working'
        self.assertNotIn(alerts[0]['key'], self.collect().preserve_prefixes)

    def test_job_attention_does_not_copy_reports(self):
        self.data['jobs'] = [dict(id=str(i), state=state, error='PRIVATE', prompt='PRIVATE', result='PRIVATE')
                             for i, state in enumerate(('waiting_for_input', 'needs_attention', 'uncertain'))]
        self.data['jobs'].append(dict(id='blocked', state='failed', delivery=dict(stage='blocked', preview='PRIVATE')))
        self.data['jobs'].append(dict(id='okay', state='answered', result='PRIVATE'))
        alerts = self.collect()
        self.assertEqual(len(alerts), 4)
        self.assertNotIn('PRIVATE', json.dumps(alerts))

    def test_task_attention_uses_candidate_without_error_text(self):
        candidate = 'a' * 40
        self.tasks = [dict(id='t1', title='Change settings', state='review_ready',
                           repository='owner/project', head_sha=candidate, automation_error='PRIVATE',
                           delivery_workflow=dict(state='attention', error='PRIVATE'),
                           pr_repair=dict(id='repair1', state='needs_attention', candidate=candidate, message='PRIVATE'),
                           builds={candidate: dict(blocker='policy_changed', error='PRIVATE')})]
        alerts = self.collect()
        self.assertEqual(len(alerts), 5)
        self.assertNotIn('PRIVATE', json.dumps(alerts))
        keys = {a['key'] for a in alerts}
        self.tasks[0].update(title='Renamed', updated_at='later')
        self.assertEqual(keys, {a['key'] for a in self.collect()})
        self.tasks[0]['head_sha'] = 'b' * 40
        self.assertNotIn(key('task-review', 't1', candidate), {a['key'] for a in self.collect()})
        self.contributions.snapshot.assert_not_called()

    def test_automatic_and_terminal_tasks_do_not_request_review(self):
        self.tasks = [dict(id='auto', state='review_ready', auto_publish=True),
                      dict(id='done', state='completed', automation_error='old')]
        self.assertEqual(self.collect(), [])

    def test_integration_transitions_have_distinct_keys(self):
        event = dict(id='i1', repository='owner/project', name='Worker', state='blocked', reason='PRIVATE')
        self.coordinator.snapshot.return_value = dict(events=[event])
        blocked = self.collect()
        self.assertEqual(len(blocked), 1)
        self.assertNotIn('PRIVATE', json.dumps(blocked))
        for state in ('needs_attention', 'validation_pending', 'validation_failed'):
            event['state'] = state
            self.assertNotEqual(blocked[0]['key'], self.collect()[0]['key'])
        event['state'] = 'completed'
        self.assertEqual(self.collect(), [])

    def test_bounded_output_preserves_omitted_issues_and_rotates(self):
        self.data['jobs'] = [dict(id=str(i), state='needs_attention') for i in range(MAX_ALERTS + 5)]
        alerts = self.collect()
        self.assertEqual(len(alerts), MAX_ALERTS)
        all_keys = {key('job', str(i), 'needs_attention') for i in range(MAX_ALERTS + 5)}
        self.assertEqual(set(alerts.preserve_prefixes), all_keys - {a['key'] for a in alerts})
        self.assertEqual({a['key'] for a in alerts} | {a['key'] for a in self.collect()}, all_keys)

    def test_incomplete_namespaces_preserve_unseen_receipts(self):
        self.tasks = [dict(id=str(i), state='implementing') for i in range(1001)]
        self.coordinator.snapshot.return_value = dict(events=[dict(id=str(i), state='completed') for i in range(100)])
        alerts = self.collect()
        self.assertIn('task-', alerts.preserve_prefixes)
        self.assertIn('integration:', alerts.preserve_prefixes)
