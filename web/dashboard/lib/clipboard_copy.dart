import 'package:flutter/services.dart';
import 'artifact_download.dart';

/// Copy text, or download it when the browser has no clipboard API.
///
/// A plain-HTTP dashboard — the LAN default — is not a secure context, so
/// `navigator.clipboard` is unavailable and Flutter's clipboard call fails.
/// Downloading preserves the intent instead of failing silently.
Future<String> copyTextOrDownload(
  String text,
  String filename, {
  String copied = 'Copied.',
  String downloaded = 'Clipboard unavailable over HTTP; downloaded instead.',
  Future<void> Function(String text)? copy,
  Future<void> Function(String content, String filename)? download,
}) async {
  try {
    await (copy ?? _copy)(text);
    return copied;
  } catch (_) {
    try {
      await (download ?? downloadArtifact)(text, filename);
      return downloaded;
    } catch (_) {
      return 'Copy and download failed. Select the text to copy it manually.';
    }
  }
}

Future<void> _copy(String text) => Clipboard.setData(ClipboardData(text: text));
