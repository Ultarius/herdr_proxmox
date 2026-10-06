import 'package:web/web.dart' as web;

String? readTheme() {
  try {
    return web.window.localStorage.getItem('herdr.theme');
  } catch (_) {
    return null;
  }
}

void writeTheme(String value) {
  // Appearance is optional; blocked browser storage must not break navigation.
  try {
    web.window.localStorage.setItem('herdr.theme', value);
  } catch (_) {}
}
