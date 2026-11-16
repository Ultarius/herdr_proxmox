"""CI contracts follow declarations and retain useful failure evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('ci_policy', Path(__file__).parents[1] / 'scripts/ci.py')
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class CIPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.declare('>=3.11')

    def declare(self, requirement):
        (self.root / 'pyproject.toml').write_text('[project]\nrequires-python=' + json.dumps(requirement), encoding='utf-8')

    def test_floor_changes_select_the_new_minimum_without_workflow_edits(self):
        self.assertEqual(ci.runtime(self.root, (3, 12))['minimum'], '3.11')
        self.declare('>=3.12')
        result = ci.runtime(self.root, (3, 13))
        self.assertEqual(result['minimum'], '3.12')
        self.assertEqual(result['compatibility_needed'], 'true')

    def test_native_floor_does_not_request_duplicate_coverage(self):
        self.assertEqual(ci.runtime(self.root, (3, 11))['compatibility_needed'], 'false')

    def test_compatibility_can_select_a_newer_floor_before_installing_it(self):
        self.declare('>=3.13')
        result = ci.runtime(self.root, (3, 12), select_only=True)
        self.assertEqual(result['minimum'], '3.13')
        self.assertEqual(result['compatibility_needed'], 'true')
        with self.assertRaisesRegex(ValueError, 'older'):
            ci.runtime(self.root, (3, 12))

    def test_old_interpreter_import_does_not_fail_before_bootstrap_diagnostic(self):
        import builtins
        original = builtins.__import__
        def without_tomllib(name, *arguments, **options):
            if name == 'tomllib':
                raise ModuleNotFoundError(name)
            return original(name, *arguments, **options)
        module = importlib.util.module_from_spec(SPEC)
        with patch('builtins.__import__', side_effect=without_tomllib):
            SPEC.loader.exec_module(module)
        with self.assertRaisesRegex(ValueError, 'bootstrap the configured primary'):
            module.runtime(self.root, (3, 10))

    def test_unsupported_primary_and_unreviewed_requirement_shapes_fail(self):
        with self.assertRaisesRegex(ValueError, 'older'):
            ci.runtime(self.root, (3, 10))
        self.declare('>=3.11,<4')
        with self.assertRaisesRegex(ValueError, 'review runtime coverage'):
            ci.runtime(self.root, (3, 12))

    def test_github_output_contains_the_same_selected_runtime(self):
        output = self.root / 'output'
        with patch.object(ci, 'runtime', return_value=ci.runtime(self.root, (3, 12))):
            self.assertEqual(ci.main(['runtime', '--github-output', str(output)]), 0)
        self.assertEqual(output.read_text().splitlines(), ['minimum=3.11', 'native=3.12', 'compatibility_needed=true'])

    def test_summary_orders_slowest_first_preserves_failure_and_excludes_commands(self):
        report, summary = self.root / 'report', self.root / 'summary'
        rows = [dict(id='short', status='passed', duration_ms=10, command='private-path'),
                dict(id='long', status='failed', duration_ms=2500)]
        report.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        ci.summarize(report, summary, 'Core')
        content = summary.read_text()
        self.assertLess(content.index('| long | failed | 2.50 |'), content.index('| short |'))
        self.assertNotIn('private-path', content)
        self.assertIn('not a full job duration or a coverage verdict', content)

    def test_missing_report_is_reported_as_missing_not_a_pass(self):
        summary = self.root / 'summary'
        ci.summarize(self.root / 'absent', summary, 'Core')
        self.assertIn('No check timing report was produced', summary.read_text())
        self.assertNotIn('passed', summary.read_text())

    def test_malformed_status_cannot_be_published_as_success(self):
        report = self.root / 'report'
        report.write_text(json.dumps(dict(id='check', status='waived', duration_ms=0)), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'status'):
            ci.summarize(report, self.root / 'summary', 'Core')

    def suite(self, name, size, annotation="@TestOn('browser')"):
        directory = self.root / 'test'
        directory.mkdir(exist_ok=True)
        (directory / name).write_text((annotation + '\n' if annotation else '') + 'x' * size, encoding='utf-8')

    def test_browser_shards_cover_every_browser_suite_once_and_balance_by_size(self):
        sizes = dict(a_dom_test=90_000, b_dom_test=60_000, c_dom_test=50_000, d_dom_test=5_000,
                     e_dom_test=4_000, f_dom_test=3_000)
        for name, size in sizes.items():
            self.suite(name + '.dart', size)
        self.suite('state_test.dart', 80_000, annotation='')  # A VM suite: the web build runs it.
        self.suite('helper.dart', 10)                         # Not a suite at all.
        plan = ci.browser_shards(self.root / 'test', 3)
        self.assertEqual(sorted(name for shard in plan for name in shard), sorted(name + '.dart' for name in sizes))
        # The largest suites land on different shards, not together.
        owners = [next(index for index, shard in enumerate(plan) if name in shard)
                  for name in ('a_dom_test.dart', 'b_dom_test.dart', 'c_dom_test.dart')]
        self.assertEqual(len(set(owners)), 3)
        # The first shard also runs the navigation check, so it gets the lightest share.
        weight = lambda shard: sum(sizes[name[:-5]] for name in shard)
        self.assertEqual(min(plan, key=weight), plan[0])
        self.assertEqual(plan, ci.browser_shards(self.root / 'test', 3))  # Every shard job sees the same plan.

    def test_browser_shards_refuse_a_suite_that_would_run_nowhere(self):
        self.suite('a_dom_test.dart', 10)
        self.suite('b_dom_test.dart', 10, annotation="@TestOn('chrome')")
        with self.assertRaisesRegex(ValueError, 'b_dom_test.dart.*run in no CI job'):
            ci.browser_shards(self.root / 'test', 1)

    def test_browser_shards_refuse_more_shards_than_suites(self):
        self.suite('a_dom_test.dart', 10)
        with self.assertRaisesRegex(ValueError, 'cannot fill 2 shards'):
            ci.browser_shards(self.root / 'test', 2)
        with self.assertRaisesRegex(ValueError, 'positive'):
            ci.browser_shards(self.root / 'test', 0)

    def test_the_workflow_shards_plan_every_real_browser_suite(self):
        root = Path(__file__).parents[1]
        workflow = (root / '.github/workflows/ci.yml').read_text(encoding='utf-8')
        shards = json.loads(workflow.split('shard: ', 1)[1].split('\n', 1)[0])
        tests = root / 'web/jaspr_dashboard/test'
        plan = ci.browser_shards(tests, len(shards))
        browser = sorted(path.name for path in tests.glob('*_test.dart')
                         if '@TestOn(' in path.read_text(encoding='utf-8'))
        self.assertEqual(sorted(name for shard in plan for name in shard), browser)
        self.assertIn('ci.py browser-shards', workflow)
        with patch('builtins.print') as shown:
            self.assertEqual(ci.main(['browser-shards', '--directory', str(tests), '--index', '0',
                                      '--total', str(len(shards))]), 0)
        self.assertEqual(shown.call_args.args[0].splitlines(),
                         [(tests / name).as_posix() for name in plan[0]])
