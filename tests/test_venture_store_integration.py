"""The studio against the real organization store, with simulated agents.

The other venture tests use a stand-in store. This one covers the seams that
only exist with the real one: request replay, meeting preparation, the
facilitator's worktree, advisors on a meeting, and the transcript gate.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from contributions import Contributions
import organizations as gateway
import test_organizations as store_fixtures
import test_ventures as venture_fixtures
import venture_feeds
import ventures


class RunningHerdr:
    @staticmethod
    def status():
        return dict(state='running')


class VentureStoreIntegrationTests(unittest.TestCase):
    tearDown = store_fixtures.OrganizationTests.tearDown
    command = store_fixtures.OrganizationTests.command
    action = store_fixtures.OrganizationTests.action
    organization = store_fixtures.OrganizationTests.organization
    drain = store_fixtures.OrganizationTests.drain

    def setUp(self):
        store_fixtures.OrganizationTests.setUp(self)
        self.original = self.store.command
        self.store.command = self.simulate
        self.store.runtime_ready = lambda runtime: None if runtime in ('codex', 'opencode') else 'Not installed.'
        self.answers = {}
        self.service = Contributions(self.root / 'data/tasks.sqlite3', self.projects, self.store, MagicMock())
        self.addCleanup(self.service.close)
        self.ventures = self.service.ventures
        self.ventures.background = lambda work: work()
        self.ventures.herdr = RunningHerdr()
        self.org = self.organization()

    def studio(self, **body):
        return self.ventures.action(dict(organization_id=self.org, **body), 'admin', 'admin')

    def simulate(self, *args, **kwargs):
        if args[:2] == ('worktree', 'create'):
            self.calls.append((args, kwargs))
            source = args[args.index('--cwd') + 1] if '--cwd' in args else None
            path, branch = args[args.index('--path') + 1], args[args.index('--branch') + 1]
            repository = source or next(item for item in args if isinstance(item, str) and Path(item).is_dir())
            subprocess.run(['git', '-C', repository, 'worktree', 'add', '-b', branch, path], check=True,
                           capture_output=True)
            return {'root_pane': {'pane_id': f'w{900 + len(self.calls)}:p1'}}
        response = self.original(*args, **kwargs)
        if args[:2] == ('agent', 'prompt') and '--wait' in args and 'Then write' in args[3]:
            self.answer_meeting(args[3])
        return response

    def answer_meeting(self, prompt):
        """What a chair does: the artifact, the transcript, then the receipt."""
        kind = 'pitch' if 'VENTURE STUDIO PITCH' in prompt else 'scout'
        artifact = Path(re.search(r'exactly (?:this new file: )?(.+?\.md)', prompt)[1])
        transcript = Path(re.search(r'Also write (.+?discussion\.json)', prompt)[1])
        receipt = Path(re.search(r'Then write (.+?receipt\.json)', prompt)[1])
        speakers = json.loads(re.search(r'Profile IDs: (.+?)\. Record', prompt)[1])
        self.seen_speakers = speakers
        artifact.write_text('```json\n' + json.dumps(self.answers[kind]) + '\n```\n', encoding='utf-8')
        parts = [dict(profile_id=identity, name='simulated', round=1, content='My own view, with evidence.')
                 for identity in speakers.values()]
        transcript.write_text(json.dumps(dict(contributions=parts)) + '\n', encoding='utf-8')
        job_id = receipt.parent.name
        current = next(job for job in self.store.snapshot()['jobs'] if job['id'] == job_id)
        receipt.write_text(json.dumps(dict(
            discussion_id=job_id, attempt_id=(current.get('submission') or {}).get('attempt_id'),
            artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            transcript_sha256=hashlib.sha256(transcript.read_bytes()).hexdigest())), encoding='utf-8')

    def settle(self, run):
        self.drain()
        self.ventures.advance()
        with closing(self.service.connect()) as db:
            return json.loads(db.execute('SELECT data FROM venture_studio_runs WHERE id=?', (run['id'],)).fetchone()[0])

    def runs(self, kind):
        with closing(self.service.connect()) as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT data FROM venture_studio_runs WHERE kind=? ORDER BY rowid', (kind,))]

    def discussions(self):
        return [job for job in self.store.job_records() if job.get('kind') == 'discussion']

    def test_scout_pitch_and_approval_through_the_real_store(self):
        settings = dict(runtime='codex')
        studio = self.studio(mode='studio_setup', confirmed=True, settings=settings)
        self.assertEqual(len(studio['team']['members']), 4)
        panel = self.studio(mode='studio_panel', stakeholders=[dict(role='investor', settings=settings),
                                                               dict(role='customer', settings=settings)])
        self.drain()  # The group's facilitator launch finishes starting.
        self.studio(mode='studio_policy', studio_focus=['freight forwarding'],
                    studio_sources=['https://news.example/feed'], studio_max_candidates=2)
        item = venture_fixtures.feed_item(venture_feeds.now().isoformat())
        self.answers['scout'] = dict(candidates=[venture_fixtures.candidate('Carrier Desk', 'N1')])
        self.answers['pitch'] = dict(summary='They agree on paid demand.', reviews=[
            dict(idea='Carrier Desk', stakeholder=role, round=1, verdict='back', objections=['Prove the margin.'],
                 questions=[], would_change_mind='Three paid pilots.') for role in panel['roles']])
        with patch.object(venture_feeds, 'fetch', return_value=dict(
                status=200, source='https://news.example/feed', fetched_at=venture_feeds.now().isoformat(),
                items=[item], etag='', modified='')):
            scout = self.studio(mode='studio_run', kind='scout')
        self.assertEqual(scout['state'], 'meeting', scout.get('error'))
        # The guidance reached the facilitator's own worktree, not the source notebook.
        folder = Path(scout['evidence_file']).parent
        for name in (ventures.STUDIO_ENTRY_FILE, ventures.STUDIO_RULES_FILE, ventures.STUDIO_REVIEW_FILE,
                     ventures.STUDIO_CONSULT_FILE):
            self.assertTrue((folder / name).is_file(), name)
        self.assertNotEqual(folder.resolve().parents[2], (self.projects / studio['notebook']).resolve())

        settled = self.settle(scout)
        self.assertEqual(settled['state'], 'ready', settled.get('error') or settled.get('failure'))
        # Stakeholders attended this one meeting as advisors; their group did not change.
        job = next(job for job in self.discussions() if job['id'] == scout['job_id'])
        self.assertEqual(job['group']['advisors'], [panel['members'][role] for role in panel['roles']])
        self.assertTrue(set(panel['members'].values()) <= set(self.seen_speakers.values()))
        group = next(item for item in self.store.snapshot()['groups'] if item['id'] == studio['group_id'])
        self.assertFalse(set(panel['members'].values()) & set(group['members']))

        pitch, = self.runs('pitch')
        reviewed = self.settle(pitch)
        self.assertEqual(reviewed['state'], 'ready', reviewed.get('error') or reviewed.get('failure'))
        self.assertEqual(reviewed['review_count'], 2)
        candidate = self.ventures.get_candidate(self.org, reviewed['candidate_ids'][0])
        self.assertEqual((candidate['panel']['back'], len(candidate['stakeholder_reviews'])), (2, 2))

        meetings = len(self.discussions())
        # A lost response, or a gateway that restarted, replays the same meeting.
        request = dict(request_id='studio-' + scout['id'], organization_id=self.org, group_id=studio['group_id'],
                       prompt=scout['prompt'], advisors=job['group']['advisors'])
        self.assertEqual(self.store.action('discuss', request)['id'], scout['job_id'])
        restarted = gateway.OrganizationStore(self.store.path, self.projects, self.simulate)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.action('discuss', request)['id'], scout['job_id'])
        self.assertEqual(len(self.discussions()), meetings)

        created = self.studio(mode='candidate_decide', candidate_id=candidate['id'], decision='approve',
                              confirmed=True)
        self.assertEqual(created['state'], 'created')
        venture = self.ventures.get(created['venture_id'], self.org)
        self.assertTrue((self.projects / venture['notebook'] / 'venture.md').is_file())


if __name__ == '__main__':
    unittest.main()
