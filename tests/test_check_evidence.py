import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from check_runner import read_report, required_passed, run_scripts


def usable_bash():
    if os.name != 'nt':
        return 'bash'
    found = shutil.which('git')
    candidate = Path(found).parent.parent / 'bin/bash.exe' if found else None
    return str(candidate) if candidate and candidate.exists() else None


BASH = usable_bash()

spec = importlib.util.spec_from_file_location('evidence_script', Path(__file__).parents[1] / 'scripts/check-evidence.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class CheckEvidenceTests(unittest.TestCase):
    def test_successful_web_check_cannot_hide_a_missing_or_failed_check(self):
        # Protocol rule, independent of which checks a repository chooses:
        # a passing check must never mask a required check that did not pass.
        for command, expected in ((['herdr-nonexistent-linter'], 'unavailable'),
                                  ([sys.executable, '-c', 'raise SystemExit(1)'], 'failed')):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / 'checks.jsonl'
                self.assertEqual(script.execute('flutter-release', [sys.executable, '-c', 'pass'], report), 0)
                self.assertNotEqual(script.execute('extra-lint', command, report), 0)
                checks = read_report(report, 'build')
                self.assertEqual(checks[-1]['status'], expected)
                self.assertFalse(required_passed(checks))

    def test_only_actual_passes_satisfy_required_checks(self):
        self.assertFalse(required_passed([]))
        self.assertTrue(required_passed([dict(status='passed', exit_code=0)]))
        for status in ('failed', 'waived', 'skipped', 'unavailable', 'not_run'):
            self.assertFalse(required_passed([dict(status=status, exit_code=0)]))

    def test_missing_tools_and_unexecuted_plan_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'checks.jsonl'
            self.assertEqual(script.execute('tool', ['herdr-nonexistent-test-tool'], report), 127)
            script.finish(report, ['tool', 'tests'])
            checks = read_report(report, 'build')
            self.assertEqual([c['status'] for c in checks], ['unavailable', 'not_run'])
            self.assertFalse(required_passed(checks))
            self.assertTrue(all(c['source'] == 'script' for c in checks))

    def test_malformed_or_duplicate_report_cannot_claim_success(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'checks.jsonl'
            for value in ({'id': 'lint', 'status': 'passed', 'exit_code': 3},
                          {'id': 'lint', 'status': []}, {'id': 'lint', 'status': 'passed', 'exit_code': True}):
                report.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    read_report(report, 'build')
            value = dict(id='lint', status='passed', exit_code=0)
            report.write_text(json.dumps(value) + '\n' + json.dumps(value))
            with self.assertRaises(ValueError):
                read_report(report, 'build')

    @unittest.skipUnless(BASH, 'a POSIX bash is unavailable')
    def test_a_failed_report_check_keeps_the_run_nonzero_despite_exit_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            (tree / 'scripts').mkdir()
            script = tree / 'scripts/build-web.sh'
            script.write_text(
                '#!/bin/sh\n'
                'printf \'%s\\n\' \'{"id":"lint","status":"failed","exit_code":1}\' >> "$HERDR_CHECK_REPORT"\n'
                'exit 0\n')
            checks, exit_code, timed_out = run_scripts(
                tree, ['scripts/build-web.sh'], tree, 60, BASH, 200_000)
        self.assertFalse(timed_out)
        self.assertFalse(required_passed(checks))
        self.assertNotEqual(exit_code, 0)


if __name__ == '__main__':
    unittest.main()
