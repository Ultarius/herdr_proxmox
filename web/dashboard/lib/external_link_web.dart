import 'package:web/web.dart' as web;

void openExternalLink(Uri uri) {
  if (!['https', 'http'].contains(uri.scheme) ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty)
    throw ArgumentError('Invalid setup link');
  web.window.open(uri.toString(), '_blank', 'noopener,noreferrer');
}

// Called synchronously from a user click; also works on many HTTP dashboards.
bool copySetupClipboard(String text) {
  final active = web.document.activeElement;
  final input = web.HTMLTextAreaElement()..value = text;
  input.style.position = 'fixed';
  input.style.opacity = '0';
  web.document.body?.appendChild(input);
  try {
    input.select();
    return web.document.execCommand('copy');
  } catch (_) {
    return false;
  } finally {
    input.remove();
    if (active is web.HTMLElement) active.focus();
  }
}
