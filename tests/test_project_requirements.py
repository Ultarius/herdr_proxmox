"""Deriving project requirements from the files a repository already maintains.

These tests hold the properties the scope depends on: anything derived carries
its evidence, anything unresolved is reported rather than filled in, and a
component is a directory so a workspace is not flattened into one answer.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import project_inventory
import project_requirements


def write(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return root


def analyse(root, **kwargs):
    return project_requirements.analyse(root, **kwargs)


def component(report, path='.'):
    return next(item for item in report['components'] if item['path'] == path)


def requirement(item, tool):
    return next(entry for entry in item['requirements'] if entry['tool'] == tool)


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_react_application_yields_its_runtime_and_its_frameworks(self):
        # The case that prompted this: npm + React must not be a mystery.
        write(self.root, {
            'package.json': json.dumps({
                'name': 'app', 'engines': {'node': '>=20 <23'},
                'dependencies': {'react': '^18.2.0', 'react-dom': '^18.2.0'},
                'devDependencies': {'vite': '^5.0.0', 'typescript': '^5.4.0'},
                'scripts': {'test': 'vitest', 'build': 'vite build'},
            }),
            'package-lock.json': '{}',
            'tsconfig.json': '{}',
            'vite.config.ts': '',
            'src/App.tsx': '',
            'src/main.tsx': '',
        })
        item = component(analyse(self.root))
        node = requirement(item, 'node')
        self.assertEqual(node['constraint'], '>=20 <23')
        self.assertEqual(node['constraint'], '>=20 <23')
        self.assertEqual(node['observations'][0]['field'], 'engines.node')
        self.assertEqual(node['observations'][0]['file'], 'package.json')
        self.assertTrue(node['established'])
        self.assertEqual([entry['id'] for entry in item['frameworks']],
                         ['react', 'typescript', 'vite'])
        # A lockfile is dependency-resolution evidence, not a requirement.
        self.assertEqual(item['evidence']['lockfiles'], ['package-lock.json'])
        # A config file is configured tooling, not an installed toolchain.
        self.assertEqual([entry['id'] for entry in item['configured_tooling']],
                         ['typescript', 'vite'])
        # The only thing left open is the Vite config, which is a module rather
        # than a declaration: reading a requirement out of it means running it.
        self.assertEqual([entry['evidence'] for entry in item['unresolved']],
                         ['vite.config.ts'])

    def test_a_missing_version_source_is_unresolved_with_the_files_that_were_tried(self):
        write(self.root, {'package.json': json.dumps({'name': 'app'})})
        node = requirement(component(analyse(self.root)), 'node')
        self.assertFalse(node['established'])
        self.assertEqual(node['constraint'], '')
        self.assertIn('.nvmrc', node['reason'])
        self.assertIn('engines.node', node['reason'])

    def test_nvmrc_is_used_when_the_manifest_declares_nothing(self):
        write(self.root, {'package.json': '{}', '.nvmrc': '20.11.1\n'})
        node = requirement(component(analyse(self.root)), 'node')
        self.assertEqual(node['constraint'], '')
        self.assertEqual(node['selected'], '20.11.1')
        self.assertEqual(node['observations'][0]['role'], 'selection')

    def test_candidate_commands_are_proposed_not_claimed(self):
        write(self.root, {'package.json': '{}'})
        commands = component(analyse(self.root))['candidate_commands']
        self.assertEqual([entry['command'] for entry in commands],
                         [['npm', 'test'], ['npm', 'run', 'build']])
        self.assertTrue(all(entry['source'] == 'convention' for entry in commands))

    def test_the_repository_provides_commands_and_suggestions_are_separated(self):
        write(self.root, {'package.json': json.dumps(
            {'scripts': {'test': 'vitest run', 'lint': 'eslint .'}})})
        commands = component(analyse(self.root))['candidate_commands']
        provided = {entry['purpose']: entry['command'] for entry in commands
                    if entry['source'] == 'package.json#scripts'}
        suggested = {entry['purpose'] for entry in commands if entry['source'] == 'convention'}
        # The project's own scripts are commands; a convention is only offered
        # where the repository does not already say how to run something.
        self.assertEqual(provided, {'test': ['npm', 'run', 'test'], 'lint': ['npm', 'run', 'lint']})
        self.assertIn('build', suggested)
        self.assertNotIn('test', suggested)

    def test_package_manager_is_identified_from_the_lockfile_that_is_present(self):
        for lockfile, manager in (('package-lock.json', 'npm'), ('pnpm-lock.yaml', 'pnpm'),
                                  ('yarn.lock', 'yarn'), ('bun.lockb', 'bun')):
            with self.subTest(lockfile=lockfile):
                root = Path(tempfile.mkdtemp())
                self.addCleanup(lambda r=root: __import__('shutil').rmtree(r, ignore_errors=True))
                write(root, {'package.json': '{}', lockfile: ''})
                item = component(analyse(root))
                self.assertEqual(item['evidence']['lockfiles'], [lockfile])
                self.assertEqual(item['ecosystems'][0]['id'], 'node')

    def test_a_computed_configuration_is_unresolved_rather_than_interpreted(self):
        write(self.root, {'package.json': '{}', 'vite.config.ts': 'export default {}',
                          'webpack.config.js': 'module.exports = {}'})
        item = component(analyse(self.root))
        computed = {entry['evidence']: entry['reason'] for entry in item['unresolved']
                    if 'computed rather than declared' in entry['reason']}
        self.assertEqual(sorted(computed), ['vite.config.ts', 'webpack.config.js'])
        # The runtime is separately unresolved: no version source was found.
        self.assertIn('No version declaration', requirement(item, 'node')['reason'])


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_dotnet_api_and_an_angular_client_are_two_components(self):
        write(self.root, {
            'global.json': json.dumps({'sdk': {'version': '8.0.100'}}),
            'Api/Api.csproj': '<Project />',
            'Api/Program.cs': '',
            'web/package.json': json.dumps({'dependencies': {'@angular/core': '^17.0.0'}}),
            'web/angular.json': '{}',
            'web/src/app.component.ts': '',
        })
        report = analyse(self.root)
        self.assertEqual([item['path'] for item in report['components']], ['.', 'Api', 'web'])
        self.assertEqual(requirement(component(report, '.'), 'dotnet')['selected'], '8.0.100')
        self.assertEqual([entry['id'] for entry in component(report, 'web')['frameworks']],
                         ['angular'])
        # Each component owns the files under it, so the workspace root does not
        # claim the Angular sources as its own.
        self.assertNotIn('typescript', [entry['id'] for entry in component(report, '.')['languages']])
        self.assertIn('typescript', [entry['id'] for entry in component(report, 'web')['languages']])

    def test_every_component_carries_its_own_evidence(self):
        write(self.root, {'package.json': '{}', 'web/package.json': '{}'})
        report = analyse(self.root)
        root, web = component(report, '.'), component(report, 'web')
        self.assertEqual(root['evidence']['manifests'], ['package.json'])
        self.assertEqual(web['evidence']['manifests'], ['web/package.json'])

    def test_coverage_and_component_languages_agree_about_the_same_file(self):
        # The evidence that resolves a header can sit outside the component that
        # owns it. Classifying each component's own subset separately called the
        # header ambiguous inside `web` while the coverage summary called it C.
        write(self.root, {'package.json': '{}', 'main.c': '',
                          'web/package.json': '{}', 'web/api.h': ''})
        report = analyse(self.root)
        self.assertEqual([entry['id'] for entry in component(report, 'web')['languages']], ['c'])
        coverage = {entry['id']: entry['files'] for entry in report['coverage']['languages']}
        self.assertEqual(coverage['c'], 2)

    def test_a_nested_component_reads_its_own_manifest_not_the_root(self):
        # Reading by bare name returned the repository root's package.json, which
        # is indistinguishable from inherited configuration and equally wrong.
        write(self.root, {
            'package.json': json.dumps({'engines': {'node': '>=20 <21'}}),
            'web/package.json': json.dumps({'engines': {'node': '>=22 <23'}}),
        })
        report = analyse(self.root)
        root = requirement(component(report, '.'), 'node')
        web = requirement(component(report, 'web'), 'node')
        self.assertEqual(root['constraint'], '>=20 <21')
        self.assertEqual(web['constraint'], '>=22 <23')
        self.assertEqual(web['observations'][0]['file'], 'web/package.json')
        self.assertNotEqual(root['constraint'], web['constraint'])


class ConflictTests(unittest.TestCase):
    """A project contradicting itself is reported, never silently resolved."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_selection_that_violates_the_constraint_is_reported(self):
        write(self.root, {'package.json': json.dumps({'engines': {'node': '>=22'}}),
                          '.nvmrc': '18.0.0\n'})
        item = component(analyse(self.root))
        node = requirement(item, 'node')
        self.assertEqual(node['constraint'], '>=22')
        self.assertEqual(node['selected'], '18.0.0')
        self.assertEqual([entry['kind'] for entry in node['conflicts']], ['unsatisfied'])
        self.assertIn('18.0.0 does not satisfy the declared >=22', node['conflicts'][0]['reason'])
        self.assertIn('Node.js unsatisfied',
                      [entry['what'] for entry in item['unresolved']])

    def test_a_satisfying_selection_is_not_a_conflict(self):
        write(self.root, {'package.json': json.dumps({'engines': {'node': '>=18 <23'}}),
                          '.nvmrc': '20.11.1\n'})
        node = requirement(component(analyse(self.root)), 'node')
        self.assertEqual(node['conflicts'], [])

    def test_two_selections_that_disagree_are_reported(self):
        write(self.root, {'package.json': '{}', '.nvmrc': '20.11.1\n',
                          '.node-version': '18.19.0\n'})
        node = requirement(component(analyse(self.root)), 'node')
        self.assertEqual([entry['kind'] for entry in node['conflicts']], ['disagreement'])
        self.assertEqual(node['conflicts'][0]['values'], ['18.19.0', '20.11.1'])

    def test_both_declarations_are_kept_as_observations(self):
        write(self.root, {'package.json': json.dumps({'engines': {'node': '>=22'}}),
                          '.nvmrc': '18.0.0\n'})
        node = requirement(component(analyse(self.root)), 'node')
        self.assertEqual(sorted((entry['role'], entry['file']) for entry in node['observations']),
                         [('constraint', 'package.json'), ('selection', '.nvmrc')])


class OtherEcosystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_php_composer_requirement_including_extensions(self):
        write(self.root, {'composer.json': json.dumps({
            'require': {'php': '^8.2', 'ext-json': '*', 'laravel/framework': '^11.0'}})})
        item = component(analyse(self.root))
        php = requirement(item, 'php')
        self.assertEqual(php['constraint'], '^8.2')
        self.assertEqual(php['observations'][0]['field'], 'require.php')
        self.assertEqual(php['observations'][0]['file'], 'composer.json')
        self.assertEqual([entry['id'] for entry in item['frameworks']], ['laravel'])

    def test_go_module_version_from_the_directive(self):
        write(self.root, {'go.mod': 'module example.com/x\n\ngo 1.22.1\n\nrequire (\n)\n'})
        go = requirement(component(analyse(self.root)), 'go')
        self.assertEqual(go['constraint'], '>=1.22.1')
        self.assertEqual(go['observations'][0]['field'], 'go')

    def test_go_directives_are_minimums_including_patch_releases(self):
        from dependency_constraints import evaluate
        for directive in ('1.22', '1.22.1'):
            with self.subTest(directive=directive):
                files = {'go.mod': ('module example.com/x\ngo ' + directive + '\n').encode()}
                report = analyse(self.root, inventory=project_inventory.from_paths(files),
                                 read=lambda name, limit=None: files.get(name))
                go = requirement(component(report), 'go')
                declarations = project_requirements.declarations(go)
                self.assertEqual(declarations, [('>=' + directive, 'gomod')])
                self.assertEqual(evaluate(declarations[0][0], '1.23.0', 'gomod'), 'satisfied')
                self.assertEqual(evaluate('>=' + directive, '1.21.9', 'gomod'), 'violated')

    def test_python_minor_selection_is_a_family_not_an_exact_pep440_version(self):
        files = {'pyproject.toml': b'[project]\nrequires-python = ">=3.12.1,<3.13"\n',
                 '.python-version': b'3.12\n'}
        report = analyse(self.root, inventory=project_inventory.from_paths(files),
                         read=lambda name, limit=None: files.get(name))
        python = requirement(component(report), 'python')
        self.assertEqual(python['selected'], '3.12')
        self.assertFalse(python['conflicts'])
        self.assertEqual(project_requirements.declarations(python),
                         [('>=3.12.1,<3.13', 'pep440'), ('3.12', 'python-version')])

    def test_python_family_conflicts_are_reported_before_preparation(self):
        for selected in ('3.12', 'v3.12', 'V3.12'):
            for constraint, conflict in (('>=3.13', True), ('>=3.12.1,<3.13', False)):
                with self.subTest(selected=selected, constraint=constraint):
                    files = {'pyproject.toml': ('[project]\nrequires-python="' + constraint + '"\n').encode(),
                             '.python-version': selected.encode()}
                    report = analyse(self.root, inventory=project_inventory.from_paths(files),
                                     read=lambda name, limit=None: files.get(name))
                    python = requirement(component(report), 'python')
                    self.assertEqual(bool(python['conflicts']), conflict)

    def test_rust_workspace_pin_and_lockfile(self):
        write(self.root, {'Cargo.toml': '[package]\nname = "x"\n',
                          'Cargo.lock': '', 'rust-toolchain.toml': '[toolchain]\nchannel = "1.77.0"\n'})
        item = component(analyse(self.root))
        self.assertEqual(item['evidence']['lockfiles'], ['Cargo.lock'])
        self.assertEqual(requirement(item, 'rust-toolchain')['selected'], '1.77.0')

    def test_python_requires_python_from_pyproject(self):
        write(self.root, {'pyproject.toml': '[project]\nname = "x"\nrequires-python = ">=3.10"\n'
                                             'dependencies = ["flask>=3.0", "pytest"]\n'})
        item = component(analyse(self.root))
        python = requirement(item, 'python')
        self.assertEqual(python['constraint'], '>=3.10')
        self.assertEqual(python['observations'][0]['field'], 'project.requires-python')
        self.assertEqual([entry['id'] for entry in item['frameworks']], ['flask', 'pytest'])

    def test_poetrys_python_constraint_is_read_in_its_own_notation(self):
        files = {'pyproject.toml': b'[tool.poetry.dependencies]\npython = "^3.11"\n'}
        def read(name, limit=None):
            return files.get(name)
        item = component(analyse(self.root, inventory=project_inventory.from_paths(['pyproject.toml']),
                                 read=read))
        python = requirement(item, 'python')
        self.assertEqual(python['constraint'], '^3.11')
        self.assertEqual(python['notation'], 'poetry')

    def test_pep621_statement_wins_and_poetry_stays_an_observation(self):
        files = {'pyproject.toml': (b'[project]\nrequires-python = ">=3.11"\n'
                                    b'[tool.poetry.dependencies]\npython = "^3.10"\n')}
        def read(name, limit=None):
            return files.get(name)
        item = component(analyse(self.root, inventory=project_inventory.from_paths(['pyproject.toml']),
                                 read=read))
        python = requirement(item, 'python')
        self.assertEqual(python['constraint'], '>=3.11')
        self.assertEqual(python['notation'], 'pep440')
        conflicts = [entry for entry in python['conflicts'] if entry['kind'] == 'disagreement']
        self.assertEqual(conflicts[0]['values'], ['>=3.11', '^3.10'])

    def test_every_python_statement_can_refuse_a_selected_interpreter(self):
        files = {'pyproject.toml': (b'[project]\nrequires-python = ">=3.11"\n'
                                  b'[tool.poetry.dependencies]\npython = "~3.11"\n'),
                 '.python-version': b'3.12.1'}
        result = analyse(self.root, inventory=project_inventory.from_paths(files), read=lambda name, limit=None: files.get(name))
        python = requirement(component(result), 'python')
        unsatisfied = [item for item in python['conflicts'] if item['kind'] == 'unsatisfied']
        self.assertEqual(len(unsatisfied), 1)
        self.assertIn('~3.11', unsatisfied[0]['reason'])

    def test_flutter_and_dart_stay_separate_requirements(self):
        # An FVM pin names Flutter. Reading it into the Dart requirement would
        # substitute one SDK's version for another's, which is the substitution
        # this design exists to prevent.
        write(self.root, {'pubspec.yaml': 'name: x\nenvironment:\n  sdk: ^3.5.0\n',
                          '.fvmrc': json.dumps({'flutter': '3.44.8'})})
        item = component(analyse(self.root))
        dart = requirement(item, 'dart')
        self.assertEqual(dart['constraint'], '^3.5.0')
        self.assertEqual([entry['file'] for entry in dart['observations']], ['pubspec.yaml'])
        flutter = requirement(item, 'flutter')
        self.assertEqual(flutter['selected'], '3.44.8')
        self.assertEqual([entry['file'] for entry in flutter['observations']], ['.fvmrc'])
        self.assertEqual(flutter['executable'], 'flutter')

    def test_pubspec_sdk_is_read_when_no_fvm_pin_exists(self):
        write(self.root, {'pubspec.yaml': 'name: x\nenvironment:\n  sdk: ^3.5.0\n  flutter: ">=3.0.0"\n'})
        dart = requirement(component(analyse(self.root)), 'dart')
        self.assertEqual(dart['constraint'], '^3.5.0')
        self.assertEqual(dart['observations'][0]['file'], 'pubspec.yaml')

    def test_nested_flutter_reads_the_nearest_ancestor_pin_but_pure_dart_does_not(self):
        write(self.root, {
            '.fvmrc': '{"flutter":"3.44.8"}',
            'web/app/pubspec.yaml': 'name: app\ndependencies:\n  flutter:\n    sdk: flutter\n',
            'web/app/pubspec.lock': '',
            'web/state/pubspec.yaml': 'name: state\nenvironment:\n  sdk: ^3.12.0\n',
        })
        report = analyse(self.root)
        flutter = requirement(component(report, 'web/app'), 'flutter')
        self.assertEqual(flutter['selected'], '3.44.8')
        self.assertEqual(flutter['observations'][0]['file'], '.fvmrc')
        self.assertEqual(flutter['selection_lookup']['boundary'], 'repository root')
        self.assertNotIn('flutter', [item['tool'] for item in component(report, 'web/state')['requirements']])
        self.assertNotIn('unregistered manifests', [item['what'] for item in report['unresolved']])

    def test_nearer_fvm_pin_shadows_ancestors_even_when_unreadable(self):
        write(self.root, {'.fvmrc': '{"flutter":"3.44.8"}',
            'web/.fvmrc': '{"flutter":"3.40.0"}',
            'web/app/pubspec.yaml': 'name: app\ndependencies:\n  flutter:\n    sdk: flutter\n'})
        flutter = requirement(component(analyse(self.root), 'web/app'), 'flutter')
        self.assertEqual(flutter['selected'], '3.40.0')
        self.assertEqual(flutter['selection_lookup']['shadowed'], ['.fvmrc'])
        (self.root / 'web/.fvmrc').write_text('{malformed', encoding='utf-8')
        flutter = requirement(component(analyse(self.root), 'web/app'), 'flutter')
        self.assertEqual(flutter['selected'], '')
        self.assertFalse(flutter['established'])
        self.assertEqual(flutter['selection_lookup']['status'], 'unresolved')
        self.assertTrue(flutter['observations'][0]['unreadable'])

    def test_fvm_lookup_does_not_read_above_repository_root(self):
        write(self.root, {'.fvmrc': '{"flutter":"3.44.8"}',
            'isolated/pubspec.yaml': 'name: app\ndependencies:\n  flutter:\n    sdk: flutter\n'})
        flutter = requirement(component(analyse(self.root / 'isolated')), 'flutter')
        self.assertEqual(flutter['selected'], '')
        self.assertFalse(flutter['established'])

    def test_fvm_missing_flutter_key_reports_missing_selection_not_unreadable_file(self):
        write(self.root, {'.fvmrc': '{"flavors":{}}',
            'pubspec.yaml': 'name: app\ndependencies:\n  flutter:\n    sdk: flutter\n'})
        flutter = requirement(component(analyse(self.root)), 'flutter')
        self.assertFalse(flutter['established'])
        self.assertIn('no readable Flutter selection', flutter['reason'])
        self.assertNotIn('could not be read', flutter['reason'])

    def test_known_computed_manifest_is_not_reported_as_unregistered(self):
        write(self.root, {'app.csproj': '<Project />', 'Directory.Build.props': '<Project />'})
        report = analyse(self.root)
        self.assertNotIn('unregistered manifests', [item['what'] for item in report['unresolved']])
        item = component(report)
        self.assertIn('msbuild', [entry['id'] for entry in item['configured_tooling']])
        self.assertIn('Directory.Build.props requirements', [entry['what'] for entry in item['unresolved']])

    def test_fvm_ancestor_search_uses_indexed_paths(self):
        # A large inventory may be iterated for census, but ancestor lookup
        # must reuse that index rather than scan every filename per component.
        from unittest.mock import patch
        write(self.root, {'.fvmrc': '{"flutter":"3.44.8"}',
            'web/pubspec.yaml': 'name: app\ndependencies:\n  flutter:\n    sdk: flutter\n'})
        inventory = project_inventory.scan(self.root, include_paths=True)
        original = project_requirements.requirements_for

        def indexed(ecosystem, present, read, directory, paths):
            self.assertIsInstance(paths, dict)
            self.assertIn('web/pubspec.yaml', paths)
            return original(ecosystem, present, read, directory, paths)

        with patch.object(project_requirements, 'requirements_for', side_effect=indexed):
            flutter = requirement(component(analyse(self.root, inventory=inventory), 'web'), 'flutter')
        self.assertEqual(flutter['selected'], '3.44.8')

    def test_rust_version_is_a_compiler_minimum_not_cargo(self):
        write(self.root, {'Cargo.toml': '[package]\nname = "x"\nrust-version = "1.75"\n'})
        rust = requirement(component(analyse(self.root)), 'rust')
        # Cargo documents rust-version as a minimum supported toolchain.
        self.assertEqual(rust['constraint'], '>=1.75')
        self.assertEqual(rust['executable'], 'rustc')


class HonestyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_an_unreadable_or_malformed_manifest_never_crashes_the_analysis(self):
        for content in ('{not json', '', '[]', 'null', '{"engines": "not an object"}'):
            with self.subTest(content=content):
                root = Path(tempfile.mkdtemp())
                self.addCleanup(lambda r=root: __import__('shutil').rmtree(r, ignore_errors=True))
                write(root, {'package.json': content})
                node = requirement(component(analyse(root)), 'node')
                self.assertFalse(node['established'])

    def test_a_manifest_for_an_unregistered_ecosystem_is_reported_not_guessed(self):
        write(self.root, {'mix.exs': 'defmodule X do end', 'lib/x.ex': ''})
        report = analyse(self.root)
        self.assertIn('unregistered manifests',
                      [entry['what'] for entry in report['unresolved']])

    def test_an_empty_repository_produces_an_empty_analysis_with_coverage(self):
        report = analyse(self.root)
        self.assertEqual(report['components'], [])
        self.assertIn('coverage', report)
        self.assertEqual(report['coverage']['files'], 0)

    def test_a_truncated_scan_is_carried_into_the_analysis(self):
        write(self.root, {f'p{index}/package.json': '{}' for index in range(20)})
        inventory = project_inventory.scan(self.root, {'max_paths': 5}, include_paths=True)
        report = project_requirements.analyse(self.root, inventory=inventory)
        self.assertTrue(report['coverage']['truncated'])

    def test_analysis_does_not_execute_or_open_anything_outside_the_manifests(self):
        write(self.root, {'package.json': json.dumps({'engines': {'node': '20.0.0'}}),
                          'src/evil.js': 'throw new Error("must not run")'})
        opened = []
        def read(name):
            opened.append(name)
            return (self.root / name).read_bytes()
        analyse(self.root, read=read)
        self.assertTrue(all(name in ('package.json',) for name in opened), opened)

    def test_a_manifest_is_read_once_however_many_requirements_consult_it(self):
        # engines.node, three dependency fields and the script list all consult
        # package.json. In the committed scope every read is two Git invocations,
        # so re-reading it is one fact established five times over.
        write(self.root, {'package.json': json.dumps({
            'engines': {'node': '>=20'}, 'dependencies': {'react': '^18.0.0'},
            'devDependencies': {'vite': '^5.0.0'}, 'peerDependencies': {'react-dom': '^18.0.0'},
            'scripts': {'test': 'vitest', 'build': 'vite build'}})})
        opened = []
        def read(name):
            opened.append(name)
            return (self.root / name).read_bytes()
        analyse(self.root, read=read)
        self.assertEqual(opened, ['package.json'])


class InferredTests(unittest.TestCase):
    """A project with source but no manifest still names its runtime."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_plain_php_files_without_composer_yield_a_php_component(self):
        # The case this work started from: index.php and nothing else.
        write(self.root, {'index.php': '<?php', 'classes/User.php': '<?php',
                          'template/header.php': '<?php'})
        report = analyse(self.root)
        self.assertEqual(len(report['components']), 1)
        item = report['components'][0]
        self.assertEqual(item['basis'], 'inferred')
        self.assertEqual([entry['id'] for entry in item['languages']], ['php'])
        php = requirement(item, 'php')
        self.assertFalse(php['established'])
        self.assertIn('no manifest there declares a version', php['reason'])
        # No framework, no candidate command, and nothing invented.
        self.assertEqual(item['frameworks'], [])
        self.assertEqual(item['candidate_commands'], [])
        self.assertEqual(item['evidence']['manifests'], [])

    def test_a_manifest_component_is_not_duplicated_by_an_inferred_one(self):
        write(self.root, {'composer.json': json.dumps({'require': {'php': '^8.2'}}),
                          'index.php': '<?php'})
        report = analyse(self.root)
        self.assertEqual([item['basis'] for item in report['components']], ['manifest'])
        self.assertEqual(requirement(report['components'][0], 'php')['constraint'], '^8.2')

    def test_unregistered_languages_do_not_produce_a_component(self):
        write(self.root, {'main.rb': ''})
        report = analyse(self.root)
        self.assertEqual(report['components'], [])

    def test_a_committed_vendor_tree_never_becomes_a_component_or_requirement(self):
        # A vendored JavaScript bundle is a committed third-party artifact, not
        # this project's Node requirement. Inferring one would move the whole
        # repository onto the generic environment path over somebody else's code.
        readings = {'web/dashboard/pubspec.yaml':
                    b'name: dashboard\nenvironment:\n  sdk: ">=3.0.0 <4.0.0"\n'}
        def read(name, limit=None):
            return readings.get(name)
        paths = ['web/dashboard/pubspec.yaml', 'web/dashboard/lib/main.dart',
                 'web/jaspr_dashboard/web/vendor/xterm/xterm.js']
        inventory = project_inventory.from_paths(paths)
        self.assertEqual(inventory['skipped'], dict(vendor=1))
        report = analyse(self.root, inventory=inventory, read=read)
        self.assertEqual([item['path'] for item in report['components']], ['web/dashboard'])
        self.assertEqual([entry['tool'] for item in report['components']
                          for entry in item['requirements']], ['dart'])
        # First-party sources in the same languages still infer their runtime.
        first_party = analyse(self.root, inventory=project_inventory.from_paths(
            [*paths, 'web/toolbar.js']), read=read)
        self.assertIn('node', [entry['tool'] for item in first_party['components']
                               for entry in item['requirements']])

    def test_a_mixed_project_infers_only_the_uncovered_ecosystem(self):
        write(self.root, {'package.json': json.dumps({'engines': {'node': '20.0.0'}}),
                          'src/app.ts': '', 'index.php': '<?php'})
        report = analyse(self.root)
        bases = {item['basis'] for item in report['components']}
        self.assertEqual(bases, {'manifest', 'inferred'})
        inferred = next(item for item in report['components'] if item['basis'] == 'inferred')
        self.assertEqual([entry['id'] for entry in inferred['ecosystems']], ['php'])

    def test_a_covered_ecosystem_does_not_suppress_inference_elsewhere(self):
        # A managed component covers its own directory, not the whole tree: PHP
        # under `legacy/` is a different component from the one under `app/`.
        write(self.root, {'app/composer.json': json.dumps({'require': {'php': '^8.2'}}),
                          'app/index.php': '<?php', 'legacy/index.php': '<?php'})
        report = analyse(self.root)
        self.assertEqual([(item['path'], item['basis']) for item in report['components']],
                         [('app', 'manifest'), ('legacy', 'inferred')])

    def test_inference_does_not_collide_with_a_component_at_the_root(self):
        write(self.root, {'package.json': json.dumps({'engines': {'node': '20.0.0'}}),
                          'legacy/index.php': '<?php'})
        report = analyse(self.root)
        paths = [(item['path'], item['basis']) for item in report['components']]
        self.assertEqual(paths, [('.', 'manifest'), ('legacy', 'inferred')])

    def test_a_component_owns_the_directories_beneath_it(self):
        # One component per directory would fragment this into `classes` and
        # `template`; the shallowest shared directory owns them all.
        write(self.root, {'index.php': '<?php', 'classes/User.php': '<?php',
                          'template/header.php': '<?php'})
        report = analyse(self.root)
        self.assertEqual([item['path'] for item in report['components']], ['.'])
        self.assertEqual([entry['id'] for entry in report['components'][0]['languages']], ['php'])

    def test_sibling_source_groups_do_not_become_one_component(self):
        # Two directories with no evidence connecting them: the common ancestor
        # would invent a boundary, so they stay separate groups.
        write(self.root, {'a/index.php': '<?php', 'b/index.php': '<?php'})
        report = analyse(self.root)
        self.assertEqual([item['path'] for item in report['components']], ['a', 'b'])
        for item in report['components']:
            self.assertEqual(item['basis'], 'inferred')
            self.assertEqual([entry['evidence'] for entry in item['unresolved']],
                             [item['path'] + '/index.php'])

    def test_a_root_file_and_a_subdirectory_are_one_group(self):
        write(self.root, {'index.php': '<?php', 'legacy/db.php': '<?php',
                          'other/deep/x.php': '<?php'})
        report = analyse(self.root)
        self.assertEqual([item['path'] for item in report['components']], ['.'])
        self.assertEqual(report['components'][0]['languages'][0]['files'], 3)


class EvidenceStateTests(unittest.TestCase):
    """A located declaration and an extracted requirement are separate facts."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_an_unreadable_manifest_does_not_establish_a_requirement(self):
        write(self.root, {'package.json': json.dumps({'engines': {'node': '>=20'}}),
                          'web/package.json': json.dumps({'engines': {'node': '>=20'}})})
        # Oversize the nested manifest past the read limit.
        (self.root / 'web' / 'package.json').write_text(
            json.dumps({'engines': {'node': '>=20'}, 'pad': 'x' * (project_requirements.MAX_MANIFEST_BYTES + 10)}))
        web = requirement(component(analyse(self.root), 'web'), 'node')
        self.assertFalse(web['established'])
        self.assertEqual(web['constraint'], '')
        self.assertTrue(web['observations'][0]['unreadable'])
        self.assertIn('could not be read', web['reason'])
        item = component(analyse(self.root), 'web')
        self.assertIn('Node.js version', [entry['what'] for entry in item['unresolved']])

    def test_a_readable_manifest_establishes_the_requirement(self):
        write(self.root, {'package.json': json.dumps({'engines': {'node': '>=20'}})})
        node = requirement(component(analyse(self.root)), 'node')
        self.assertTrue(node['established'])
        self.assertFalse(node['observations'][0]['unreadable'])

    def test_a_repository_of_unrecognised_files_explains_itself(self):
        # An empty component list is a result; the reason for it is not optional.
        write(self.root, {'data.bin': '', 'archive.xyz': '', 'notes.custom': ''})
        report = analyse(self.root)
        self.assertEqual(report['components'], [])
        reasons = {entry['what']: entry['reason'] for entry in report['unresolved']}
        self.assertIn('languages', reasons)
        self.assertIn('3 files were found and none matched', reasons['languages'])


class DependencyEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_a_pubspec_flutter_dependency_is_reachable(self):
        # The registry declared this and nothing ever looked it up, so Flutter
        # was registered-but-unreachable for a pubspec-only project.
        write(self.root, {'pubspec.yaml':
                          'name: app\n'
                          'dependencies:\n'
                          '  flutter:\n'
                          '    sdk: flutter\n'
                          '  http: ^1.2.0\n'})
        item = component(analyse(self.root))
        self.assertEqual([entry['id'] for entry in item['frameworks']], ['flutter'])
        self.assertEqual([entry['command'] for entry in item['candidate_commands']],
                         [['flutter', 'test'], ['flutter', 'analyze']])

    def test_a_pure_dart_package_is_not_claimed_as_flutter(self):
        # The framework list and the requirement list must agree: neither
        # mentions Flutter for a package that does not use it.
        write(self.root, {'pubspec.yaml': 'name: lib\ndependencies:\n  http: ^1.2.0\n'})
        item = component(analyse(self.root))
        self.assertEqual(item['frameworks'], [])
        self.assertEqual([entry['tool'] for entry in item['requirements']], ['dart'])
        self.assertEqual([entry['command'] for entry in item['candidate_commands']],
                         [['dart', 'test'], ['dart', 'analyze']])

    def test_unknown_paths_are_exposed_with_an_explicit_sample_bound(self):
        files = {f'data/{index:03}.unrecognised': ''
                 for index in range(project_inventory.UNKNOWN_EVIDENCE_LIMIT + 3)}
        write(self.root, files)
        report = analyse(self.root)
        coverage = report['coverage']
        self.assertEqual(coverage['unknown'], len(files))
        self.assertEqual(len(coverage['unknown_evidence']), project_inventory.UNKNOWN_EVIDENCE_LIMIT)
        self.assertTrue(set(coverage['unknown_evidence']).issubset(files))
        # The sample is disclosed once, in coverage. The finding keeps the total
        # as its answer and only an illustrative few paths on its line.
        finding = next(entry for entry in report['unresolved'] if entry['what'] == 'languages')
        self.assertIn(f"{len(files)} files were found", finding['reason'])
        self.assertLessEqual(len(finding['evidence'].split(', ')),
                             project_inventory.EVIDENCE_LIMIT)

    def test_a_flutter_environment_constraint_requires_flutter(self):
        write(self.root, {'pubspec.yaml': 'name: app\nenvironment:\n  sdk: ^3.5.0\n'
                                          '  flutter: ">=3.0.0"\n'})
        item = component(analyse(self.root))
        self.assertEqual(sorted(entry['tool'] for entry in item['requirements']),
                         ['dart', 'flutter'])
        self.assertEqual(requirement(item, 'flutter')['constraint'], '>=3.0.0')


if __name__ == '__main__':
    unittest.main()
