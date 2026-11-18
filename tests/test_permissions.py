import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from permissions import accessible_paths, prepare_permissions, permission_mode

class PermissionTests(unittest.TestCase):
    def test_managed_grants_cover_only_exact_execution_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            owned = home / 'storage' / 'objects' / ('a' * 64) / 'data'
            pool = home / 'storage' / 'objects' / ('b' * 64) / 'data'
            profile = dict(id='managed', runtime='opencode')
            prepare_permissions(profile, home / 'outputs', home,
                                managed_directories=(str(owned), str(pool), str(owned)), managed_root=home / 'storage')
            contents = (home / '.config/opencode/agents/herdr-dashboard-managed.md').read_text()
            rule = json.loads(next(line.removeprefix('permission: ') for line in contents.splitlines() if line.startswith('permission: ')))
            self.assertEqual(set(rule['external_directory']), {str(owned / '**'), str(pool / '**')})
            self.assertNotIn(str(home / 'storage' / '**'), rule['external_directory'])
            with self.assertRaisesRegex(ValueError, 'exact absolute'):
                prepare_permissions(profile, home, home, managed_directories=(str(owned / '*'),))
            with self.assertRaisesRegex(ValueError, 'outside the verified'):
                prepare_permissions(profile, home, home, managed_directories=(str(home / 'other'),), managed_root=home / 'storage')

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
        for runtime in ('opencode', 'claude', 'agy'):
            with self.assertRaises(ValueError):
                permission_mode({'permission_mode': 'codex_no_approval'}, runtime)

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

    def test_codex_may_write_its_dashboard_outputs_without_asking(self):
        # Codex sandboxes writes to its workspace. A meeting artifact or chat
        # reply lives under the dashboard's output folder, so without an added
        # root every write there stops on an approval prompt nobody answers.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            outputs = home / 'outputs'
            owned = home / 'storage' / 'objects' / ('a' * 64) / 'data'
            owned.mkdir(parents=True)
            profile = dict(id='investor', runtime='codex', model='gpt-5.5')
            args = prepare_permissions(profile, outputs, home,
                                       output_directories=('chat-replies', 'discussion-artifacts', 'chat-replies'),
                                       managed_directories=(str(owned),), managed_root=home / 'storage')
            self.assertEqual(args, ['--add-dir', str(outputs / 'chat-replies'),
                                    '--add-dir', str(outputs / 'discussion-artifacts'),
                                    '--add-dir', str(owned)])
            self.assertTrue((outputs / 'discussion-artifacts').is_dir())
            # Only the output folders, never the gateway's own state beside them.
            self.assertNotIn(str(outputs), args)
            # Codex never gets an OpenCode agent file.
            self.assertFalse((home / '.config/opencode').exists())
            # Nothing to save means nothing is added.
            self.assertEqual(prepare_permissions(profile, outputs, home), [])
            with self.assertRaisesRegex(ValueError, 'Unknown dashboard output'):
                prepare_permissions(profile, outputs, home, output_directories=('../state',))
            with self.assertRaisesRegex(ValueError, 'outside the verified'):
                prepare_permissions(profile, outputs, home, managed_directories=(str(home / 'other'),),
                                    managed_root=home / 'storage')

    def test_unattended_codex_keeps_its_sandbox_and_assisted_setup_gets_the_exact_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            outputs = home / 'outputs'
            folder = 'environment-assistance/' + 'a' * 32
            profile = dict(id='helper', runtime='codex', permission_mode='codex_no_approval')
            args = prepare_permissions(profile, outputs, home, output_directories=(folder,))
            self.assertEqual(args, ['--sandbox', 'workspace-write', '--ask-for-approval', 'never',
                                    '--add-dir', str(outputs / folder)])
            self.assertTrue((outputs / folder).is_dir())
            self.assertFalse((home / '.codex').exists())
            with self.assertRaisesRegex(ValueError, 'Unknown dashboard output'):
                prepare_permissions(profile, outputs, home, output_directories=('environment-assistance',))

    def test_codex_rejects_linked_assistance_output_ancestors(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            outputs = home / 'outputs'
            outputs.mkdir()
            unrelated = home / 'unrelated'
            unrelated.mkdir()
            try:
                (outputs / 'environment-assistance').symlink_to(unrelated, target_is_directory=True)
            except OSError:
                self.skipTest('Creating a directory symlink is unavailable.')
            profile = dict(id='helper', runtime='codex')
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                prepare_permissions(profile, outputs, home, output_directories=('environment-assistance/' + 'b' * 32,))
            self.assertEqual(list(unrelated.iterdir()), [])

    def test_a_crafted_profile_identity_cannot_escape_the_agent_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for crafted in ('../../evil', 'a/b', '..', 'a\\\\b'):
                with self.assertRaises(ValueError):
                    prepare_permissions({'id': crafted, 'runtime': 'opencode'},
                                        home / 'outputs', home)
            # A generated identity still works, and nothing was written outside.
            self.assertEqual(prepare_permissions({'id': 'ab12', 'runtime': 'opencode'},
                                                home / 'outputs', home), [])
            self.assertFalse((home / 'evil.md').exists())
            self.assertFalse((home / 'a').exists())

    def test_output_grants_do_not_add_source_checkout_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            prepare_permissions(dict(id='source', runtime='opencode'), home / 'outputs', home,
                                output_directories=('discussion-artifacts',))
            content = (home / '.config/opencode/agents/herdr-dashboard-source.md').read_text(encoding='utf-8')
            rule = json.loads(next(line.removeprefix('permission: ') for line in content.splitlines()
                                   if line.startswith('permission: ')))
            self.assertEqual(rule, {'external_directory': {str(home / 'outputs/discussion-artifacts/**'): 'allow'}})
