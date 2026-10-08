/// Extract links from the output stream, before terminal line wrapping. Retain
/// incomplete chunks so neither a split URL nor a split ANSI sequence is copied.
class CliSetupLinks {
  String _output = '';
  List<String> urls = [];

  void clear() {
    _output = '';
    urls = [];
  }

  void add(String chunk) {
    _output += chunk;
    if (_output.length > 65536) {
      _output = _output.substring(_output.length - 65536);
    }
    final plain = _output
        .replaceAll(RegExp(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'), '')
        .replaceAll(RegExp(r'\x1b\[[0-?]*[ -/]*[@-~]'), '');
    final found = RegExp(r'''https?://[^\s\x00-\x1f<>"']+(?=\s)''')
        .allMatches(plain)
        .map((match) => match.group(0)!)
        .where((url) => Uri.tryParse(url)?.hasAuthority == true)
        .toSet()
        .toList();
    urls = found.reversed.take(3).toList();
  }

  String? get claudeState {
    for (final url in urls) {
      final uri = Uri.parse(url);
      if ((uri.host == 'claude.ai' || uri.host == 'claude.com') &&
          uri.path.endsWith('/oauth/authorize')) {
        return uri.queryParameters['state'];
      }
    }
    return null;
  }
}

/// The masked field submits one line. In particular, never send a pasted
/// newline as an early Enter or discard Claude's #state suffix.
String setupSubmission(String text, {required bool claude, String? state}) {
  final value = claude ? text.replaceAll(RegExp(r'\s+'), '') : text.trim();
  if (value.isEmpty) {
    throw const FormatException('Paste the authorization code first.');
  }
  if (RegExp(r'[\x00-\x1f\x7f]').hasMatch(value)) {
    throw const FormatException(
      'Paste a single code or API key, without control characters.',
    );
  }
  if (claude) {
    final parts = value.split('#');
    if (parts.length != 2 ||
        parts.any((part) => !RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(part))) {
      throw const FormatException(
        'Paste the full authorization code from Claude, including # and the text after it.',
      );
    }
    if (state != null && parts[1] != state) {
      throw const FormatException(
        'This code belongs to a different sign-in attempt. Open the current sign-in URL and copy a fresh code.',
      );
    }
  }
  return '$value\r';
}
