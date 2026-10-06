import 'dart:async';
import 'dart:js_interop';
import 'package:web/web.dart' as web;

Future<String?> readConfigurationFile() async {
  final input = web.HTMLInputElement()
    ..type = 'file'
    ..accept = '.json,application/json';
  input.style.display = 'none';
  final result = Completer<String?>();
  Timer? focusTimer;
  bool reading = false;
  void finish(String? text) {
    if (!result.isCompleted) result.complete(text);
  }

  void fail(Object error) {
    if (!result.isCompleted) result.completeError(error);
  }

  final focus = ((web.Event _) {
    // Older browsers omit cancel. Allow change to arrive first, and never cancel
    // an asynchronous file read that has already started.
    focusTimer?.cancel();
    focusTimer = Timer(const Duration(seconds: 1), () {
      if (!reading && (input.files?.length ?? 0) == 0) finish(null);
    });
  }).toJS;
  input.addEventListener('cancel', ((web.Event _) => finish(null)).toJS);
  input.addEventListener(
    'change',
    ((web.Event _) {
      if (result.isCompleted || reading) return;
      final file = input.files?.item(0);
      if (file == null) {
        finish(null);
        return;
      }
      reading = true;
      if (file.size > 2000000) {
        fail(StateError('Configuration exceeds 2 MB.'));
        return;
      }
      file.text().toDart.then((text) => finish(text.toDart), onError: fail);
    }).toJS,
  );
  web.window.addEventListener('focus', focus);
  web.document.body!.appendChild(input);
  try {
    input.click();
    return await result.future;
  } finally {
    focusTimer?.cancel();
    web.window.removeEventListener('focus', focus);
    input.remove();
  }
}
