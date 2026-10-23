"""Frozen build modes and real Git worktrees using managed execution storage."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import environment_adapters
import environment_plan
import execution_storage
import toolchain_policy


class ExecutionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.projects = self.base / 'projects'
        self.projects.mkdir()
        self.project = self.projects / 'game'
        self.project.mkdir()
        self.root = self.base / 'storage'
        self.variables = patch.dict(os.environ, HERDR_PROJECTS=str(self.projects),
                                    HERDR_WORKSPACE_STORAGE_ROOT=str(self.root))
        self.variables.start()
        self.addCleanup(self.variables.stop)
        self.git('init')
        self.git('config', 'user.name', 'Execution storage test')
        self.git('config', 'user.email', 'storage@example.invalid')
        (self.project / 'Cargo.lock').write_text('version = 3\n', encoding='utf-8')
        (self.project / 'source.rs').write_text('fn main() {}\n', encoding='utf-8')
        self.git('add', 'Cargo.lock', 'source.rs')
        self.git('commit', '-m', 'fixture')
        self.agent_id, self.second_id, self.validation_id = 'a' * 32, 'b' * 32, 'c' * 32

    def git(self, *arguments):
        return subprocess.check_output(['git', '-C', str(self.project), *arguments], stderr=subprocess.DEVNULL)

    def entry(self, name='rust-toolchain', *, mode='compact', storage=True):
        value = dict(name=name, component='.', cwd='.', required='1.0.0',
                     executables=[str(self.base / 'sdk' / 'bin' / ('rustc' if name == 'rust-toolchain' else 'node'))])
        if mode is not None:
            value['build_mode'] = mode
        if storage:
            value['storage_contract'] = 1
        return value

    def plan(self):
        report = dict(project='game', components=[], requirements=[],
                      source=dict(scope='committed', revision='f' * 40))
        return environment_plan.build(report, release_entries={})

    def bound_policy(self, plan):
        artifact = dict(provider='archive', version='1.0.0')
        plan = dict(plan, requirements=[dict(capability='rust-toolchain', label='Rust', version='1.0.0',
                    components=['.'], artifact=artifact, evidence=[], adapter_version=2)])
        receipt = dict(state='ready', digest=plan['digest'], revision=plan['revision'], receipts=[
            dict(capability='rust-toolchain', version='1.0.0', components=['.'], specification=artifact,
                 probe=dict(executable=str(self.base / 'sdk' / 'bin' / 'rustc'), cargo_version='1.0.0'))])
        return environment_plan.bindings(plan, receipt)

    def prepare(self, entries=None, *, checkout=None, lane='agent', identity=None, create=True):
        return execution_storage.prepare(entries or [self.entry()], checkout or self.project,
            lane=lane, execution_id=identity or self.agent_id, create=create, projects=self.projects)

    def test_compact_is_default_full_is_explicit_and_each_plan_freezes_its_mode(self):
        compact = self.plan()
        self.assertEqual(compact['build_mode'], 'compact')
        compact_policy = self.bound_policy(compact)
        compact_environment = toolchain_policy.environment(compact_policy, base={})
        self.assertEqual(compact_policy['toolchains'][0]['storage_contract'], 1)
        self.assertEqual(compact_environment['CARGO_PROFILE_DEV_DEBUG'], 'line-tables-only')
        self.assertEqual(compact_environment['CARGO_PROFILE_TEST_DEBUG'], 'line-tables-only')
        self.assertEqual(compact_environment['CARGO_INCREMENTAL'], '0')
        self.assertFalse(any('ASSERTIONS' in key for key in compact_environment))
        execution_storage.configure_settings(dict(build_mode='full'))
        full = self.plan()
        self.assertEqual(full['build_mode'], 'full')
        self.assertNotEqual(compact['digest'], full['digest'])
        full_environment = toolchain_policy.environment(self.bound_policy(full), base={})
        self.assertEqual(full_environment['CARGO_PROFILE_DEV_DEBUG'], '2')
        self.assertEqual(full_environment['CARGO_PROFILE_TEST_DEBUG'], '2')
        self.assertEqual(full_environment['CARGO_INCREMENTAL'], '1')
        # A saved compact policy keeps its flags after the administrator changes defaults.
        self.assertEqual(toolchain_policy.environment(compact_policy, base={}), compact_environment)
        self.assertEqual(execution_storage.settings()['build_mode'], 'full')

    def test_legacy_plan_keeps_build_and_storage_behavior_until_reapproved(self):
        plan = self.plan()
        plan.pop('build_mode')
        plan.pop('storage_version')
        policy = self.bound_policy(plan)
        entry = policy['toolchains'][0]
        self.assertNotIn('build_mode', entry)
        self.assertNotIn('storage_contract', entry)
        self.assertEqual(environment_adapters.get('rust-toolchain').build_environment(entry), {})
        environment = toolchain_policy.environment(policy, base={})
        self.assertFalse(any(key.startswith('CARGO_PROFILE_') or key == 'CARGO_INCREMENTAL' for key in environment))
        before = copy.deepcopy(policy)
        result = self.prepare(policy['toolchains'])
        self.assertEqual(result['environment'], {})
        self.assertEqual(result['manifest'], {})
        self.assertEqual(result['outputs'], [])
        self.assertEqual(policy, before)

    def test_current_storage_contract_is_independent_of_build_mode(self):
        plan = self.plan()
        plan.pop('build_mode')
        entry = self.bound_policy(plan)['toolchains'][0]
        self.assertEqual(entry['storage_contract'], 1)
        self.assertNotIn('build_mode', entry)
        self.assertFalse(self.root.exists())

    def test_private_cargo_home_and_managed_output_are_separate_from_sources_and_downloads(self):
        configuration = self.prepare()
        variables = configuration['environment']
        cargo_home = Path(variables['CARGO_HOME'])
        output = Path(variables['CARGO_TARGET_DIR'])
        self.assertTrue(cargo_home.is_relative_to(self.root / 'objects'))
        self.assertTrue(output.is_relative_to(self.root / 'objects'))
        self.assertEqual(configuration['outputs'], [str(output)])
        self.assertNotEqual(cargo_home.parent, output)
        self.assertFalse(cargo_home.is_relative_to(self.project))
        self.assertFalse(output.is_relative_to(self.project))
        self.assertEqual(variables['HERDR_BUILD_OUTPUT_ROOT'], str(output))
        self.assertEqual(int(variables['HERDR_BUILD_OUTPUT_LIMIT_BYTES']), execution_storage.DEFAULTS['active_output_limit_bytes'])
        shared = Path(configuration['manifest']['caches'][0]['shared'])
        self.assertFalse(cargo_home.is_relative_to(shared))
        entries = execution_storage.manager(projects=self.projects).inventory(active_execution_ids={self.agent_id})['entries']
        self.assertEqual({entry['kind'] for entry in entries}, {'build-output', 'execution-cache', 'download-cache'})
        self.assertTrue(all(entry['project'] == str(self.project) for entry in entries))
        self.assertTrue((self.project / 'source.rs').is_file())

    def test_validation_worktree_outside_projects_uses_original_project_ownership(self):
        external = self.base / 'validation-runs' / self.validation_id / 'checkout'
        external.parent.mkdir(parents=True)
        self.git('worktree', 'add', '--detach', str(external))
        agent = self.prepare()
        validation = self.prepare(checkout=external, lane='validation', identity=self.validation_id)
        self.assertEqual(agent['manifest']['repository'], validation['manifest']['repository'])
        self.assertNotEqual(agent['environment']['CARGO_HOME'], validation['environment']['CARGO_HOME'])
        self.assertNotEqual(agent['environment']['CARGO_TARGET_DIR'], validation['environment']['CARGO_TARGET_DIR'])
        self.assertNotEqual(agent['manifest']['caches'][0]['shared'], validation['manifest']['caches'][0]['shared'])
        records = execution_storage.manager(projects=self.projects).inventory(active_execution_ids={self.agent_id, self.validation_id})['entries']
        self.assertTrue(records)
        self.assertTrue(all(record['project'] == str(self.project) for record in records))

    def test_npm_cache_metadata_stays_private_to_each_execution(self):
        entries = [self.entry('node')]
        first = self.prepare(entries)
        second = self.prepare(entries, identity=self.second_id)
        validation = self.prepare(entries, lane='validation', identity=self.validation_id)
        self.assertNotEqual(first['environment']['npm_config_cache'], second['environment']['npm_config_cache'])
        self.assertNotEqual(first['environment']['npm_config_cache'], validation['environment']['npm_config_cache'])
        self.assertNotIn('NODE_PATH', first['environment'])
        self.assertNotIn('CARGO_HOME', first['environment'])
        self.assertEqual(first['outputs'], [])
        self.assertFalse((self.project / 'node_modules').exists())
        self.assertFalse((self.project / 'vendor').exists())

    def test_preview_and_unsupported_adapters_do_not_allocate_storage(self):
        preview = self.prepare(create=False)
        self.assertIn('CARGO_TARGET_DIR', preview['environment'])
        self.assertFalse(self.root.exists())
        dotnet = dict(name='dotnet-sdk', component='.', executables=[], storage_contract=1)
        configuration = self.prepare([dotnet])
        self.assertEqual(configuration['environment'], {})
        self.assertFalse(self.root.exists())

    def test_finish_closes_only_managed_execution_roots_and_preserves_project_evidence(self):
        configuration = self.prepare()
        output = Path(configuration['environment']['CARGO_TARGET_DIR'])
        (output / 'rebuildable.o').write_bytes(b'object')
        result = execution_storage.finish(configuration, self.project, close=True)
        self.assertEqual(result['errors'], [])
        entries = execution_storage.manager(projects=self.projects).inventory(active_execution_ids=())['entries']
        self.assertEqual({entry['state'] for entry in entries if entry['execution_id'] == self.agent_id}, {'closed'})
        self.assertTrue(all(entry['eligible'] for entry in entries))
        self.assertTrue((self.project / 'Cargo.lock').exists())
        self.assertTrue((self.project / 'source.rs').exists())

    def test_component_dispatch_uses_exact_gateway_paths_without_allocating_another_store(self):
        import environment_dispatch
        import environment_providers
        entry = self.entry()
        entry.update(required='1.0.0', adapter_version=environment_adapters.get(entry['name']).version,
                     executables=[str(self.base / 'sdk/bin/rustc')])
        configuration = self.prepare([entry])
        caches = self.base / 'binding' / self.agent_id
        _, digest = environment_dispatch.install(dict(plan_digest='frozen', toolchains=[entry]), self.project, caches,
                                                 execution_environment=configuration['environment'])
        with patch.object(environment_dispatch.Path, 'cwd', return_value=self.project), \
                patch.object(environment_providers, 'verify_entry'), \
                patch.object(execution_storage, 'manager', side_effect=AssertionError('Dispatcher must not allocate')), \
                patch.object(environment_dispatch.os, 'execve') as execute:
            environment_dispatch.main([str(caches / 'dispatch/bindings.json'), digest, 'rustc', '--version'])
        environment = execute.call_args.args[2]
        self.assertEqual(environment['CARGO_HOME'], configuration['environment']['CARGO_HOME'])
        self.assertEqual(environment['CARGO_TARGET_DIR'], configuration['environment']['CARGO_TARGET_DIR'])
        self.assertEqual(environment['HERDR_BUILD_STORAGE_ROOT'], str(self.root))

    def test_tool_clean_can_remove_its_outputs_without_destroying_storage_ownership(self):
        import shutil
        configuration = self.prepare()
        output = Path(configuration['environment']['CARGO_TARGET_DIR'])
        (output / 'compiled-object').write_bytes(b'generated')
        shutil.rmtree(output)
        reopened = self.prepare()
        self.assertEqual(configuration['outputs'], reopened['outputs'])
        self.assertTrue(output.is_dir())
        execution_storage.finish(reopened, self.project, close=True)
        rows = execution_storage.manager(projects=self.projects).inventory(active_execution_ids=())['entries']
        self.assertTrue(all(row['eligible'] for row in rows), rows)

    def test_unknown_storage_contract_is_refused_before_allocation(self):
        entry = dict(self.entry(), storage_contract=2)
        with self.assertRaisesRegex(ValueError, 'storage contract changed'):
            self.prepare([entry])
        self.assertFalse(self.root.exists())

    def test_projects_configuration_is_explicit_and_environment_cannot_redirect_it(self):
        with patch.dict(os.environ, HERDR_PROJECTS=str(self.base / 'wrong-projects')):
            with self.assertRaisesRegex(ValueError, 'configured absolute projects directory'):
                execution_storage.manager()
            with self.assertRaisesRegex(ValueError, 'configured absolute projects directory'):
                execution_storage.prepare([self.entry()], self.project, lane='agent', execution_id=self.agent_id)
            self.assertFalse(self.root.exists())
            binding = self.prepare()
            self.assertEqual(binding['projects'], str(self.projects))
            self.assertTrue(execution_storage.grants(binding, projects=self.projects))
            execution_storage.finish(binding, self.project, close=True)

    def test_allocation_retries_back_off_but_respect_the_total_deadline(self):
        from workspace_storage import StorageBusy
        clock, sleeps = [0.0], []
        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        store = Mock()
        store.allocate.side_effect = StorageBusy('busy')
        with patch.object(execution_storage.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(execution_storage.time, 'sleep', side_effect=sleep):
            with self.assertRaisesRegex(StorageBusy, 'retry this execution'):
                execution_storage._allocate(store, 'owned')
        self.assertEqual(sleeps[:5], [.1, .2, .4, .8, 1])
        self.assertTrue(all(0 < seconds <= 1 for seconds in sleeps))
        self.assertAlmostEqual(clock[0], 30)
        sleeps.clear()
        store.allocate.side_effect = [StorageBusy('busy'), StorageBusy('busy'), self.root]
        with patch.object(execution_storage.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(execution_storage.time, 'sleep', side_effect=sleep):
            self.assertEqual(execution_storage._allocate(store, 'owned'), self.root)
        self.assertEqual(sleeps, [.1, .2])

    def test_unavailable_optional_download_pool_does_not_block_preparation_or_close(self):
        from workspace_storage import Manager
        allocate = Manager.allocate
        for error in (ValueError('invalid pool'), OSError('unavailable pool')):
            with self.subTest(error=type(error).__name__):
                def without_pool(manager, identity, **options):
                    if options.get('kind') == 'download-cache':
                        raise error
                    return allocate(manager, identity, **options)
                with patch.object(Manager, 'allocate', autospec=True, side_effect=without_pool), \
                        patch('package_cache.seed') as seed:
                    binding = self.prepare()
                    self.assertTrue(all(Path(path).is_dir() for path in binding['outputs']))
                    seed.assert_not_called()
                    result = execution_storage.finish(binding, self.project, close=True)
                self.assertIn('normal downloads remain enabled', result['errors'][0])
                rows = execution_storage.manager(projects=self.projects).inventory(active_execution_ids=())['entries']
                self.assertTrue(all(row['state'] == 'closed' for row in rows))


if __name__ == '__main__':
    unittest.main()
