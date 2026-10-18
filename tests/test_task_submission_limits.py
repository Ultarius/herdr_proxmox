"""Admission fairness, resource bounds and safe release without real sleeps."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
from task_submission_limits import SubmissionLimits, SubmissionBusy
from operation_metrics import Metrics


class SubmissionLimitTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.metrics = Metrics()
        self.patch = patch('task_submission_limits.METRICS', self.metrics)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def gate(self, **settings):
        return SubmissionLimits(clock=lambda: self.now, **settings)

    def test_tokens_refill_and_distinct_authenticated_sessions_are_independent(self):
        gate = self.gate(burst=1, refill_seconds=10)
        with gate.claim('run', 'secret'):
            pass
        with self.assertRaises(SubmissionBusy) as caught:
            with gate.claim('run', 'secret'):
                self.fail('Limited request entered')
        self.assertEqual(caught.exception.retry_after, 10)
        with gate.claim('another', 'another-secret'):
            pass
        self.now += 10
        with gate.claim('run', 'secret'):
            pass
        self.assertNotIn('secret', repr(gate.sessions))
        self.assertNotIn('secret', repr(self.metrics.snapshot()))
        self.assertEqual(self.metrics.snapshot()['counters']['task.submit_limited'], 1)

    def test_session_and_global_concurrency_release_on_exceptions(self):
        gate = self.gate(per_session=1, max_active=2)
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            with gate.claim('run', 'secret'):
                with self.assertRaises(SubmissionBusy):
                    with gate.claim('run', 'secret'):
                        pass
                with gate.claim('other', 'token'):
                    with self.assertRaises(SubmissionBusy):
                        with gate.claim('third', 'token'):
                            pass
                raise RuntimeError('interrupted')
        self.assertEqual(gate.active, 0)
        with gate.claim('run', 'secret'):
            pass

    def test_saturated_table_never_evicts_active_or_unreplenished_sessions(self):
        gate = self.gate(burst=1, refill_seconds=10, max_sessions=1)
        with gate.claim('run', 'secret'):
            with self.assertRaises(SubmissionBusy):
                with gate.claim('new', 'token'):
                    pass
        with self.assertRaises(SubmissionBusy):
            with gate.claim('new', 'token'):
                pass
        # Replenished inactive rows can be replaced without forgiving debt.
        self.now += 10
        with gate.claim('new', 'token'):
            pass
        self.assertEqual(len(gate.sessions), 1)

    def test_configuration_is_explicit_and_bounded(self):
        with patch.dict('os.environ', {'HERDR_TASK_SUBMIT_BURST': '3'}):
            self.assertEqual(SubmissionLimits.configured().burst, 3)
        for value in ('0', '10000', 'invalid'):
            with self.subTest(value=value), patch.dict('os.environ', {'HERDR_TASK_SUBMIT_BURST': value}):
                with self.assertRaises(ValueError):
                    SubmissionLimits.configured()
