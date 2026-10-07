"""Worker instructions are small routing documents, not flattened manuals."""
from pathlib import Path
import re
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import integration_coordinator
from contributions import task_tool_guidance


class WorkerSkillTests(unittest.TestCase):
    def test_local_bundle_is_inside_checkout_and_rejects_modified_cache(self):
        from worker_guidance import local_bundle
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            subprocess.run(['git', 'init', str(checkout)], check=True, capture_output=True)
            bundle = local_bundle(checkout)
            self.assertTrue(bundle.is_relative_to(checkout.resolve()))
            self.assertTrue((bundle / 'references/tools.md').is_file())
            self.assertEqual(local_bundle(checkout), bundle)
            (bundle / 'references/tools.md').write_text('modified')
            with self.assertRaisesRegex(ValueError, 'cache was modified'):
                local_bundle(checkout)

    def test_real_git_checkout_stays_clean_when_guidance_is_copied(self):
        from worker_guidance import local_bundle
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            subprocess.run(['git', 'init', str(checkout)], check=True, capture_output=True)
            (checkout / '.gitignore').write_text('.ci-cache/\n')
            def status():
                return subprocess.check_output(['git', '-c', 'safe.directory=' + checkout.as_posix(),
                                                '-C', str(checkout), 'status', '--porcelain'])
            before = status()
            bundle = local_bundle(checkout)
            self.assertEqual(status(), before)
            entry = integration_coordinator.merge_skill(checkout)
            self.assertIn((bundle / 'references/tools.md').as_posix(), entry)
            self.assertNotIn('/opt/herdr-web', entry)

    def test_local_exclude_preserved_and_recovery_quarantines_damage(self):
        from worker_guidance import local_bundle
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            subprocess.run(['git', 'init', str(checkout)], check=True, capture_output=True)
            exclude = checkout / '.git/info/exclude'
            exclude.write_bytes(b'# custom\nlocal-only')
            bundle = local_bundle(checkout)
            original = (bundle / 'references/tools.md').read_bytes()
            (bundle / 'references/tools.md').write_text('damaged')
            with self.assertRaisesRegex(ValueError, 'modified'):
                local_bundle(checkout)
            self.assertEqual(local_bundle(checkout, recover=True), bundle)
            self.assertEqual((bundle / 'references/tools.md').read_bytes(), original)
            quarantined = list(bundle.parent.glob('*.quarantine-*'))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual((quarantined[0] / 'references/tools.md').read_text(), 'damaged')
            local_bundle(checkout, recover=True)
            self.assertEqual(len(list(bundle.parent.glob('*.quarantine-*'))), 1)
            self.assertEqual(exclude.read_bytes().count(b'/.ci-cache/herdr-guidance/'), 1)
            self.assertTrue(exclude.read_bytes().startswith(b'# custom\nlocal-only\n'))
            self.assertEqual(subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain']), b'')

    def test_repair_requires_idle_exclusive_session_without_queued_work(self):
        import threading
        from unittest.mock import Mock
        from organizations import OrganizationStore
        store = object.__new__(OrganizationStore)
        store.lock = threading.RLock()
        store.agent_locks = {}
        path = str(Path.cwd())
        run = dict(id='launch', kind='launch', profile_id='worker', state='persona_sent', worktree_path=path)
        store.job_records = Mock(return_value=[run])
        store.active_checkouts = Mock(return_value=[('worker', 'launch', 'Worker', path)])
        with patch('collaboration.current_run') as current, patch('worker_guidance.local_bundle', return_value=Path(path)) as bundle:
            self.assertEqual(store.repair_guidance('worker', path), path)
            current.assert_called_once_with(store, run)
            bundle.assert_called_once_with(path, recover=True)
            current.side_effect = ValueError('session changed')
            with self.assertRaisesRegex(ValueError, 'session changed'):
                store.repair_guidance('worker', path)
            self.assertEqual(bundle.call_count, 1)
            store.job_records.return_value = [run, dict(kind='chat', state='queued', profile_id='worker')]
            with self.assertRaisesRegex(ValueError, 'queued'):
                store.repair_guidance('worker', path)
            store.job_records.return_value = [run]
            store.active_checkouts.return_value.append(('other', 'other-launch', 'Other', path))
            with self.assertRaisesRegex(ValueError, 'exclusive'):
                store.repair_guidance('worker', path)
            self.assertEqual(bundle.call_count, 1)
            # A workspace launch shares the checkout; recovery refuses it.
            store.job_records.return_value = [dict(run, worktree_path=None, source_project=path)]
            store.active_checkouts.return_value = [('worker', 'launch', 'Worker', path)]
            with self.assertRaisesRegex(ValueError, 'isolated worker worktree'):
                store.repair_guidance('worker', path)
            self.assertEqual(bundle.call_count, 1)

    def test_project_discovery_entries_resolve_to_the_canonical_bundle(self):
        root = Path(__file__).parents[1]
        canonical = (root / 'web/gateway/skills/herdr-worktree-integration/SKILL.md').resolve()
        # Codex and OpenCode discover .agents/skills; Claude Code discovers
        # .claude/skills. Both entries must reach the same maintained bundle
        # rather than growing a second copy of the procedures.
        for directory in ('.agents', '.claude'):
            bridge = root / directory / 'skills/herdr-worktree-integration/SKILL.md'
            text = bridge.read_text(encoding='utf-8')
            self.assertEqual(re.search(r'^name: (\S+)$', text, re.M).group(1),
                             bridge.parent.name)
            target = re.search(r'\]\(([^)]+)\)', text).group(1)
            self.assertEqual((bridge.parent / target).resolve(), canonical)
            self.assertTrue((bridge.parent / target).is_file())

    def test_all_references_are_accessible_absolute_files_in_the_bundle(self):
        root = Path(integration_coordinator.__file__).parent / 'skills/herdr-worktree-integration'
        entry = integration_coordinator.merge_skill()
        paths = re.findall(r'\]\(([^)]+)\)', entry)
        self.assertEqual(len(paths), 5)
        for value in paths:
            path = Path(value)
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.is_relative_to(root.resolve()))
            self.assertTrue(path.is_file())
        self.assertLess(len(entry), 4000)  # Leave room for the actual task and schema.

    def test_detailed_references_are_not_read_or_inlined_into_prompts(self):
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path.name != 'SKILL.md':
                raise AssertionError('Reference was eagerly loaded: ' + str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            self.assertIn('references/tools.md', integration_coordinator.merge_skill())
            self.assertLess(len(task_tool_guidance()), 1000)

    def test_missing_references_fail_before_dispatch(self):
        with patch.object(Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(ValueError, 'reference is missing'):
                integration_coordinator.merge_skill()


if __name__ == '__main__':
    unittest.main()
