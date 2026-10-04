from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from dashboard_access import DashboardAccess, configured_bind


class DashboardAccessTests(unittest.TestCase):
    def test_guards_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'access.json'
            keys = Mock()
            schedule = Mock()
            keys.snapshot.return_value = {'key_count': 0}
            access = DashboardAccess(path, keys, '0.0.0.0', schedule)
            with self.assertRaises(ValueError): access.update({'mode': 'ssh', 'tunnel_ready': True})
            keys.snapshot.return_value = {'key_count': 1}
            with self.assertRaises(ValueError): access.update({'mode': 'ssh'})
            with self.assertRaises(ValueError): access.update({'mode': 'other'})
            self.assertFalse(path.exists())
            access.update({'mode': 'ssh', 'tunnel_ready': True})
            schedule.assert_called_once()
            self.assertEqual(configured_bind(path, '0.0.0.0'), '127.0.0.1')
            with self.assertRaises(ValueError): access.update({'mode':'lan'})
            restarted = DashboardAccess(path, keys, '127.0.0.1', schedule)
            self.assertEqual(restarted.snapshot()['mode'], 'ssh')
            restarted.update({'mode': 'lan'})
            self.assertEqual(configured_bind(path, '127.0.0.1'), '0.0.0.0')

    def test_unchanged_mode_does_not_schedule_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'access.json'
            schedule = Mock()
            access = DashboardAccess(path, Mock(), '0.0.0.0', schedule)
            access.update({'mode':'lan'})
            schedule.assert_not_called()
            self.assertFalse(path.exists())
