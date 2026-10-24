import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:herdr_dashboard/tasks_page.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/task_backup.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'helpers/dashboard_connection.dart';

class BackupFixture {
  Completer<http.Response> pending = Completer<http.Response>();
  int exports = 0, estimatedBytes = 1024;
  bool failSave = false;
  final saved = <TaskBackup>[];
  late final DashboardBloc connection = DashboardBloc(
    client: MockClient((request) async {
      if (request.url.path == '/api/tasks/export/info')
        return http.Response(
          jsonEncode({'estimated_bytes': estimatedBytes}),
          200,
        );
      if (request.url.path == '/api/tasks/export') {
        exports++;
        return pending.future;
      }
      if (request.url.path == '/api/tasks')
        return http.Response('{"tasks":[],"github":{}}', 200);
      if (request.url.path == '/api/projects/connections')
        return http.Response('{"connections":[]}', 200);
      return http.Response('{"workspaces":[],"agents":[]}', 200);
    }),
  );
  Future<void> save(TaskBackup file) async {
    if (failSave) throw StateError('Internal private browser failure');
    saved.add(file);
  }

  void finish() => pending.complete(
    http.Response.bytes(
      utf8.encode('SQLite format 3\x00all metadata'),
      200,
      headers: {
        'content-disposition':
            'attachment; filename="herdr-tasks-20261024T120000Z.sqlite3"',
      },
    ),
  );
}

void main() {
  Future<void> withBackup(
    WidgetTester tester,
    Future<void> Function(BackupFixture) body,
  ) async {
    final fixture = BackupFixture();
    await withDashboardConnection(tester, fixture.connection, () async {
      try {
        await tester.pumpWidget(
          MaterialApp(
            home: Scaffold(body: TasksPage(saveTaskBackup: fixture.save)),
          ),
        );
        await pumpUntil(
          tester,
          () => find.text(taskBackupLabel).evaluate().isNotEmpty,
        );
        await body(fixture);
      } finally {
        if (!fixture.pending.isCompleted)
          fixture.pending.complete(http.Response('{}', 503));
      }
    });
  }

  OutlinedButton action(WidgetTester tester, String label) =>
      tester.widget<OutlinedButton>(find.widgetWithText(OutlinedButton, label));
  Future<void> start(WidgetTester tester, BackupFixture fixture) async {
    await clickText(tester, taskBackupLabel);
    await pumpUntil(tester, () => fixture.exports == 1);
  }

  testWidgets(
    'successful export saves exact bytes and server filename and offers dismissal',
    (tester) async {
      await withBackup(tester, (f) async {
        await start(tester, f);
        expect(action(tester, 'Exporting tasks…').onPressed, isNull);
        expect(action(tester, 'GitHub publishing').onPressed, isNull);
        f.finish();
        await pumpUntil(
          tester,
          () => find.text(taskBackupNotice).evaluate().isNotEmpty,
        );
        expect(f.saved.single.filename, 'herdr-tasks-20261024T120000Z.sqlite3');
        expect(
          f.saved.single.bytes,
          utf8.encode('SQLite format 3\x00all metadata'),
        );
        expect(action(tester, 'GitHub publishing').onPressed, isNotNull);
        await clickText(tester, 'Dismiss backup message');
        expect(find.text(taskBackupNotice), findsNothing);
      });
    },
  );

  testWidgets('gateway failures permit retry and can be dismissed', (
    tester,
  ) async {
    await withBackup(tester, (f) async {
      await start(tester, f);
      f.pending.complete(
        http.Response('{"error":"Task storage is busy. Retry export."}', 503),
      );
      await pumpUntil(
        tester,
        () => find
            .text('Task storage is busy. Retry export.')
            .evaluate()
            .isNotEmpty,
      );
      expect(action(tester, taskBackupLabel).onPressed, isNotNull);
      expect(f.saved, isEmpty);
      await clickText(tester, 'Dismiss backup message');
      expect(find.text('Task storage is busy. Retry export.'), findsNothing);
    });
  });

  testWidgets('a role change discards an in-flight export', (tester) async {
    await withBackup(tester, (f) async {
      await start(tester, f);
      f.connection.operator.value = {'role': 'operator'};
      f.finish();
      await tester.pump(const Duration(milliseconds: 100));
      expect(f.saved, isEmpty);
      expect(action(tester, taskBackupLabel).onPressed, isNull);
      expect(find.text(taskBackupNotice), findsNothing);
    });
  });

  testWidgets('an account change discards old export results', (tester) async {
    await withBackup(tester, (f) async {
      await start(tester, f);
      await f.connection.disconnect();
      f.connection.connect('next-admin');
      await tester.pump(const Duration(milliseconds: 100));
      f.finish();
      await tester.pump(const Duration(milliseconds: 100));
      expect(f.saved, isEmpty);
      expect(find.text(taskBackupNotice), findsNothing);
    });
  });

  testWidgets('a disposed task page cannot save a late response', (
    tester,
  ) async {
    await withBackup(tester, (f) async {
      await start(tester, f);
      await tester.pumpWidget(const SizedBox.shrink());
      f.finish();
      await tester.pump(const Duration(milliseconds: 100));
      expect(f.saved, isEmpty);
      expect(tester.takeException(), isNull);
    });
  });

  testWidgets(
    'save failures show friendly text and a new export clears the error',
    (tester) async {
      await withBackup(tester, (f) async {
        f.failSave = true;
        await start(tester, f);
        f.finish();
        await pumpUntil(
          tester,
          () => find
              .textContaining('Could not save the task backup.')
              .evaluate()
              .isNotEmpty,
        );
        expect(
          find.textContaining('Internal private browser failure'),
          findsNothing,
        );
        f.failSave = false;
        f.pending = Completer<http.Response>();
        await clickText(tester, taskBackupLabel);
        await pumpUntil(tester, () => f.exports == 2);
        expect(
          find.textContaining('Could not save the task backup.'),
          findsNothing,
        );
        f.finish();
        await pumpUntil(tester, () => f.saved.isNotEmpty);
        expect(find.text(taskBackupNotice), findsOneWidget);
      });
    },
  );

  testWidgets(
    'browser size preflight rejects large exports without starting a copy',
    (tester) async {
      await withBackup(tester, (f) async {
        f.estimatedBytes = taskBackupBrowserLimit + 1;
        await clickText(tester, taskBackupLabel);
        await pumpUntil(
          tester,
          () => find.text(taskBackupLimitNotice).evaluate().isNotEmpty,
        );
        expect(f.exports, 0);
        expect(f.saved, isEmpty);
      });
    },
  );

  testWidgets('publishing setup and disconnected sessions disable export', (
    tester,
  ) async {
    await withBackup(tester, (f) async {
      await clickText(tester, 'GitHub publishing');
      await pumpUntil(
        tester,
        () => find.byType(AlertDialog).evaluate().isNotEmpty,
      );
      final underlying = find.widgetWithText(
        OutlinedButton,
        taskBackupLabel,
        skipOffstage: false,
      );
      expect(tester.widget<OutlinedButton>(underlying).onPressed, isNull);
      await clickText(tester, 'Cancel');
      await tester.pump(const Duration(milliseconds: 400));
      await f.connection.disconnect();
      await tester.pump();
      expect(action(tester, taskBackupLabel).onPressed, isNull);
      expect(f.exports, 0);
    });
  });
  testWidgets('browser sessions hand large backups to the native downloader', (
    tester,
  ) async {
    await withBackup(tester, (f) async {
      f.estimatedBytes = taskBackupBrowserLimit + 1;
      f.connection.connect('');
      await tester.pump(const Duration(milliseconds: 100));
      await clickText(tester, taskBackupLabel);
      await pumpUntil(tester, () => f.saved.isNotEmpty);
      expect(f.exports, 0);
      expect(f.saved.single.downloadUri?.path, '/api/tasks/export');
      expect(f.saved.single.bytes, isEmpty);
      expect(find.text(taskBackupStartedNotice), findsOneWidget);
    });
  });
}
