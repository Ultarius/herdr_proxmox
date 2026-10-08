import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/cli_setup_auth.dart';

void main() {
  const url =
      'https://claude.com/cai/oauth/authorize?code=true&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&state=test-state';

  test('links survive every output chunk boundary and ANSI color sequence', () {
    const output = 'Visit: \x1b[34m$url\x1b[0m\r\nPaste code here > ';
    for (var split = 0; split <= output.length; split++) {
      final links = CliSetupLinks();
      links.add(output.substring(0, split));
      links.add(output.substring(split));
      expect(links.urls, [url], reason: 'split at $split');
      expect(links.claudeState, 'test-state');
    }
  });

  test(
    'incomplete URLs are withheld; latest links win and clear removes state',
    () {
      final links = CliSetupLinks();
      links.add(url);
      expect(links.urls, isEmpty);
      links.add('\n$url\n');
      expect(links.urls, [url]);
      links.add('https://claude.ai/oauth/authorize?state=new-state\n');
      expect(links.claudeState, 'new-state');
      links.clear();
      expect(links.urls, isEmpty);
      expect(links.claudeState, isNull);
    },
  );

  test('OSC titles and non-web links are not exposed', () {
    final links = CliSetupLinks();
    links.add('\x1b]0;https://hidden.example/ \x07javascript:alert(1)\n');
    expect(links.urls, isEmpty);
  });

  test('Claude code retains #state and submits exactly one Enter', () {
    expect(
      setupSubmission(
        '  one-time-\ncode#test-state\r\n',
        claude: true,
        state: 'test-state',
      ),
      'one-time-code#test-state\r',
    );
  });

  test('missing or stale Claude state and control sequences are rejected', () {
    for (final value in [
      '',
      'code',
      'code#',
      '#state',
      'code#old-state',
      'code\x1b#test-state',
      'https://example.com/?code=code#test-state',
    ]) {
      expect(
        () => setupSubmission(value, claude: true, state: 'test-state'),
        throwsFormatException,
      );
    }
  });

  test('other CLI input trims outer whitespace without rewriting the key', () {
    expect(setupSubmission(' key+/=#value\n', claude: false), 'key+/=#value\r');
    expect(
      () => setupSubmission('first\nsecond', claude: false),
      throwsFormatException,
    );
    expect(() => setupSubmission('  ', claude: false), throwsFormatException);
  });
}
