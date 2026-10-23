"""Regression for a Cargo project with browser assets and Node helper scripts.

Use an explicit census/reader so parser and planner contracts run on every host;
Linux filesystem admission and real publisher execution have separate suites.
"""
from pathlib import Path
from dataclasses import replace
import hashlib
import io
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
import environment_adapters
import environment_artifacts
import environment_plan
import environment_recipes
import environment_policy
import environment_requirements
from environment_preparation import EnvironmentPreparation
import project_inventory
import project_analysis
import project_requirements

TARGET = dict(os='linux', architecture='x64', libc='glibc')
FILES = {
    'Cargo.toml': b'[package]\nname="game"\nversion="0.1.0"\nrust-version="1.97.1"\n'
                  b'[features]\ndefault=["client"]\nclient=[]\n',
    'rust-toolchain.toml': b'[toolchain]\nchannel="1.97.1"\nprofile="minimal"\ncomponents=["rustfmt","clippy"]\n',
    'README.md': b'## Validation\n```powershell\ncargo test --no-default-features\n'
                 b'cargo run --features dev -- --smoke-test\n```\n',
    'src/lib.rs': b'pub fn game() {}',
    'assets/dev/hotpatch-compat.js': b'window.patch = true;',
    'tools/generate_audio.cjs': b"const fs = require('fs');",
}


def report(files=None):
    files = FILES if files is None else files
    value = project_requirements.analyse(Path('.'), inventory=project_inventory.from_paths(files),
                                         read=lambda name, limit=None: files.get(name))
    value.update(project='game', source=dict(scope='committed', revision='a' * 40))
    return value


class RustProjectEnvironmentTests(unittest.TestCase):
    def test_source_helpers_are_visible_but_not_mandatory_runtimes(self):
        value = report()
        self.assertEqual({item['path'] for item in value['components']}, {'.', 'tools', 'assets/dev'})
        plan = environment_plan.build(value, release_entries={})
        self.assertEqual([item['capability'] for item in plan['requirements']], ['rust-toolchain'])
        self.assertEqual({item['path'] for item in plan['source_components']}, {'tools', 'assets/dev'})

    def test_real_node_manifests_and_operator_overrides_remain_required(self):
        files = dict(FILES, **{'web/package.json': b'{"name":"web"}'})
        plan = environment_plan.build(report(files), release_entries={})
        self.assertIn('node', [item['capability'] for item in plan['requirements']])
        self.assertTrue(any(item['capability'] == 'node' for item in plan['blockers']))
        value = report()
        value['requirements'] = [dict(tool='node', override=dict(constraint='20.19.2', notation='semver'))]
        plan = environment_plan.build(value, release_entries={})
        self.assertEqual(len([item for item in plan['requirements'] if item['capability'] == 'node']), 2)

    def test_manifest_free_javascript_still_requires_an_environment(self):
        plan = environment_plan.build(report({'src/app.js': b'console.log(1)'}), release_entries={})
        self.assertEqual([item['capability'] for item in plan['requirements']], ['node'])
        self.assertTrue(plan['blockers'])

    def test_other_inferred_languages_keep_their_own_sdk_gates(self):
        files = dict(FILES, **{'simulation/main.py': b'print(1)', 'server/main.go': b'package main'})
        plan = environment_plan.build(report(files), release_entries={})
        self.assertTrue({'python', 'go', 'rust-toolchain'}.issubset(
            {item['capability'] for item in plan['requirements']}))
        self.assertEqual({item['capability'] for item in plan['source_components']}, {'node'})
        self.assertNotIn('simulation', {item['path'] for item in plan['source_components']})

    def test_source_only_heuristic_warns_about_manifest_free_node_servers(self):
        value = report(dict(FILES, **{'server/main.cjs': b"require('http').createServer().listen(3000);"}))
        sources = environment_plan.source_components(value)
        self.assertIn('server', {item['path'] for item in sources})
        self.assertTrue(all('Node servers or tools' in item['reason'] and 'override' in item['reason'] for item in sources))

    def test_capability_aliases_share_the_asset_classification(self):
        value = report()
        for component in value['components']:
            if component['basis'] == 'inferred':
                for requirement in component['requirements']:
                    requirement['tool'] = 'typescript'
        service = project_analysis.ProjectAnalysis('.')
        service.report = Mock(return_value=value)
        service.assessed = Mock(return_value=dict(verdict='missing', measured=None, provisionable=False))
        with patch.dict(environment_plan.environment_capabilities.CAPABILITY_IDS, {'typescript': 'node'}):
            effective = service.effective('game')
            self.assertNotIn('typescript', [entry['tool'] for entry in effective['requirements']])
            self.assertEqual([item['capability'] for item in environment_plan.build(effective, release_entries={})['requirements']], ['rust-toolchain'])

    def test_each_plan_classifies_source_components_once_or_reuses_effective_report(self):
        value = report()
        with patch.object(environment_plan, 'source_components', wraps=environment_plan.source_components) as classify:
            first = environment_plan.build(value, release_entries={})
            classify.assert_called_once_with(value)
        value['source_components'] = first['source_components']
        with patch.object(environment_plan, 'source_components', side_effect=AssertionError('classification repeated')):
            second = environment_plan.build(value, release_entries={})
        self.assertEqual(first['requirements'], second['requirements'])
        self.assertEqual(first['source_components'], second['source_components'])

    def test_host_readiness_and_plan_share_asset_and_override_classification(self):
        for override in (None, dict(toolchain='node', constraint='20.19.2', notation='semver', origin='operator', actor='admin', at='')):
            with self.subTest(override=override):
                service = project_analysis.ProjectAnalysis('.', overrides=lambda: {'game': override} if override else {})
                service.report = Mock(return_value=report())
                service.assessed = Mock(return_value=dict(verdict='missing', measured=None, provisionable=False))
                effective = service.effective('game')
                plan = environment_plan.build(effective, release_entries={})
                self.assertEqual(effective['source_components'], plan['source_components'])
                self.assertEqual(any(item['tool'] == 'node' for item in effective['requirements']), bool(override))
                self.assertEqual(bool(effective['source_components']), not bool(override))

    def test_headless_commands_are_proposed_not_approved(self):
        candidates = environment_recipes.candidates(report(), FILES)
        self.assertEqual([item['arguments'] for item in candidates], [
            ['cargo', 'test', '--no-default-features'], ['cargo', 'build', '--no-default-features']])
        self.assertEqual({item['evidence'] for item in candidates}, {'README.md'})
        self.assertEqual([item['derived'] for item in candidates], [False, True])
        plan = dict(check_candidates=candidates, selected_checks=[])
        self.assertEqual(environment_recipes.select(plan, [], 'admin'), [])
        self.assertEqual(environment_recipes.automatic(plan), [item['id'] for item in candidates])
        selected = environment_recipes.select(plan, [candidates[0]['id']], 'admin')
        self.assertEqual(selected[0]['selected_by'], 'admin')

    def test_build_only_docs_pair_a_headless_test_with_the_same_evidence(self):
        for name in ('README.md', 'README.markdown', 'engine/README.md'):
            with self.subTest(name=name):
                directory = name.rpartition('/')[0]
                files = {key: value for key, value in FILES.items() if key != 'README.md'}
                if directory:
                    files = {directory + '/' + key: value for key, value in files.items()}
                files[name] = b'cargo test\n```sh\ncargo build --no-default-features\ncargo build --no-default-features\n```\ncargo test\n'
                candidates = environment_recipes.candidates(report(files), files)
                self.assertEqual(len(candidates), 2)
                self.assertEqual({tuple(item['arguments']) for item in candidates}, {
                    ('cargo', 'test', '--no-default-features'), ('cargo', 'build', '--no-default-features')})
                self.assertEqual({item['evidence'] for item in candidates}, {name})
                self.assertEqual({item['source'] for item in candidates}, {project_requirements.HEADLESS_CARGO_SOURCE})
                self.assertEqual([item['derived'] for item in candidates], [False, True])

    def test_unreadable_and_unfenced_readmes_do_not_propose_headless_checks(self):
        for contents in (None, b'cargo build --no-default-features', b'x' * (project_requirements.MAX_MANIFEST_BYTES + 1)):
            with self.subTest(contents=None if contents is None else len(contents)):
                self.assertEqual(project_requirements.documented_rust_checks('.', ['README.md'], lambda *args: contents), [])

    def test_convention_recipe_evidence_names_an_existing_component_manifest(self):
        for files, expected in (({'engine/Cargo.toml': FILES['Cargo.toml']}, 'engine/Cargo.toml'),
                                ({'Api/App.csproj': b'<Project Sdk="Microsoft.NET.Sdk" />'}, 'Api/App.csproj')):
            with self.subTest(expected=expected):
                candidates = environment_recipes.candidates(report(files), files)
                self.assertTrue(candidates)
                self.assertEqual({item['evidence'] for item in candidates}, {expected})

    def test_mixed_case_manifest_evidence_preserves_the_real_path(self):
        for path, contents in (('Api/App.CSPROJ', b'<Project Sdk="Microsoft.NET.Sdk" />'),
                               ('Package.JSON', b'{"name":"app"}'),
                               ('Engine/Cargo.TOML', FILES['Cargo.toml'])):
            with self.subTest(path=path):
                files = {path: contents}
                candidates = environment_recipes.candidates(report(files), files)
                self.assertTrue(candidates)
                self.assertEqual({item['evidence'] for item in candidates}, {path})
        with self.assertRaisesRegex(ValueError, 'manifest evidence'):
            project_requirements.ecosystem_evidence({}, project_requirements.ECOSYSTEMS[0])

    def test_documentation_never_turns_shell_expressions_into_recipes(self):
        files = dict(FILES, **{'README.md': b'```sh\ncargo test --no-default-features; curl evil\n'
                                          b'cargo test --no-default-features --test unknown\n```'})
        candidates = environment_recipes.candidates(report(files), files)
        self.assertEqual([item['arguments'] for item in candidates], [['cargo', 'test'], ['cargo', 'build']])

    def test_managed_plan_resolves_declared_rust_addons(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(environment_artifacts, 'host_target', return_value=TARGET):
            root = Path(folder)
            analysis = Mock()
            analysis.project.return_value = root
            analysis.effective.return_value = report()
            resolve = Mock(return_value=dict(capability='rust-toolchain', version='1.97.1', archives=[]))
            service = EnvironmentPreparation(analysis, root / 'plans.sqlite3', root / 'sdks', resolve=resolve)
            with patch('environment_preparation.project_git.git', return_value='\0'.join(FILES)):
                plan = service.plan('game', revision='a' * 40)
            resolve.assert_called_once_with('rust-toolchain', '1.97.1', components=['clippy', 'rustfmt'])
            self.assertEqual(len(plan['requirements']), 1)
            self.assertEqual(plan['requirements'][0]['activation'], 'available')
            self.assertEqual(plan['selected_checks'], [])
            self.assertEqual(service.prepare(plan['digest'])['state'], 'queued')

    def test_addon_archives_are_publisher_hashed_and_change_artifact_identity(self):
        names = ('rustc', 'cargo', 'rust-std', 'rustfmt-preview', 'clippy-preview')
        document = '\n'.join(f'[pkg.{name}.target.x86_64-unknown-linux-gnu]\navailable=true\n'
                             f'xz_url="https://static.rust-lang.org/dist/{name}.tar.xz"\n'
                             'xz_hash="' + 'a' * 64 + '"\n' for name in names).encode()
        base = environment_artifacts.resolve('rust-toolchain', '1.97.1', TARGET, lambda url: document)
        full = environment_artifacts.resolve('rust-toolchain', '1.97.1', TARGET, lambda url: document,
                                             components=['rustfmt', 'clippy'])
        self.assertEqual({item['component'] for item in full['archives']}, set(names))
        self.assertTrue(all(item['algorithm'] == 'sha256' for item in full['archives']))
        self.assertNotEqual(environment_artifacts.identity(base), environment_artifacts.identity(full))
        with self.assertRaisesRegex(ValueError, 'Only rustfmt and clippy'):
            environment_artifacts.resolve('rust-toolchain', '1.97.1', TARGET, lambda url: document, components=['miri'])

    def test_prepared_rust_policy_binds_only_approved_headless_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            projects = Path(folder)
            root = projects / 'game'
            (root / 'sdk').mkdir(parents=True)
            executable = root / 'sdk/rustc'
            executable.write_text('SDK double')
            value = environment_plan.build(report(), release_entries={})
            item = value['requirements'][0]
            spec = dict(capability='rust-toolchain', version='1.97.1')
            item.update(version='1.97.1', state='acquire', artifact=spec,
                        adapter_version=environment_adapters.get('rust-toolchain').version)
            value['check_candidates'] = environment_recipes.candidates(report(), FILES)
            value['selected_checks'] = environment_recipes.select(value, ['.:0:test'], 'admin')
            value['digest'] = environment_plan.digest(value)
            receipt = dict(state='ready', digest=value['digest'], revision='a' * 40, receipts=[dict(
                capability='rust-toolchain', version='1.97.1', components=['.'], specification=spec,
                probe=dict(executable=str(executable), cargo_version='1.97.1',
                           components={'rustfmt': 'rustfmt 1.97.1'}))])
            store = Mock()
            store.prepared.return_value = value, receipt
            entries = {name: dict(mode='100644', kind='blob', size=len(contents), blob='b' * 40)
                       for name, contents in FILES.items()}
            with patch('environment_policy.project_git.git', return_value='a' * 40), \
                    patch('environment_policy.common_directory', return_value=root / '.git'), \
                    patch('environment_policy.tree', return_value=entries), \
                    patch('environment_policy.project_analysis.committed_reader', return_value=lambda name, limit=None: FILES.get(name)), \
                    patch('environment_policy.toolchain_policy.resolve_legacy_committed', return_value=dict(toolchains=[], checks=[], lockfiles={})):
                service = environment_policy.EnvironmentPolicies(projects, store)
                policy = service.resolve(root)
                self.assertFalse(policy['blockers'])
                self.assertFalse(policy['validation_blockers'])
                self.assertIn('rustfmt', environment_adapters.session_tools(policy['toolchains'])[0]['commands'])
                self.assertNotIn('clippy-driver', environment_adapters.session_tools(policy['toolchains'])[0]['commands'])
                self.assertEqual(policy['recipes'][0]['arguments'], [str(executable.with_name('cargo')), 'test', '--no-default-features'])
                self.assertIn('README.md', policy['declarations'])
                # Selecting only the paired build must still freeze the README.
                value['selected_checks'] = environment_recipes.select(value, ['.:1:build'], 'admin')
                value['digest'] = environment_plan.digest(value)
                receipt['digest'] = value['digest']
                self.assertIn('README.md', service.resolve(root)['declarations'])
                original = entries.pop('README.md')
                for renamed in (False, True):
                    if renamed:
                        entries['RENAMED.md'] = original
                    with self.assertRaisesRegex(ValueError, 'declaration is missing.*README.md'):
                        service.resolve(root)
                entries['README.md'] = original
                for invalid, message in ((dict(original, mode='120000'), 'regular file'),
                                         (dict(original, size=262145), '256 KiB')):
                    entries['README.md'] = invalid
                    with self.assertRaisesRegex(ValueError, message):
                        service.resolve(root)
                entries['README.md'] = original
                value['selected_checks'] = []
                value['digest'] = environment_plan.digest(value)
                receipt['digest'] = value['digest']
                policy = service.resolve(root)
                self.assertIn('No approved validation recipes selected for this environment.', policy['validation_blockers'])

    def test_non_rust_component_selections_are_explicitly_unsupported(self):
        item = dict(capability='node', version='20.19.2', constraint='20.19.2', notation='semver', conflicts=[],
                    evidence=[dict(selection_lookup=dict(components=['extra']))])
        resolver = Mock()
        environment_requirements.plan_archive(item, paths=[], read=lambda name: None,
                                             revision='a' * 40, resolve=resolver, specifications={})
        self.assertEqual(item['state'], 'unsupported')
        self.assertIn('only for the Rust', item['reason'])
        resolver.assert_not_called()

    def test_non_rust_empty_components_are_allowed_but_malformed_values_are_rejected(self):
        for components, state in (([], 'acquire'), (None, 'unsupported'), ('extra', 'unsupported'), (False, 'unsupported')):
            with self.subTest(components=components):
                item = dict(capability='node', version='20.19.2', constraint='20.19.2', notation='semver', conflicts=[],
                            evidence=[dict(selection_lookup=dict(components=components))])
                environment_requirements.plan_archive(item, paths=[], read=lambda name: None,
                                                     revision='a' * 40, resolve=Mock(return_value={}), specifications={})
                self.assertEqual(item['state'], state)

    def test_component_sets_share_a_specification_only_when_identical(self):
        specifications, resolver = {}, Mock(return_value={})
        for components in (['rustfmt'], ['rustfmt'], ['clippy']):
            item = dict(capability='rust-toolchain', version='1.97.1', constraint='1.97.1', notation='semver', conflicts=[],
                        evidence=[dict(selection_lookup=dict(components=components))])
            environment_requirements.plan_archive(item, paths=[], read=lambda name: None,
                                                 revision='a' * 40, resolve=resolver, specifications=specifications)
            self.assertEqual(item['state'], 'acquire')
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(len(specifications), 2)

    def test_session_tools_advertise_only_verified_rust_addons(self):
        entry = dict(name='rust-toolchain')
        self.assertEqual(environment_adapters.session_tools([entry])[0]['commands'], ['rustc', 'cargo'])
        entry['artifact_receipt'] = dict(probe=dict(components={'clippy-driver': 'clippy 0.1.97'}))
        commands = environment_adapters.session_tools([entry])[0]['commands']
        self.assertIn('cargo-clippy', commands)
        self.assertNotIn('rustfmt', commands)

    def test_session_tool_filtering_is_adapter_metadata_not_a_rust_branch(self):
        adapter = replace(environment_adapters.get('node'), capability='fixture', commands=('compiler', 'formatter'),
                          component_commands=(('format-probe', ('formatter',)),))
        with patch.dict(environment_adapters.ADAPTERS, {'fixture': adapter}):
            entry = dict(name='fixture')
            self.assertEqual(environment_adapters.session_tools([entry])[0]['commands'], ['compiler'])
            entry['artifact_receipt'] = dict(probe=dict(components={'format-probe': 'ok'}))
            self.assertEqual(environment_adapters.session_tools([entry])[0]['commands'], ['compiler', 'formatter'])

    def test_optional_distribution_verifies_before_publication_and_preserves_published_paths(self):
        # Layout matches the Rust 1.97.1 Linux publisher archives: a top-level
        # components file names rustfmt-preview/clippy-preview payload folders.
        blobs = {}
        for package, commands in (('rustfmt-preview', ('rustfmt', 'cargo-fmt')),
                                  ('clippy-preview', ('clippy-driver', 'cargo-clippy'))):
            content = io.BytesIO()
            with tarfile.open(fileobj=content, mode='w:xz') as archive:
                top = package.removesuffix('-preview') + '-1.97.1-x86_64-unknown-linux-gnu'
                files = {'components': (package + '\n').encode(),
                         package + '/manifest.in': b'file:bin/tool\n',
                         **{package + '/bin/' + name: b'publisher executable fixture' for name in commands}}
                for name, data in files.items():
                    member = tarfile.TarInfo(top + '/' + name)
                    member.size, member.mode = len(data), 0o755
                    archive.addfile(member, io.BytesIO(data))
            blobs[package] = content.getvalue()
        spec = dict(capability='rust-toolchain', version='1.97.1', archives=[dict(
            component=name, url='https://static.rust-lang.org/dist/' + name + '.tar.xz',
            algorithm='sha256', hash=hashlib.sha256(blob).hexdigest()) for name, blob in blobs.items()])
        def fetch(url, destination, **kwargs):
            destination.write_bytes(blobs[url.rsplit('/', 1)[-1].removesuffix('.tar.xz')])
        def verify(capability, root):
            return dict(executable=str(root / 'bin/rustc'), versions=['1.97.1'])
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(environment_artifacts.os, 'geteuid', return_value=1000, create=True), \
                patch.object(environment_artifacts.subprocess, 'run', return_value=Mock(returncode=0, stdout='tool 1.97.1')):
            final = Path(folder) / environment_artifacts.identity(spec)
            with patch.object(environment_artifacts, 'verify_components', side_effect=ValueError('staging probe failed')):
                with self.assertRaisesRegex(ValueError, 'staging probe failed'):
                    environment_artifacts.acquire_archives(spec, folder, fetch=fetch, verify=verify)
            self.assertFalse(final.exists())
            self.assertEqual(list(Path(folder).iterdir()), [])
            with patch.object(environment_artifacts, 'verify_components', wraps=environment_artifacts.verify_components) as verify_components:
                receipt = environment_artifacts.acquire_archives(spec, folder, fetch=fetch, verify=verify)
                verify_components.assert_called_once()
                self.assertNotEqual(verify_components.call_args.args[1], final)
            self.assertEqual(set(receipt['probe']['components']), {'rustfmt', 'clippy-driver'})
            self.assertFalse((final / 'manifest.in').exists())
            original_receipt = (final / 'herdr-receipt.json').read_bytes()
            with patch.object(environment_artifacts, 'verify_components', side_effect=ValueError('reuse probe failed')):
                with self.assertRaisesRegex(ValueError, 'reuse probe failed'):
                    environment_artifacts.acquire_archives(spec, folder, fetch=fetch, verify=verify)
            self.assertTrue((final / 'bin/rustfmt').is_file())
            self.assertEqual((final / 'herdr-receipt.json').read_bytes(), original_receipt)
            self.assertEqual(list(Path(folder).glob('.unverified-*')), [])
            with patch.object(environment_artifacts, 'verify_components', wraps=environment_artifacts.verify_components) as verify_components:
                environment_artifacts.acquire_archives(spec, folder, fetch=fetch, verify=verify)
                verify_components.assert_called_once_with(spec, final)

    def test_requested_addons_must_have_commands_and_successful_probes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'bin').mkdir()
            for name in ('rustfmt', 'cargo-fmt', 'clippy-driver', 'cargo-clippy'):
                (root / 'bin' / name).write_text('component double')
            spec = dict(capability='rust-toolchain', archives=[dict(component=name)
                        for name in ('rustfmt-preview', 'clippy-preview')])
            runner = Mock(return_value=Mock(returncode=0, stdout='tool 1.0.0'))
            self.assertEqual(set(environment_artifacts.verify_components(spec, root, runner)), {'rustfmt', 'clippy-driver'})
            (root / 'bin/cargo-clippy').unlink()
            with self.assertRaisesRegex(ValueError, 'component clippy-driver is missing'):
                environment_artifacts.verify_components(spec, root, runner)
            (root / 'bin/cargo-clippy').write_text('component double')
            runner.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, 'did not report a version'):
                environment_artifacts.verify_components(spec, root, runner)


if __name__ == '__main__':
    unittest.main()
