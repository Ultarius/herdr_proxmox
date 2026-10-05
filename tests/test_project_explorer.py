import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from project_explorer import browse, ANCHORED_ACCESS

class ExplorerTests(unittest.TestCase):
    @unittest.skipUnless(ANCHORED_ACCESS, 'Linux descriptor access required')
    def test_listing_and_bounded_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'repo').mkdir()
            (root / 'notes.md').write_text('hello', encoding='utf-8')
            (root / 'large.txt').write_bytes(b'a' * 70000)
            (root / 'binary').write_bytes(b'abc\x00def')
            listing = browse(root, {})
            self.assertEqual(listing['entries'][0]['name'], 'repo')
            self.assertEqual(browse(root, {'path': 'notes.md'})['content'], 'hello')
            self.assertTrue(browse(root, {'path': 'large.txt'})['truncated'])
            self.assertTrue(browse(root, {'path': 'binary'})['binary'])
            self.assertEqual(browse(root, {'path': 'repo'})['entries'], [])
            for path in ['../secret', '/etc/passwd', 'C:\\secret', '..\\secret']:
                with self.assertRaises(ValueError):
                    browse(root, {'path': path})

    @unittest.skipUnless(ANCHORED_ACCESS, 'Linux descriptor access required')
    def test_symlink_is_listed_but_not_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'real').mkdir()
            try:
                (root / 'link').symlink_to(root / 'real', target_is_directory=True)
            except OSError:
                self.skipTest('Symlink creation unavailable')
            self.assertEqual(next(e for e in browse(root, {})['entries'] if e['name'] == 'link')['kind'], 'link')
            with self.assertRaises(ValueError):
                browse(root, {'path': 'link'})

    def test_unsupported_platform_fails_closed(self):
        with patch('project_explorer.ANCHORED_ACCESS', False), patch('project_explorer.os.open') as opened:
            with self.assertRaisesRegex(ValueError, 'Linux gateway'):
                browse(Path('.'), {})
            opened.assert_not_called()

    @unittest.skipUnless(ANCHORED_ACCESS, 'Linux descriptor access required')
    def test_ancestor_swap_cannot_escape_for_preview_or_listing(self):
        for selected in ('repo/note.txt', 'repo'):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'projects'
                root.mkdir()
                repo = root / 'repo'
                repo.mkdir()
                (repo / 'note.txt').write_text('inside', encoding='utf-8')
                outside = Path(directory) / 'outside'
                outside.mkdir()
                (outside / 'secret.txt').write_text('secret', encoding='utf-8')
                (outside / 'note.txt').write_text('outside', encoding='utf-8')
                original = os.open
                swapped = False
                def swap(path, flags, **kwargs):
                    nonlocal swapped
                    fd = original(path, flags, **kwargs)
                    if str(path) == 'repo' and not swapped:
                        swapped = True
                        repo.rename(root / 'previous')
                        repo.symlink_to(outside, target_is_directory=True)
                    return fd
                with patch('project_explorer.os.open', side_effect=swap):
                    result = browse(root, {'path': selected})
                if selected.endswith('.txt'):
                    self.assertEqual(result['content'], 'inside')
                else:
                    self.assertEqual([e['name'] for e in result['entries']], ['note.txt'])

    @unittest.skipUnless(ANCHORED_ACCESS, 'Linux descriptor access required')
    def test_preopen_ancestor_and_root_links_are_rejected(self):
        for replace_root in (False, True):
            with self.subTest(root=replace_root), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'projects'
                (root / 'repo').mkdir(parents=True)
                outside = Path(directory) / 'outside'
                outside.mkdir()
                (outside / 'note.txt').write_text('secret', encoding='utf-8')
                target = root if replace_root else root / 'repo'
                target.rename(Path(directory) / 'previous')
                target.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    browse(root, {'path': 'repo/note.txt'})

    @unittest.skipUnless(ANCHORED_ACCESS, 'Linux descriptor access required')
    def test_final_link_swap_is_rejected_and_handles_are_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'note.txt'
            file.write_text('inside', encoding='utf-8')
            outside = root / 'secret.txt'
            outside.write_text('secret', encoding='utf-8')
            original = os.open
            descriptors = []
            def swap(path, flags, **kwargs):
                if str(path) == 'note.txt':
                    file.unlink()
                    file.symlink_to(outside)
                fd = original(path, flags, **kwargs)
                descriptors.append(fd)
                return fd
            with patch('project_explorer.os.open', side_effect=swap):
                with self.assertRaises(ValueError):
                    browse(root, {'path': 'note.txt'})
            for fd in descriptors:
                with self.assertRaises(OSError):
                    os.fstat(fd)
