import 'dart:convert';
import 'dart:io';

// Static preview of the compiled Jaspr shell and Flutter dashboard.
// The authenticated Herdr API runs in the Proxmox container.
Future<void> main() async {
  final root = Directory.fromUri(Platform.script.resolve('../web/public/'));
  if (!File('${root.path}/index.html').existsSync()) {
    stderr.writeln(
      'Build web assets first using scripts/build-web.ps1 or .sh.',
    );
    exitCode = 1;
    return;
  }
  stdout.writeln('Starting web preview');
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 8788);
  stdout.writeln('Web preview ready at http://127.0.0.1:8788');
  await for (final request in server) {
    try {
      if (request.uri.path.startsWith('/api/')) {
        request.response.statusCode = HttpStatus.serviceUnavailable;
        request.response.headers.contentType = ContentType.json;
        request.response.write(
          jsonEncode({
            'error':
                'This is a static web preview. Use the Proxmox SSH tunnel launch configuration for live Herdr data.',
          }),
        );
      } else if (request.method != 'GET' && request.method != 'HEAD') {
        request.response.statusCode = HttpStatus.methodNotAllowed;
      } else {
        final segments = request.uri.pathSegments;
        if (segments.any(
          (s) => s == '..' || s.contains('/') || s.contains('\\'),
        )) {
          request.response.statusCode = HttpStatus.notFound;
        } else {
          var path = '${root.path}/${segments.join('/')}';
          if (Directory(path).existsSync()) path = '$path/index.html';
          final file = File(path);
          if (!file.existsSync()) {
            request.response.statusCode = HttpStatus.notFound;
          } else {
            final extension = path.split('.').last;
            final type =
                {
                  'html': 'text/html; charset=utf-8',
                  'js': 'application/javascript',
                  'json': 'application/json',
                  'css': 'text/css',
                  'wasm': 'application/wasm',
                  'png': 'image/png',
                  'svg': 'image/svg+xml',
                  'ico': 'image/x-icon',
                  'woff2': 'font/woff2',
                  'ttf': 'font/ttf',
                }[extension] ??
                'application/octet-stream';
            request.response.headers.set('Content-Type', type);
            request.response.headers.set('Cache-Control', 'no-store');
            if (request.method == 'GET')
              await request.response.addStream(file.openRead());
          }
        }
      }
    } catch (error) {
      stderr.writeln('Preview request failed: $error');
    } finally {
      await request.response.close();
    }
  }
}
