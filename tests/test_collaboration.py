"""Discussion workflow tests with simulated agent file-writing, not live models."""
from pathlib import Path
import re
import json
import uuid
import unittest
from contextlib import closing
import test_organizations as fixtures


class CollaborationTests(unittest.TestCase):
    tearDown = fixtures.OrganizationTests.tearDown
    command = fixtures.OrganizationTests.command
    action = fixtures.OrganizationTests.action
    organization = fixtures.OrganizationTests.organization
    hire = fixtures.OrganizationTests.hire
    drain = fixtures.OrganizationTests.drain

    def setUp(self):
        fixtures.OrganizationTests.setUp(self)
        self.original = self.store.command
        self.writes = True
        self.store.command = self.simulate
        self.org = self.organization()
        self.max = self.hire(self.org, 'Max')
        self.iris = self.hire(self.org, 'Iris')
        for profile in (self.max, self.iris):
            self.action('launch', organization_id=self.org, profile_id=profile)
        self.drain()

    def simulate(self, *args, **kwargs):
        response = self.original(*args, **kwargs)
        if args[:2] == ('agent', 'read'):
            return {'output': 'Agent response in terminal'}
        if args[:2] == ('agent', 'prompt') and '--wait' in args and self.writes:
            match = re.search(r'exactly (?:this new file: )?(.+?\.md)', args[3])
            if match:
                Path(match[1]).write_text('# Recommended actions\nReview evidence before implementation.', encoding='utf-8')
                transcript = re.search(r'Also write (.+?discussion\.json)', args[3])
                if transcript:
                    ids = json.loads(re.search(r'Profile IDs: (.+?)\. Record', args[3])[1])
                    parts = [dict(profile_id=i, name='simulated', round=r, content='Evidence-backed proposal') for r in (1, 2) for i in ids.values()]
                    Path(transcript[1]).write_text(json.dumps({'contributions': parts}), encoding='utf-8')
        return response

    def job(self, job_id):
        return next(j for j in self.store.snapshot()['jobs'] if j['id'] == job_id)

    def group(self):
        group = self.action('group', organization_id=self.org, name='Planning', description='Discuss architecture', members=[self.max, self.iris])
        self.drain()
        return group

    def test_chat_wait_snapshot_and_deduplication(self):
        body = dict(request_id=uuid.uuid4().hex, organization_id=self.org, profile_id=self.max, prompt='Explain this project')
        response = self.store.action('chat', body)
        self.assertEqual(self.store.action('chat', body), response)
        self.drain()
        self.assertEqual(self.job(response['id'])['state'], 'answered')
        self.assertIn('Agent response', self.job(response['id'])['result'])
        prompts = [a for a, _ in self.calls if a[:2] == ('agent', 'prompt') and a[3] == body['prompt']]
        self.assertEqual(len(prompts), 1)
        self.assertIn('--wait', prompts[0])
        inspected = self.store.action('inspect', dict(request_id=uuid.uuid4().hex, organization_id=self.org, profile_id=self.max))
        self.assertEqual(inspected['status'], 'idle')

    def test_group_discussion_and_artifact_persist(self):
        group = self.group()
        run = self.action('discuss', organization_id=self.org, group_id=group)
        self.drain()
        job = self.job(run)
        self.assertEqual(job['state'], 'artifact_ready', job['error'])
        self.assertEqual(len(job['contributions']), 4)
        self.assertEqual([c['round'] for c in job['contributions']], [1, 1, 2, 2])
        self.assertIn('Recommended actions', job['result'])
        prompts = [a[3] for a, _ in self.calls if a[:2] == ('agent', 'prompt') and '--wait' in a]
        self.assertEqual(len(prompts), 2)  # Group persona plus one user message.
        self.assertIn('You are the group conversation', prompts[1])
        group_item = next(g for g in self.store.snapshot()['groups'] if g['id'] == group)
        facilitator_run = next(j for j in self.store.snapshot()['jobs'] if j.get('profile_id') == group_item['facilitator_id'] and j['kind'] == 'launch')
        self.assertEqual(job['group_run']['alias'], facilitator_run['alias'])
        self.assertIn('herdr agent prompt', prompts[1])
        with closing(self.store.connect()) as db:
            self.assertEqual(self.store.get(db, 'jobs', run)['result'], job['result'])

    def test_group_creation_and_messages_reuse_real_agent_binding(self):
        group_id = self.group()
        group = next(g for g in self.store.snapshot()['groups'] if g['id'] == group_id)
        profile_id = group['facilitator_id']
        run = next(j for j in self.store.snapshot()['jobs'] if j.get('profile_id') == profile_id)
        self.assertEqual(run['state'], 'persona_sent')
        for prompt in ('Compare options', 'Refine the recommendation'):
            job_id = self.action('discuss', organization_id=self.org, group_id=group_id, prompt=prompt)
            self.drain()
            self.assertEqual(self.job(job_id)['state'], 'artifact_ready')
            self.assertEqual(self.job(job_id)['group_run']['alias'], run['alias'])
            self.assertEqual(self.job(job_id)['prompt'], prompt)
        launches = [a for a, _ in self.calls if a[:2] == ('agent', 'start') and a[2] == run['alias']]
        self.assertEqual(len(launches), 1)

    def test_legacy_group_gets_agent_on_first_message(self):
        with closing(self.store.connect()) as db, db:
            self.store.put(db, 'groups', dict(id='legacy', organization_id=self.org,
                name='Legacy', description='Discuss options', members=[self.max, self.iris]))
        job_id = self.action('discuss', organization_id=self.org, group_id='legacy', prompt='Review this')
        self.drain()
        self.assertEqual(self.job(job_id)['state'], 'artifact_ready', self.job(job_id)['error'])
        self.assertTrue(next(g for g in self.store.snapshot()['groups'] if g['id'] == 'legacy')['facilitator_id'])

    def test_missing_artifact_is_not_success(self):
        self.writes = False
        run = self.action('discuss', organization_id=self.org, group_id=self.group())
        self.drain()
        self.assertEqual(self.job(run)['state'], 'needs_attention')
        self.assertEqual(self.job(run)['result'], '')
        self.assertIn('valid discussion document', self.job(run)['error'])

    def test_membership_scope_and_released_binding(self):
        other = self.organization('Other')
        stranger = self.hire(other, 'Stranger')
        for members in ([self.max], [self.max, self.max], [self.max, stranger]):
            with self.assertRaises(ValueError):
                self.action('group', organization_id=self.org, name='Bad', description='Topic', members=members)
        run = next(j for j in self.store.snapshot()['jobs'] if j.get('profile_id') == self.max and j['kind'] == 'launch')
        self.action('release', organization_id=self.org, job_id=run['id'])
        with self.assertRaisesRegex(ValueError, 'Launch this agent'):
            self.action('chat', organization_id=self.org, profile_id=self.max, prompt='Hello')

    def test_reservation_prevents_interleaved_prompts(self):
        # Reserve a running discussion without invoking the worker.
        with closing(self.store.connect()) as db, db:
            self.store.put(db, 'jobs', dict(id='reserved', organization_id=self.org, kind='discussion', participants=[self.max, self.iris], state='running'))
        with self.assertRaisesRegex(ValueError, 'running task'):
            self.action('chat', organization_id=self.org, profile_id=self.max, prompt='Hello')
        with self.assertRaisesRegex(ValueError, 'running task'):
            self.action('delegate', organization_id=self.org, sender_id=self.max, profile_id=self.iris, task='Hello')

    def test_blocked_agent_input_and_key_validation(self):
        run = next(j for j in self.store.snapshot()['jobs'] if j.get('profile_id') == self.max and j['kind'] == 'launch')
        self.agents[run['alias']]['agent_status'] = 'blocked'
        with self.assertRaisesRegex(ValueError, 'Unsupported terminal key'):
            self.action('input', organization_id=self.org, profile_id=self.max, key='shell')
        job = self.action('input', organization_id=self.org, profile_id=self.max, key='enter')
        self.drain()
        self.assertEqual(self.job(job)['state'], 'input_sent')
        self.assertIn(('agent', 'send-keys', run['alias'], 'enter'), [args for args, _ in self.calls])
