import 'package:flutter/services.dart';
import 'package:juice/juice.dart';
import 'artifact_download.dart';

Future<void> exportDiscussionArtifact(
  BuildContext context,
  Map<String, dynamic> job, {
  bool download = false,
}) async {
  String message;
  try {
    final content = job['result'] as String;
    if (download) {
      await downloadArtifact(content, 'discussion-artifact-${job['id']}.md');
    } else {
      await Clipboard.setData(ClipboardData(text: content));
    }
    message = download ? 'Markdown download started.' : 'Markdown copied.';
  } catch (_) {
    message = 'Export failed. Select the artifact text to copy it manually.';
  }
  if (context.mounted)
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
}
