"""The environment plan: what preparing a project would involve, and what it cannot.

These tests hold the contracts work package 1 introduces. A plan is built without
executing code or installing anything, chooses one exact version per requirement
by a fixed precedence, distinguishes a version Herdr cannot obtain from a version
it cannot choose, and carries a digest that changes exactly when the environment
would change.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import environment_capabilities
import environment_plan
from environment_store import EnvironmentPlanStore


def requirement(tool, constraint, notation='semver', components=('web',), discovered=None,
                conflicts=(), override=None, selected=''):
    """One resolved requirement, in the shape `ProjectAnalysis.effective` produces."""
    if discovered is None:
        discovered = [dict(component=components[0], constraint=constraint, selected=selected,
                           notation=notation, established=True, observations=[], selection_lookup=None)]
    return dict(tool=tool, label=tool, purpose='SDK', notation=notation, executable=tool,
                components=list(components), override=override, conflicts=list(conflicts),
                discovered=discovered)


def report(*requirements, project='app', revision='a' * 40, scope='committed'):
    return dict(project=project, requirements=list(requirements),
                source=dict(scope=scope, revision=revision if scope == 'committed' else '',
                            analyzer=5, boundary='repository root', at='', cache_seconds=0))


RELEASE = {'flutter': dict(label='Flutter', version='3.47.9', acquirable=True,
                           declared_by='.fvmrc#flutter', script='install/dev-tools-install.sh')}


class CapabilityTests(unittest.TestCase):
    def test_a_recognised_tool_maps_to_a_declared_capability(self):
        # Capability identity and command spelling are separate: rustc provides
        # the Rust toolchain and is not an executable named "rust-toolchain".
        self.assertEqual(environment_capabilities.capability_for('node'), 'node')
        self.assertEqual(environment_capabilities.capability_for('rust'), 'rust-toolchain')
        self.assertEqual(environment_capabilities.capability_for('rust-toolchain'), 'rust-toolchain')
        self.assertEqual(environment_capabilities.capability_for('dotnet'), 'dotnet-sdk')

    def test_an_unknown_tool_is_reported_rather_than_dropped(self):
        entry = environment_capabilities.describe('someunknowntool')
        self.assertFalse(entry['known'])
        self.assertFalse(entry['acquirable'])
        self.assertIn('No adapter is registered', entry['reason'])

    def test_support_is_advertised_per_capability_and_operation(self):
        entry = environment_capabilities.describe('node')
        self.assertTrue(entry['known'])
        # Discovery can read a requirement and verify a probe today; it cannot yet
        # acquire anything per project, and the registry must say so rather than
        # implying a universal "supported" flag.
        self.assertFalse(entry['acquirable'])
        self.assertNotIn('acquire', entry['operations'])
        self.assertIn('resolve', entry['operations'])
        self.assertIn('verify', entry['operations'])

    def test_the_release_catalog_is_kept_separate_from_project_intent(self):
        # The release pins Flutter from its own .fvmrc. That is release
        # capability, not the version a managed project may need.
        entries = environment_capabilities.release_catalog()
        self.assertIn('flutter', entries)
        self.assertTrue(entries['flutter']['acquirable'])
        self.assertEqual(entries['flutter']['version'], '3.47.9')
        self.assertEqual(environment_capabilities.release_exact('flutter'), '3.47.9')
        # No capability claims acquisition from the release catalog.
        self.assertFalse(environment_capabilities.describe('flutter')['acquirable'])


class SelectionPolicyTests(unittest.TestCase):
    """Precedence, and the distinction between unresolvable and unobtainable."""

    def test_existing_measurements_include_every_candidate_without_reprobing(self):
        data = report(requirement('node', '>=22 <23'))
        data['requirements'][0]['assessment'] = dict(measured='20.11.1',
            candidates=[dict(measured='20.11.1'), dict(measured='22.1.0')])
        value = environment_plan.build(data, release_entries={}, measure=environment_plan.measurements(data))
        self.assertEqual(value['requirements'][0]['installed'], ['20.11.1', '22.1.0'])
        self.assertEqual(value['requirements'][0]['version'], '22.1.0')
        self.assertEqual(value['requirements'][0]['state'], 'ready')

    def test_an_empty_override_does_not_restore_the_repository_constraint(self):
        value = environment_plan.build(report(requirement('node', '>=20',
            override={'constraint': '', 'notation': 'semver'})), release_entries={},
            measure=lambda name: ('22.1.0', ''))
        self.assertEqual(value['requirements'][0]['state'], 'unresolved')
        self.assertEqual(value['requirements'][0]['basis'], 'override')

    def test_every_range_and_selection_participates_in_selection(self):
        data = requirement('flutter', '>=3.0.0', notation='pub', selected='3.47.9')
        value = environment_plan.build(report(data), release_entries={}, measure=lambda name: ('3.40.0', ''))
        self.assertNotEqual(value['requirements'][0]['state'], 'ready')

    def test_components_can_select_different_versions_of_the_same_capability(self):
        report_data = report()
        report_data['components'] = [dict(path=name, requirements=[dict(tool='node', label='Node',
            constraint='', selected=version, notation='semver', established=True)])
            for name, version in [('old', '20.11.1'), ('new', '22.1.0')]]
        value = environment_plan.build(report_data, release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertEqual([item['constraint'] for item in value['requirements']], ['20.11.1', '22.1.0'])
        self.assertEqual([item['components'] for item in value['requirements']], [['old'], ['new']])
    @staticmethod
    def installed(version, tool='node'):
        """A probe that measures one command and finds nothing else."""
        return lambda name: ((version, '') if name == tool else (None, 'No executable was found.'))

    def test_an_installed_satisfying_version_is_ready(self):
        plan = environment_plan.build(
            report(requirement('node', '>=20 <23')), release_entries={},
            measure=self.installed('20.11.1'))
        item = plan['requirements'][0]
        self.assertEqual(item['state'], environment_plan.READY)
        self.assertEqual(item['version'], '20.11.1')
        self.assertEqual(item['selected_from'], 'installed')
        self.assertEqual(plan['blockers'], [])

    def test_an_installed_version_that_misses_the_range_is_not_reused(self):
        # Reusing 20.11.1 for a >=22 requirement would be a false pass.
        plan = environment_plan.build(
            report(requirement('node', '>=22 <23')), release_entries={},
            measure=self.installed('20.11.1'))
        item = plan['requirements'][0]
        self.assertNotEqual(item['state'], environment_plan.READY)
        self.assertEqual(item['version'], '')
        self.assertIn('20.11.1', item['reason'])

    def test_an_artifact_the_release_can_install_becomes_acquire(self):
        plan = environment_plan.build(report(requirement('flutter', '^3.47.0', notation='pub')),
                                      release_entries=RELEASE, measure=lambda name: (None, 'absent'))
        item = plan['requirements'][0]
        self.assertEqual(item['state'], environment_plan.ACQUIRE)
        self.assertEqual(item['version'], '3.47.9')
        self.assertEqual(item['selected_from'], 'artifact')

    def test_a_release_pin_below_the_requirement_is_unsupported_with_the_reason(self):
        plan = environment_plan.build(report(requirement('flutter', '^4.0.0', notation='pub')),
                                      release_entries=RELEASE, measure=lambda name: (None, 'absent'))
        item = plan['requirements'][0]
        self.assertEqual(item['state'], environment_plan.UNSUPPORTED)
        self.assertIn('3.47.9', item['reason'])
        self.assertIn('does not satisfy', item['reason'])

    def test_a_declared_version_with_no_installer_is_unsupported_not_unresolved(self):
        # The version is known; Herdr cannot obtain it. That is not the same as
        # failing to choose a version, and the two must not be conflated.
        plan = environment_plan.build(report(requirement('dotnet', '8.0.100', notation='dotnet')),
                                      release_entries={}, measure=lambda name: (None, 'absent'))
        item = plan['requirements'][0]
        self.assertEqual(item['state'], environment_plan.UNSUPPORTED)
        self.assertEqual(item['constraint'], '8.0.100')

    def test_nothing_declared_is_unresolved(self):
        plan = environment_plan.build(report(requirement('node', '')),
                                      release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertEqual(plan['requirements'][0]['state'], environment_plan.UNRESOLVED)

    def test_conflicting_declarations_are_unresolved_before_any_selection(self):
        conflicts = [dict(kind='disagreement', reason='two pins differ')]
        plan = environment_plan.build(report(requirement('node', '>=20 <23', conflicts=conflicts)),
                                      release_entries={}, measure=lambda name: (None, 'absent'))
        item = plan['requirements'][0]
        self.assertEqual(item['state'], environment_plan.UNRESOLVED)
        self.assertIn('conflicting', item['reason'])

    def test_an_override_outranks_the_repository_declaration(self):
        override = dict(constraint='>=22', notation='semver')
        plan = environment_plan.build(
            report(requirement('node', '>=20 <23', override=override)),
            release_entries={}, measure=lambda name: (None, 'absent'))
        item = plan['requirements'][0]
        self.assertEqual(item['constraint'], '>=22')
        self.assertEqual(item['basis'], 'override')

    def test_a_single_exact_selection_is_used_when_no_constraint_exists(self):
        plan = environment_plan.build(
            report(requirement('node', '', discovered=[
                dict(component='web', constraint='', selected='22.1.0', notation='semver',
                     established=True, observations=[], selection_lookup=None)])),
            release_entries={}, measure=lambda name: (None, 'absent'))
        item = plan['requirements'][0]
        self.assertEqual(item['constraint'], '22.1.0')
        self.assertEqual(item['basis'], 'repository_selection')

    def test_two_exact_selections_are_not_silently_picked_between(self):
        plan = environment_plan.build(
            report(requirement('node', '', components=('a', 'b'), discovered=[
                dict(component='a', constraint='', selected='22.1.0', notation='semver',
                     established=True, observations=[], selection_lookup=None),
                dict(component='b', constraint='', selected='20.1.0', notation='semver',
                     established=True, observations=[], selection_lookup=None)])),
            release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertEqual(plan['requirements'][0]['state'], environment_plan.UNRESOLVED)


class DigestTests(unittest.TestCase):
    def test_the_same_inputs_produce_the_same_digest(self):
        first = environment_plan.build(report(requirement('node', '>=20 <23')),
                                       release_entries={}, measure=lambda name: (None, 'absent'))
        second = environment_plan.build(report(requirement('node', '>=20 <23')),
                                        release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertEqual(first['digest'], second['digest'])

    def test_a_different_revision_changes_the_digest(self):
        first = environment_plan.build(report(requirement('node', '>=20 <23')),
                                       release_entries={}, measure=lambda name: (None, 'absent'))
        second = environment_plan.build(report(requirement('node', '>=20 <23'), revision='b' * 40),
                                        release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertNotEqual(first['digest'], second['digest'])

    def test_the_digest_ignores_the_build_timestamp_only(self):
        # `at` records when, not what; everything else is execution-affecting.
        first = environment_plan.build(report(requirement('node', '>=20 <23')),
                                       release_entries={}, measure=lambda name: (None, 'absent'))
        second = dict(environment_plan.build(report(requirement('node', '>=20 <23')),
                                               release_entries={}, measure=lambda name: (None, 'absent')),
                      at='1999-01-01T00:00:00+00:00')
        self.assertEqual(first['digest'], environment_plan.digest(second))

    def test_the_digest_is_a_sha256_hex(self):
        plan = environment_plan.build(report(requirement('node', '>=20 <23')),
                                      release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertRegex(plan['digest'], r'^[0-9a-f]{64}$')


class PreviewTests(unittest.TestCase):
    def test_a_working_tree_report_plans_as_a_preview(self):
        # A working-tree report must never authorise preparation; the plan still
        # builds so an operator can see what a commit would require.
        plan = environment_plan.build(report(requirement('node', '>=20 <23'), scope='working_tree'),
                                      release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertTrue(plan['preview'])
        self.assertEqual(plan['revision'], '')

    def test_a_committed_report_plans_against_its_revision(self):
        plan = environment_plan.build(report(requirement('node', '>=20 <23')),
                                      release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertFalse(plan['preview'])
        self.assertEqual(plan['revision'], 'a' * 40)


class StoreTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.temp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp, ignore_errors=True))
        self.store = EnvironmentPlanStore(Path(self.temp) / 'env.sqlite3')
        self.plan = environment_plan.build(report(requirement('node', '>=20 <23')),
                                            release_entries={}, measure=lambda name: (None, 'absent'))

    def test_a_plan_round_trips_by_digest(self):
        digest = self.store.save(self.plan)
        self.assertEqual(self.store.get(digest)['digest'], digest)
        self.assertEqual(self.store.latest('app')['digest'], digest)
        self.assertEqual(self.store.digest_for('app', 'a' * 40), digest)

    def test_saving_the_same_plan_twice_is_idempotent(self):
        first = self.store.save(self.plan)
        second = self.store.save(self.plan)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.history('app')), 1)

    def test_a_new_revision_is_a_new_identity_not_an_overwrite(self):
        self.store.save(self.plan)
        other = environment_plan.build(report(requirement('node', '>=20 <23'), revision='b' * 40),
                                       release_entries={}, measure=lambda name: (None, 'absent'))
        self.assertNotEqual(other['digest'], self.plan['digest'])
        self.store.save(other)
        self.assertEqual(len(self.store.history('app')), 2)

    def test_an_unrecognised_digest_is_not_an_error(self):
        self.assertIsNone(self.store.get('nope'))
        self.assertIsNone(self.store.get(''))

    def test_a_plan_without_a_valid_digest_is_refused(self):
        with self.assertRaises(ValueError):
            self.store.save(dict(self.plan, digest='short'))
        with self.assertRaises(ValueError):
            self.store.save(dict(self.plan, project=''))


class TwoComponentTests(unittest.TestCase):
    """The work-package exit criterion: two components, one plan, no execution."""

    def test_two_components_plan_independently_with_their_own_evidence(self):
        report_data = report(
            requirement('dotnet', '8.0.100', notation='dotnet', components=('Api',)),
            requirement('node', '>=20 <23', components=('web',)),
            project='app')
        probe = lambda name: (('20.11.1', '') if name == 'node' else (None, 'absent'))
        plan = environment_plan.build(report_data, release_entries={}, measure=probe)
        by_tool = {item['tool']: item for item in plan['requirements']}
        self.assertEqual(by_tool['node']['state'], environment_plan.READY)
        self.assertEqual(by_tool['node']['components'], ['web'])
        self.assertEqual(by_tool['dotnet']['state'], environment_plan.UNSUPPORTED)
        self.assertEqual(by_tool['dotnet']['components'], ['Api'])
        # Building the plan probed nothing: it reads the measurements it is given.
        self.assertEqual(plan['blockers'][0]['capability'], 'dotnet-sdk')
        self.assertIn('install', plan['blockers'][0]['remedy'])


if __name__ == '__main__':
    unittest.main()
