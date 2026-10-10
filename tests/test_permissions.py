import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from permissions import accessible_paths, prepare_permissions, permission_mode

class PermissionTests(unittest.TestCase):
    def test_policies_are_per_agent_and_defaults_do_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            profile = {'id': 'testprofile', 'runtime': 'opencode'}
            self.assertEqual(prepare_permissions(profile, home / 'outputs', home), [])
            self.assertFalse((home / '.config').exists())
            for mode in ('dashboard_outputs', 'full_autonomy'):
                args = prepare_permissions(dict(profile, permission_mode=mode), home / 'outputs', home)
                self.assertEqual(args, ['--agent', 'herdr-dashboard-testprofile'])
                content = (home / '.config/opencode/agents/herdr-dashboard-testprofile.md').read_text(encoding='utf-8')
                rule = json.loads(next(line.split('permission: ', 1)[1] for line in content.splitlines() if line.startswith('permission: ')))
                if mode == 'full_autonomy':
                    self.assertEqual(rule, 'allow')
                else:
                    self.assertEqual(set(rule['external_directory']), {str(home / 'outputs' / folder / '**') for folder in ('chat-replies', 'discussion-artifacts')})
            self.assertFalse((home / '.config/opencode/opencode.json').exists())

    def test_unsupported_runtime_or_mode_is_rejected(self):
        with self.assertRaises(ValueError): permission_mode({'permission_mode': 'invalid'}, 'opencode')
        with self.assertRaises(ValueError): permission_mode({'permission_mode': 'full_autonomy'}, 'codex')

    def test_custom_globs_combine_with_outputs_and_work_with_cli_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for mode in ('default', 'dashboard_outputs'):
                profile = dict(id='custom', runtime='opencode', permission_mode=mode,
                               accessible_paths=['~/shared/**', '/home/herdr/worktrees/**'])
                self.assertTrue(prepare_permissions(profile, home / 'outputs', home))
                content = (home / '.config/opencode/agents/herdr-dashboard-custom.md').read_text(encoding='utf-8')
                rule = json.loads(next(line.split('permission: ', 1)[1] for line in content.splitlines() if line.startswith('permission: ')))
                self.assertEqual(rule['external_directory']['~/shared/**'], 'allow')
                self.assertEqual(rule['external_directory']['/home/herdr/worktrees/**'], 'allow')
                self.assertEqual(len(rule['external_directory']), 2 if mode == 'default' else 4)
                self.assertNotIn('edit', rule)

    def test_path_patterns_are_validated(self):
        self.assertEqual(accessible_paths({'accessible_paths': ['~/docs/**', '~/docs/**']}, 'opencode'), ['~/docs/**'])
        for paths in ('/path/*', ['relative/*'], ['/path/*\npermission: allow'], [None], ['/path/*'] * 21):
            with self.assertRaises(ValueError): accessible_paths({'accessible_paths': paths}, 'opencode')
        with self.assertRaises(ValueError): accessible_paths({'accessible_paths': ['~/docs/**']}, 'codex')

    def test_variant_is_agent_scoped_without_widening_default_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            profile = dict(id='variant', runtime='opencode', provider='opencode-go', model='test', reasoning='high')
            self.assertEqual(prepare_permissions(profile, home, home), ['--agent', 'herdr-dashboard-variant'])
            content = (home / '.config/opencode/agents/herdr-dashboard-variant.md').read_text()
            self.assertIn('variant: "high"', content)
            self.assertIn('model: "opencode-go/test"', content)
            self.assertIn('permission: {}', content)

    def test_a_run_that_must_save_output_is_granted_it_in_any_mode(self):
        # The gateway tells a discussion exactly where to write its artifact and
        # transcript. A read-only meeting with CLI defaults must not stall on an
        # interactive permission prompt the gateway can never answer.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            profile = {'id': 'group', 'runtime': 'opencode'}
            args = prepare_permissions(profile, home / 'outputs', home,
                                       output_directories=('discussion-artifacts',))
            self.assertEqual(args, ['--agent', 'herdr-dashboard-group'])
            rule = json.loads(next(line.split('permission: ', 1)[1]
                                   for line in (home / '.config/opencode/agents/herdr-dashboard-group.md').read_text().splitlines()
                                   if line.startswith('permission: ')))
            self.assertEqual(set(rule['external_directory']),
                             {str(home / 'outputs' / 'discussion-artifacts' / '**')})
            # A chat is granted the reply directory instead, not both.
            prepare_permissions(dict(profile, id='chat'), home / 'outputs', home,
                                output_directories=('chat-replies',))
            rule = json.loads(next(line.split('permission: ', 1)[1]
                                   for line in (home / '.config/opencode/agents/herdr-dashboard-chat.md').read_text().splitlines()
                                   if line.startswith('permission: ')))
            self.assertEqual(set(rule['external_directory']),
                             {str(home / 'outputs' / 'chat-replies' / '**')})
            # Without an output contract a default profile still writes nothing.
            self.assertEqual(prepare_permissions(profile, home / 'outputs', home), [])
