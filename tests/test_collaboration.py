"""Discussion workflow tests with simulated agent file-writing, not live models."""
from pathlib import Path
import re
import json
import uuid
import unittest
import threading
from unittest.mock import patch
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
        if args[:2] == ('pane', 'close'):
            for alias, agent in list(self.agents.items()):
                if agent['pane_id'] == args[2]:
                    del self.agents[alias]
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

    def test_removal_preserves_artifacts_and_members_but_closes_facilitator(self):
        group_id = self.group()
        discussion = self.action('discuss', organization_id=self.org, group_id=group_id)
        self.drain()
        job = self.job(discussion)
        run = self.job(job['group_run']['id'])
        body = dict(request_id=uuid.uuid4().hex, organization_id=self.org, group_id=group_id)
        result = self.store.action('remove_group', body)
        self.assertEqual(self.store.action('remove_group', body), result)
        snapshot = self.store.snapshot()
        self.assertFalse(snapshot['groups'])
        self.assertEqual({p['id'] for p in snapshot['profiles']}, {self.max, self.iris})
        self.assertNotIn(run['alias'], self.agents)
        self.assertEqual(self.job(run['id'])['state'], 'released')
        self.assertEqual(self.job(discussion)['result'], job['result'])
        self.assertTrue((self.store.path.parent / 'discussion-artifacts' / discussion / 'action-plan.md').is_file())
        closes = [a for a, _ in self.calls if a[:2] == ('pane', 'close')]
        self.assertEqual(closes, [('pane', 'close', run['pane_id'])])
        with self.assertRaisesRegex(ValueError, 'removed'):
            self.action('discuss', organization_id=self.org, group_id=group_id)

    def test_remove_agent_checks_membership_and_reparents_direct_reports(self):
        group_id = self.group()
        with self.assertRaisesRegex(ValueError, 'Planning'):
            self.action('remove_agent', organization_id=self.org, profile_id=self.max)
        self.action('remove_group', organization_id=self.org, group_id=group_id)
        child = self.hire(self.org, 'Child', manager_id=self.max)
        self.action('remove_agent', organization_id=self.org, profile_id=self.max)
        profiles = self.store.snapshot()['profiles']
        self.assertNotIn(self.max, [p['id'] for p in profiles])
        self.assertEqual(next(p for p in profiles if p['id'] == child)['manager_id'], '')
        with self.assertRaisesRegex(ValueError, 'removed'):
            self.hire(self.org, id=self.max)

    def test_removal_rejects_active_tasks_replaced_sessions_and_wrong_organization(self):
        group_id = self.group()
        group = next(g for g in self.store.snapshot()['groups'] if g['id'] == group_id)
        run = next(j for j in self.store.snapshot()['jobs'] if j.get('profile_id') == group['facilitator_id'])
        self.store.update_job(run['id'], state='running')
        with self.assertRaisesRegex(ValueError, 'active work'):
            self.action('remove_group', organization_id=self.org, group_id=group_id)
        self.store.update_job(run['id'], state='persona_sent')
        self.agents[run['alias']]['agent_status'] = 'working'
        with self.assertRaisesRegex(ValueError, 'still working'):
            self.action('remove_group', organization_id=self.org, group_id=group_id)
        self.agents[run['alias']]['agent_status'] = 'idle'
        self.agents[run['alias']]['agent_session'] = {'value': 'replacement'}
        with self.assertRaisesRegex(ValueError, 'conversation changed'):
            self.action('remove_group', organization_id=self.org, group_id=group_id)
        with self.assertRaisesRegex(ValueError, 'another organization'):
            self.action('remove_group', organization_id=self.organization('Other'), group_id=group_id)
        self.assertFalse(any(a[:2] == ('pane', 'close') for a, _ in self.calls))
        self.assertTrue(self.store.snapshot()['groups'])

    def test_removal_close_failure_keeps_entry_and_binding(self):
        group_id = self.group()
        def failing_close(*args, **kwargs):
            if args[:2] == ('pane', 'close'):
                raise ValueError('close failed')
            return self.simulate(*args, **kwargs)
        with patch.object(self.store, 'command', side_effect=failing_close):
            with self.assertRaisesRegex(ValueError, 'close failed'):
                self.action('remove_group', organization_id=self.org, group_id=group_id)
        self.assertTrue(self.store.snapshot()['groups'])
        self.assertFalse(any(j['state'] == 'released' for j in self.store.snapshot()['jobs']))

    def test_offline_agent_removal_preserves_worktree_files(self):
        run = next(j for j in self.store.snapshot()['jobs'] if j.get('profile_id') == self.max)
        checkout = self.projects / '.herdr-worktrees' / 'preserved'
        checkout.mkdir(parents=True)
        changed = checkout / 'work.txt'
        changed.write_text('uncommitted work')
        self.store.update_job(run['id'], worktree_path=str(checkout))
        del self.agents[run['alias']]
        self.action('remove_agent', organization_id=self.org, profile_id=self.max)
        self.assertEqual(changed.read_text(), 'uncommitted work')
        self.assertFalse(any(a[:2] == ('pane', 'close') for a, _ in self.calls))

    def test_inactive_members_are_named_and_can_be_relaunched(self):
        group = self.group()
        for member in (self.max, self.iris):
            run = next(j for j in self.store.snapshot()['jobs'] if j['kind'] == 'launch' and j['profile_id'] == member)
            self.action('release', organization_id=self.org, job_id=run['id'])
        with self.assertRaisesRegex(ValueError, 'Max, Iris'):
            self.action('discuss', organization_id=self.org, group_id=group)
        states = self.store.snapshot()['member_states']
        self.assertFalse(states[self.max]['active'])
        self.assertEqual(states[self.max]['status'], 'off')
        self.action('launch', organization_id=self.org, profile_id=self.max)
        self.drain()
        self.assertTrue(self.store.snapshot()['member_states'][self.max]['active'])

    def test_missing_live_agent_is_off_despite_persisted_launch(self):
        run = next(j for j in self.store.snapshot()['jobs'] if j['kind'] == 'launch' and j['profile_id'] == self.max)
        del self.agents[run['alias']]
        state = self.store.snapshot()['member_states'][self.max]
        self.assertFalse(state['active'])
        self.assertEqual(state['status'], 'off')
        self.assertEqual(state['run_id'], run['id'])

    def test_chat_wait_snapshot_and_deduplication(self):
        body = dict(request_id=uuid.uuid4().hex, organization_id=self.org, profile_id=self.max, prompt='Explain this project')
        response = self.store.action('chat', body)
        self.assertEqual(self.store.action('chat', body), response)
        self.drain()
        self.assertEqual(self.job(response['id'])['state'], 'answered')
        self.assertIn('Recommended actions', self.job(response['id'])['result'])
        self.assertEqual(self.job(response['id'])['result_format'], 'markdown')
        prompts = [a for a, _ in self.calls if a[:2] == ('agent', 'prompt') and a[3].startswith('User message:\n' + body['prompt'] + '\n')]
        self.assertEqual(len(prompts), 1)
        self.assertIn('--wait', prompts[0])
        inspected = self.store.action('inspect', dict(request_id=uuid.uuid4().hex, organization_id=self.org, profile_id=self.max))
        self.assertEqual(inspected['status'], 'idle')

    def test_missing_chat_reply_does_not_fall_back_to_terminal_snapshot(self):
        self.writes = False
        job_id = self.action('chat', organization_id=self.org, profile_id=self.max, prompt='Hi')
        self.drain()
        job = self.job(job_id)
        self.assertEqual(job['state'], 'needs_attention')
        self.assertEqual(job['result'], '')
        self.assertFalse(any(args[:2] == ('agent', 'read') for args, _ in self.calls))

    def test_inspection_reads_working_terminal_and_partial_reply(self):
        run = next(j for j in self.store.snapshot()['jobs'] if j.get('profile_id') == self.max and j['kind'] == 'launch')
        self.agents[run['alias']]['agent_status'] = 'working'
        with closing(self.store.connect()) as db, db:
            self.store.put(db, 'jobs', dict(id='streamtest', organization_id=self.org, kind='chat', profile_id=self.max, state='running'))
        directory = self.store.path.parent / 'chat-replies' / 'streamtest'
        directory.mkdir(parents=True)
        (directory / 'reply.md').write_text('Partial answer with code: `map(fn)`', encoding='utf-8')
        result = self.store.action('inspect', dict(request_id=uuid.uuid4().hex, organization_id=self.org, profile_id=self.max))
        self.assertEqual(result['reply_job_id'], 'streamtest')
        self.assertIn('Partial answer', result['reply_draft'])
        read = [args for args, _ in self.calls if args[:2] == ('agent', 'read')][-1]
        self.assertEqual(read[read.index('--source') + 1], 'visible')

    def test_group_discussion_and_artifact_persist(self):
        group = self.group()
        self.assertTrue(next(g for g in self.store.snapshot()['groups'] if g['id'] == group)['read_only'])
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

    def test_discussion_context_preserves_distinct_worktrees_and_live_directory_mismatch(self):
        group = self.group()
        member = next(j for j in self.store.snapshot()['jobs']
                      if j['kind'] == 'launch' and j['profile_id'] == self.max)
        checkout = str(self.projects / '.herdr-worktrees' / 'max')
        self.store.update_job(member['id'], worktree_path=checkout)
        self.agents[member['alias']]['cwd'] = str(self.projects / 'different-project')
        job_id = self.action('discuss', organization_id=self.org, group_id=group,
                             prompt='Share the latest work')
        self.drain()
        job = self.job(job_id)
        self.assertEqual(job['state'], 'artifact_ready', job['error'])
        prompt = next(a[3] for a, _ in self.calls if a[:2] == ('agent', 'prompt')
                      and a[2] == job['group_run']['alias'] and 'Workspace context (' in a[3])
        line = next(line for line in prompt.splitlines() if line.startswith('Workspace context ('))
        contexts = json.loads(line.split(': ', 1)[1])
        max_context = next(c for c in contexts if c['alias'] == member['alias'])
        self.assertEqual(max_context['configured_project'], str(self.projects))
        self.assertEqual(max_context['launch_directory'], checkout)
        self.assertEqual(max_context['herdr_reported_cwd'], str(self.projects / 'different-project'))
        iris_context = next(c for c in contexts if c['name'] == 'Iris')
        self.assertEqual(iris_context['launch_directory'], str(self.projects))
        self.assertIsNone(iris_context['herdr_reported_cwd'])

    def test_live_discussion_inspects_bound_agents_and_incremental_documents(self):
        group = self.group()
        job_id = self.action('discuss', organization_id=self.org, group_id=group)
        self.drain()
        job = self.job(job_id)
        self.store.update_job(job_id, state='running')
        self.agents[job['group_run']['alias']]['agent_status'] = 'working'
        self.agents[job['runs'][0]['alias']]['agent_status'] = 'blocked'
        directory = self.store.path.parent / 'discussion-artifacts' / job_id
        (directory / 'action-plan.md').write_text('Draft synthesis', encoding='utf-8')
        (directory / 'discussion.json').write_text(json.dumps({'contributions': [
            dict(profile_id=self.max, name='untrusted name', round=1, content='First reply')]}), encoding='utf-8')
        def inspect(org=self.org):
            return self.store.action('inspect', dict(request_id=uuid.uuid4().hex, organization_id=org, job_id=job_id))
        result = inspect()
        self.assertEqual(result['artifact_draft'], 'Draft synthesis')
        self.assertEqual(result['contributions'][0]['name'], 'Max')
        self.assertEqual([s['status'] for s in result['streams']], ['working', 'blocked', 'idle'])
        self.assertTrue(all(s['output'] for s in result['streams']))
        self.assertEqual(self.job(job_id)['state'], 'running')
        (directory / 'discussion.json').write_text('{"contributions":', encoding='utf-8')
        self.assertEqual(inspect()['contributions'], [])
        other = self.organization('Other')
        with self.assertRaises(ValueError):
            inspect(other)
        self.action('release', organization_id=self.org, job_id=job['runs'][0]['id'])
        self.assertEqual(inspect()['streams'][1]['status'], 'unavailable')

    def test_group_paths_are_saved_in_facilitator_policy(self):
        # This fixture normally uses Codex; change saved profiles before group creation.
        with closing(self.store.connect()) as db, db:
            for profile_id in (self.max, self.iris):
                profile = self.store.get(db, 'profiles', profile_id)
                profile['runtime'] = 'opencode'
                self.store.put(db, 'profiles', profile)
        with patch('organizations.prepare_permissions', return_value=[]):
            group_id = self.action('group', organization_id=self.org, name='Paths',
                                  description='Review references', members=[self.max, self.iris],
                                  accessible_paths=['~/reference/**'], permission_mode='dashboard_outputs')
            self.drain()
        with closing(self.store.connect()) as db:
            group = self.store.get(db, 'groups', group_id)
            facilitator = self.store.get(db, 'profiles', group['facilitator_id'])
        self.assertEqual(group['accessible_paths'], ['~/reference/**'])
        self.assertEqual(facilitator['accessible_paths'], group['accessible_paths'])

    def test_transcript_download_returns_saved_json_and_checks_scope(self):
        job_id = self.action('discuss', organization_id=self.org, group_id=self.group())
        self.drain()
        path = self.store.path.parent / 'discussion-artifacts' / job_id / 'discussion.json'
        def download(org=self.org):
            return self.store.action('transcript', dict(request_id=uuid.uuid4().hex,
                                    organization_id=org, job_id=job_id))
        result = download()
        self.assertEqual(result['filename'], 'discussion.json')
        exported = json.loads(result['content'])
        self.assertEqual(exported['contributions'], self.job(job_id)['contributions'])
        self.assertEqual(json.loads(path.read_text())['contributions'][0]['name'], 'simulated')
        self.assertEqual(len(json.loads(result['content'])['contributions']), 4)
        other = self.action('save', name='Other', purpose='Other')
        with self.assertRaises(ValueError):
            download(other)
        self.store.update_job(job_id, state='running')
        with self.assertRaisesRegex(ValueError, 'finalized'):
            download()
        self.store.update_job(job_id, state='artifact_ready')
        path.write_text('{"contributions": []}')
        with self.assertRaises(ValueError):
            download()

    def test_late_artifact_recovery_validates_files_without_reprompting(self):
        group_id = self.group()
        job_id = self.action('discuss', organization_id=self.org, group_id=group_id)
        self.drain()
        self.store.update_job(job_id, state='needs_attention', result='', contributions=[], error='Earlier wait interrupted')
        body = dict(request_id=uuid.uuid4().hex, organization_id=self.org, job_id=job_id)
        before = len([a for a, _ in self.calls if a[:2] == ('agent', 'prompt')])
        response = self.store.action('recover', body)
        recovered = self.job(job_id)
        self.assertEqual(recovered['state'], 'artifact_ready')
        self.assertIn('Recommended actions', recovered['result'])
        self.assertEqual(len(recovered['contributions']), 4)
        self.assertEqual(recovered['previous_error'], 'Earlier wait interrupted')
        self.assertEqual(recovered['error'], '')
        self.assertEqual(self.store.action('recover', body), response)
        self.assertEqual(len([a for a, _ in self.calls if a[:2] == ('agent', 'prompt')]), before)
        self.assertEqual(self.job(job_id)['state'], 'artifact_ready')

    def test_recovery_rejects_blocked_replaced_and_invalid_output(self):
        job_id = self.action('discuss', organization_id=self.org, group_id=self.group())
        self.drain()
        job = self.job(job_id)
        self.store.update_job(job_id, state='needs_attention', result='')
        def recover(org=self.org):
            return self.store.action('recover', dict(request_id=uuid.uuid4().hex, organization_id=org, job_id=job_id))
        agent = self.agents[job['group_run']['alias']]
        agent['agent_status'] = 'blocked'
        with self.assertRaisesRegex(ValueError, 'not ready'):
            recover()
        agent['agent_status'] = 'idle'
        old_session = agent['agent_session']
        agent['agent_session'] = {'value': 'replacement'}
        with self.assertRaisesRegex(ValueError, 'conversation changed'):
            recover()
        agent['agent_session'] = old_session
        directory = self.store.path.parent / 'discussion-artifacts' / job_id
        transcript = directory / 'discussion.json'
        original = transcript.read_text(encoding='utf-8')
        transcript.write_text('{"contributions":', encoding='utf-8')
        with self.assertRaises(ValueError):
            recover()
        transcript.write_text(original, encoding='utf-8')
        (directory / 'action-plan.md').unlink()
        with self.assertRaisesRegex(ValueError, 'valid discussion document'):
            recover()
        other = self.organization('Other')
        with self.assertRaises(ValueError):
            recover(other)
        self.assertEqual(self.job(job_id)['state'], 'needs_attention')

    def test_live_names_preserve_aliases_and_distinguish_groups(self):
        self.group()
        agents = list(self.agents.values())
        labeled = self.store.label_agents(agents)
        self.assertEqual([a['name'] for a in labeled], [a['name'] for a in agents])
        self.assertEqual([a['display_name'] for a in labeled], ['Max', 'Iris', 'Planning'])
        self.assertEqual(labeled[-1]['entity_type'], 'group')
        changed = dict(agents[0], agent_session={'value': 'replacement'})
        self.assertNotIn('display_name', self.store.label_agents([changed])[0])
        unknown = dict(name='external-agent', pane_id='w999:p1', agent='codex')
        self.assertEqual(self.store.label_agents([unknown])[0], unknown)

    def test_group_label_during_initialization_and_failed_launch_requires_session_binding(self):
        group_id = self.group()
        group = next(g for g in self.store.snapshot()['groups'] if g['id'] == group_id)
        run = next(j for j in self.store.snapshot()['jobs']
                   if j['kind'] == 'launch' and j['profile_id'] == group['facilitator_id'])
        agent = dict(self.agents[run['alias']], agent_status='working')
        for state in ('running', 'needs_attention'):
            self.store.update_job(run['id'], state=state)
            labeled = self.store.label_agents([agent])[0]
            self.assertEqual(labeled['display_name'], 'Planning')
            self.assertEqual(labeled['entity_type'], 'group')
            self.assertEqual(labeled['name'], run['alias'])
            replaced = dict(agent, agent_session={'value': 'different-session'})
            self.assertNotIn('display_name', self.store.label_agents([replaced])[0])
        self.store.update_job(run['id'], state='running', agent_session=None)
        self.assertNotIn('display_name', self.store.label_agents([agent])[0])
        self.store.update_job(run['id'], state='released', agent_session=run['agent_session'])
        self.assertNotIn('display_name', self.store.label_agents([agent])[0])

    def test_group_creation_and_messages_reuse_real_agent_binding(self):
        group_id = self.group()
        group = next(g for g in self.store.snapshot()['groups'] if g['id'] == group_id)
        profile_id = group['facilitator_id']
        run = next(j for j in self.store.snapshot()['jobs'] if j.get('profile_id') == profile_id)
        self.assertEqual(run['state'], 'persona_sent')
        startup = next(a[3] for a, _ in self.calls
                       if a[:3] == ('agent', 'prompt', run['alias']))
        self.assertIn('INITIALIZATION ONLY', startup)
        self.assertNotIn(group['description'], startup)
        self.assertIn('do not list, read or prompt other agents', startup)
        self.assertFalse(any(j['kind'] == 'discussion' for j in self.store.snapshot()['jobs']))
        for prompt in ('Compare options', 'Refine the recommendation'):
            job_id = self.action('discuss', organization_id=self.org, group_id=group_id, prompt=prompt)
            self.drain()
            self.assertEqual(self.job(job_id)['state'], 'artifact_ready')
            self.assertEqual(self.job(job_id)['group_run']['alias'], run['alias'])
            self.assertEqual(self.job(job_id)['prompt'], prompt)
            delivered = [a[3] for a, _ in self.calls
                         if a[:3] == ('agent', 'prompt', run['alias'])][-1]
            self.assertIn(f"Purpose: {group['description']}", delivered)
            self.assertIn(f"User message:\n{prompt}", delivered)
            self.assertIn('Selected members (live Herdr aliases):', delivered)
            self.assertIn('Discussion directory:', delivered)
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

    def test_inspection_does_not_block_store_updates(self):
        group = self.group()
        job_id = self.action('discuss', organization_id=self.org, group_id=group)
        self.drain()
        entered, release, updated = threading.Event(), threading.Event(), threading.Event()
        original = self.store.command
        def slow(*args, **kwargs):
            if args[:2] == ('agent', 'get'):
                entered.set()
                release.wait(5)
            return original(*args, **kwargs)
        self.store.command = slow
        outcome = []
        thread = threading.Thread(target=lambda: outcome.append(self.store.action('inspect', dict(request_id=uuid.uuid4().hex, organization_id=self.org, job_id=job_id))))
        thread.start()
        self.assertTrue(entered.wait(2))
        writer = threading.Thread(target=lambda: (self.store.update_job(job_id, progress='Updated while inspecting'), updated.set()))
        writer.start()
        try:
            self.assertTrue(updated.wait(1), 'Inspection held the global store lock')
        finally:
            release.set()
            writer.join(5)
            thread.join(5)
        self.assertEqual(len(outcome[0]['streams']), 3)
        self.assertTrue(all(s['status'] == 'idle' for s in outcome[0]['streams']))

    def test_group_history_is_scoped_paginated_and_survives_archival(self):
        group = self.group()
        ids = []
        for _ in range(3):
            ids.append(self.action('discuss', organization_id=self.org, group_id=group))
            self.drain()
        page = self.store.history(dict(group_id=group, limit=2))
        discussions = [j for j in page['jobs'] if j['kind'] == 'discussion']
        self.assertEqual([j['id'] for j in discussions], ids[1:])
        second = self.store.history(dict(group_id=group, before=page['next_before'], limit=2))
        self.assertEqual([j['id'] for j in second['jobs'] if j['kind'] == 'discussion'], ids[:1])
        self.assertIsNone(second['next_before'])
        self.action('remove_group', organization_id=self.org, group_id=group)
        archived = self.store.history(dict(group_id=group))
        self.assertTrue(archived['group']['removed_at'])
        self.assertEqual(len([j for j in archived['jobs'] if j['kind'] == 'discussion']), 3)
        self.assertEqual(len(self.store.snapshot(directory=True)['archived_groups']), 1)
        self.assertEqual(self.store.snapshot(directory=True)['jobs'], [])

    def test_state_and_activity_omit_repeated_launch_snapshots(self):
        group = self.group()
        job_id = self.action('discuss', organization_id=self.org, group_id=group)
        self.drain()
        state = self.store.state_snapshot()
        saved = next(j for j in state['jobs'] if j['id'] == job_id)
        self.assertNotIn('contributions', saved)
        self.assertNotIn('runs', saved)
        self.assertNotIn('group_run', saved)
        activity = self.store.activity(dict(organization_id=self.org))
        saved = next(j for j in activity['jobs'] if j['id'] == job_id)
        self.assertTrue(saved['contributions'])
        self.assertTrue(saved['result'])
        self.assertNotIn('runs', saved)
        self.assertTrue(all(p['organization_id'] == self.org for p in activity['profiles']))
