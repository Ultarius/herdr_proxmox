import 'dart:js_interop';
import 'package:web/web.dart' as web;

Future<void> downloadArtifact(String content, String filename) async {
  final blob = web.Blob(
    [content.toJS].toJS,
    web.BlobPropertyBag(
      type: filename.endsWith('.json')
          ? 'application/json;charset=utf-8'
          : 'text/markdown;charset=utf-8',
    ),
  );
  final url = web.URL.createObjectURL(blob);
  final link = web.HTMLAnchorElement()
    ..href = url
    ..download = filename;
  web.document.body!.appendChild(link);
  link.click();
  link.remove();
  await Future<void>.delayed(const Duration(seconds: 1));
  web.URL.revokeObjectURL(url);
}
