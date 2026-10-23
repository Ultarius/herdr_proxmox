"""Python selection, immutable recipes and distinct execution environments."""
import copy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import environment_adapters
import environment_python as python
import environment_python_execution as execution
from environment_execution import materialize_execution_policy
from project_access import ANCHORED_ACCESS
import toolchain_policy

TARGET = dict(os='linux', architecture='x64', libc='glibc')


class SelectionTests(unittest.TestCase):
    def test_discovered_minor_request_selects_latest_patch_and_exact_pin_stays_exact(self):
        import project_inventory
        import project_requirements
        data = copy.deepcopy(python.catalog())
        template = next(item for item in data['interpreters'].values()
                        if item['name'] == 'cpython' and item['major'] == 3 and item['minor'] == 12
                        and item['arch']['family'] == 'x86_64' and not item.get('variant'))
        data['interpreters'] = {str(patch): dict(template, patch=patch) for patch in (0, 1, 14)}
        for selected, expected in (('3.12', '3.12.14'), ('3.12.1', '3.12.1'),
                                   ('>=3.12,<3.13', '3.12.14')):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as folder:
                files = {'pyproject.toml': b'[project]\nrequires-python=">=3.12,<3.13"\n',
                         '.python-version': selected.encode()}
                report = project_requirements.analyse(Path(folder),
                    inventory=project_inventory.from_paths(files), read=lambda name, limit=None: files.get(name))
                requirement = report['components'][0]['requirements'][0]
                declarations = project_requirements.declarations(requirement)
                self.assertEqual(python.select(declarations, TARGET, data)['version'], expected)

    def test_range_selection_is_catalog_bounded_and_all_declarations_apply(self):
        value = python.select([('>=3.11', 'pep440'), ('<3.13', 'pep440')], TARGET)
        self.assertEqual(value, python.select([('>=3.11', 'pep440'), ('<3.13', 'pep440')], TARGET))
        self.assertTrue(value['version'].startswith('3.12.'))
        self.assertEqual(value['provider'], 'pinned_uv')
        self.assertEqual(value['selection_policy'], 'highest-stable-compatible-in-pinned-catalog-v1')
        self.assertEqual(len(value['distribution']['sha256']), 64)

    def test_unavailable_exact_pin_conflict_and_unknown_syntax_are_refused(self):
        for declarations in ([('3.99.99', 'pep440')], [('>=3.13', 'pep440'), ('<3.12', 'pep440')],
                             [('dynamic()', 'pep440')], []):
            with self.subTest(declarations=declarations), self.assertRaises(ValueError):
                python.select(declarations, TARGET)

    def test_arm_catalog_never_selects_x64_and_host_limit_is_explicit(self):
        value = python.select([('==3.12.9', 'pep440')], dict(TARGET, architecture='arm64'))
        self.assertEqual(value['distribution']['arch']['family'], 'aarch64')
        with self.assertRaisesRegex(ValueError, 'glibc'):
            python.select([('>=3.11', 'pep440')], dict(TARGET, libc='musl'))

    def test_adapter_registers_both_python_spellings(self):
        self.assertIn('python', environment_adapters.managed())
        for name in ('python', 'python3'):
            self.assertEqual(environment_adapters.commands()[name], 'python')

    def test_manager_environment_drops_inherited_selection(self):
        with patch.dict(os.environ, dict(PYTHONPATH='/wrong', PYTHONHOME='/wrong',
                VIRTUAL_ENV='/wrong', UV_PYTHON='/wrong', PIP_INDEX_URL='https://wrong'), clear=False):
            value = python.clean_environment(Path('/private'))
        self.assertNotIn('PYTHONPATH', value)
        self.assertNotIn('PYTHONHOME', value)
        self.assertNotIn('VIRTUAL_ENV', value)
        self.assertNotIn('PIP_INDEX_URL', value)
        self.assertEqual(value['UV_NO_CONFIG'], '1')
        self.assertEqual(value['UV_PYTHON_INSTALL_BIN'], 'false')


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.spec = python.select([('==3.12.9', 'pep440')], TARGET)

    def recipe(self, files, **changes):
        return execution.recipe('.', files, lambda name: files[name], self.spec, changes.get('revision', 'a' * 40))

    def test_manager_inputs_and_revision_participate_in_identity_not_execution_paths(self):
        files = {'pyproject.toml': b'[project]\nname="demo"\n', 'uv.lock': b'version=1\n'}
        value = self.recipe(files)
        self.assertEqual(value['manager'], 'uv')
        self.assertTrue(execution.valid_recipe(value))
        self.assertNotEqual(value['digest'], self.recipe(dict(files, **{'uv.lock': b'version=2\n'}))['digest'])
        self.assertNotEqual(value['digest'], self.recipe(files, revision='b' * 40)['digest'])
        self.assertNotIn('environment', value)
        self.assertNotIn('executable', value)

    def test_poetry_workspace_and_unlocked_dependencies_refuse_explicitly(self):
        for files, message in (({'poetry.lock': b''}, 'Poetry'),
                ({'pyproject.toml': b'[tool.uv.workspace]\nmembers=["api"]'}, 'workspace'),
                ({'requirements.txt': b'django'}, 'lock')):
            with self.subTest(files=files), self.assertRaisesRegex(ValueError, message):
                self.recipe(files)

    def test_hash_locked_pip_and_no_recursive_include(self):
        value = self.recipe({'requirements.lock': ('demo==1.0 --hash=sha256:' + 'a' * 64).encode()})
        self.assertEqual(value['manager'], 'pip')
        for text in ('-r other.txt', '--index-url https://other', 'demo>=1', 'file:///tmp/wheel'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'SHA-256'):
                self.recipe({'requirements.lock': text.encode()})

    def test_source_only_recipe_needs_no_dependency_permission(self):
        value = self.recipe({})
        self.assertEqual(value['manager'], 'venv')
        self.assertIsNone(value['dependency'])

    def test_nonregular_committed_inputs_have_an_actionable_refusal(self):
        for component, files in (('.', {'pyproject.toml': None}), ('.', {'requirements.lock': None}),
                                 ('.', {'uv.lock': None}), ('child', {'pyproject.toml': None})):
            with self.subTest(component=component, files=files), self.assertRaisesRegex(
                    ValueError, 'readable committed regular file'):
                execution.recipe(component, files, lambda name: files[name], self.spec, 'a' * 40)

    def test_permission_refusal_precedes_any_filesystem_or_process_effect(self):
        value = self.recipe({'pyproject.toml': b'', 'uv.lock': b'version=1'})
        run = Mock()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, 'separate project-code permission'):
                materialize_execution_policy(dict(toolchains=[dict(name='python', restore_recipe=value)]), root, root / 'never', run=run)
            self.assertFalse((root / 'never').exists())
        run.assert_not_called()

    def test_same_version_from_another_base_interpreter_is_not_a_valid_binding(self):
        entry = dict(required='3.12.9')
        sdk = dict(probe=dict(functionality=dict(prefix='/approved/base')))
        observed = dict(versions=['3.12.9'], functionality=dict(prefix='/execution/venv', base_prefix='/other/base'))
        with self.assertRaisesRegex(ValueError, 'frozen interpreter'):
            execution.verify_probe(observed, entry, Path('/execution/venv'), sdk)


@unittest.skipUnless(ANCHORED_ACCESS and sys.platform == 'linux', 'Requires native anchored Linux filesystem access.')
class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / 'project'
        self.checkout.mkdir()
        self.spec = dict(capability='python', version='.'.join(map(str, sys.version_info[:3])), target=TARGET,
                         uv_version='fixture')
        self.recipe = execution.recipe('.', [], lambda _: b'', self.spec, 'a' * 40)
        self.receipt = dict(artifact_id=execution.identity(self.spec), specification=self.spec,
                            probe=dict(executable=sys.executable, functionality=dict(prefix=sys.base_prefix)))
        self.entry = dict(name='python', label='Python', component='.', cwd='.', adapter_version=environment_adapters.get('python').version,
            required=self.spec['version'], executables=[sys.executable],
            restore_recipe=self.recipe, artifact_receipt=self.receipt)
        self.policy = dict(toolchains=[self.entry], recipes=[], plan_digest='f' * 64)
        # Real venv creation/probes, with a fixture base interpreter instead of a
        # network download. Production verifies PBS identity before this boundary.
        verify = patch.object(python, 'verify')
        verify.start()
        self.addCleanup(verify.stop)

    def test_launch_and_validation_are_distinct_environments_with_one_frozen_policy(self):
        before = copy.deepcopy(self.policy)
        launch, receipts = materialize_execution_policy(self.policy, self.checkout, self.root / 'launch')
        validation, verified = materialize_execution_policy(self.policy, self.checkout, self.root / 'validation')
        self.assertEqual(self.policy, before)
        self.assertNotEqual(launch['toolchains'][0]['executables'], validation['toolchains'][0]['executables'])
        self.assertEqual(receipts[0]['recipe_digest'], verified[0]['recipe_digest'])
        self.assertEqual(receipts[0]['probe']['functionality']['base_prefix'], sys.base_prefix)
        self.assertNotEqual(receipts[0]['probe']['functionality']['prefix'], sys.base_prefix)
        self.assertTrue(execution.evidence_path(self.entry, dict(path=verified[0]['executable']), verified))
        self.assertFalse(execution.evidence_path(self.entry, dict(path=receipts[0]['executable']), verified))
        run = Mock(wraps=subprocess.run)
        reused, _ = materialize_execution_policy(self.policy, self.checkout, self.root / 'launch', run=run)
        self.assertEqual(len(run.call_args_list), 1)  # Probe, never replay venv/restore.
        self.assertEqual(reused['toolchains'], launch['toolchains'])

    def test_failed_operation_is_recorded_and_never_implicitly_replayed(self):
        run = Mock(return_value=Mock(returncode=1))
        with self.assertRaisesRegex(ValueError, 'restoration failed'):
            materialize_execution_policy(self.policy, self.checkout, self.root / 'failed', run=run)
        marker = self.root / 'failed/python' / self.recipe['digest'] / 'restore.json'
        self.assertEqual(json.loads(marker.read_text())['state'], 'failed')
        run.reset_mock()
        with self.assertRaisesRegex(ValueError, 'inspection'):
            materialize_execution_policy(self.policy, self.checkout, self.root / 'failed', run=run)
        run.assert_not_called()

    def test_restore_environment_uses_the_adapter_variable_contract(self):
        contract = environment_adapters.get('python')
        contract = replace(contract, execution_variables=contract.execution_variables + (('FIXTURE_ENV', '{environment}'),))
        run = Mock(wraps=subprocess.run)
        with patch.dict(environment_adapters.ADAPTERS, {'python': contract}):
            _, receipts = materialize_execution_policy(self.policy, self.checkout, self.root / 'restore-env', run=run)
        create = next(call for call in run.call_args_list if call.args[0][1:5] == ['-I', '-B', '-m', 'venv'])
        self.assertEqual(create.kwargs['env']['FIXTURE_ENV'], receipts[0]['environment'])
        self.assertEqual(create.kwargs['env']['VIRTUAL_ENV'], receipts[0]['environment'])
        self.assertEqual(contract.environment(sys.executable, receipts[0]['environment'])['PYTHONDONTWRITEBYTECODE'], '1')
        self.assertEqual(create.kwargs['env']['UV_PYTHON'], self.receipt['probe']['executable'])

    def test_input_mutation_refuses_before_creating_environment(self):
        files = {'uv.lock': b'version=1', 'pyproject.toml': b''}
        value = execution.recipe('.', files, lambda name: files[name], self.spec, 'a' * 40)
        for name, content in files.items():
            (self.checkout / name).write_bytes(content)
        (self.checkout / 'uv.lock').write_bytes(b'version=2')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            materialize_execution_policy(dict(toolchains=[dict(self.entry, restore_recipe=value)]),
                                  self.checkout, self.root / 'changed', authorized=True)
        self.assertFalse((self.root / 'changed').exists())

    def test_committed_plan_freezes_python_without_running_a_project_manager(self):
        from environment_preparation import EnvironmentPreparation
        from project_analysis import ProjectAnalysis
        (self.checkout / 'pyproject.toml').write_text(
            '[project]\nname="demo"\nversion="0.1.0"\nrequires-python=">=3.12,<3.13"\n', encoding='utf-8')
        (self.checkout / 'uv.lock').write_text('version=1\n', encoding='utf-8')
        for arguments in (['git', 'init', '-q'], ['git', 'add', '.'],
                ['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'base']):
            subprocess.run(arguments, cwd=self.checkout, check=True, capture_output=True)
        service = EnvironmentPreparation(ProjectAnalysis(self.root), self.root / 'plans.db', self.root / 'sdks')
        with patch('environment_artifacts.host_target', return_value=TARGET), patch.object(python, 'acquire') as acquire:
            plan = service.plan('project')
        acquire.assert_not_called()
        item = plan['requirements'][0]
        self.assertEqual(item['capability'], 'python')
        self.assertEqual(item['activation'], 'available')
        self.assertTrue(item['version'].startswith('3.12.'))
        self.assertEqual(item['restore_recipes'][0]['manager'], 'uv')
        self.assertNotIn('restore_permission', plan)
        approved = service.authorize_restore(plan['digest'], True, 'administrator')
        self.assertNotEqual(approved['digest'], plan['digest'])
        self.assertNotIn('restore_permission', service.store.get(plan['digest']))
        self.assertEqual(approved['restore_permission'], dict(allowed=True, actor='administrator'))

    def test_base_fingerprint_allows_internal_links_but_refuses_external_links(self):
        tree = self.root / 'sdk'
        (tree / 'bin').mkdir(parents=True)
        (tree / 'bin/python3.12').write_bytes(b'python')
        (tree / 'bin/python3').symlink_to('python3.12')
        first = python.fingerprint(tree)
        self.assertEqual(first, python.fingerprint(tree))
        (tree / 'bin/python3.12').write_bytes(b'changed')
        self.assertNotEqual(first, python.fingerprint(tree))
        (tree / 'bin/external').symlink_to('/bin/sh')
        with self.assertRaisesRegex(ValueError, 'external symbolic link'):
            python.fingerprint(tree)

    def test_isolated_imports_preserve_the_sdk_with_explicit_bytecode_flag(self):
        tree = self.root / 'sdk'
        tree.mkdir()
        (tree / 'sdk_fixture.py').write_text('value = 42\n')
        before = python.fingerprint(tree)
        code = 'import sys; sys.path.insert(0, sys.argv[1]); import sdk_fixture; assert sdk_fixture.value == 42'
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([sys.executable, '-I', '-B', '-c', code, str(tree)], env=env, check=True)
        self.assertEqual(before, python.fingerprint(tree))
        # The environment setting alone cannot protect an isolated interpreter.
        subprocess.run([sys.executable, '-I', '-c', code, str(tree)], env=env, check=True)
        self.assertNotEqual(before, python.fingerprint(tree))

    def test_publication_needs_the_restore_receipt_not_only_a_matching_version(self):
        bound, receipts = materialize_execution_policy(self.policy, self.checkout, self.root / 'publish')
        policy = dict(self.policy, recipes=[dict(id='python:test')])
        evidence = dict(policy_digest=toolchain_policy.digest(policy), plan_digest=policy['plan_digest'],
            required_checks_verified=True, checks=[dict(id='python:test', status='passed', exit_code=0)],
            toolchain_evidence=[dict(name='python', component='.', actual=self.entry['required'],
                matches=True, path=bound['toolchains'][0]['executables'][0])])
        self.assertFalse(toolchain_policy.evidence_matches(policy, evidence))
        evidence['environment_restores'] = receipts
        self.assertTrue(toolchain_policy.evidence_matches(policy, evidence))
        evidence['environment_restores'] = [dict(receipts[0], base_artifact='wrong')]
        self.assertFalse(toolchain_policy.evidence_matches(policy, evidence))


@unittest.skipUnless(ANCHORED_ACCESS and sys.platform == 'linux', 'Requires native anchored Linux filesystem access.')
class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.spec = python.select([('==3.12.9', 'pep440')], TARGET)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as stream:
            item = tarfile.TarInfo('uv-linux/uv')
            data = b'fixture preparation tool'
            item.size, item.mode = len(data), 0o755
            stream.addfile(item, io.BytesIO(data))
        self.uv = archive.getvalue()
        self.distribution = b'fixture Python archive'
        self.spec['uv_archive']['sha256'] = hashlib.sha256(self.uv).hexdigest()
        self.spec['distribution']['sha256'] = hashlib.sha256(self.distribution).hexdigest()
        self.calls = []

    def fetch(self, url, destination, **options):
        destination.write_bytes(self.uv if url == self.spec['uv_archive']['url'] else self.distribution)

    def run_fixture(self, arguments, **context):
        self.calls.append((arguments, context))
        if arguments[1:] == ['--version']:
            return Mock(returncode=0, stdout='uv 0.12.19 (fixture)\n')
        if arguments[1:4] == ['--no-config', 'python', 'install']:
            env = context['env']
            final = Path(env['UV_PYTHON_INSTALL_DIR'])
            executable = final / self.spec['installation_key'] / 'bin/python3'
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'fixture interpreter')
            # uv creates an absolute minor-version directory alias.
            (final / 'cpython-3.12-linux-x86_64-gnu').symlink_to(executable.parents[1], target_is_directory=True)
            self.assertTrue(env['UV_PYTHON_INSTALL_MIRROR'].startswith('file:'))
            metadata = json.loads(Path(env['UV_PYTHON_DOWNLOADS_JSON_URL']).read_text())
            self.assertEqual(metadata[self.spec['installation_key']]['sha256'], self.spec['distribution']['sha256'])
            return Mock(returncode=0, stderr='')
        return Mock(returncode=0, stdout=json.dumps(dict(version='3.12.9', implementation='cpython',
            prefix=str(Path(arguments[0]).parents[1]), base_prefix=str(Path(arguments[0]).parents[1]))))

    def test_downloads_are_verified_and_fixups_happen_at_final_path(self):
        receipt = python.acquire(self.spec, self.root, fetch=self.fetch, run=self.run_fixture)
        final = self.root / execution.identity(self.spec)
        self.assertTrue(Path(receipt['probe']['executable']).is_relative_to(final))
        self.assertEqual(receipt['integrity'], 'distribution_checksums_and_local_fingerprint')
        alias = final / 'interpreters/cpython-3.12-linux-x86_64-gnu'
        self.assertEqual(os.readlink(alias), self.spec['installation_key'])
        python.verify(self.spec, receipt)
        self.calls.clear()
        repeated = python.acquire(self.spec, self.root, fetch=Mock(side_effect=AssertionError('download replay')), run=self.run_fixture)
        self.assertEqual(receipt['artifact_id'], repeated['artifact_id'])
        self.assertEqual(len(self.calls), 1)  # Functionality reprobe only.
        alias.unlink()
        alias.symlink_to(final / 'interpreters' / self.spec['installation_key'], target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'external symbolic link'):
            python.verify(self.spec, receipt)
        alias.unlink()
        alias.symlink_to(self.spec['installation_key'], target_is_directory=True)
        Path(receipt['probe']['executable']).write_bytes(b'drift')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            python.verify(self.spec, receipt)

    def test_checksum_mismatch_never_executes_downloaded_tools(self):
        self.spec['uv_archive']['sha256'] = '0' * 64
        run = Mock()
        with self.assertRaisesRegex(ValueError, 'checksum'):
            python.acquire(self.spec, self.root, fetch=self.fetch, run=run)
        run.assert_not_called()

    def test_link_normalization_refuses_external_targets_before_mutation(self):
        tree = self.root / 'interpreters'
        tree.mkdir()
        (tree / 'runtime').mkdir()
        (tree / 'alias').symlink_to(tree / 'runtime', target_is_directory=True)
        (tree / 'external').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'external symbolic link: external'):
            python.normalize_installation_links(tree)
        self.assertTrue(Path(os.readlink(tree / 'alias')).is_absolute())


if __name__ == '__main__':
    unittest.main()
