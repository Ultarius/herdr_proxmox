"""The version-constraint engine, and the answers it must refuse to give.

Boundary cases are taken from the published rules, not from intuition:
npm ranges (<https://github.com/npm/node-semver#advanced-range-syntax>),
PEP 440 (<https://packaging.python.org/en/latest/specifications/version-specifiers/>)
and RubyGems pessimistic constraints. The tests asserting `undecidable` matter
most: an engine that guesses is worse than one that stops.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from dependency_constraints import SATISFIED, UNDECIDABLE, VIOLATED, evaluate


def semver(constraint, version):
    return evaluate(constraint, version, 'semver')


def pep440(constraint, version):
    return evaluate(constraint, version, 'pep440')


def poetry(constraint, version):
    return evaluate(constraint, version, 'poetry')


class ComparisonTests(unittest.TestCase):
    def test_python_family_conflicts_use_normalized_overlap(self):
        from dependency_constraints import family_verdict
        for family in ('3.12', 'v3.12', 'V3.12'):
            self.assertEqual(family_verdict(family, '>=3.12.1,<3.13', 'pep440'), SATISFIED)
            self.assertEqual(family_verdict(family, '>=3.13', 'pep440'), VIOLATED)
            self.assertEqual(family_verdict(family, '<3.12', 'pep440'), VIOLATED)
            self.assertEqual(family_verdict(family, '!=3.12.*', 'pep440'), VIOLATED)

    def test_python_major_request_can_use_another_minor_family(self):
        from dependency_constraints import family_verdict
        for constraint in ('!=3.0.*', '!=3.0.*,!=3.1.*', '>3.12,<3.13'):
            with self.subTest(constraint=constraint):
                self.assertEqual(family_verdict('3', constraint, 'pep440'), SATISFIED)
        self.assertEqual(family_verdict('3', '!=3.*', 'pep440'), VIOLATED)

    def test_composer_exact_versions_and_comparators_are_zero_padded(self):
        for constraint, version, expected in (
                ('8.1', '8.1.9', VIOLATED), ('8.1', '8.1.0', SATISFIED),
                ('=8.1', '8.1.1', VIOLATED), ('>8.1', '8.1.1', SATISFIED),
                ('>8.1', '8.1.0', VIOLATED), ('<=8.1', '8.1.1', VIOLATED),
                ('<=8.1', '8.1.0', SATISFIED), ('8.1.*', '8.1.9', SATISFIED),
                ('8.1.0.0', '8.1', SATISFIED), ('>=8.1.*', '8.2', UNDECIDABLE)):
            with self.subTest(constraint=constraint, version=version):
                self.assertEqual(evaluate(constraint, version, 'composer'), expected)
    def test_python_version_file_families_do_not_change_pep440_equality(self):
        for request, accepted, rejected in (
                ('3', '3.13.1', '4.0.0'), ('3.12', '3.12.14', '3.13.0'),
                ('3.12.1', '3.12.1', '3.12.14')):
            with self.subTest(request=request):
                self.assertEqual(evaluate(request, accepted, 'python-version'), SATISFIED)
                self.assertEqual(evaluate(request, rejected, 'python-version'), VIOLATED)
        self.assertEqual(pep440('3.12', '3.12.14'), VIOLATED)
        self.assertEqual(evaluate('>=3.12,<3.13', '3.12.14', 'python-version'), SATISFIED)
        self.assertEqual(evaluate('==3.12', '3.12.14', 'python-version'), VIOLATED)
        self.assertEqual(evaluate('==3.12.*', '3.12.14', 'python-version'), SATISFIED)
        for request in ('3.12.*', 'pypy@3.12', '3.12.1.2'):
            self.assertEqual(evaluate(request, '3.12.14', 'python-version'), UNDECIDABLE)

    def test_composer_tilde_bounds_depend_on_the_number_of_components(self):
        for constraint, accepted, rejected in (
                ('~8', '8.9.0', '9.0.0'), ('~8.1', '8.3.0', '9.0.0'),
                ('~8.1.2', '8.1.9', '8.2.0'), ('~0.1', '0.9.0', '1.0.0')):
            with self.subTest(constraint=constraint):
                self.assertEqual(evaluate(constraint, accepted, 'composer'), SATISFIED)
                self.assertEqual(evaluate(constraint, rejected, 'composer'), VIOLATED)
        self.assertEqual(evaluate('~8.1', '8.0.9', 'composer'), VIOLATED)
        self.assertEqual(semver('~8.1', '8.3.0'), VIOLATED)
        self.assertEqual(poetry('~8.1', '8.3.0'), VIOLATED)
        self.assertEqual(evaluate('~1.2.3.4', '1.2.3.9', 'composer'), SATISFIED)
        self.assertEqual(evaluate('~1.2.3.4', '1.2.4.0', 'composer'), VIOLATED)

    def test_missing_components_are_zero_on_both_sides(self):
        # The padding contract: 1.2 and 1.2.0 are the same version.
        self.assertEqual(semver('=1.2', '1.2.0'), SATISFIED)
        self.assertEqual(semver('=1.2.0', '1.2'), SATISFIED)
        self.assertEqual(semver('>=1.2', '1.2.0'), SATISFIED)
        self.assertEqual(semver('<=1.2', '1.2.0'), SATISFIED)
        self.assertEqual(semver('>1.2', '1.2.0'), VIOLATED)
        self.assertEqual(semver('<1.2.0', '1.2'), VIOLATED)

    def test_a_partial_version_is_an_x_range(self):
        # npm: `1.2` := `>=1.2.0 <1.3.0`
        self.assertEqual(semver('1.2', '1.2.9'), SATISFIED)
        self.assertEqual(semver('1.2', '1.3.0'), VIOLATED)
        self.assertEqual(semver('1', '1.9.9'), SATISFIED)
        self.assertEqual(semver('1', '2.0.0'), VIOLATED)

    def test_open_operators_increment_the_last_stated_component(self):
        # npm: `>1` := `>=2.0.0`, `<=1.2` := `<1.3.0`
        self.assertEqual(semver('>1', '2.0.0'), SATISFIED)
        self.assertEqual(semver('>1', '1.9.9'), VIOLATED)
        self.assertEqual(semver('>1.2', '1.3.0'), SATISFIED)
        self.assertEqual(semver('>1.2', '1.2.7'), VIOLATED)
        self.assertEqual(semver('<=1.2', '1.2.9'), SATISFIED)
        self.assertEqual(semver('<=1.2', '1.3.0'), VIOLATED)
        self.assertEqual(semver('<=1.2.3', '1.2.3'), SATISFIED)
        self.assertEqual(semver('<=1.2.3', '1.2.4'), VIOLATED)


class XRangeTests(unittest.TestCase):
    def test_wildcard_components_match_their_prefix(self):
        self.assertEqual(semver('1.2.x', '1.2.9'), SATISFIED)
        self.assertEqual(semver('1.2.x', '1.3.0'), VIOLATED)
        self.assertEqual(semver('1.X', '1.9.3'), SATISFIED)
        self.assertEqual(semver('*', '0.0.1'), SATISFIED)

    def test_a_comparator_with_a_wildcard_desugars_to_its_bound(self):
        # npm: `>=1.2.x` := `>=1.2.0`; `>1.2.x` := `>=1.3.0`;
        #      `<1.2.x` := `<1.2.0`;  `<=1.2.x` := `<1.3.0`
        self.assertEqual(semver('>=1.2.x', '2.0.0'), SATISFIED)
        self.assertEqual(semver('>=1.2.x', '1.1.0'), VIOLATED)
        self.assertEqual(semver('>1.2.x', '1.3.0'), SATISFIED)
        self.assertEqual(semver('>1.2.x', '1.2.9'), VIOLATED)
        self.assertEqual(semver('<1.2.x', '1.1.9'), SATISFIED)
        self.assertEqual(semver('<1.2.x', '1.2.0'), VIOLATED)
        self.assertEqual(semver('<=1.2.x', '1.2.9'), SATISFIED)
        self.assertEqual(semver('<=1.2.x', '1.3.0'), VIOLATED)


class CaretTests(unittest.TestCase):
    def test_caret_moves_the_leading_nonzero_component(self):
        # npm: `^1.2.3` := `>=1.2.3 <2.0.0`; `^0.2.3` := `>=0.2.3 <0.3.0`;
        #      `^0.0.3` := `>=0.0.3 <0.0.4`
        self.assertEqual(semver('^1.2.3', '1.9.9'), SATISFIED)
        self.assertEqual(semver('^1.2.3', '1.2.2'), VIOLATED)
        self.assertEqual(semver('^1.2.3', '2.0.0'), VIOLATED)
        self.assertEqual(semver('^0.2.3', '0.2.9'), SATISFIED)
        self.assertEqual(semver('^0.2.3', '0.3.0'), VIOLATED)
        self.assertEqual(semver('^0.0.3', '0.0.3'), SATISFIED)
        self.assertEqual(semver('^0.0.3', '0.0.4'), VIOLATED)

    def test_caret_with_every_component_zero_allows_nothing(self):
        # npm: `^0.0.x` := `>=0.0.0 <0.1.0`; `^0.0` := `>=0.0.0 <0.1.0`, but
        # an explicitly zero patch leaves no room: `^0.0.0` := `=0.0.0`.
        self.assertEqual(semver('^0.0.0', '0.0.0'), SATISFIED)
        self.assertEqual(semver('^0.0.0', '0.0.2'), VIOLATED)
        self.assertEqual(semver('^0.0', '0.0.9'), SATISFIED)
        self.assertEqual(semver('^0.0', '0.1.0'), VIOLATED)
        self.assertEqual(semver('^0', '0.9.9'), SATISFIED)
        self.assertEqual(semver('^0', '1.0.0'), VIOLATED)

    def test_caret_is_not_pep440(self):
        self.assertEqual(pep440('^1.2', '1.5.0'), UNDECIDABLE)


class TildeTests(unittest.TestCase):
    def test_tilde_moves_the_stated_minor_or_the_major(self):
        # npm: `~1.2.3` and `~1.2` := `>=1.2.x <1.3.0`; `~1` := `>=1.0.0 <2.0.0`
        self.assertEqual(semver('~1.2.3', '1.2.9'), SATISFIED)
        self.assertEqual(semver('~1.2.3', '1.3.0'), VIOLATED)
        self.assertEqual(semver('~1.2', '1.2.9'), SATISFIED)
        self.assertEqual(semver('~1.2', '1.9.0'), VIOLATED)
        self.assertEqual(semver('~1', '1.9.9'), SATISFIED)
        self.assertEqual(semver('~1', '2.0.0'), VIOLATED)
        self.assertEqual(semver('~0.2', '0.2.9'), SATISFIED)
        self.assertEqual(semver('~0.2', '0.3.0'), VIOLATED)


class PoetryTests(unittest.TestCase):
    """Poetry's own constraint language, kept apart from PEP 440's."""

    def test_caret_and_tilde_desugar_as_the_published_rules_say(self):
        self.assertEqual(poetry('^3.11', '3.11.9'), SATISFIED)
        self.assertEqual(poetry('^3.11', '4.0.0'), VIOLATED)
        self.assertEqual(poetry('^0.2.3', '0.2.9'), SATISFIED)
        self.assertEqual(poetry('^0.2.3', '0.3.0'), VIOLATED)
        self.assertEqual(poetry('~3.11', '3.11.9'), SATISFIED)
        self.assertEqual(poetry('~3.11', '3.12.0'), VIOLATED)

    def test_a_bare_version_is_exact_and_commas_conjoin(self):
        self.assertEqual(poetry('3.11', '3.11.0'), SATISFIED)
        self.assertEqual(poetry('3.11', '3.11.9'), VIOLATED)
        self.assertEqual(poetry('3.11', '3.12.0'), VIOLATED)
        self.assertEqual(poetry('<=3.11', '3.11.1'), VIOLATED)
        self.assertEqual(poetry('>3.11', '3.11.1'), SATISFIED)
        self.assertEqual(poetry('3.11.*', '3.11.9'), SATISFIED)
        self.assertEqual(poetry('>=3.11,<4.0', '3.12.1'), SATISFIED)
        self.assertEqual(poetry('>=3.11,<4.0', '3.10.0'), VIOLATED)
        self.assertEqual(poetry('*', '3.9.1'), SATISFIED)

    def test_poetry_adds_operators_without_leaking_caret_into_pep440(self):
        self.assertEqual(pep440('^3.11', '3.11.9'), UNDECIDABLE)
        self.assertEqual(poetry('~=3.11', '3.11.9'), SATISFIED)
        self.assertEqual(poetry('~=3.11', '4.0.0'), VIOLATED)
        self.assertEqual(poetry('!=3.11', '3.11.0'), VIOLATED)
        self.assertEqual(poetry('!=3.11', '3.11.1'), SATISFIED)


class Pep440Tests(unittest.TestCase):
    def test_compatible_release_freezes_all_but_the_last_stated_component(self):
        # PEP 440: `~=3.8.1` := `>=3.8.1, ==3.8.*`; `~=3.8` := `>=3.8, ==3.*`
        self.assertEqual(pep440('~=3.8.1', '3.8.5'), SATISFIED)
        self.assertEqual(pep440('~=3.8.1', '3.9.0'), VIOLATED)
        self.assertEqual(pep440('~=3.8.1', '3.12.0'), VIOLATED)
        self.assertEqual(pep440('~=3.8', '3.12.0'), SATISFIED)
        self.assertEqual(pep440('~=3.8', '4.0.0'), VIOLATED)
        # A compatible release needs at least two components to be defined.
        self.assertEqual(pep440('~=3', '3.12.0'), UNDECIDABLE)
        self.assertEqual(semver('~=3.8', '3.12.0'), UNDECIDABLE)

    def test_equality_zero_pads_and_inequality_inverts_it(self):
        self.assertEqual(pep440('==3.8', '3.8.0'), SATISFIED)
        self.assertEqual(pep440('==3.8', '3.9.0'), VIOLATED)
        self.assertEqual(pep440('==3.8', '3.8.1'), VIOLATED)
        self.assertEqual(pep440('==3.8.*', '3.8.1'), SATISFIED)
        self.assertEqual(pep440('!=3.8.*', '3.8.1'), VIOLATED)
        self.assertEqual(pep440('>3.8', '3.8.1'), SATISFIED)
        self.assertEqual(pep440('<=3.8', '3.8.1'), VIOLATED)
        # PEP 440 `!=3.8` excludes 3.8 and its zero-padded form, not all of 3.8.x.
        self.assertEqual(pep440('!=3.8', '3.8.0'), VIOLATED)
        self.assertEqual(pep440('!=3.8', '3.8.2'), SATISFIED)
        self.assertEqual(pep440('!=3.8', '3.9.0'), SATISFIED)
        self.assertEqual(semver('!=1.0', '2.0.0'), UNDECIDABLE)

    def test_pep440_conjunction(self):
        self.assertEqual(pep440('>=3.8,<4.0', '3.12.2'), SATISFIED)
        self.assertEqual(pep440('>=3.8,<4.0', '4.0.0'), VIOLATED)


class ConjunctionTests(unittest.TestCase):
    def test_node_engines_style_intersection(self):
        self.assertEqual(semver('>=18 <23', '20.11.1'), SATISFIED)
        self.assertEqual(semver('>=18 <23', '23.0.0'), VIOLATED)
        self.assertEqual(semver('>=18 <23', '16.20.0'), VIOLATED)
        self.assertEqual(semver('>= 18.0.0 < 23', '20.1.0'), SATISFIED)

    def test_disjunction(self):
        self.assertEqual(semver('^20.0.0 || ^21.0.0', '21.5.0'), SATISFIED)
        self.assertEqual(semver('^20.0.0 || ^21.0.0', '19.9.0'), VIOLATED)
        # An unevaluated alternative is not a refutation.
        self.assertEqual(semver('^2.0 || git+ssh://x', '1.4.0'), UNDECIDABLE)
        # Every alternative was evaluated and refused it, so that is a refutation.
        self.assertEqual(semver('^2.0 || ^3.0', '1.4.0'), VIOLATED)
        # And a matching alternative settles it despite the unknown one.
        self.assertEqual(semver('^1.0 || git+ssh://x', '1.4.0'), SATISFIED)


class NotationTests(unittest.TestCase):
    def test_an_operator_outside_its_notation_is_refused_not_reinterpreted(self):
        self.assertEqual(semver('~=3.8.1', '3.12.0'), UNDECIDABLE)
        self.assertEqual(semver('!=1.0', '1.0'), UNDECIDABLE)
        self.assertEqual(evaluate('^1.2', '1.5.0', 'gomod'), UNDECIDABLE)
        self.assertEqual(evaluate('>=1.0', '1.5.0', 'nonsense'), UNDECIDABLE)
        self.assertEqual(evaluate('1.2', '1.2', None), UNDECIDABLE)

    def test_composer_and_pub_keep_only_their_own_operators(self):
        self.assertEqual(evaluate('^1.2.3', '1.9.0', 'composer'), SATISFIED)
        self.assertEqual(evaluate('^1.2.3', '2.0.0', 'composer'), VIOLATED)
        self.assertEqual(evaluate('^1.2.3', '1.2.9', 'pub'), SATISFIED)
        self.assertEqual(evaluate('~1.2', '1.2.9', 'pub'), UNDECIDABLE)
        self.assertEqual(evaluate('~>2.2', '2.9.0', 'rubygems'), SATISFIED)
        self.assertEqual(evaluate('~>2.2', '3.0.0', 'rubygems'), VIOLATED)
        self.assertEqual(evaluate('~>2.2.3', '2.3.0', 'rubygems'), VIOLATED)


class UndecidableTests(unittest.TestCase):
    def test_an_unmeasurable_version_is_undecidable(self):
        for version in (None, '', 'latest', 'nightly', 'abc', '1.2.3-rc1', '1.x', 'x'):
            self.assertEqual(semver('>=1.0', version), UNDECIDABLE, version)

    def test_notation_outside_the_supported_set_is_undecidable(self):
        for constraint in ('workspace:*', 'catalog:', 'file:../sibling', 'git+https://x#v1',
                           'npm:other@^1', '1.2.3-beta.1', '>=1.0.0-rc1', '>= 1.0 && < 2.0',
                           '1.2.3 || latest', '^', '~', '>=', '**', '1.2.3 - 2.3.4',
                           '===1.2.3', '>1.0 <', '1.2.3+build'):
            self.assertEqual(semver(constraint, '1.5.0'), UNDECIDABLE, constraint)

    def test_an_absent_constraint_accepts_anything_measured(self):
        for constraint in (None, '', '  ', '*', 'x', 'X'):
            self.assertEqual(semver(constraint, '1.2.3'), SATISFIED, constraint)
            self.assertEqual(pep440(constraint, '1.2.3'), SATISFIED, constraint)


if __name__ == '__main__':
    unittest.main()
