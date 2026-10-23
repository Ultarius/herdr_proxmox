"""Overrides and host assessment, in one requirement language.

An operator override is a requirement like any other: tool, notation, and a
constraint the shared evaluator interprets. These tests hold three things apart
that fail separately — the requirement, the executable that was found, and
whether this release could install something that would satisfy it.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import project_toolchains
from organizations import OrganizationStore

NOTATION = 'semver'


def fake_run(version, returncode=0):
    return Mock(return_value=Mock(returncode=returncode, stdout=f'tool {version}\n', stderr=''))


def which(name, path='/usr/bin/' + 'x'):
    return lambda value: f'/usr/bin/{value}' if value == name else None


def saved(toolchain='node', constraint='', notation=NOTATION, repository='app'):
    return dict(repository=repository, origin='operator_override', toolchain=toolchain,
                constraint=constraint, notation=notation, actor='admin', at='2026-01-01T00:00:00Z')


class RequirementShapeTests(unittest.TestCase):
    def test_tool_names_are_identifiers_never_paths_or_flags(self):
        for value in ('php', 'flutter', 'node', 'go-1.23', 'gcc_13', 'a' * 64):
            self.assertEqual(project_toolchains.tool_name(value), value)
        for value in ('', 'PHP', '../php', '/usr/bin/php', 'php;rm -rf', '-rf', 'php --version',
                      'php\n', 'a' * 65, None, 7, ['php']):
            with self.assertRaisesRegex(ValueError, 'lowercase identifier'):
                project_toolchains.tool_name(value)

    def test_a_constraint_is_free_text_under_a_bound(self):
        for value, expected in ((None, ''), ('', ''), ('20', '20'), ('>=20 <23', '>=20 <23'),
                                ('  ^1.2.3  ', '^1.2.3')):
            self.assertEqual(project_toolchains.tool_constraint(value), expected)
        for value in ('x' * 121, 'a\nb', 'a\x00b'):
            with self.assertRaises(ValueError):
                project_toolchains.tool_constraint(value)

    def test_a_notation_must_be_one_the_evaluator_understands(self):
        self.assertNotIn('python-version', project_toolchains.NOTATIONS)
        with self.assertRaises(ValueError):
            project_toolchains.tool_notation('python-version')
        self.assertEqual(project_toolchains.tool_notation(None), 'semver')
        self.assertEqual(project_toolchains.tool_notation('pep440'), 'pep440')
        for value in ('npm', 'semvar', '', 7):
            if value == '':
                self.assertEqual(project_toolchains.tool_notation(value), 'semver')
                continue
            with self.assertRaisesRegex(ValueError, 'supported version notation'):
                project_toolchains.tool_notation(value)

    def test_a_legacy_record_keeps_its_meaning(self):
        # Records written before the unified model carry `version` and no
        # notation; they are upgraded on read rather than rewritten.
        legacy = dict(repository='app', toolchain='php', version='8.2',
                      declared_by='admin', declared_at='2025-01-01T00:00:00Z')
        upgraded = project_toolchains.normalized(legacy)
        self.assertEqual(upgraded['constraint'], '8.2')
        self.assertEqual(upgraded['notation'], 'semver')
        self.assertEqual(upgraded['actor'], 'admin')
        self.assertEqual(upgraded['origin'], 'operator_override')
        self.assertIsNone(project_toolchains.normalized(None))


class ProbeArgumentTests(unittest.TestCase):
    def test_a_tool_whose_version_is_a_subcommand_is_probed_that_way(self):
        # `go` reports its version through the documented subcommand rather than
        # a flag; assuming `--version` everywhere reported it as unmeasured.
        self.assertEqual(project_toolchains.probe_arguments('go'), ('version',))
        self.assertEqual(project_toolchains.probe_arguments('node'), ('--version',))

    def test_the_declared_arguments_reach_the_process_invocation(self):
        runner = Mock(return_value=Mock(returncode=0,
                                        stdout='go version go1.22.1 linux/amd64\n', stderr=''))
        measured = project_toolchains.measure('go', '/usr/local/go/bin/go', run=runner)
        self.assertEqual(runner.call_args[0][0], ['/usr/local/go/bin/go', 'version'])
        self.assertEqual(measured[0], '1.22.1')


class AssessmentTests(unittest.TestCase):
    def test_python3_is_detected_without_a_python_alias(self):
        report = project_toolchains.assess(saved('python', '>=3.11', 'pep440'),
            which=which('python3'), run=fake_run('3.12.2'))
        self.assertEqual(report['verdict'], 'satisfied')
        self.assertEqual(report['selected'], '/usr/bin/python3')

    def test_python_aliases_are_all_measured_and_duplicate_paths_are_not_repeated(self):
        report = project_toolchains.assess(saved('python'),
            which=lambda name: '/usr/bin/' + name, run=fake_run('3.12.2'))
        self.assertEqual([item['path'] for item in report['candidates']],
                         ['/usr/bin/python3', '/usr/bin/python'])
        report = project_toolchains.assess(saved('python'),
            which=lambda name: '/usr/bin/python3', run=fake_run('3.12.2'))
        self.assertEqual(len(report['candidates']), 1)

    def assess(self, record, entry=None, catalog=None, name='node', version='20.11.1'):
        return project_toolchains.assess(record, entry, catalog,
                                         which=which(name), run=fake_run(version))

    def test_a_bare_constraint_matches_at_its_precision(self):
        self.assertEqual(self.assess(saved(constraint='20'), version='20.11.1')['verdict'], 'satisfied')
        self.assertEqual(self.assess(saved(constraint='20'), version='21.0.0')['verdict'], 'violated')

    def test_a_range_constraint_is_evaluated_not_prefix_matched(self):
        # The old private matcher compared strings; `>=20 <23` is not a version.
        satisfied = self.assess(saved(constraint='>=20 <23'), version='22.1.0')
        self.assertEqual(satisfied['verdict'], 'satisfied')
        self.assertEqual(self.assess(saved(constraint='>=20 <23'), version='23.0.0')['verdict'],
                         'violated')

    def test_notation_decides_the_operators_meaning(self):
        # `~=` is a compatible release in PEP 440 and meaningless in semver.
        pep440 = saved(toolchain='python', constraint='~=3.8.1', notation='pep440')
        self.assertEqual(self.assess(pep440, name='python', version='3.12.0')['verdict'], 'violated')
        as_semver = saved(toolchain='python', constraint='~=3.8.1')
        self.assertEqual(self.assess(as_semver, name='python', version='3.12.0')['verdict'],
                         'undecidable')

    def test_an_empty_constraint_reports_what_was_measured(self):
        verdict = self.assess(saved(), version='20.11.1')
        self.assertEqual(verdict['verdict'], 'satisfied')
        self.assertIn('declares no constraint', verdict['detail'])

    def test_every_candidate_is_measured_and_the_first_usable_is_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            release = Path(folder) / 'node'
            release.write_text('fixture')
            entry = {'required': '20.0.0', 'executable': str(release)}
            report = project_toolchains.assess(
                saved(), entry, {'node': {}}, which=which('node'), run=fake_run('20.11.1'))
        self.assertEqual([item['kind'] for item in report['candidates']], ['release', 'path'])
        self.assertTrue(all(item['measured'] == '20.11.1' for item in report['candidates']))
        self.assertEqual(report['selected'], str(release))

    def test_a_configured_executable_that_is_absent_still_leaves_path_as_an_option(self):
        entry = {'required': '20.0.0', 'executable': '/opt/node-20.0.0/bin/node'}
        report = project_toolchains.assess(saved(), entry, {'node': {}}, which=which('node'),
                                           run=fake_run('22.1.0'))
        self.assertEqual([item['present'] for item in report['candidates']], [False, True])
        self.assertEqual(report['selected'], '/usr/bin/node')
        self.assertEqual(report['verdict'], 'satisfied')

    def test_nothing_present_is_missing_not_a_verdict(self):
        report = project_toolchains.assess(saved(), None, None, which=lambda name: None,
                                           run=Mock(side_effect=AssertionError('nothing to probe')))
        self.assertEqual(report['verdict'], 'missing')
        self.assertEqual(report['selected'], '')
        self.assertIn('answered a version request', report['detail'])

    def test_a_probe_failure_is_reported_rather_than_raised(self):
        report = project_toolchains.assess(saved(), None, None, which=which('node'),
                                           run=Mock(side_effect=OSError('exec format error')))
        self.assertEqual(report['verdict'], 'missing')
        self.assertIn('could not be run', report['detail'])


class ProvisioningOfferTests(unittest.TestCase):
    def test_an_installer_that_would_not_satisfy_the_requirement_does_not_authorize_installing(self):
        # Existence is not satisfaction. The release pins 3.44.8 and the project
        # needs a different major, so an install would not resolve the blocker.
        # (`^3.10.0` would accept it — this is the case that does not.)
        entry = {'required': '3.44.8', 'executable': '/opt/flutter/bin/flutter'}
        report = project_toolchains.assess(
            saved(toolchain='flutter', constraint='^2.0.0'), entry, {'flutter': {}},
            which=lambda name: None, run=fake_run('3.10.0'))
        self.assertTrue(report['installer'])
        self.assertFalse(report['provisionable'])
        self.assertFalse(report['offer']['satisfies'])
        self.assertIn('does not satisfy', report['offer']['reason'])

    def test_a_compatible_range_does_authorize_installing(self):
        entry = {'required': '3.44.8', 'executable': '/opt/flutter/bin/flutter'}
        report = project_toolchains.assess(
            saved(toolchain='flutter', constraint='^3.10.0'), entry, {'flutter': {}},
            which=lambda name: None, run=fake_run('3.10.0'))
        self.assertTrue(report['provisionable'])

    def test_a_satisfying_installer_authorizes_installing(self):
        entry = {'required': '3.44.8', 'executable': '/opt/flutter/bin/flutter'}
        report = project_toolchains.assess(
            saved(toolchain='flutter', constraint='^3.44.0'), entry, {'flutter': {}},
            which=lambda name: None, run=fake_run('3.44.8'))
        self.assertTrue(report['provisionable'])
        self.assertTrue(report['offer']['satisfies'])

    def test_a_constraint_the_evaluator_cannot_read_never_authorizes_installing(self):
        entry = {'required': '1.0.0', 'executable': ''}
        report = project_toolchains.assess(
            saved(constraint='workspace:*'), entry, {'node': {}}, which=which('node'),
            run=fake_run('1.5.0'))
        self.assertFalse(report['provisionable'])

    def test_no_catalog_entry_means_no_installer_and_no_offer(self):
        report = project_toolchains.assess(saved(constraint='8.2'), None, {'flutter': {}},
                                           which=which('node'), run=fake_run('20.0.0'))
        self.assertFalse(report['installer'])
        self.assertFalse(report['provisionable'])
        self.assertIsNone(report['offer'])

    def test_a_release_that_declares_no_version_offers_nothing(self):
        entry = {'required': '', 'executable': ''}
        report = project_toolchains.assess(saved(), entry, {'node': {}}, which=which('node'),
                                           run=fake_run('20.0.0'))
        self.assertFalse(report['provisionable'])
        self.assertIn('declares no version', report['offer']['reason'])


class SnapshotTests(unittest.TestCase):
    def test_every_override_is_assessed_and_release_scope_stays_separate(self):
        records = {'app': saved(constraint='>=20 <23'), 'lib': saved(toolchain='flutter')}
        entries = {'node': {'required': '20.0.0', 'executable': ''},
                   'flutter': {'required': '3.44.8', 'executable': ''}}
        result = project_toolchains.snapshot(records, entries, {'node': {}, 'flutter': {}},
                                             which=lambda name: '/usr/bin/' + name,
                                             run=fake_run('20.11.1'))
        self.assertEqual(sorted(result['projects']), ['app', 'lib'])
        self.assertEqual(result['installed'], ['flutter', 'node'])
        self.assertIn('Herdr build tools', result['release_scope'])
        self.assertIn('semver', result['notations'])
        self.assertIn('pep440', result['notations'])


class StoreOverrideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = self.root / 'projects'
        (self.projects / 'raspinfo').mkdir(parents=True)
        self.command = Mock(side_effect=AssertionError('Overrides cannot launch agents'))
        self.store = OrganizationStore(self.root / 'org.sqlite3', self.projects, self.command)
        self.addCleanup(self.store.close)

    def test_an_override_round_trips_in_the_shared_shape(self):
        result = self.store.set_project_toolchain(
            {'repository': 'raspinfo', 'toolchain': 'php', 'constraint': '^8.2'}, 'admin')
        self.assertEqual((result['toolchain'], result['constraint'], result['notation']),
                         ('php', '^8.2', 'semver'))
        self.assertEqual(result['origin'], 'operator_override')
        self.assertEqual((result['actor'], bool(result['at'])), ('admin', True))
        stored = self.store.project_toolchain('raspinfo')
        self.assertEqual(stored['constraint'], '^8.2')

    def test_a_legacy_version_field_is_accepted_and_read_as_a_constraint(self):
        result = self.store.set_project_toolchain(
            {'repository': 'raspinfo', 'toolchain': 'php', 'version': '8.2'}, 'admin')
        self.assertEqual(result['constraint'], '8.2')
        self.assertEqual(self.store.project_toolchain('raspinfo')['constraint'], '8.2')

    def test_a_notation_can_be_declared(self):
        result = self.store.set_project_toolchain(
            {'repository': 'raspinfo', 'toolchain': 'php', 'constraint': '>=8.1',
             'notation': 'composer'}, 'admin')
        self.assertEqual(result['notation'], 'composer')

    def test_replacing_and_clearing_keep_one_record_per_project(self):
        self.store.set_project_toolchain({'repository': 'raspinfo', 'toolchain': 'php',
                                          'constraint': '8.2'}, 'admin')
        self.store.set_project_toolchain({'repository': 'raspinfo', 'toolchain': 'composer',
                                          'constraint': ''}, 'admin')
        self.assertEqual(sorted(self.store.project_toolchains()), ['raspinfo'])
        self.assertEqual(self.store.project_toolchain('raspinfo')['toolchain'], 'composer')
        cleared = self.store.set_project_toolchain({'repository': 'raspinfo', 'toolchain': ''}, 'admin')
        self.assertEqual(cleared['toolchain'], '')
        self.assertIsNone(self.store.project_toolchain('raspinfo'))
        self.assertEqual(self.store.project_toolchains(), {})

    def test_an_unknown_or_escaping_project_is_refused(self):
        for repository in ('../outside', 'absent', '/etc', ''):
            with self.assertRaises(ValueError):
                self.store.set_project_toolchain({'repository': repository, 'toolchain': 'php'}, 'admin')

    def test_an_invalid_override_names_the_field_it_refused(self):
        from request_validation import FieldValidationError
        for body, field in (({'repository': 'raspinfo', 'toolchain': 'PHP'}, 'toolchain'),
                            ({'repository': 'raspinfo', 'toolchain': 'php',
                              'notation': 'npm'}, 'notation'),
                            ({'repository': 'raspinfo', 'toolchain': 'php',
                              'constraint': 'x' * 200}, 'constraint')):
            with self.assertRaises(FieldValidationError) as refused:
                self.store.set_project_toolchain(body, 'admin')
            self.assertEqual(set(refused.exception.field_errors), {field})
        self.assertEqual(self.store.project_toolchains(), {})

    def test_overrides_survive_a_restart(self):
        self.store.set_project_toolchain({'repository': 'raspinfo', 'toolchain': 'php',
                                          'constraint': '^8.2'}, 'admin')
        reopened = OrganizationStore(self.root / 'org.sqlite3', self.projects, self.command)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.project_toolchain('raspinfo')['constraint'], '^8.2')

    def test_overrides_survive_a_configuration_round_trip(self):
        from organization_transfer import export_configuration, import_configuration
        self.store.set_project_toolchain({'repository': 'raspinfo', 'toolchain': 'php',
                                          'constraint': '^8.2', 'notation': 'composer'}, 'admin')
        bundle = export_configuration(self.store)
        self.assertEqual(len(bundle['project_toolchains']), 1)
        target_root = self.root / 'target'
        (target_root / 'projects' / 'raspinfo').mkdir(parents=True)
        target = OrganizationStore(target_root / 'org.sqlite3', target_root / 'projects', self.command)
        self.addCleanup(target.close)
        result = import_configuration(target, bundle)
        self.assertEqual(result['imported']['project_toolchains'], 1)
        restored = target.project_toolchain('raspinfo')
        self.assertEqual((restored['constraint'], restored['notation']),
                         ('^8.2', 'composer'))


class ImportedOverrideTests(unittest.TestCase):
    """An import is not a privileged rewrite of a project's requirement."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.command = Mock(side_effect=AssertionError('Overrides cannot launch agents'))
        self.target_root = self.root / 'target'
        (self.target_root / 'projects' / 'raspinfo').mkdir(parents=True)
        self.target = OrganizationStore(self.target_root / 'org.sqlite3',
                                        self.target_root / 'projects', self.command)
        self.addCleanup(self.target.close)

    def bundle(self, overrides):
        return dict(format='herdr-configuration', version=1, organizations=[], profiles=[],
                    groups=[], project_toolchains=overrides)

    def override(self, **changes):
        item = dict(repository='raspinfo', origin='operator_override', toolchain='php',
                    constraint='^8.2', notation='semver', actor='admin', at='2026-01-01T00:00:00Z')
        item.update(changes)
        return item

    def test_an_imported_override_is_validated_like_any_other(self):
        from organization_transfer import import_configuration
        for item in (self.override(notation='npm'), self.override(toolchain='PHP'),
                     self.override(constraint='x' * 200),
                     self.override(repository='../outside'),
                     self.override(repository='/etc'),
                     self.override(repository=''),
                     self.override(toolchain='')):
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    import_configuration(self.target, self.bundle([item]))
        self.assertEqual(self.target.project_toolchains(), {})

    def test_a_bundle_that_fails_validation_writes_nothing_at_all(self):
        from organization_transfer import import_configuration
        with self.assertRaises(ValueError):
            import_configuration(self.target, self.bundle([
                self.override(), self.override(repository='other', notation='nonsense')]))
        self.assertEqual(self.target.project_toolchains(), {})

    def test_a_duplicate_project_in_one_bundle_is_refused(self):
        from organization_transfer import import_configuration
        with self.assertRaisesRegex(ValueError, 'Duplicate project'):
            import_configuration(self.target, self.bundle([self.override(), self.override()]))

    def test_an_identical_retry_succeeds_and_a_differing_collision_is_refused(self):
        from organization_transfer import import_configuration
        import_configuration(self.target, self.bundle([self.override()]))
        self.assertEqual(self.target.project_toolchain('raspinfo')['constraint'], '^8.2')
        # Same content: a successful retry after a lost response.
        import_configuration(self.target, self.bundle([self.override()]))
        self.assertEqual(self.target.project_toolchain('raspinfo')['constraint'], '^8.2')
        # Different content: never silently applied.
        with self.assertRaisesRegex(ValueError, 'different content'):
            import_configuration(self.target, self.bundle([self.override(constraint='>=9.0')]))
        self.assertEqual(self.target.project_toolchain('raspinfo')['constraint'], '^8.2')


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
