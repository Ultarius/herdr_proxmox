import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from project_explorer import browse

class ExplorerTests(unittest.TestCase):
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
