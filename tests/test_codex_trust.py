from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from codex_trust import approved_roots, ensure_trusted, trust_root


class CodexTrustTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.home, self.projects = root / 'home', root / 'projects'
        self.repository = self.projects / 'ventures' / 'studio'
        (self.repository / '.git').mkdir(parents=True)
        (self.repository / 'docs').mkdir()
        self.config = self.home / '.codex' / 'config.toml'

    def trusted(self):
        return tomllib.loads(self.config.read_text(encoding='utf-8'))['projects']

    def ensure(self, project, *, approved=None, **options):
        return ensure_trusted(project, self.projects, self.home,
                              approved={self.repository} if approved is None else approved, **options)

    def test_the_repository_root_is_trusted_once_and_other_settings_are_kept(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('model = "gpt-6-luna"\n\n[projects."/srv/other"]\ntrust_level = "trusted"',
                               encoding='utf-8')
        # Codex asks about the repository root, also from a folder inside it.
        self.assertEqual(trust_root(self.repository / 'docs', self.projects), self.repository)
        self.assertTrue(self.ensure(self.repository / 'docs'))
        self.assertFalse(self.ensure(self.repository))
        settings = tomllib.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(settings['model'], 'gpt-6-luna')
        self.assertEqual(settings['projects'], {'/srv/other': dict(trust_level='trusted'),
                                                str(self.repository): dict(trust_level='trusted')})

    def test_a_folder_without_a_repository_is_trusted_itself_and_a_missing_file_is_created(self):
        folder = self.projects / 'notes'
        folder.mkdir()
        self.assertTrue(self.ensure(folder, approved={folder}))
        self.assertEqual(self.trusted(), {str(folder): dict(trust_level='trusted')})

    def test_an_operator_s_choice_and_an_unreadable_file_are_left_alone(self):
        self.config.parent.mkdir(parents=True)
        untrusted = '[projects.' + json.dumps(str(self.repository)) + ']\ntrust_level = "untrusted"\n'
        self.config.write_text(untrusted, encoding='utf-8')
        self.assertFalse(self.ensure(self.repository))
        self.assertEqual(self.config.read_text(encoding='utf-8'), untrusted)
        self.config.write_text('model = \n', encoding='utf-8')
        self.assertFalse(self.ensure(self.repository))
        self.assertEqual(self.config.read_text(encoding='utf-8'), 'model = \n')

    def test_nothing_outside_managed_projects_is_trusted(self):
        for folder in (self.projects, self.home, Path(self.temp.name)):
            self.assertIsNone(trust_root(folder, self.projects))
            self.assertFalse(self.ensure(folder, approved={folder}))
        self.assertFalse(self.config.exists())

    def test_only_explicit_roots_are_trusted_and_a_parent_refusal_is_preserved(self):
        self.assertFalse(ensure_trusted(self.repository, self.projects, self.home))
        other = self.projects / 'other'
        other.mkdir()
        approved = approved_roots('["ventures/studio"]', self.projects)
        self.assertFalse(self.ensure(other, approved=approved))
        with self.assertRaisesRegex(ValueError, 'JSON list of project roots'):
            approved_roots('[".."]', self.projects)
        self.config.parent.mkdir(parents=True)
        refused = '[projects.' + json.dumps(str(self.repository.parent)) + ']\ntrust_level = "untrusted"\n'
        self.config.write_bytes(refused.encode())
        self.assertFalse(self.ensure(self.repository, approved=approved))
        self.assertEqual(self.config.read_bytes(), refused.encode())

    def test_custom_codex_home_with_non_ascii_project_and_crlf_configuration(self):
        directory = self.home / 'codex-custom'
        directory.mkdir(parents=True)
        config = directory / 'config.toml'
        original = b'model = "example"\r\n'
        config.write_bytes(original)
        folder = self.projects / 'R\N{NIGHT WITH STARS}'
        folder.mkdir()
        self.assertTrue(self.ensure(folder, approved={folder}, codex_home=directory))
        self.assertFalse(self.config.exists())
        self.assertTrue(config.read_bytes().startswith(original))
        self.assertEqual(tomllib.loads(config.read_text(encoding='utf-8'))['projects'][str(folder)],
                         dict(trust_level='trusted'))

    @unittest.skipUnless(shutil.which('git'), 'Git unavailable')
    def test_a_linked_checkout_uses_its_approved_main_repository(self):
        checkout = self.projects / 'linked'
        for args in [('init',), ('-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                                 'commit', '--allow-empty', '-m', 'Initial'),
                     ('worktree', 'add', '--detach', str(checkout))]:
            subprocess.run(['git', '-C', str(self.repository), *args], check=True, capture_output=True)
        self.assertEqual(trust_root(checkout, self.projects), self.repository)
        self.assertTrue(self.ensure(checkout))
        self.assertEqual(self.trusted(), {str(self.repository): dict(trust_level='trusted')})


if __name__ == '__main__':
    unittest.main()
