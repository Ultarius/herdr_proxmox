from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from knowledge import Knowledge


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.projects = self.root / 'projects'
        self.projects.mkdir()
        (self.projects / 'repo').mkdir()
        (self.projects / 'other').mkdir()
        self.path = self.root / 'tasks.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
        self.snapshot = dict(organizations=[dict(id='org', name='Team'), dict(id='foreign', name='Other')], profiles=[
            dict(id='agent', organization_id='org', project=str(self.projects / 'repo')),
            dict(id='other-agent', organization_id='foreign', project=str(self.projects / 'other'))])
        self.service = SimpleNamespace(connect=lambda: sqlite3.connect(self.path), projects=self.projects,
                                       store=SimpleNamespace(snapshot=lambda **kwargs: self.snapshot))
        self.knowledge = Knowledge(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, title='Finding', body='Evidence', kind='finding', org='org', repo='repo'):
        return self.knowledge.action(dict(mode='create', organization_id=org, repository=repo,
                                          kind=kind, title=title, body=body), 'damien', 'admin')['id']

    def listing(self, **changes):
        return self.knowledge.action(dict(mode='list', organization_id='org', **changes), 'reader', 'viewer')

    def test_search_kind_state_and_literal_wildcard_are_scoped(self):
        first = self.create('Literal 100% result', kind='question')
        self.create('Other', kind='decision')
        self.create('Literal 100% foreign', org='foreign', repo='other')
        self.assertEqual([r['id'] for r in self.listing(query='100%', kind='question', state='hypothesis')['records']], [first])
        self.assertEqual(self.listing(query=first)['records'][0]['id'], first)
        self.assertEqual(self.listing(query='   ')['records'].__len__(), 2)
        with self.assertRaises(ValueError):
            self.listing(repository='other')
        with self.assertRaises(ValueError):
            self.listing(kind='invalid')

    def test_review_requires_admin_reason_and_preserves_claims(self):
        identifier = self.create(body='Reported claim')
        request = dict(mode='review', organization_id='org', id=identifier, reason='Checked against commit')
        with self.assertRaisesRegex(ValueError, 'administrator'):
            self.knowledge.action(request, 'reader', 'viewer')
        with self.assertRaisesRegex(ValueError, 'Explain'):
            self.knowledge.action(dict(request, reason=''), 'damien', 'admin')
        self.knowledge.action(request, 'damien', 'admin')
        record = self.listing()['records'][0]
        self.assertEqual(record['state'], 'reviewed')
        self.assertEqual(record['body'], 'Reported claim')
        self.assertFalse(record['source']['checks_verified'])
        self.assertEqual(record['audit'][0]['actor'], 'damien')
        self.assertEqual(self.knowledge.context('org', ['repo'])['records'][0]['state'], 'reviewed')

    def test_supersession_excludes_old_context_and_rejects_cross_scope_and_cycles(self):
        old, new = self.create('Old'), self.create('New')
        foreign = self.create('Foreign', org='foreign', repo='other')
        body = dict(mode='supersede', organization_id='org', id=old, reason='Replaced')
        for target in (old, foreign):
            with self.assertRaises(ValueError):
                self.knowledge.action(dict(body, replacement_id=target), 'damien', 'admin')
        self.knowledge.action(dict(body, replacement_id=new), 'damien', 'admin')
        self.assertEqual([r['id'] for r in self.knowledge.context('org', ['repo'])['records']], [new])
        with self.assertRaises(ValueError):
            self.knowledge.action(dict(body, id=new, replacement_id=old), 'damien', 'admin')
        self.assertEqual(self.listing(state='superseded')['records'][0]['replacement_id'], new)

    def test_atomic_capture_is_idempotent_and_checks_do_not_promote_claims(self):
        task = dict(id='task', organization_id='org', repository='repo', title='Completed search',
                    description='Acceptance criteria', state='completed', head_sha='a' * 40,
                    profile_id='agent', run_id='run', builds={'a' * 40: dict(state='complete', target='a' * 40, required_checks_verified=True)})
        for _ in range(2):
            with closing(sqlite3.connect(self.path)) as db, db:
                self.knowledge.capture_task(db, task, 'complete')
        records = self.listing()['records']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['state'], 'reported')
        self.assertTrue(records[0]['source']['checks_verified'])
        self.assertEqual(records[0]['source']['source_sha'], 'a' * 40)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.knowledge.capture_task(db, dict(task, id='mismatched-build', builds={'a' * 40: dict(state='complete', target='b' * 40, required_checks_verified=True)}), 'complete')
        mismatch = next(r for r in self.listing()['records'] if r['source']['task_id'] == 'mismatched-build')
        self.assertFalse(mismatch['source']['checks_verified'])
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                self.knowledge.capture_task(db, dict(task, head_sha='b' * 40), 'candidate')
                raise ValueError('rollback')
        except ValueError:
            pass
        self.assertEqual(len(self.listing()['records']), 2)

    def test_receipt_claims_remain_reported_and_never_copy_tokens(self):
        task = dict(id='task', organization_id='org', repository='repo', profile_id='agent', run_id='run')
        receipt = dict(commit='a' * 40, token='private-token', knowledge=[dict(kind='finding', title='Cause', body='authorization=private-secret https://example.test/token?secret=bad Authorization: Bearer bearer-raw-123', state='verified'), dict(kind='question', title='Unknown', body='Needs reproduction')])
        for _ in range(2):
            self.knowledge.capture_receipt(task, receipt)
        records = self.listing()['records']
        self.assertEqual(len(records), 2)
        serialized = json.dumps(records)
        self.assertNotIn('private-token', serialized)
        self.assertNotIn('private-secret', serialized)
        self.assertNotIn('secret=bad', serialized)
        self.assertNotIn('bearer-raw-123', serialized)
        self.assertTrue(all(not r['source']['checks_verified'] for r in records))
        self.assertEqual({r['state'] for r in records}, {'reported', 'hypothesis'})

    def test_group_context_and_capture_are_scoped_bounded_and_cited(self):
        relevant = self.create('Model metadata cause', 'Models failed to parse')
        self.create('Other organization', org='foreign', repo='other')
        job = dict(id='meeting', kind='discussion', organization_id='org', state='artifact_ready',
                   group=dict(id='group', name='Review'), prompt='Model metadata', result='Discussed the failure',
                   runs=[dict(profile_id='agent', profile=dict(project=str(self.projects / 'repo')))], knowledge_ids=[relevant],
                   knowledge=[dict(kind='decision', title='Cache models', body='Decision with limitations')])
        repos = self.knowledge.discussion_repositories(job)
        self.assertEqual(repos, ['repo'])
        pack = self.knowledge.context('org', repos, job['prompt'], limit=2000)
        self.assertEqual(pack['records'][0]['id'], relevant)
        self.assertLessEqual(len(json.dumps(pack['records']).encode()), 2000)
        self.assertNotIn('Other organization', json.dumps(pack))
        self.knowledge.capture_discussion(job)
        self.knowledge.capture_discussion(job)
        records = self.listing()['records']
        self.assertEqual(len(records), 3)
        source = next(r['source'] for r in records if r['kind'] == 'decision')
        self.assertEqual(source['knowledge_ids'], [relevant])
        self.assertFalse(source['checks_verified'])

    def test_pagination_and_backfill_survive_reopen(self):
        for i in range(53):
            self.create('Finding ' + str(i))
        first = self.listing()
        second = self.listing(before=first['next_before'])
        self.assertEqual(len(first['records']), 50)
        self.assertEqual(len(second['records']), 3)
        self.assertFalse({r['id'] for r in first['records']} & {r['id'] for r in second['records']})
        task = dict(id='old-task', organization_id='org', repository='repo', title='Old task', state='review_ready')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO tasks VALUES (?,?)', ('old-task', json.dumps(task)))
        self.knowledge = Knowledge(self.service)
        self.assertEqual(len(self.listing(query='Old task')['records']), 1)
        self.knowledge = Knowledge(self.service)
        self.assertEqual(len(self.listing(query='Old task')['records']), 1)

    def test_completed_delegation_reports_are_preserved_as_claims(self):
        job = dict(id='delegation', kind='delegate', state='reported_complete', organization_id='org', updated_at='now', result='Finished work', profile_id='agent', profile=dict(name='Nora', project=str(self.projects / 'repo')))
        self.service.store.knowledge_jobs = lambda: [dict(id='delegation', kind='delegate', state='reported_complete', updated_at='now')]
        self.service.store.job_record = lambda identity: job
        self.knowledge.sync_jobs()
        self.knowledge.sync_jobs()
        record = self.listing()['records'][0]
        self.assertEqual(record['source']['kind'], 'delegation')
        self.assertEqual(record['state'], 'reported')
        self.assertFalse(record['source']['checks_verified'])
        self.assertEqual(len(self.listing()['records']), 1)

    def test_open_questions_do_not_displace_concrete_findings(self):
        self.create('Model metadata failure', kind='question')
        finding = self.create('Model metadata failure', kind='finding')
        records = self.knowledge.context('org', ['repo'], 'Model metadata')['records']
        self.assertEqual(records[0]['id'], finding)

    def test_consultation_and_handover_notes_become_reported_knowledge(self):
        task = dict(id='task', organization_id='org', repository='repo', title='Feature', profile_id='agent', run_id='run')
        receipt = dict(commit='a' * 40, token='secret', tests=[],
                       handover={'state': 'half done', 'next_step': 'run tests'})
        self.knowledge.capture_receipt(task, receipt)
        record = self.listing()['records'][0]
        self.assertEqual(record['kind'], 'guidance')
        self.assertEqual(record['state'], 'reported')
        self.assertEqual(record['source']['kind'], 'handover')
        self.assertIn('Next step: run tests', record['body'])
        chat = dict(id='chat1', kind='chat', state='answered', organization_id='org', updated_at='now',
                    consultation=True, question='Does this conflict?', result='Yes, with the filter.', profile_id='agent')
        self.service.store.knowledge_jobs = lambda: [dict(id='chat1', kind='chat', state='answered', updated_at='now')]
        self.service.store.job_record = lambda identity: chat
        self.knowledge.sync_jobs()
        consult = next(r for r in self.listing()['records'] if r['source']['kind'] == 'consult')
        self.assertEqual(consult['kind'], 'finding')
        self.assertIn('filter', consult['body'])
