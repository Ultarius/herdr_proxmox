import 'dart:convert';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/task_backup.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:test/test.dart';

void main() {
  late DashboardBloc connection;
  late Completer<http.Response> response;
  var exports = 0;
  var estimatedBytes = 1024;
  late Completer<void> started;
  setUp(() async {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    response = Completer<http.Response>();
    exports = 0;
    estimatedBytes = 1024;
    started = Completer<void>();
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (const {
          '/api/tasks/export/info',
          '/api/tasks/export/json/info',
        }.contains(request.url.path))
          return http.Response(
            jsonEncode({'estimated_bytes': estimatedBytes}),
            200,
          );
        if (const {
          '/api/tasks/export',
          '/api/tasks/export/json',
        }.contains(request.url.path)) {
          exports++;
          started.complete();
          expect(request.method, 'GET');
          expect(
            request.url.query,
            isEmpty,
            reason: 'Export ignores all task view filters',
          );
          return response.future;
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    connection.connect('admin');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
  });
  tearDown(() async {
    if (!response.isCompleted) response.complete(http.Response('{}', 503));
    await connection.disconnect();
    connection.dispose();
  });

  test('JSON export keeps server filename, readable bytes and MIME', () async {
    final future = fetchTaskBackup(connection, asJson: true);
    final bytes = utf8.encode('{"tables":{"tasks":{"rows":[]}}}');
    response.complete(
      http.Response.bytes(
        bytes,
        200,
        headers: {
          'content-disposition':
              'attachment; filename="herdr-tasks-20261024T120000Z.json"',
        },
      ),
    );
    final backup = await future;
    expect(backup.bytes, bytes);
    expect(backup.filename, 'herdr-tasks-20261024T120000Z.json');
    expect(backup.contentType, 'application/json');
  });

  test('large JSON uses its own native download endpoint', () async {
    estimatedBytes = taskBackupBrowserLimit + 1;
    await connection.disconnect();
    connection.connect('');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    final backup = await fetchTaskBackup(connection, asJson: true);
    expect(backup.downloadUri!.path, '/api/tasks/export/json');
    expect(exports, 0);
  });

  test('all task backup bytes are retained in a portable filename', () async {
    final future = fetchTaskBackup(connection);
    final bytes = utf8.encode('SQLite format 3\x00complete database');
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
    final backup = await future;
    expect(backup.bytes, bytes);
    expect(backup.filename, 'herdr-tasks-20261024T120000Z.sqlite3');
    expect(exports, 1);
  });

  test('operators cannot request a database export', () async {
    connection.operator.value = {'role': 'operator'};
    await expectLater(
      fetchTaskBackup(connection),
      throwsA(
        isA<StateError>().having(
          (error) => error.message,
          'message',
          'Only an administrator can export all tasks.',
        ),
      ),
    );
    expect(exports, 0);
  });

  test('role changes discard an export already in flight', () async {
    final future = fetchTaskBackup(connection);
    final assertion = expectLater(
      future,
      throwsA(
        isA<StateError>().having(
          (error) => error.message,
          'message',
          'Connection changed. Export the backup again.',
        ),
      ),
    );
    await started.future;
    connection.operator.value = {'role': 'operator'};
    response.complete(http.Response('database bytes', 200));
    await assertion;
  });

  test(
    'gateway errors are surfaced rather than downloaded as a backup',
    () async {
      final future = fetchTaskBackup(connection);
      final assertion = expectLater(
        future,
        throwsA(predicate((error) => '$error'.contains('Storage unavailable'))),
      );
      response.complete(http.Response('{"error":"Storage unavailable"}', 503));
      await assertion;
    },
  );

  test(
    'exports from a previous session are discarded after reconnect',
    () async {
      final future = fetchTaskBackup(connection);
      final assertion = expectLater(future, throwsStateError);
      await started.future;
      await connection.disconnect();
      connection.connect('next-admin');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      response.complete(http.Response('previous database', 200));
      await assertion;
    },
  );
  test(
    'large backups are refused during preflight, before making a copy',
    () async {
      estimatedBytes = taskBackupBrowserLimit + 1;
      await expectLater(
        fetchTaskBackup(connection),
        throwsA(
          isA<StateError>().having(
            (e) => e.message,
            'message',
            taskBackupLimitNotice,
          ),
        ),
      );
      expect(exports, 0);
    },
  );

  test('a missing or unsafe server filename is refused', () async {
    final pending = fetchTaskBackup(connection);
    final assertion = expectLater(
      pending,
      throwsA(
        isA<StateError>().having(
          (e) => e.message,
          'message',
          contains('filename was not supplied'),
        ),
      ),
    );
    response.complete(
      http.Response(
        'bytes',
        200,
        headers: {'content-disposition': 'attachment; filename="../../token"'},
      ),
    );
    await assertion;
  });
  test(
    'large backups in browser sessions use native streaming without buffered bytes',
    () async {
      estimatedBytes = taskBackupBrowserLimit + 1;
      connection.connect('');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      final backup = await fetchTaskBackup(connection);
      expect(backup.downloadUri, Uri.base.resolve('/api/tasks/export'));
      expect(backup.bytes, isEmpty);
      expect(
        backup.filename,
        isEmpty,
        reason: 'The browser honors the server filename directly',
      );
      expect(exports, 0);
    },
  );
}
