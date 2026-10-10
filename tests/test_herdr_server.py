from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from herdr_errors import HerdrError, parse_error
from herdr_server import HerdrServer


class HerdrServerTests(unittest.TestCase):
    def test_running_session_does_not_launch_duplicate(self):
        with patch('herdr_server.subprocess.run') as run:
            self.assertTrue(HerdrServer(Mock()).start({})['already_running'])
            run.assert_not_called()

    def test_start_is_allowlisted_and_uses_user_manager(self):
        command = Mock(side_effect=[ValueError('server_not_running'), {}])
        with patch('herdr_server.os.getuid', return_value=1000, create=True), patch('herdr_server.subprocess.run') as run:
            run.return_value.returncode = 0
            self.assertEqual(HerdrServer(command).start({})['state'], 'running')
            self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'start', 'herdr-session.service'])
            self.assertEqual(run.call_args.kwargs['env']['XDG_RUNTIME_DIR'], '/run/user/1000')
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_rejects_arbitrary_commands_and_reports_start_failure(self):
        server = HerdrServer(Mock(side_effect=ValueError('server_not_running')))
        with self.assertRaises(ValueError): server.start({'command':'anything'})
        with patch('herdr_server.os.getuid', return_value=1000, create=True), patch('herdr_server.subprocess.run') as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, 'Could not start'): server.start({})

    def test_unknown_cli_failure_is_not_treated_as_stopped(self):
        with self.assertRaises(ValueError): HerdrServer(Mock(side_effect=ValueError('broken protocol'))).status()

    def test_structured_server_not_running_envelope_is_treated_as_stopped(self):
        # `command()` now raises the parsed error: the friendly message no
        # longer contains the code, so matching only the message text broke
        # the smoke test's stopped-server check.
        error = parse_error('{"id":"cli:workspace:list","error":{"code":"server_not_running",'
                            '"message":"no herdr server is running at /run/herdr.sock; run `herdr` to start or attach it"}}')
        self.assertEqual(HerdrServer(Mock(side_effect=error)).status(), {'state': 'stopped'})
        with self.assertRaises(ValueError):
            HerdrServer(Mock(side_effect=HerdrError('protocol is being upgraded', 'protocol_mismatch'))).status()
