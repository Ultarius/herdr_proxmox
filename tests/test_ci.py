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
