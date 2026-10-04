import 'package:web/web.dart' as web;

class LogWindow {
  final window = web.window.open('about:blank', '_blank');
  LogWindow() {
    if (window == null) throw Exception('Allow pop-ups to open the text log.');
    window!.opener = null;
    window!.document.body?.textContent = 'Opening saved terminal text…';
  }
  void open(String url) => window!.location.replace(url);
  void close() => window?.close();
}
