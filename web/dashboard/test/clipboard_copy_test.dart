import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/clipboard_copy.dart';

void main() {
  testWidgets('a stalled clipboard falls back instead of hanging', (
    tester,
  ) async {
    var downloaded = false;
    final result = copyTextOrDownload(
      'text',
      'file.txt',
      copy: (_) => Completer<void>().future,
      download: (_, __) async => downloaded = true,
    );
    await tester.pump(const Duration(seconds: 3));
    expect(await result, contains('downloaded instead'));
    expect(downloaded, isTrue);
  });

  test(
    'reports failure when both clipboard and download are unavailable',
    () async {
      final message = await copyTextOrDownload(
        'text',
        'file.txt',
        copy: (_) async => throw StateError('clipboard denied'),
        download: (_, __) async => throw StateError('download denied'),
      );
      expect(message, contains('Copy and download failed'));
    },
  );

  test('falls back to a download when the clipboard is unavailable', () async {
    final downloads = <List<String>>[];
    final message = await copyTextOrDownload(
      '# Action brief',
      'artifact.md',
      copied: 'Markdown copied.',
      downloaded:
          'Clipboard unavailable over HTTP; Markdown downloaded instead.',
      copy: (_) async => throw StateError('clipboard unavailable'),
      download: (content, filename) async => downloads.add([content, filename]),
    );
    expect(message, contains('downloaded instead'));
    expect(downloads, [
      ['# Action brief', 'artifact.md'],
    ]);
  });

  test('copies without downloading when the clipboard works', () async {
    final copied = <String>[];
    var downloaded = false;
    final message = await copyTextOrDownload(
      'text',
      'file.md',
      copied: 'Markdown copied.',
      copy: (text) async => copied.add(text),
      download: (content, filename) async => downloaded = true,
    );
    expect(message, 'Markdown copied.');
    expect(copied, ['text']);
    expect(downloaded, isFalse);
  });
}
