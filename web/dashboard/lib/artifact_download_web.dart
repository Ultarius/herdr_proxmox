import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';
import 'package:web/web.dart' as web;

/// Browsers save a blob asynchronously after the click. Revoking the object URL
/// before that write completes fails or stalls the download (observed as a
/// transfer stuck at 100%), so keep it alive well past the save instead of
/// racing it. FileSaver.js uses 40 seconds; the caller is not blocked.
void _save(web.Blob blob, String filename) {
  final url = web.URL.createObjectURL(blob);
  final link = web.HTMLAnchorElement()
    ..href = url
    ..download = filename;
  web.document.body!.appendChild(link);
  link.click();
  link.remove();
  Timer(const Duration(seconds: 60), () => web.URL.revokeObjectURL(url));
}

Future<void> downloadBinaryArtifact(
  Uint8List content,
  String filename, {
  String contentType = 'application/gzip',
}) async {
  _save(
    web.Blob([content.toJS].toJS, web.BlobPropertyBag(type: contentType)),
    filename,
  );
}

Future<void> downloadArtifact(String content, String filename) async {
  _save(
    web.Blob(
      [content.toJS].toJS,
      web.BlobPropertyBag(
        type: filename.endsWith('.json')
            ? 'application/json;charset=utf-8'
            : 'text/markdown;charset=utf-8',
      ),
    ),
    filename,
  );
}
