@TestOn('browser')
library;

import 'dart:convert';
import 'dart:js_interop';
import 'package:jaspr/client.dart';
import 'package:herdr_jaspr_dashboard/pages/tasks_page.dart';
import 'package:herdr_jaspr_dashboard/services.dart';
import 'package:herdr_dashboard_state/task_backup.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:test/test.dart';
import 'package:web/web.dart' as web;

Future<void> until(bool Function() ready) async {
  final deadline = DateTime.now().add(const Duration(seconds: 5));
  while (!ready() && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
  expect(
    ready(),
    isTrue,
    reason: 'Task backup UI did not reach the expected state',
  );
}

void main() {
  late ClientAppBinding binding;
  late DashboardBloc connection;
  late web.HTMLDivElement root;
  late Completer<http.Response> response;
  late JSFunction capture;
  final downloads = <web.HTMLAnchorElement>[];
  var exports = 0;
  var estimatedBytes = 1024;
  web.HTMLButtonElement exportButton() {
    final buttons = root.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
      final button = buttons.item(i) as web.HTMLButtonElement;
      final label = button.textContent ?? '';
      if (label.contains(taskBackupLabel) || label.contains('Exporting tasks'))
        return button;
    }
    throw StateError('Task backup toolbar control is missing');
  }

  setUpAll(() {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    binding = ClientAppBinding();
  });
  setUp(() async {
    exports = 0;
    estimatedBytes = 1024;
    downloads.clear();
    response = Completer<http.Response>();
    root = web.HTMLDivElement()..id = 'task-backup-test';
    web.document.body!.appendChild(root);
    capture = ((web.Event event) {
      final target = event.target;
      if (target is web.HTMLAnchorElement &&
          (target.download.startsWith('herdr-tasks-') ||
              Uri.parse(target.href).path == '/api/tasks/export')) {
        event.preventDefault();
        downloads.add(target);
      }
    }).toJS;
    web.document.body!.addEventListener('click', capture);
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/tasks/export/info')
          return http.Response(
            jsonEncode({'estimated_bytes': estimatedBytes}),
            200,
          );
        if (request.url.path == '/api/tasks/export') {
          exports++;
          expect(request.url.query, isEmpty);
          return response.future;
        }
        return http.Response(
          request.url.path == '/api/tasks'
              ? '{"tasks":[],"github":{}}'
              : '{"workspaces":[],"agents":[]}',
          200,
        );
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('admin');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    binding.attachRootComponent(
      const TasksPage(),
      attachTo: '#task-backup-test',
    );
    await until(() => root.textContent!.contains(taskBackupLabel));
  });
  tearDown(() async {
    if (!response.isCompleted) response.complete(http.Response('{}', 503));
    web.document.body!.removeEventListener('click', capture);
    binding.detachRootComponent();
    await connection.disconnect();
    await BlocScope.reset();
    root.remove();
  });

  test('export downloads complete response bytes as a SQLite backup', () async {
    exportButton().click();
    await until(() => exports == 1 && exportButton().disabled);
    expect(root.textContent, contains('Exporting tasks'));
    exportButton().click();
    expect(exports, 1);
    expect(downloads, isEmpty);
    final bytes = utf8.encode('SQLite format 3\x00all task metadata');
    response.complete(
      http.Response.bytes(
        bytes,
        200,
        headers: {
          'content-disposition':
              'attachment; filename="herdr-tasks-20261024T120000Z.sqlite3"',
        },
      ),
    );
    await until(() => downloads.length == 1 && !exportButton().disabled);
    expect(downloads.single.download, matches(r'^herdr-tasks-.*\.sqlite3$'));
    expect(downloads.single.href, startsWith('blob:'));
    final blobResponse = await web.window
        .fetch(downloads.single.href.toJS)
        .toDart;
    final buffer = await blobResponse.arrayBuffer().toDart;
    expect(buffer.toDart.asUint8List(), bytes);
    expect(root.textContent, contains(taskBackupNotice));
  });

  test(
    'failed exports preserve the page and offer retry without downloading errors',
    () async {
      exportButton().click();
      await until(() => exports == 1);
      response.complete(
        http.Response('{"error":"Task storage is busy. Retry export."}', 503),
      );
      await until(
        () => root.textContent!.contains('Task storage is busy. Retry export.'),
      );
      expect(exportButton().disabled, isFalse);
      expect(root.textContent, contains('Tasks'));
      expect(downloads, isEmpty);
    },
  );

  test(
    'a role change prevents an in-flight backup from being downloaded',
    () async {
      exportButton().click();
      await until(() => exports == 1);
      connection.operator.value = {'role': 'operator'};
      response.complete(http.Response('private database bytes', 200));
      await until(
        () =>
            exportButton().disabled &&
            !root.textContent!.contains('Exporting tasks'),
      );
      await Future<void>.delayed(const Duration(milliseconds: 30));
      expect(exportButton().disabled, isTrue);
      expect(downloads, isEmpty);
    },
  );
  test(
    'large cookie-session backups use a same-origin native download link',
    () async {
      estimatedBytes = taskBackupBrowserLimit + 1;
      connection.connect('');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      await until(() => !exportButton().disabled);
      exportButton().click();
      await until(() => downloads.isNotEmpty);
      expect(
        Uri.parse(downloads.single.href),
        Uri.base.resolve('/api/tasks/export'),
      );
      expect(
        downloads.single.download,
        isEmpty,
        reason: 'Native downloads use Content-Disposition',
      );
      expect(
        exports,
        0,
        reason: 'Database bytes must not be read into JavaScript',
      );
      expect(root.textContent, contains(taskBackupStartedNotice));
    },
  );
}
