import 'package:juice/juice.dart';
import 'artifact_download.dart';
import 'clipboard_copy.dart';

Future<void> exportDiscussionArtifact(
  BuildContext context,
  Map<String, dynamic> job, {
  bool download = false,
}) async {
  String message;
  try {
    final content = job['result'] as String;
    final filename = 'discussion-artifact-${job['id']}.md';
    if (download) {
      await downloadArtifact(content, filename);
      message = 'Markdown download started.';
    } else {
      message = await copyTextOrDownload(
        content,
        filename,
        copied: 'Markdown copied.',
        downloaded: 'Clipboard unavailable over HTTP; Markdown downloaded instead.',
      );
    }
  } catch (_) {
    message = 'Export failed. Select the artifact text to copy it manually.';
  }
  if (context.mounted)
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
}
