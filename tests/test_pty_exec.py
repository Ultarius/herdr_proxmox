"""Exercise the Linux controlling-terminal wrapper in a real child session."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(sys.platform == 'linux', 'Requires Linux PTY ioctls')
class PtyExecTests(unittest.TestCase):
    def test_child_acquires_controlling_terminal_and_executes_with_environment(self):
        import pty
        master, slave = pty.openpty()
        try:
            environment = dict(os.environ, HERDR_PTY_TEST='inherited')
            code = ('import os; '
                    'assert os.tcgetpgrp(0) == os.getpgrp(); '
                    'assert os.environ["HERDR_PTY_TEST"] == "inherited"; '
                    'print("controlling terminal acquired")')
            wrapper = Path(__file__).parents[1] / 'web/gateway/pty_exec.py'
            result = subprocess.run([sys.executable, str(wrapper), sys.executable, '-c', code],
                                    stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    start_new_session=True, env=environment, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn(b'controlling terminal acquired', result.stdout)
        finally:
            os.close(master)
            os.close(slave)
