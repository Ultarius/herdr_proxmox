/// Extract HTTP(S) links from rendered logical terminal lines, not ANSI output.
List<Uri> setupLinks(String text) {
  final links = <Uri>[];
  for (final match in RegExp(
    r"https?://[^\s<>\x00-\x1f]+",
    caseSensitive: false,
  ).allMatches(text)) {
    final value = match.group(0)!.replaceFirst(RegExp(r'[.,;]+$'), '');
    final uri = Uri.tryParse(value);
    if (uri != null &&
        ['http', 'https'].contains(uri.scheme.toLowerCase()) &&
        uri.host.isNotEmpty &&
        uri.userInfo.isEmpty &&
        !links.contains(uri))
      links.add(uri);
    if (links.length >= 10) break;
  }
  return links;
}
