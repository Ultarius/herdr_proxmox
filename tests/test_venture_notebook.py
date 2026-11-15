from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import project_git
import venture_notebook


@unittest.skipUnless(shutil.which('git'), 'Git required')
class VentureNotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'projects').mkdir()

    def venture(self):
        return dict(organization_id='org', name='Settlement Desk', thesis='A settlement API.',
                    configuration=dict(simulation=dict(architecture='api')), product='Settlement API')

    def tick(self):
        return dict(id='a' * 32, simulated_date='2026-11-15',
                    ledger=dict(capital_before=100000, burn=1000, capital_after=99000, score=None))

    def test_provision_and_tick_commit_once(self):
        target = venture_notebook.provision(self.root / 'projects', 'ventures/settlement', self.venture())
        self.assertTrue((target / 'venture.md').is_file())
        self.assertTrue((target / 'product.md').is_file())
        self.assertTrue((target / 'outreach').is_dir())
        tick = self.tick()
        changes = [dict(kind='add', title='Japanese onboarding', reason='Cited bank change.', evidence=['N1'])]
        first = venture_notebook.commit_tick(self.root / 'projects', 'ventures/settlement', tick, changes,
                                             dict(monthly=48000, reason='Cited segment.'))
        self.assertTrue(first)
        second = venture_notebook.commit_tick(self.root / 'projects', 'ventures/settlement', tick, changes,
                                              dict(monthly=48000, reason='Cited segment.'))
        self.assertEqual(second, '')
        lines = (target / 'ledger.csv').read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn('Japanese onboarding', (target / 'product.md').read_text(encoding='utf-8'))
        self.assertEqual(len(project_git.git(target, 'log', '--format=%H').strip().splitlines()), 2)

    def test_provision_refuses_existing_content_and_path_escape(self):
        target = self.root / 'projects' / 'ventures' / 'used'
        target.mkdir(parents=True)
        (target / 'already.txt').write_text('x', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'already contains'):
            venture_notebook.provision(self.root / 'projects', 'ventures/used', self.venture())
        with self.assertRaisesRegex(ValueError, 'inside the projects'):
            venture_notebook.provision(self.root / 'projects', '../escape', self.venture())


if __name__ == '__main__':
    unittest.main()
