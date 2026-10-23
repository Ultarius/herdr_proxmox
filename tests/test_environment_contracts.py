"""Shared environment workers accept strategies without ecosystem branches."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import environment_adapters as adapters
import environment_artifacts as artifacts
import environment_execution
import environment_plan
import environment_providers as providers
import environment_python
import environment_recipes
import toolchain_policy


def adapter(**changes):
    return adapters.Adapter('fixture-sdk', 'bin/compiler', ('compiler', 'compile-alias'),
        ('--version',), ('--version',), r'(1\.0\.0)', 1, aliases=('compile-alias',), **changes)


class ContractTests(unittest.TestCase):
    def test_build_storage_and_prompt_contracts_are_adapter_owned(self):
        install = Mock(return_value=Path('/execution/commands'))
        contract = adapter(build_profiles=(('compact', (('FIXTURE_DEBUG', 'lines'),)),),
            build_guidance=(('compact', 'Fixture compact explanation.'),),
            output_variables=(('FIXTURE_OUTPUT', '{outputs}/compiled'),),
            capacity_installer=install, capacity_guidance='Fixture broker explanation.')
        entry = dict(name='fixture-sdk', component='.', executables=['/sdk/compiler'], build_mode='compact')
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': contract}):
            self.assertEqual(toolchain_policy.environment(dict(toolchains=[entry]), base={})['FIXTURE_DEBUG'], 'lines')
            self.assertEqual(contract.output_environment('/generated'), {'FIXTURE_OUTPUT': '/generated/compiled'})
            self.assertEqual(adapters.install_wrappers([entry], '/scratch'), Path('/execution/commands'))
            metadata = adapters.session_tools([entry])[0]
            self.assertEqual(metadata['build_guidance'], 'Fixture compact explanation.')
            self.assertEqual(metadata['capacity_guidance'], 'Fixture broker explanation.')
            import session_workspace
            guidance = session_workspace.toolchain_guidance(dict(toolchain_policy=dict(policy=dict(toolchains=[entry]))))
            self.assertIn('Fixture compact explanation.', guidance)
            self.assertIn('Fixture broker explanation.', guidance)
            self.assertNotIn('Cargo', guidance)
        install.assert_called_once_with('/scratch')
        self.assertEqual(contract.build_environment(dict(entry, build_mode=None)), {})

    def test_installer_provider_dispatch_does_not_use_an_archive_or_capability_branch(self):
        acquire = Mock(return_value={'verified': True})
        provider = providers.Provider(acquire=acquire)
        specification = dict(provider='fixture-installer', capability='fixture-sdk')
        with patch.dict(providers.PROVIDERS, {'fixture-installer': provider}), patch.dict(
                adapters.ADAPTERS, {'fixture-sdk': adapter(provider='fixture-installer')}):
            result = artifacts.acquire(specification, '/store', cancelled=lambda: False)
        self.assertEqual(result, {'verified': True})
        self.assertEqual(acquire.call_args.args, (specification, '/store'))
        self.assertIsNone(acquire.call_args.kwargs['verify'])
        acquire.reset_mock()
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': adapter()}), self.assertRaisesRegex(
                ValueError, 'not declared by its capability'):
            artifacts.acquire(specification, '/store')
        acquire.assert_not_called()
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': adapter(provider='unregistered')}), self.assertRaisesRegex(
                ValueError, 'registered environment provider'):
            artifacts.acquire(dict(specification, provider='unregistered'), '/store')

    def test_full_declarations_reach_provider_and_exact_only_entry_point_refuses(self):
        item = dict(capability='python', conflicts=[], notation='pep440', evidence=[
            dict(observations=[dict(role='constraint', value='>=3.11', notation='pep440'),
                               dict(role='constraint', value='~3.11', notation='poetry')])])
        target = dict(os='linux', architecture='x64', libc='glibc')
        selected = providers.resolve_requirement(item, target)
        self.assertTrue(selected['version'].startswith('3.11.'))
        with patch.object(environment_python, 'select', return_value=selected) as select:
            self.assertEqual(providers.resolve_requirement(item, target), selected)
        self.assertEqual(select.call_args.args, ([('>=3.11', 'pep440'), ('~3.11', 'poetry')], target))
        with self.assertRaisesRegex(ValueError, 'full requirement declarations'):
            artifacts.resolve('python', '3.12.9', target=target)

    def test_materialization_and_command_rebinding_are_generic_and_preserve_policy(self):
        def materialize(entry, checkout, caches, **options):
            entry.update(executables=['/execution/bin/runtime'], execution_environment='/execution')
            return dict(provider='fixture', state='complete')
        contract = adapter(materialize_entry=materialize,
                           execution_variables=(('FIXTURE_ENV', '{environment}'), ('FIXTURE_EXE', '{executable}')))
        entry = dict(name='fixture-sdk', component='app', cwd='app', adapter_version=1,
                     executables=['/sdk/bin/compiler'], restore_recipe=dict(requires_restore=True))
        policy = dict(toolchains=[entry], recipes=[dict(id='check', cwd='app', arguments=['/sdk/bin/compiler', 'test'])])
        original = copy.deepcopy(policy)
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': contract}):
            runtime, receipts = environment_execution.materialize_execution_policy(policy, '/project', '/cache', authorized=True)
        self.assertEqual(policy, original)
        self.assertEqual(runtime['recipes'][0]['arguments'], ['/execution/bin/runtime', 'test'])
        self.assertEqual(runtime['recipes'][0]['bindings'], runtime['toolchains'])
        self.assertEqual(receipts, [dict(provider='fixture', state='complete')])
        self.assertEqual(contract.command('/execution/bin/runtime', 'compile-alias'), '/execution/bin/runtime')
        self.assertEqual(contract.environment('/execution/bin/runtime', '/execution'),
                         dict(FIXTURE_ENV='/execution', FIXTURE_EXE='/execution/bin/runtime'))

    def test_permission_preflight_has_no_partial_effects_in_a_mixed_policy(self):
        materialize = Mock()
        contract = adapter(materialize_entry=materialize)
        entries = [dict(name='fixture-sdk', restore_recipe=dict(requires_restore=required))
                   for required in (False, True)]
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': contract}), self.assertRaisesRegex(ValueError, 'separate project-code'):
            environment_execution.materialize_execution_policy(dict(toolchains=entries), '/project', '/cache')
        materialize.assert_not_called()

    def test_evidence_path_and_provider_receipt_verification_use_declared_hooks(self):
        evidence = Mock(return_value=True)
        verify = Mock()
        contract = adapter(provider='fixture-installer', evidence_path=evidence)
        entry, observed, receipts = dict(name='fixture-sdk', executables=['/base']), dict(path='/execution'), [dict(state='complete')]
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': contract}), patch.dict(
                providers.PROVIDERS, {'fixture-installer': providers.Provider(acquire=Mock(), verify_entry=verify)}):
            self.assertTrue(contract.matches_evidence_path(entry, observed, receipts))
            providers.verify_entry(entry)
        evidence.assert_called_once_with(entry, observed, receipts)
        verify.assert_called_once_with(entry)
        self.assertTrue(adapter().matches_evidence_path(entry, dict(path='/base'), []))
        self.assertFalse(adapter().matches_evidence_path(entry, observed, []))
        evidence.return_value = 'undecidable'
        self.assertFalse(contract.matches_evidence_path(entry, observed, receipts))

    def test_automatic_checks_are_declared_by_the_command_adapter(self):
        check = dict(id='fixture-test', source='fixture', arguments=['compiler', 'check'])
        eligibility = Mock(return_value=True)
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': adapter(automatic_check=eligibility)}):
            self.assertEqual(environment_recipes.automatic(dict(check_candidates=[check])), ['fixture-test'])
        eligibility.assert_called_once_with(check)
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': adapter()}):
            self.assertEqual(environment_recipes.automatic(dict(check_candidates=[check])), [])

    def test_binding_exposes_protected_inputs_without_inspecting_recipe_in_policy_resolver(self):
        contract = adapter(materialize_entry=Mock())
        requirement = dict(capability='fixture-sdk', label='Fixture', components=['app'], version='1.0.0',
                           artifact=dict(provider='archive'), restore_recipes=[dict(component='app', inputs={'app/fixture.lock': 'hash'})])
        plan = dict(digest='plan', revision='a' * 40, requirements=[requirement])
        receipt = dict(state='ready', digest='plan', revision='a' * 40,
            receipts=[dict(capability='fixture-sdk', version='1.0.0', components=['app'],
                           specification=requirement['artifact'], probe=dict(executable=str(Path.cwd() / 'sdk/bin/compiler')))])
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': contract}):
            policy = environment_plan.bindings(plan, receipt)
        self.assertEqual(policy['toolchains'][0]['protected_inputs'], ['app/fixture.lock'])
        self.assertEqual(policy['toolchains'][0]['artifact_receipt'], receipt['receipts'][0])
        self.assertFalse(policy['restore_authorized'])

    def test_companion_evidence_does_not_require_a_separate_sdk_adapter(self):
        primary = dict(name='fixture-sdk', component='.', required='1.0.0', executables=['/sdk/compiler'],
                       companions=[dict(name='fixture-helper', component='.', required='2.0.0', executables=['/sdk/helper'])])
        policy = dict(toolchains=[primary], plan_digest='plan', recipes=[dict(id='test')])
        evidence = dict(policy_digest=toolchain_policy.digest(policy), plan_digest='plan',
            required_checks_verified=True, checks=[dict(id='test', status='passed', exit_code=0)],
            toolchain_evidence=[dict(name=name, component='.', actual=version, matches=True, path=path)
                for name, version, path in (('fixture-sdk', '1.0.0', '/sdk/compiler'), ('fixture-helper', '2.0.0', '/sdk/helper'))])
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': adapter()}):
            self.assertTrue(toolchain_policy.evidence_matches(policy, evidence))
            evidence['toolchain_evidence'][1]['path'] = '/another/helper'
            self.assertFalse(toolchain_policy.evidence_matches(policy, evidence))

    def test_cache_settings_are_adapter_owned_and_only_selected_directories_are_created(self):
        contract = adapter(cache_directories=('fixture',),
                           cache_variables=(('FIXTURE_HOME', '{caches}/fixture'), ('FIXTURE_FLAG', '1')))
        with tempfile.TemporaryDirectory() as folder, patch.dict(adapters.ADAPTERS, {'fixture-sdk': contract}):
            root = Path(folder) / 'execution'
            variables = adapters.execution_caches([dict(name='fixture-sdk')], root)
            self.assertFalse(root.exists())
            self.assertEqual(Path(variables['FIXTURE_HOME']), root / 'fixture')
            self.assertEqual(variables['FIXTURE_FLAG'], '1')
            adapters.execution_caches([dict(name='fixture-sdk')], root, create=True)
            self.assertEqual([path.name for path in root.iterdir()], ['fixture'])
            node_root = Path(folder) / 'node-execution'
            self.assertEqual(adapters.execution_caches([dict(name='node')], node_root, create=True), {})
            self.assertTrue(node_root.is_dir())
            self.assertEqual(list(node_root.iterdir()), [])

    def test_candidate_argument_hooks_resolve_entry_points_without_generic_ecosystem_checks(self):
        proposal = dict(path='app', candidate_commands=[dict(command=['compiler', 'test'], purpose='test', source='convention')])
        hook = Mock(return_value=['compiler', 'test', 'fixture.project'])
        with patch.dict(adapters.ADAPTERS, {'fixture-sdk': adapter(candidate_arguments=hook)}):
            result = environment_recipes.candidates(dict(components=[proposal]), ['app/fixture.project'])
        self.assertEqual(result[0]['arguments'], ['compiler', 'test', 'fixture.project'])
        hook.assert_called_once_with(['compiler', 'test'], 'app', {'app/fixture.project'})
        dotnet = adapters.get('dotnet-sdk').candidate_arguments
        self.assertIsNone(dotnet(['dotnet', 'test'], 'app', ['app/A.csproj', 'app/B.csproj']))
        self.assertEqual(dotnet(['dotnet', 'test'], 'app', ['app/A.csproj', 'app/B.csproj', 'app/Team.sln']),
                         ['dotnet', 'test', 'Team.sln'])


if __name__ == '__main__':
    unittest.main()
