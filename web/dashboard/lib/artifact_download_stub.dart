import 'dart:typed_data';

Future<void> downloadTaskBackupLink(Uri uri) async {
  throw UnsupportedError(
    'Task backup downloads are available in the web dashboard.',
  );
}

Future<void> downloadArtifact(String content, String filename) async {
  throw UnsupportedError(
    'Artifact download is available in the web dashboard.',
  );
}

Future<void> downloadBinaryArtifact(
  Uint8List content,
  String filename, {
  String contentType = 'application/gzip',
}) async {
  throw UnsupportedError(
    'Artifact download is available in the web dashboard.',
  );
}

Future<void> openGitHubCompare(Uri uri) async => throw UnsupportedError(
  'Open the displayed GitHub comparison URL in your browser.',
);
