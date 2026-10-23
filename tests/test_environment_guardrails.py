"""Production launch/validation/publication wiring, using executable SDK doubles.

These prove the gateway protocol and refusal boundaries, not vendor installation.
The separate real-SDK qualification exercises publisher binaries.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import environment_plan
import environment_adapters
import environment_policy
import environment_launch
import environment_dispatch
from environment_policy import EnvironmentPolicies
from environment_preparation import EnvironmentPreparation
from project_analysis import ProjectAnalysis
from contributions import Contributions
from organizations import OrganizationStore, policy_guidance
import toolchain_policy
from validation import ValidationRuns
from collaboration import repository_review_context, repository_review_instructions


class Unprepared:
    """A store with no completed preparation, so bindings stay unavailable."""

    def prepared(self, project, revision):
        return None, None


class ClaimTests(unittest.TestCase):
    def test_rust_reuse_requires_the_receipted_cargo_version(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('rustc', 'cargo'):
                (root / name).write_text('SDK double')
            spec = {'capability': 'rust-toolchain'}
            plan = dict(digest='plan', revision='a' * 40, requirements=[dict(capability='rust-toolchain',
                label='Rust', version='1.85.1', components=['.'], artifact=spec,
                adapter_version=environment_adapters.get('rust-toolchain').version)])
            receipt = dict(state='ready', digest='plan', revision='a' * 40, receipts=[dict(
                capability='rust-toolchain', version='1.85.1', components=['.'], specification=spec,
                probe=dict(executable=str(root / 'rustc'), cargo_version='1.85.0'))])
            policy = environment_plan.bindings(plan, receipt)
            runner = Mock(side_effect=[Mock(returncode=0, stdout='rustc 1.85.1', stderr=''),
                                       Mock(returncode=0, stdout='cargo 1.86.0', stderr='')])
            evidence = toolchain_policy.verify(policy, run=runner, root=root)
            self.assertTrue(evidence[0]['matches'])
            self.assertEqual(evidence[1]['name'], 'cargo')
            self.assertFalse(evidence[1]['matches'])

    def test_validation_scope_blockers_gate_publication_separately(self):
        base = dict(toolchains=[dict(name='dotnet-sdk', required='8.0.100', component='Api',
                                     executables=['/sdk/dotnet'])],
                    blockers=[], validation_blockers=[], plan_digest='plan',
                    recipes=[dict(id='Api:0:build')], declarations={}, lockfiles={}, checks=[])
        evidence = dict(policy_changes=[], plan_digest='plan', required_checks_verified=True,
                        checks=[dict(id='Api:0:build', status='passed', exit_code=0)],
                        toolchain_evidence=[dict(name='dotnet-sdk', component='Api', actual='8.0.100',
                                                 matches=True, path='/sdk/dotnet')])
        for policy, expected in ((base, True),
                                 (dict(base, validation_blockers=['No approved validation recipes.']), False),
                                 (dict(base, blockers=['Api: no prepared binding.']), False)):
            self.assertEqual(toolchain_policy.evidence_matches(policy, dict(evidence,
                policy_digest=toolchain_policy.digest(policy))), expected)

    def test_reported_mismatches_are_claims_and_missing_claims_are_not_passes(self):
        frozen = {'policy': {'toolchains': [dict(name='dotnet-sdk', component='Api', required='8.0.100')]}}
        findings = environment_plan.reported_discrepancies(frozen, {'tools': [
            dict(tool='dotnet', component='Api', version='9.0.100')]})
        self.assertEqual(findings[0]['required'], '8.0.100')
        self.assertEqual(findings[0]['reported'], '9.0.100')
        self.assertIs(findings[0]['verified'], False)
        self.assertEqual(environment_plan.reported_discrepancies(frozen, {}), [])
        with self.assertRaises(ValueError):
            environment_plan.reported_discrepancies(frozen, {'tools': [{}]})


class LaunchBindingTests(unittest.TestCase):
    def test_native_plain_text_pane_acknowledgements_are_not_treated_as_json(self):
        import server
        for arguments in (('pane', 'run', 'w1:p1', 'export KEY=value'), ('pane', 'close', 'w1:p1')):
            with patch('server.subprocess.run', return_value=Mock(returncode=0, stdout='Accepted\n', stderr='')):
                self.assertEqual(server.command(*arguments), {'admitted': True})
    def frozen(self, sdk):
        return dict(digest='policy', checkout=str(sdk.parent), policy=dict(plan_digest='plan', toolchains=[
            dict(name='dotnet-sdk', required='8.0.100', component='Api', cwd='Api', executables=[str(sdk)])]))

    def test_only_selected_environment_is_sent_without_gateway_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            sdk = Path(folder) / 'sdk' / 'dotnet'
            with patch.dict('os.environ', {'PRIVATE_GATEWAY_SECRET': 'do-not-copy'}):
                record = environment_launch.binding(self.frozen(sdk), Path(folder) / 'caches')
            self.assertEqual(record['state'], 'requested')
            self.assertEqual(record['environment']['DOTNET_ROOT'], str(sdk.parent))
            self.assertEqual(record['environment']['HERDR_ENVIRONMENT_PLAN'], 'plan')
            self.assertNotIn('PRIVATE_GATEWAY_SECRET', record['environment'])
            self.assertEqual(record['environment']['PATH'].split(environment_launch.os.pathsep)[0], str(sdk.parent))

    def test_different_component_sdks_require_one_component_dispatcher(self):
        with tempfile.TemporaryDirectory() as folder:
            frozen = self.frozen(Path(folder) / 'sdk' / 'dotnet')
            frozen['policy']['toolchains'].append(dict(name='dotnet-sdk', required='9.0.100',
                component='Other', cwd='Other', executables=[str(Path(folder) / 'other' / 'dotnet')]))
            record = environment_launch.binding(frozen, Path(folder) / 'caches')
            self.assertEqual(record['state'], 'requested')
            self.assertEqual(record['selection'], 'component_dispatch')
            self.assertEqual(record['environment']['DOTNET_ROOT'], '')
            self.assertNotIn(str(Path(folder) / 'other'), record['environment']['PATH'])

    def test_non_conflicting_capability_keeps_its_direct_sdk(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frozen = self.frozen(root / 'sdk' / 'dotnet')
            frozen['policy']['toolchains'].append(dict(name='dotnet-sdk', required='9.0.100',
                component='Other', cwd='Other', executables=[str(root / 'other' / 'dotnet')]))
            rustc = root / 'rust' / 'bin' / 'rustc'
            frozen['policy']['toolchains'].append(dict(name='rust-toolchain', required='1.85.1',
                component='Crate', cwd='Crate', executables=[str(rustc)]))
            record = environment_launch.binding(frozen, root / 'caches')
            self.assertEqual(record['selection'], 'component_dispatch')
            self.assertEqual(record['environment']['DOTNET_ROOT'], '')
            self.assertEqual(record['environment']['RUSTC'], str(rustc))
            self.assertIn(str(rustc.parent), record['environment']['PATH'].split(environment_launch.os.pathsep))
            self.assertNotIn(str(root / 'other'), record['environment']['PATH'])
            self.assertNotIn(str(root / 'sdk'), record['environment']['PATH'])

    def test_dispatcher_only_shims_capabilities_that_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frozen = self.frozen(root / 'sdk' / 'dotnet')
            frozen['policy']['toolchains'].append(dict(name='dotnet-sdk', required='9.0.100',
                component='Other', cwd='Other', executables=[str(root / 'other' / 'dotnet')]))
            frozen['policy']['toolchains'].append(dict(name='rust-toolchain', required='1.85.1',
                component='Crate', cwd='Crate', executables=[str(root / 'rust' / 'bin' / 'rustc')]))
            launchers, _ = environment_dispatch.install(frozen['policy'], root, root / 'caches')
            self.assertTrue((launchers / 'dotnet').exists())
            self.assertFalse((launchers / 'rustc').exists())
            self.assertFalse((launchers / 'cargo').exists())

    def test_binding_uses_daemon_tab_creation_and_only_closes_its_original_pane(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Mock(path=root / 'organizations.sqlite3', projects=root)
            store.command.return_value = dict(root_pane=dict(pane_id='w1:p2'), tab=dict(workspace_id='w1'))
            job = dict(id='a' * 32, worktree_path=str(root / 'checkout'))
            with patch.object(environment_launch, 'activate_shell') as activate:
                result = environment_launch.configure(store, job, self.frozen(root / 'sdk' / 'dotnet'), 'w1:p1', 'w1')
            self.assertEqual(result, ('w1:p2', 'w1'))
            call = store.command.call_args_list[0].args
            self.assertEqual(call[:2], ('tab', 'create'))
            self.assertIn('DOTNET_ROOT=' + str(root / 'sdk'), call)
            self.assertEqual(store.command.call_args_list[1].args, ('pane', 'close', 'w1:p1'))
            store.wait_for_shell.assert_called_once_with('w1:p2')
            activate.assert_called_once()

    def test_an_older_runtime_failure_never_falls_back_to_the_unbound_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Mock(path=root / 'organizations.sqlite3', projects=root)
            store.command.side_effect = ValueError('unknown option --env')
            with self.assertRaisesRegex(ValueError, 'unknown option'):
                environment_launch.configure(store, dict(id='a' * 32), self.frozen(root / 'sdk' / 'dotnet'), 'w1:p1', 'w1')
            self.assertEqual(store.command.call_count, 1)
            store.wait_for_shell.assert_not_called()

    def test_missing_projects_contract_is_a_clear_launch_configuration_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Mock(path=root / 'organizations.sqlite3', projects=None)
            with self.assertRaisesRegex(ValueError, 'configured absolute projects directory'):
                environment_launch.configure(store, dict(id='a' * 32), self.frozen(root / 'sdk' / 'dotnet'), 'w1:p1', 'w1')
            self.assertTrue(any(call.kwargs.get('storage_preparation', {}).get('state') == 'failed'
                                for call in store.update_job.call_args_list))

    def test_a_response_from_another_workspace_never_closes_the_original_pane(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Mock(path=root / 'organizations.sqlite3', projects=root)
            store.command.return_value = dict(root_pane=dict(pane_id='w2:p2'))
            with self.assertRaisesRegex(ValueError, 'assigned workspace'):
                environment_launch.configure(store, dict(id='a' * 32), self.frozen(root / 'sdk' / 'dotnet'), 'w1:p1', 'w1')
            self.assertEqual(store.command.call_count, 1)

    def test_process_inheritance_is_checked_before_it_is_recorded(self):
        store = Mock()
        store.command.return_value = dict(process_info=dict(shell_pid=10,
            foreground_process_group_id=11, foreground_processes=[dict(pid=11)]))
        job = dict(id='job', pane_id='w1:p2', environment_binding=dict(
            state='requested', environment={'DOTNET_ROOT': '/sdk', 'HERDR_ENVIRONMENT_PLAN': 'plan'}))
        with patch.object(environment_launch, '_process_start', return_value='start'), \
                patch.object(environment_launch, '_process_environment', return_value={
                    'DOTNET_ROOT': '/sdk', 'HERDR_ENVIRONMENT_PLAN': 'plan', 'API_KEY': 'not-recorded'}):
            environment_launch.verify_process(store, job)
        record = store.update_job.call_args.kwargs['environment_binding']
        self.assertEqual(record['state'], 'inherited')
        self.assertNotIn('API_KEY', record['environment'])
        store.reset_mock()
        with patch.object(environment_launch, '_process_start', return_value='start'), \
                patch.object(environment_launch, '_process_environment', return_value={'DOTNET_ROOT': '/other'}):
            with self.assertRaisesRegex(ValueError, 'did not inherit.*DOTNET_ROOT'):
                environment_launch.verify_process(store, job)
        store.update_job.assert_not_called()

    def test_process_id_reuse_is_not_accepted_as_the_same_agent(self):
        store = Mock()
        store.command.return_value = dict(process_info=dict(shell_pid=10,
            foreground_process_group_id=11, foreground_processes=[dict(pid=11)]))
        job = dict(id='job', pane_id='w1:p2', environment_binding=dict(state='requested', environment={}))
        with patch.object(environment_launch, '_process_start', side_effect=['first', 'replacement']), \
                patch.object(environment_launch, '_process_environment', return_value={}):
            with self.assertRaisesRegex(ValueError, 'foreground agent changed'):
                environment_launch.verify_process(store, job)
        store.update_job.assert_not_called()


class PolicySourceTests(unittest.TestCase):
    """Where approval evidence comes from, and what an unbound capability says.

    These need Git but no Linux process boundary, so they hold on every host that
    can resolve a policy at all.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects = Path(self.temp.name) / 'projects'
        self.repo = self.projects / 'app'
        (self.repo / 'sub dir').mkdir(parents=True)
        (self.repo / 'global.json').write_text('{"sdk":{"version":"8.0.100"}}', encoding='utf-8')
        (self.repo / 'sub dir' / 'package.json').write_text('{"name":"x"}', encoding='utf-8')
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.git('add', '-A')
        self.git('commit', '-qm', 'fixture')
        self.revision = self.git('rev-parse', 'HEAD')

    def git(self, *arguments, input=None):
        return subprocess.run(['git', '-C', str(self.repo), *arguments], check=True, input=input,
                              capture_output=True, text=True).stdout.strip()

    def test_a_committed_vendor_tree_does_not_take_a_repository_off_its_legacy_route(self):
        # Herdr's own self-build compatibility route requires every discovered
        # requirement to be Flutter, Dart or Python. A committed vendored browser
        # bundle used to infer a Node requirement and push the repository onto the
        # generic path, where no adapter could approve it and validation stopped.
        repo = self.projects / 'self'
        for relative in ('web/dashboard', 'web/jaspr_dashboard/web/vendor/xterm', 'scripts'):
            (repo / relative).mkdir(parents=True)
        (repo / '.fvmrc').write_text('{"flutter":"3.24.0"}', encoding='utf-8')
        (repo / 'web/dashboard/pubspec.yaml').write_text(
            'name: dashboard\nenvironment:\n  sdk: ">=3.0.0 <4.0.0"\n'
            'dependencies:\n  flutter:\n    sdk: flutter\n', encoding='utf-8')
        (repo / 'web/jaspr_dashboard/web/vendor/xterm/xterm.js').write_text(
            'window.Terminal = function () {};\n', encoding='utf-8')
        (repo / 'scripts/validate.sh').write_text('echo ok\n', encoding='utf-8')
        (repo / 'scripts/build-web.sh').write_text('echo ok\n', encoding='utf-8')
        for arguments in (('init', '-q'), ('config', 'user.email', 'test@example.invalid'),
                          ('config', 'user.name', 'Test'), ('add', '-A'), ('commit', '-qm', 'fixture')):
            subprocess.run(['git', '-C', str(repo), *arguments], check=True, capture_output=True)
        policy = EnvironmentPolicies(self.projects, Unprepared()).resolve(repo)
        self.assertIsNone(policy.get('plan_digest'))
        self.assertFalse(policy.get('blockers'))
        self.assertNotIn('node', {entry['name'] for entry in policy['toolchains']})
        self.assertEqual(sorted(policy['checks']), ['scripts/build-web.sh', 'scripts/validate.sh'])

    def test_a_single_present_check_script_is_enough_for_the_legacy_route(self):
        # The validation runner uses the first candidate check script it finds,
        # so a fork that ships only scripts/build-web.sh is still Herdr's own
        # build; demanding every candidate name moved it to the generic path.
        repo = self.projects / 'fork'
        for relative in ('web/dashboard', 'scripts'):
            (repo / relative).mkdir(parents=True)
        (repo / '.fvmrc').write_text('{"flutter":"3.44.8"}', encoding='utf-8')
        (repo / 'web/dashboard/pubspec.yaml').write_text('name: dashboard\n', encoding='utf-8')
        (repo / 'scripts/build-web.sh').write_text('echo ok\n', encoding='utf-8')
        for arguments in (('init', '-q'), ('config', 'user.email', 'test@example.invalid'),
                          ('config', 'user.name', 'Test'), ('add', '-A'), ('commit', '-qm', 'fixture')):
            subprocess.run(['git', '-C', str(repo), *arguments], check=True, capture_output=True)
        policy = EnvironmentPolicies(self.projects, Unprepared()).resolve(repo)
        self.assertIsNone(policy.get('plan_digest'))
        self.assertEqual(policy['checks'], ['scripts/build-web.sh'])
        self.assertFalse(policy.get('blockers'))

    def test_the_approval_listing_carries_gits_own_identity_for_each_blob(self):
        entries = environment_policy.tree(self.repo, self.revision)
        self.assertEqual(entries['global.json']['blob'], self.git('hash-object', 'global.json'))
        self.assertEqual(entries['global.json']['size'], len('{"sdk":{"version":"8.0.100"}}'))
        self.assertEqual(entries['global.json']['mode'], '100644')
        # A path containing a space survives the listing verbatim.
        self.assertIn('sub dir/package.json', entries)

    def test_declaration_fingerprints_are_the_committed_blob_identities(self):
        policy = EnvironmentPolicies(self.projects, Unprepared()).resolve(self.repo)
        self.assertEqual(policy['declarations']['global.json'], self.git('hash-object', 'global.json'))

    def test_the_approval_listing_is_not_rebuilt_for_every_resolve(self):
        # Resolve runs on launch, on submission and on each delivery check; the
        # listing and the fingerprints are functions of the committed revision.
        settled = EnvironmentPolicies(self.projects, Unprepared())
        listing = Mock(wraps=environment_policy.project_git.git)
        with patch.object(environment_policy.project_git, 'git', listing):
            settled.resolve(self.repo)
            settled.resolve(self.repo)
        recursive = [call for call in listing.call_args_list
                     if 'ls-tree' in call.args and '-r' in call.args]
        self.assertEqual(len(recursive), 1, recursive)

    def test_submodules_have_an_explicit_coverage_blocker(self):
        self.git('update-index', '--add', '--cacheinfo',
                 '160000,' + self.revision + ',vendor/external')
        self.git('commit', '-qm', 'submodule fixture')
        policy = EnvironmentPolicies(self.projects, Unprepared()).resolve(self.repo)
        self.assertTrue(any('Submodule contents were not analysed (vendor/external)' in reason
                            for reason in policy['blockers']), policy['blockers'])
        self.assertIn('global.json', policy['declarations'])

    def test_tree_metadata_has_its_own_budget_without_relaxing_other_git_reads(self):
        blob = self.git('hash-object', '-w', '--stdin', input='')
        listing = ''.join(f'100644 blob {blob}\tfile{index:05}.py\n' for index in range(14000))
        revision = self.git('mktree', input=listing)
        self.assertEqual(len(environment_policy.tree(self.repo, revision)), 14000)
        with self.assertRaisesRegex(ValueError, 'Git output is too large'):
            environment_policy.project_git.git(self.repo, 'ls-tree', '-r', '-z', '-l', revision)

    def test_one_blocker_per_capability_with_a_remedy_that_applies(self):
        plan = dict(digest='d' * 64, revision='a' * 40, requirements=[
            dict(capability='dotnet-sdk', label='.NET SDK', version='8.0.100', components=['Api', 'Other']),
            dict(capability='node', label='Node.js', version='>=20 <23',
                 components=[f'pkg{index:02}' for index in range(40)])])
        with patch('environment_artifacts.host_target', return_value=dict(os='linux', architecture='x64', libc='glibc')):
            policy = environment_plan.bindings(plan, None)
        # A capability that can be prepared and one that cannot are different
        # facts; forty components must not become forty copies of either.
        self.assertEqual(len(policy['blockers']), 2, policy['blockers'])
        managed = next(item for item in policy['blockers'] if item.startswith('.NET SDK'))
        unmanaged = next(item for item in policy['blockers'] if item.startswith('Node.js'))
        self.assertIn('prepare this environment', managed)
        self.assertNotIn('no managed activation adapter', managed)
        self.assertIn('no managed activation adapter', unmanaged)
        self.assertIn('pkg00, pkg01, pkg02, pkg03 and 36 more', unmanaged)

    def test_managed_adapter_on_another_host_does_not_offer_impossible_preparation(self):
        plan = dict(digest='d' * 64, revision='a' * 40, requirements=[dict(
            capability='dotnet-sdk', label='.NET SDK', version='8.0.100', components=['Api'])])
        with patch('environment_artifacts.host_target', side_effect=ValueError('Requires Linux glibc.')):
            reason = environment_plan.bindings(plan)['blockers'][0]
        self.assertIn('Requires Linux glibc', reason)
        self.assertNotIn('prepare this environment', reason)

    def test_unresolved_selection_is_not_reported_as_an_unprepared_sdk(self):
        plan = dict(digest='d' * 64, revision='a' * 40, requirements=[dict(
            capability='dotnet-sdk', label='.NET SDK', version='', components=['Api'],
            state='unresolved', reason='The project declares conflicting requirements.')])
        reason = environment_plan.bindings(plan)['blockers'][0]
        self.assertIn('conflicting requirements', reason)
        self.assertNotIn('prepare this environment', reason)


@unittest.skipUnless(sys.platform == 'linux', 'Requires native Linux process/worktree execution.')
class GatewayPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = self.root / 'projects'
        self.repo = self.projects / 'app'
        (self.repo / 'Api').mkdir(parents=True)
        (self.repo / 'global.json').write_text('{"sdk":{"version":"8.0.100"}}')
        (self.repo / 'Api/App.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
        (self.repo / 'Api/Class.cs').write_text('class Example {}')
        self.git('init', '-q')
        self.baseline = self.commit()
        sdk = self.root / 'sdk'
        sdk.mkdir()
        self.executable = sdk / 'dotnet'
        self.executable.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo 8.0.100; exit 0; fi\n'
            'test "$DOTNET_ROOT" = "' + str(sdk) + '" || exit 41\n'
            'test "$(basename "$PWD")" = Api || exit 42\necho native-check-ran\n')
        self.executable.chmod(0o755)
        self.spec = dict(capability='dotnet-sdk', version='8.0.100', archives=[])
        def acquire(specification, *arguments, **options):
            if specification.get('provider', 'archive') != 'archive':
                import environment_artifacts
                return environment_artifacts.acquire(specification, *arguments, **options)
            return dict(artifact_id='fixture', specification=self.spec,
                        probe={'executable': str(self.executable), 'versions': ['8.0.100']})
        self.service = EnvironmentPreparation(ProjectAnalysis(self.projects), self.root / 'environments.sqlite3',
            self.root / 'sdks', resolve=lambda *args: self.spec,
            acquire=acquire)
        self.policies = EnvironmentPolicies(self.projects, self.service.store)
        self.binding = patch.object(toolchain_policy, 'PROJECT_RESOLVER', self.policies.resolve)
        self.binding.start()
        self.addCleanup(self.binding.stop)
        self.contributions = Contributions.__new__(Contributions)
        self.contributions.path_for = lambda task: self.repo

    def git(self, *arguments):
        return subprocess.run(['git', '-C', str(self.repo), *arguments], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git('add', '-A')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def prepare(self):
        plan = self.service.plan('app', self.baseline)
        identifiers = [item['id'] for item in plan['check_candidates'] if item['purpose'] == 'build']
        self.assertEqual(len(identifiers), 1)
        plan = self.service.select(plan['digest'], identifiers, 'test_admin')
        job = self.service.prepare(plan['digest'])
        self.service.tick()
        self.assertEqual(self.service.store.job(job['id'])['state'], 'ready')
        return plan

    def validate(self, target, **fields):
        runner = ValidationRuns(self.projects, lambda _: None, root=self.root / 'validation')
        run = dict(id='a' * 32, event_id='b' * 64, repository='app', path=str(self.repo), target=target,
                   started_at='test', actor='test')
        run.update(fields)
        folder = runner.root / run['id']
        folder.mkdir(parents=True)
        runner._execute(run, folder)
        return run

    def test_validation_scope_blockers_do_not_read_as_environment_failures(self):
        plan = self.service.plan('app', self.baseline)
        plan = self.service.select(plan['digest'], [], 'test_admin')
        job = self.service.prepare(plan['digest'])
        self.service.tick()
        self.assertEqual(self.service.store.job(job['id'])['state'], 'ready')
        organizations = OrganizationStore.__new__(OrganizationStore)
        organizations.projects = self.projects
        frozen = organizations.frozen_policy({'source_project': str(self.repo)}, {})
        self.assertEqual(frozen['policy']['blockers'], [])
        self.assertTrue(frozen['policy']['validation_blockers'])
        self.assertNotIn('Environment blocker', policy_guidance(frozen))
        self.assertFalse(self.contributions.policy_matches({}, {}))
        runner = ValidationRuns(self.projects, lambda _: None, root=self.root / 'validation')
        run = dict(id='a' * 32, event_id='b' * 64, repository='app', path=str(self.repo),
                   target=self.baseline, started_at='test', actor='test',
                   frozen_plan_digest=frozen['plan_digest'], frozen_policy_digest=frozen['digest'])
        folder = runner.root / run['id']
        folder.mkdir(parents=True)
        runner._execute(run, folder)
        self.assertEqual(run['state'], 'error')
        self.assertEqual(run['blocker'], 'validation_unapproved')
        self.assertIn('No approved validation recipes selected', run['note'])

    def test_unprepared_dotnet_cannot_publish_even_without_a_flutter_declaration(self):
        policy = self.policies.resolve(self.repo)
        self.assertTrue(policy['toolchains'])
        self.assertTrue(policy['blockers'])
        self.assertFalse(self.contributions.policy_matches({}, {}))

    def test_script_projects_without_sdk_requirements_keep_legacy_validation(self):
        for path in ('global.json', 'Api/App.csproj', 'Api/Class.cs'):
            (self.repo / path).unlink()
        (self.repo / 'scripts').mkdir()
        (self.repo / 'scripts/validate.sh').write_text('echo script-validation\n')
        self.baseline = self.commit()
        run = self.validate(self.baseline)
        self.assertEqual(run['state'], 'complete', run.get('note'))
        self.assertTrue(self.contributions.policy_matches({}, run))

    def test_unregistered_manifests_are_not_mistaken_for_no_sdk_requirements(self):
        for path in ('global.json', 'Api/App.csproj', 'Api/Class.cs'):
            (self.repo / path).unlink()
        (self.repo / 'pom.xml').write_text('<project/>')
        self.commit()
        policy = self.policies.resolve(self.repo)
        self.assertEqual(policy['toolchains'], [])
        self.assertTrue(policy['blockers'])
        self.assertFalse(self.contributions.policy_matches({}, {}))

    def test_prepared_prompt_real_check_and_publication_share_one_identity(self):
        plan = self.prepare()
        organizations = OrganizationStore.__new__(OrganizationStore)
        organizations.projects = self.projects
        frozen = organizations.frozen_policy({'source_project': str(self.repo)}, {})
        guidance = policy_guidance(frozen)
        self.assertIn('.NET SDK 8.0.100', guidance)
        self.assertIn('component Api', guidance)
        self.assertIn(str(self.executable), guidance)
        run = self.validate(self.baseline, frozen_plan_digest=frozen['plan_digest'],
                            frozen_policy_digest=frozen['digest'])
        self.assertEqual(run['state'], 'complete', run.get('note'))
        self.assertEqual(run['plan_digest'], plan['digest'])
        self.assertTrue(self.contributions.policy_matches({}, run))
        self.assertFalse(self.contributions.policy_matches({}, dict(run, toolchain_evidence=[])))
        self.assertFalse(self.contributions.policy_matches({}, dict(run, plan_digest='wrong')))
        store = Mock()
        store.get.return_value = {'toolchain_policy': dict(frozen, plan_digest='another-session-plan')}
        self.contributions.store = store
        self.assertFalse(self.contributions.policy_matches({'run_id': 'run', 'organization_id': 'org'}, run))

    def test_native_child_environment_is_observed_instead_of_the_gateways_environment(self):
        import os
        expected = dict(HERDR_ENVIRONMENT_PLAN='child-plan', DOTNET_ROOT=str(self.root / 'child-sdk'))
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 env=dict(os.environ, **expected))
        try:
            store = Mock()
            store.command.return_value = dict(process_info=dict(shell_pid=os.getpid(),
                foreground_process_group_id=child.pid, foreground_processes=[dict(pid=child.pid)]))
            job = dict(id='job', pane_id='w1:p2', environment_binding=dict(state='requested', environment=expected))
            environment_launch.verify_process(store, job)
            self.assertEqual(store.update_job.call_args.kwargs['environment_binding']['state'], 'inherited')
            job['environment_binding']['environment'] = dict(expected, DOTNET_ROOT='/wrong-sdk')
            with self.assertRaisesRegex(ValueError, 'did not inherit.*DOTNET_ROOT'):
                environment_launch.verify_process(store, job)
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_shell_activation_waits_for_exports_and_quotes_values(self):
        caches = self.root / 'activation'
        caches.mkdir()
        store = Mock()
        unusual = str(caches / "cache ' $not_a_variable")
        environment = {'DOTNET_ROOT': unusual, 'HERDR_ENVIRONMENT_PLAN': 'plan'}
        def run(*arguments):
            self.assertEqual(arguments[:3], ('pane', 'run', 'w1:p2'))
            subprocess.run(['bash', '-c', arguments[3]], check=True, capture_output=True)
        store.command.side_effect = run
        environment_launch.activate_shell(store, 'w1:p2', environment, caches)
        store.wait_for_shell.assert_called_once_with('w1:p2')
        self.assertEqual(list(caches.iterdir()), [])

    def test_component_dispatcher_executes_each_sdk_and_refuses_unknown_scopes(self):
        import environment_dispatch
        other = self.repo / 'Other'
        other.mkdir()
        second = self.root / 'sdk-two' / 'dotnet'
        second.parent.mkdir()
        second.write_text('#!/bin/sh\necho 9.0.100\n')
        second.chmod(0o755)
        policy = dict(plan_digest='plan', toolchains=[
            dict(name='dotnet-sdk', required='8.0.100', component='Api', executables=[str(self.executable)]),
            dict(name='dotnet-sdk', required='9.0.100', component='Other', executables=[str(second)])])
        launchers, digest = environment_dispatch.install(policy, self.repo, self.root / 'dispatch-cache')
        for component, expected in (('Api', '8.0.100'), ('Other', '9.0.100')):
            result = subprocess.run([str(launchers / 'dotnet'), '--version'], cwd=self.repo / component,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
        result = subprocess.run([str(launchers / 'dotnet'), '--version'], cwd=self.repo,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('change to the component directory', result.stderr)
        (launchers / 'bindings.json').write_text('{}')
        result = subprocess.run([str(launchers / 'dotnet')], cwd=other, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('integrity check', result.stderr)

    def test_candidate_changes_to_global_json_cannot_redefine_the_rules(self):
        self.prepare()
        (self.repo / 'global.json').write_text('{"sdk":{"version":"9.0.100"}}')
        candidate = self.commit()
        self.git('checkout', '--detach', self.baseline)
        run = self.validate(candidate)
        self.assertEqual(run.get('blocker'), 'policy_changed', run.get('note'))
        self.assertIn('global.json', run['policy_changes'])
        self.assertFalse(self.contributions.policy_matches({}, run))

    def test_session_approval_cannot_silently_migrate_to_another_plan(self):
        self.prepare()
        run = self.validate(self.baseline, frozen_plan_digest='another-plan', frozen_policy_digest='another-policy')
        self.assertEqual(run.get('blocker'), 'policy_changed')
        self.assertNotIn('toolchain_evidence', run)

    def test_repository_review_carries_the_recorded_environment(self):
        plan = self.prepare()
        store = Mock(projects=self.projects)
        context = repository_review_context(store, dict(id='discussion', discussion_scope='repository',
            group_run=dict(profile=dict(project=str(self.repo))), runs=[]))
        self.assertEqual(context['environment']['plan_digest'], plan['digest'])
        self.assertTrue(any('.NET SDK 8.0.100' in item for item in context['environment']['requirements']))
        self.assertIn('do not install or switch SDKs', repository_review_instructions(context))

    def test_mismatching_binary_prevents_all_project_checks(self):
        self.prepare()
        self.executable.write_text('#!/bin/sh\necho 9.0.100\n')
        run = self.validate(self.baseline)
        self.assertEqual(run.get('blocker'), 'missing_toolchain', run.get('note'))
        self.assertIn('Api', run['note'])
        self.assertFalse(self.contributions.policy_matches({}, run))

    def test_both_components_need_their_own_probe_evidence(self):
        self.prepare()
        policy = self.policies.resolve(self.repo)
        first = next(item for item in policy['toolchains'] if item['component'] == 'Api')
        policy['toolchains'].append(dict(first, component='Other', cwd='Other'))
        (self.repo / 'Other').mkdir()
        measured = toolchain_policy.verify(policy, root=self.repo)
        self.assertEqual({item['component'] for item in measured}, {'.', 'Api', 'Other'})
        evidence = dict(policy_digest=toolchain_policy.digest(policy), plan_digest=policy['plan_digest'],
            required_checks_verified=True, checks=[dict(id=policy['recipes'][0]['id'], status='passed', exit_code=0)],
            toolchain_evidence=measured)
        self.assertTrue(toolchain_policy.evidence_matches(policy, evidence))
        evidence['toolchain_evidence'] = measured[:1]
        self.assertFalse(toolchain_policy.evidence_matches(policy, evidence))
