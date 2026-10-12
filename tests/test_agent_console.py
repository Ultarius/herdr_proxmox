"""Console responses must target the inspected blocked conversation."""
from contextlib import closing
import sys
from pathlib import Path
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import test_organizations as fixtures


class AgentConsoleTests(unittest.TestCase):
    tearDown = fixtures.OrganizationTests.tearDown
    command = fixtures.OrganizationTests.command
    action = fixtures.OrganizationTests.action
    organization = fixtures.OrganizationTests.organization
    hire = fixtures.OrganizationTests.hire
    drain = fixtures.OrganizationTests.drain

    def setUp(self):
        fixtures.OrganizationTests.setUp(self)
        self.org = self.organization()
        self.profile = self.hire(self.org)
        # Start with a bound conversation; launch/worktree creation has its own tests.
        self.run_id = uuid.uuid4().hex
        session = {'value': uuid.uuid4().hex}
        with closing(self.store.connect()) as db, db:
            self.run = dict(id=self.run_id, kind='launch', state='persona_sent',
                            organization_id=self.org, profile_id=self.profile,
                            profile=self.store.get(db, 'profiles', self.profile),
                            alias='console-test', pane_id='w1:p1', agent_session=session)
            self.store.put(db, 'jobs', self.run)
        self.agents[self.run['alias']] = dict(name=self.run['alias'], pane_id=self.run['pane_id'],
                                             agent='codex', agent_status='blocked', agent_session=session)
        self.output = 'Access requested: /project/private\nAllow once / Reject'
        original = self.store.command
        self.store.command = lambda *args, **kwargs: (
            {'output': self.output} if args[:2] == ('agent', 'read') else original(*args, **kwargs))

    def job(self, job_id):
        with closing(self.store.connect()) as db:
            return self.store.get(db, 'jobs', job_id)

    def inspect(self):
        return self.store.action('inspect', dict(request_id=uuid.uuid4().hex,
                                 organization_id=self.org, profile_id=self.profile))

    def respond(self, token, key='enter'):
        result = self.action('input', organization_id=self.org, profile_id=self.profile,
                             key=key, console_token=token)
        self.drain()
        return self.job(result)

    def test_reviewed_prompt_can_be_answered_and_actor_is_recorded(self):
        inspection = self.inspect()
        self.assertEqual(inspection['output'], self.output)
        result = self.respond(inspection['console_token'])
        self.assertEqual(result['state'], 'input_sent')
        self.assertTrue(result['actor'])
        self.assertIn(('agent', 'send-keys', self.run['alias'], 'enter'), [a for a, _ in self.calls])

    def test_changed_prompt_is_not_answered(self):
        token = self.inspect()['console_token']
        self.output = 'Different access request'
        result = self.respond(token)
        self.assertEqual(result['state'], 'needs_attention')
        self.assertIn('Console prompt changed', result['error'])
        self.assertFalse(any(a[:2] == ('agent', 'send-keys') for a, _ in self.calls))

    def test_unblocked_agent_is_not_answered(self):
        token = self.inspect()['console_token']
        self.agents[self.run['alias']]['agent_status'] = 'working'
        result = self.respond(token)
        self.assertEqual(result['state'], 'needs_attention')
        self.assertIn('no longer blocked', result['error'])
        self.assertFalse(any(a[:2] == ('agent', 'send-keys') for a, _ in self.calls))

    def test_navigation_and_cancel_use_a_fresh_inspection(self):
        for key in ('down', 'esc'):
            self.assertEqual(self.respond(self.inspect()['console_token'], key)['state'], 'input_sent')
        self.agents[self.run['alias']]['agent_status'] = 'idle'
        self.assertIsNone(self.inspect()['console_token'])

    def test_replaced_conversation_cannot_receive_a_response(self):
        token = self.inspect()['console_token']
        self.agents[self.run['alias']]['agent_session'] = {'value': uuid.uuid4().hex}
        result = self.respond(token)
        self.assertEqual(result['state'], 'needs_attention')
        self.assertIn('conversation changed', result['error'])
        self.assertFalse(any(a[:2] == ('agent', 'send-keys') for a, _ in self.calls))
