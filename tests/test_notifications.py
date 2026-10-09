import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from notifications import Notifications, OutboxPump, record, schema
from runtime_events import RuntimeEvents


class NotificationTests(unittest.TestCase):
    def test_each_consumer_receives_coalesced_hints_independently(self):
        hub = Notifications()
        first, second = hub.subscribe(['task']), hub.subscribe(['task'])
        for _ in range(20):
            hub.publish('task', 'candidate', 'org')
        self.assertEqual(first.wait(0), {('task', 'candidate', 'org')})
        # Clearing/draining one consumer cannot consume the other's wakeup.
        first.clear()
        self.assertEqual(second.wait(0), {('task', 'candidate', 'org')})
        hub.publish('task', 'arrived-during-reconcile', 'org')
        self.assertTrue(first.wait(0))
        self.assertTrue(second.wait(0))

    def test_overflow_requests_bounded_full_reconciliation(self):
        hub = Notifications()
        mailbox = hub.subscribe(['task'])
        for i in range(2000):
            hub.publish('task', str(i))
        result = mailbox.wait(0)
        self.assertLessEqual(len(result), 256)
        self.assertIn(('resync', '', ''), result)
        hub.publish('resync')
        self.assertEqual(mailbox.wait(0), {('resync', '', '')})

    def test_browser_cursor_recovers_restart_and_missing_history(self):
        hub = Notifications()
        self.assertTrue(hub.changes(epoch='old', timeout=0)['reset'])
        for i in range(300):
            hub.publish('task', str(i))
        self.assertTrue(hub.changes(after=1, epoch=hub.epoch, timeout=0)['reset'])
        changes = hub.changes(after=299, epoch=hub.epoch, timeout=0)
        self.assertFalse(changes['reset'])
        self.assertEqual(changes['changes'][0]['key'], '299')
        with self.assertRaises(ValueError):
            hub.changes(after=-1)

    def test_outbox_is_transactional_and_replays_after_dispatch_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                schema(db)
                db.commit()
                record(db, 'task', 'rolled-back', 'org')
                db.rollback()
                with db:
                    record(db, 'task', 'committed', 'org')
            hub = Notifications()
            mailbox = hub.subscribe()
            pump = OutboxPump(hub, [path])
            original = hub.publish
            def interrupted(*args):
                original(*args)
                raise RuntimeError('crash after publish before acknowledgement')
            hub.publish = interrupted
            with self.assertRaises(RuntimeError):
                pump.drain()
            hub.publish = original
            pump.drain()
            self.assertEqual(mailbox.wait(0), {('task', 'committed', 'org')})
            pump.drain()
            self.assertFalse(mailbox.wait(0))
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT delivered FROM notification_outbox').fetchall(), [(1,)])


class RuntimeEventTests(unittest.TestCase):
    def setUp(self):
        self.hub = Notifications()
        self.command = Mock(return_value=dict(agents=[], panes=[], workspaces=[]))
        self.monitor = RuntimeEvents(self.hub, self.command, path='unused-test-socket')

    def acknowledge(self):
        self.monitor.consume(dict(id='gateway-runtime', result=dict(type='subscription_started')))

    def test_snapshot_follows_ack_and_never_replays_event_payload(self):
        self.assertFalse(self.monitor.reconcile())
        self.command.assert_not_called()
        self.acknowledge()
        self.monitor.reconcile()
        self.assertEqual(self.monitor.snapshot()['state'], 'live')
        self.monitor.consume(dict(event=dict(type='pane.agent_status_changed', state='done')))
        self.monitor.reconcile()
        self.assertEqual(self.command.call_count, 2)
        self.command.assert_called_with('api', 'snapshot', timeout=5)

    def test_events_during_snapshot_require_another_authoritative_read(self):
        self.acknowledge()
        def snapshot(*args, **kwargs):
            self.monitor.consume(dict(event=dict(type='pane.closed')))
            return dict(agents=[], panes=[], workspaces=[])
        self.command.side_effect = snapshot
        self.assertFalse(self.monitor.reconcile())
        self.assertNotEqual(self.monitor.snapshot()['state'], 'live')
        self.command.side_effect = None
        self.assertTrue(self.monitor.reconcile())

    def test_lost_events_and_disconnect_gate_effects_until_resnapshot(self):
        self.acknowledge()
        self.monitor.reconcile()
        self.monitor.guard()
        with self.assertRaises(ValueError):
            self.monitor.consume(dict(error=dict(code='events_lost')))
        self.monitor.invalidate('events_lost', disconnected=True)
        with self.assertRaisesRegex(ValueError, 'reconciled'):
            self.monitor.guard()
        self.assertFalse(self.monitor.reconcile())
        self.acknowledge()
        self.assertTrue(self.monitor.reconcile())
        self.monitor.guard()

    def test_polling_fallback_and_invalid_snapshot_do_not_claim_live_state(self):
        self.monitor.invalidate('unsupported transport', disconnected=True)
        self.monitor.guard()  # Fresh per-operation CLI checks remain usable.
        self.assertEqual(self.monitor.snapshot()['state'], 'polling')
        self.acknowledge()
        self.command.return_value = {'unexpected': 'shape'}
        with self.assertRaises(ValueError):
            self.monitor.reconcile()
        self.assertNotEqual(self.monitor.snapshot()['state'], 'live')

    def test_pane_status_subscriptions_require_ids_and_second_snapshot(self):
        stream = Mock()
        self.monitor.stream = stream
        self.monitor.subscribe(stream, [])
        request = json.loads(stream.sendall.call_args.args[0])
        self.assertNotIn('pane.agent_status_changed', [s['type'] for s in request['params']['subscriptions']])
        self.acknowledge()
        self.command.return_value = dict(snapshot=dict(agents=[], panes=[dict(id='w1:p1')], workspaces=[]))
        self.assertFalse(self.monitor.reconcile())
        stream.shutdown.assert_called_once()
        self.monitor.subscribe(stream, self.monitor.subscribed_panes)
        request = json.loads(stream.sendall.call_args.args[0])
        self.assertIn(dict(type='pane.agent_status_changed', pane_id='w1:p1'), request['params']['subscriptions'])
        with self.assertRaises(ValueError):
            self.monitor.guard()
        self.acknowledge()
        self.assertTrue(self.monitor.reconcile())
        self.command.return_value = dict(agents=[], panes=[], workspaces=[])
        self.assertFalse(self.monitor.reconcile())
        self.acknowledge()
        self.assertTrue(self.monitor.reconcile())

    def test_wrong_acknowledgement_is_rejected(self):
        with self.assertRaises(ValueError):
            self.monitor.consume(dict(id='other', result=dict(type='subscription_started')))
        self.command.assert_not_called()
