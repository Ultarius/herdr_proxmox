import 'package:flutter/services.dart';
import 'package:web/web.dart' as web;
import 'clipboard_available.dart';

Future<void> copySetupUrl(String url) async {
  // execCommand remains usable on LAN HTTP dashboards, where the modern
  // clipboard API is absent. Keep the fallback synchronous with the click.
  if (!clipboardAvailable()) {
    _legacyCopy(url);
    return;
  }
  try {
    await Clipboard.setData(ClipboardData(text: url));
  } catch (_) {
    _legacyCopy(url);
  }
}

void _legacyCopy(String text) {
  final focused = web.document.activeElement;
  final field = web.HTMLTextAreaElement()
    ..value = text
    ..readOnly = true;
  field.style
    ..position = 'fixed'
    ..left = '-9999px';
  web.document.body!.appendChild(field);
  try {
    field.select();
    if (!web.document.execCommand('copy')) {
      throw StateError('Browser denied clipboard access.');
    }
  } finally {
    field.remove();
    if (focused is web.HTMLElement) focused.focus();
  }
}

void openSetupUrl(String url) {
  final uri = Uri.parse(url);
  if (!['https', 'http'].contains(uri.scheme) || !uri.hasAuthority) return;
  final link = web.HTMLAnchorElement()
    ..href = url
    ..target = '_blank'
    ..rel = 'noopener noreferrer';
  web.document.body!.appendChild(link);
  link.click();
  link.remove();
}
