"""Folder access summaries are observations, not runtime permission decisions."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web/gateway'))
from folder_access import folder_access_request


def prompt(paths=('/home/herdr/.cache/*', '/tmp/opencode/client-target/*')):
    return ('△ Permission required\n← Access external directory ~/.cache\n\nPatterns\n\n'
            + '\n'.join('- ' + path for path in paths)
            + '\n\nAllow once   Allow always   Reject   ctrl+f fullscreen\n')


class FolderAccessTests(unittest.TestCase):
    def test_screenshot_patterns_are_explicit_and_never_expanded(self):
        parsed = folder_access_request(prompt(), 'blocked')
        self.assertEqual(parsed, dict(paths=['/home/herdr/.cache/*', '/tmp/opencode/client-target/*'],
                                      truncated=False, source='visible_terminal'))

    def test_ansi_boxed_prompt_and_hyperlinks_are_plain_text(self):
        output = '\n'.join('│ ' + line + ' │' for line in prompt().splitlines())
        output = '\x1b[33m' + output + '\x1b[0m'
        output = output.replace('/home/herdr', '\x1b]8;;https://example.com\x07/home/herdr\x1b]8;;\x07')
        self.assertEqual(folder_access_request(output, 'blocked')['paths'][0], '/home/herdr/.cache/*')

    def test_escape_sequences_are_removed_before_the_history_boundary(self):
        # Truncating first would split this hyperlink and expose its contents.
        output = '\x1b]8;;https://example.com/' + 'x' * 21000 + '\x07' + prompt() + '\x1b]8;;\x07'
        self.assertEqual(folder_access_request(output, 'blocked')['paths'],
                         ['/home/herdr/.cache/*', '/tmp/opencode/client-target/*'])

    def test_only_last_permission_panel_is_considered(self):
        parsed = folder_access_request(prompt() + prompt(('/tmp/new/*',)), 'blocked')
        self.assertEqual(parsed['paths'], ['/tmp/new/*'])
        self.assertIsNone(folder_access_request(prompt() + '\n△ Permission required\nRun command?\n', 'blocked'))

    def test_resumed_agents_never_show_retained_prompts(self):
        for status in ('idle', 'working', 'done', 'unknown', None):
            self.assertIsNone(folder_access_request(prompt(), status))

    def test_command_paths_and_unrecognized_questions_are_not_folder_requests(self):
        for output in ('mkdir -p /tmp/pinned', '$ echo "Permission required"\n' + prompt().split('\n', 1)[1],
                       'Permission required\nRun command?\nAllow once Allow always Reject',
                       prompt() + '\n$ cargo test\n', prompt() + '\nWhich option do you prefer?\n'):
            self.assertIsNone(folder_access_request(output, 'blocked'), output)

    def test_partial_or_wrapped_prompt_falls_back_to_console(self):
        for output in (prompt().split('Allow once')[0], prompt().replace('Patterns', 'Other'),
                       prompt().replace('/tmp/opencode/client-target/*', '/tmp/opencode/\nclient-target/*'),
                       prompt().replace('- /tmp/opencode/client-target/*', '- relative/path')):
            self.assertIsNone(folder_access_request(output, 'blocked'), output)

    def test_bounded_patterns_and_deduplication(self):
        parsed = folder_access_request(prompt(tuple('/tmp/%d/*' % i for i in range(30))), 'blocked')
        self.assertEqual(len(parsed['paths']), 20)
        self.assertTrue(parsed['truncated'])
        parsed = folder_access_request(prompt(('/tmp/x', '/tmp/x', '/' + 'a' * 1001)), 'blocked')
        self.assertEqual(parsed['paths'], ['/tmp/x'])
        self.assertTrue(parsed['truncated'])

    def test_bounded_history_and_paths_with_spaces(self):
        self.assertIsNone(folder_access_request(prompt() + '\n' * 21000, 'blocked'))
        self.assertEqual(folder_access_request(prompt(('~/reference docs/**',)), 'blocked')['paths'],
                         ['~/reference docs/**'])
        self.assertEqual(folder_access_request(prompt(('/tmp/literal|',)), 'blocked')['paths'],
                         ['/tmp/literal|'])


if __name__ == '__main__':
    unittest.main()
