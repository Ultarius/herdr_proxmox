import base64
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from ssh_access import SshAccess

TYPE = b'ssh-ed25519'
KEY = 'ssh-ed25519 ' + base64.b64encode(struct.pack('>I', len(TYPE)) + TYPE + struct.pack('>I', 32) + bytes(range(32))).decode() + ' test-client'


class SshAccessTests(unittest.TestCase):
    def test_add_preserves_existing_keys_and_deduplicates_comments(self):
        with tempfile.TemporaryDirectory() as home, patch('ssh_access.subprocess.run') as run:
            run.return_value.returncode = 0
            access = SshAccess(home)
            self.assertEqual(access.snapshot(), {'key_count': 0})
            self.assertEqual(access.add({'public_key': KEY}), {'key_count': 1, 'added': True})
            self.assertEqual(access.add({'public_key': KEY.replace('test-client', 'renamed')}), {'key_count': 1, 'added': False})
            self.assertEqual((Path(home) / '.ssh/authorized_keys').read_text(), KEY + '\n')
            self.assertEqual(run.call_args.args[0][:2], ['ssh-keygen', '-l'])
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_rejects_private_keys_options_multiple_keys_and_invalid_material(self):
        with tempfile.TemporaryDirectory() as home:
            access = SshAccess(home)
            for key in ('-----BEGIN OPENSSH PRIVATE KEY-----', 'command="x" ' + KEY, KEY + '\n' + KEY, 'ssh-rsa !!!', 'ssh-rsa ' + KEY.split()[1]):
                with self.assertRaises(ValueError):
                    access.add({'public_key': key})
            with patch('ssh_access.subprocess.run') as run:
                run.return_value.returncode = 1
                with self.assertRaises(ValueError):
                    access.add({'public_key': KEY})
            self.assertFalse((Path(home) / '.ssh').exists())

    def test_preserves_file_without_trailing_newline(self):
        with tempfile.TemporaryDirectory() as home, patch('ssh_access.subprocess.run') as run:
            run.return_value.returncode = 0
            directory = Path(home) / '.ssh'
            directory.mkdir()
            (directory / 'authorized_keys').write_text('# existing operator comment')
            SshAccess(home).add({'public_key': KEY})
            self.assertEqual((directory / 'authorized_keys').read_text(), '# existing operator comment\n' + KEY + '\n')
