"""Worker instructions are small routing documents, not flattened manuals."""
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import integration_coordinator
from contributions import task_tool_guidance


class WorkerSkillTests(unittest.TestCase):
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
