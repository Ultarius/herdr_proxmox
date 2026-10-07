from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from build_queue import BuildQueue


class BuildQueueTests(unittest.TestCase):
    def test_dedup_survives_restart_and_only_one_build_can_be_claimed(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = BuildQueue(directory)
            run = dict(id='a'*32, repository='repo', target='b'*40, event_id='event')
            saved, created = queue.enqueue(run)
            self.assertTrue(created)
            reopened = BuildQueue(directory)
            duplicate, created = reopened.enqueue(dict(run, id='c'*32, event_id='second-event'))
            self.assertFalse(created)
            self.assertEqual(duplicate['id'], saved['id'])
            self.assertEqual(duplicate['event_ids'], ['event', 'second-event'])
            self.assertEqual(reopened.claim()['id'], run['id'])
            self.assertIsNone(queue.claim())
            with self.assertRaisesRegex(ValueError, 'queue is full'):
                queue.enqueue(dict(run, id='d'*32, target='e'*40))
            self.assertEqual(queue.interrupt_running()[0]['state'], 'interrupted')
            self.assertIsNone(queue.claim())
            retried, created = queue.enqueue(dict(run, id='f'*32), retry=True)
            self.assertTrue(created)
            self.assertEqual(retried['attempt'], 2)
            repeated, created = queue.enqueue(dict(run, id='0'*32), retry=True)
            self.assertFalse(created)
            self.assertEqual(repeated['id'], retried['id'])
            queue.finish(dict(retried, state='failed'))
            next_run, created = queue.enqueue(dict(run, id='d'*32, target='e'*40))
            self.assertTrue(created)
            queue.finish(dict(next_run, state='cancelled'))
            self.assertIsNone(queue.claim())
