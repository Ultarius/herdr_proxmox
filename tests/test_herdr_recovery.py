import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'web/gateway'))
import herdr_errors as protocol


class ProtocolTests(unittest.TestCase):
    def test_error_envelope_distinguishes_refusal_from_ambiguous_delivery(self):
        blocked = protocol.parse_error(json.dumps({'error': {'code': 'agent_blocked', 'message': 'waiting at dialog'}}), 'agent prompt')
        self.assertEqual(blocked.code, 'agent_blocked')
        self.assertEqual(blocked.delivery, 'none')
        # Nothing was sent, so a retry cannot duplicate input.
        self.assertTrue(protocol.may_auto_retry(blocked.delivery))
        timeout = protocol.parse_error(json.dumps({'code': 'timeout', 'message': 'timed out'}), 'agent prompt')
        self.assertEqual(timeout.delivery, 'unknown')
        self.assertFalse(protocol.may_auto_retry(timeout.delivery))
        plain = protocol.parse_error('Cannot reach Herdr', 'agent list')
        self.assertEqual(plain.code, 'herdr_error')
        self.assertIn('Cannot reach Herdr', str(plain))

    def test_delivery_mapping_for_prompt_failures(self):
        self.assertEqual(protocol.delivery_for(protocol.HerdrError('x', 'agent_blocked', 'agent prompt', 'none')), 'blocked')
        self.assertEqual(protocol.delivery_for(protocol.HerdrError('x', 'timeout', 'agent prompt', 'unknown')), 'unknown')
        self.assertEqual(protocol.delivery_for(ValueError('generic')), 'unknown')
        self.assertTrue(protocol.may_auto_retry('none'))
        self.assertFalse(protocol.may_auto_retry('blocked'))

    def test_read_only_operations_are_the_only_retryable_ones(self):
        self.assertTrue(protocol.is_read_only(('agent', 'list')))
        self.assertTrue(protocol.is_read_only(('agent', 'explain')))
        self.assertFalse(protocol.is_read_only(('agent', 'prompt')))
        self.assertFalse(protocol.is_read_only(('pane', 'close')))

    def test_resume_requires_structured_arguments_to_be_verified(self):
        verified = protocol.parse_resume({'resume': {'reference': 'sess-1', 'args': ['--resume', 'sess-1'], 'source': 'claude'}})
        self.assertEqual((verified['state'], verified['reference']), ('verified', 'sess-1'))
        reported = protocol.parse_resume({'resume': {'reference': 'sess-1'}})
        self.assertEqual(reported['state'], 'reported')
        self.assertIn('will not execute', reported['reason'])
        command = protocol.parse_resume({'resume': 'claude --resume sess-1'})
        self.assertEqual(command['state'], 'reported')
        self.assertNotIn('args', command)
        self.assertEqual(protocol.parse_resume({'resume': {'reference': 'x', 'args': [1]}})['state'], 'reported')
        self.assertEqual(protocol.parse_resume({})['state'], 'unavailable')
        self.assertEqual(protocol.parse_resume(None)['state'], 'unavailable')

    def test_completion_baseline_is_scoped_and_never_credits_earlier_turns(self):
        baseline = protocol.parse_completion({'completion_seq': 7, 'server_session': 's1'})
        self.assertEqual(baseline, {'seq': 7, 'scope': 's1'})
        self.assertIsNone(protocol.parse_completion({'completion_seq': '7'}))
        self.assertIsNone(protocol.parse_completion(None))
        self.assertTrue(protocol.completion_after_baseline(baseline, {'completion_seq': 8, 'server_session': 's1'}))
        self.assertFalse(protocol.completion_after_baseline(baseline, {'completion_seq': 7, 'server_session': 's1'}))
        self.assertFalse(protocol.completion_after_baseline(baseline, {'completion_seq': 6, 'server_session': 's1'}))
        self.assertIsNone(protocol.completion_after_baseline(baseline, {'completion_seq': 9, 'server_session': 's2'}))
        self.assertIsNone(protocol.completion_after_baseline(baseline, {'completion_seq': 9}))
        self.assertIsNone(protocol.completion_after_baseline(None, {'completion_seq': 9}))

    def test_preview_strips_ansi_and_bounds_output(self):
        text = 'x' * 2000 + '\n\x1b[31mAllow Bash?\x1b[0m\n'
        result = protocol.preview(text, limit=100)
        self.assertNotIn('\x1b', result)
        self.assertIn('Allow Bash?', result)
        self.assertLessEqual(len(result), 100)
    def test_handover_notes_are_normalized_bounded_and_never_fatal(self):
        note = protocol.parse_handover({'handover': {'state': 'half done', 'decisions': 'use A', 'questions': '', 'next_step': 'run tests', 'extra': 'ignored'}})
        self.assertEqual(set(note), {'state', 'decisions', 'next_step'})
        self.assertIn('State: half done', protocol.format_handover(note))
        self.assertIn('Next step: run tests', protocol.format_handover(note))
        self.assertIsNone(protocol.parse_handover({'handover': 'a string'}))
        self.assertIsNone(protocol.parse_handover({}))
        self.assertIsNone(protocol.parse_handover({'handover': {'state': '   '}}))
        long = protocol.parse_handover({'handover': {'state': 'x' * 5000}})
        self.assertLessEqual(len(long['state']), 1500)
        self.assertEqual(protocol.format_handover(None), '')


if __name__ == '__main__':
    unittest.main()
