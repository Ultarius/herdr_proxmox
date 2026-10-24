import 'dart:convert';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:http/http.dart' as http;
import 'package:juice/juice.dart';
import 'package:test/test.dart';

class DownloadClient extends http.BaseClient {
  DownloadClient(this.response);
  final http.StreamedResponse response;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    if (request.url.path == '/api/tasks/export') return response;
    return http.StreamedResponse(
      Stream.value(utf8.encode('{"workspaces":[],"agents":[]}')),
      200,
    );
  }
}

void main() {
  setUpAll(() {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
  });
  Future<void> withResponse(
    http.StreamedResponse response,
    Future<void> Function(DashboardBloc) body,
  ) async {
    final connection = DashboardBloc(client: DownloadClient(response));
    connection.connect('admin');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    try {
      await body(connection);
    } finally {
      await connection.disconnect();
      connection.dispose();
    }
  }

  test('oversized Content-Length is refused before reading the body', () async {
    var cancelled = false;
    final stream = StreamController<List<int>>(
      onCancel: () {
        cancelled = true;
      },
    );
    try {
      await withResponse(
        http.StreamedResponse(stream.stream, 200, contentLength: 100),
        (connection) async {
          await expectLater(
            connection
                .download('tasks/export', maximumBytes: 4)
                .timeout(const Duration(seconds: 2)),
            throwsStateError,
          );
          expect(cancelled, isTrue);
        },
      );
    } finally {
      await stream.close();
    }
  });

  test(
    'chunked and falsely small responses are bounded and cancelled',
    () async {
      for (final declared in [null, 1]) {
        var cancelled = false;
        late StreamController<List<int>> stream;
        stream = StreamController<List<int>>(
          onListen: () {
            stream.add([1, 2, 3]);
            stream.add([4, 5, 6]);
            stream.close();
          },
          onCancel: () {
            cancelled = true;
          },
        );
        await withResponse(
          http.StreamedResponse(stream.stream, 200, contentLength: declared),
          (connection) async {
            await expectLater(
              connection.download('tasks/export', maximumBytes: 4),
              throwsStateError,
            );
            expect(cancelled, isTrue);
          },
        );
      }
    },
  );

  test(
    'a stalled transfer has its own timeout and cancels its stream',
    () async {
      var cancelled = false;
      final stream = StreamController<List<int>>(
        onCancel: () {
          cancelled = true;
        },
      );
      try {
        await withResponse(http.StreamedResponse(stream.stream, 200), (
          connection,
        ) async {
          await expectLater(
            connection.download(
              'tasks/export',
              maximumBytes: 4,
              timeout: const Duration(milliseconds: 30),
            ),
            throwsA(isA<TimeoutException>()),
          );
          expect(cancelled, isTrue);
        });
      } finally {
        await stream.close();
      }
    },
  );

  test('bounded gateway errors retain their actionable message', () async {
    await withResponse(
      http.StreamedResponse(
        Stream.value(utf8.encode('{"error":"Free backup space first"}')),
        503,
      ),
      (connection) async {
        await expectLater(
          connection.download('tasks/export', maximumBytes: 4),
          throwsA(
            isA<DashboardRequestException>().having(
              (e) => e.message,
              'message',
              'Free backup space first',
            ),
          ),
        );
      },
    );
  });
}
