"""Named operator identities and the root management CLI."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from operators import Operators, token_digest

admin_spec = importlib.util.spec_from_file_location('operator_admin', Path(__file__).parents[1] / 'install/operator-admin.py')
admin = importlib.util.module_from_spec(admin_spec)
admin_spec.loader.exec_module(admin)


class OperatorTests(unittest.TestCase):
    def test_tokens_resolve_to_named_identities_and_never_to_unknown_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'operators.json'
            path.write_text(json.dumps({'operators': [
                {'name': 'damien', 'role': 'admin', 'token_sha256': token_digest('secret-a')},
                {'name': 'kit', 'role': 'operator', 'token_sha256': token_digest('secret-b')},
                {'name': 'broken', 'role': 'root', 'token_sha256': token_digest('secret-c')}]}))
            operators = Operators(path)
            self.assertEqual(operators.identify('secret-a'), {'name': 'damien', 'role': 'admin'})
            self.assertEqual(operators.identify('secret-b'), {'name': 'kit', 'role': 'operator'})
            self.assertIsNone(operators.identify('secret-c'))
            self.assertIsNone(operators.identify('secret-d'))
            self.assertIsNone(operators.identify(''))
            self.assertIsNone(operators.identify(None))
            self.assertEqual(operators.snapshot(),
                             [{'name': 'damien', 'role': 'admin'}, {'name': 'kit', 'role': 'operator'}])

    def test_missing_or_corrupt_state_denies_everyone(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            missing = Operators(root / 'nope.json')
            self.assertIsNone(missing.identify('anything'))
            self.assertEqual(missing.snapshot(), [])
            (root / 'broken.json').write_text('not json')
            self.assertEqual(Operators(root / 'broken.json').snapshot(), [])

    def test_cli_manages_tokens_without_storing_them(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'operators.json'
            operators = Operators(path)

            def run(*argv):
                output = io.StringIO()
                with patch.object(admin, 'PATH', path), \
                        patch.object(admin.os, 'geteuid', return_value=0, create=True), \
                        patch.object(sys, 'argv', ['herdr-operator', *argv]), \
                        redirect_stdout(output):
                    admin.main()
                return output.getvalue().strip()

            token = run('add', 'damien', '--role', 'admin')
            self.assertNotIn(token, path.read_text())
            self.assertEqual(operators.identify(token), {'name': 'damien', 'role': 'admin'})
            kit = run('add', 'kit')
            self.assertEqual(operators.identify(kit), {'name': 'kit', 'role': 'operator'})
            with self.assertRaises(SystemExit):
                run('add', 'damien')
            with self.assertRaises(SystemExit):
                run('add', 'bad name')
            with self.assertRaises(SystemExit):
                run('remove', 'nobody')
            self.assertEqual(run('list').split(), ['damien', 'admin', 'kit', 'operator'])
            rotated = run('rotate', 'damien')
            self.assertIsNone(operators.identify(token))
            self.assertEqual(operators.identify(rotated), {'name': 'damien', 'role': 'admin'})
            run('remove', 'kit')
            self.assertEqual(operators.snapshot(), [{'name': 'damien', 'role': 'admin'}])
            self.assertEqual(json.loads(path.read_text())['operators'][0].keys(),
                             {'name', 'role', 'token_sha256'})


if __name__ == '__main__':
    unittest.main()
