import 'package:flutter_test/flutter_test.dart';
import 'package:xterm/xterm.dart';
import 'package:herdr_dashboard/setup_links.dart';

void main() {
  test('wrapped OAuth URL retains query and strips rendered ANSI', () {
    final terminal = Terminal();
    terminal.resize(30, 20);
    const link =
        'https://example.com/oauth?state=123&code_challenge=abcdef&redirect_uri=https%3A%2F%2Fexample.com%2Fcallback';
    terminal.write('\x1b[34m$link\x1b[0m\r\n');
    expect(setupLinks(terminal.buffer.getText()).single.toString(), link);
  });
  test('unsafe schemes and credentials rejected; duplicate links removed', () {
    final links = setupLinks(
      'javascript:alert(1) file:///etc/passwd https://user:password@example.com/ https://example.com/ https://example.com/',
    );
    expect(links.map((uri) => uri.toString()), ['https://example.com/']);
  });
}
