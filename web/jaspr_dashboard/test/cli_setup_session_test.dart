import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:juice/juice.dart';
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/cli_setup_session.dart';

Future<void> connect(DashboardBloc connection) async {
  final ready = connection.stream.firstWhere(
    (_) => connection.state.refreshed != null,
  );
  connection.connect('');
  await ready;
}

http.Response snapshot() => http.Response(
  '{"workspaces":[],"agents":[],"herdr_server":"running"}',
  200,
);

void main() {
  JuiceLoggerConfig.minLevel = Level.off;
  test(
    'logout closes an active setup terminal before revoking its session',
    () async {
      final calls = <String>[];
      late DashboardBloc connection;
      connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/cli-setup/start')
            return http.Response('{"id":"setup"}', 200);
          if (request.url.path == '/api/cli-setup/close') {
            expect(connection.state.connected, isTrue);
            expect(connection.disconnecting, isTrue);
            expect(jsonDecode(request.body), {'id': 'setup'});
            calls.add('close');
            return http.Response('{}', 200);
          }
          if (request.url.path == '/api/session/logout') calls.add('logout');
          return snapshot();
        }),
      );
      final session = CliSetupSession(connection);
      try {
        await connect(connection);
        expect((await session.start('codex'))?['id'], 'setup');
        await connection.disconnect();
        expect(calls, ['close', 'logout']);
        expect(connection.state.connected, isFalse);
        await session.close();
        expect(calls, [
          'close',
          'logout',
        ], reason: 'Close is idempotent across logout and disposal.');
      } finally {
        session.dispose();
        await connection.close();
      }
    },
  );

  for (final leavePage in [false, true]) {
    test(
      'logout reconciles a late setup start (page disposed: $leavePage)',
      () async {
        final starting = Completer<void>();
        final reply = Completer<http.Response>();
        final calls = <String>[];
        late DashboardBloc connection;
        connection = DashboardBloc(
          client: MockClient((request) async {
            if (request.url.path == '/api/cli-setup/start') {
              starting.complete();
              return reply.future;
            }
            if (request.url.path == '/api/cli-setup/close') {
              expect(connection.state.connected, isTrue);
              expect(jsonDecode(request.body), {'id': 'late-setup'});
              calls.add('close');
            }
            if (request.url.path == '/api/session/logout') calls.add('logout');
            return snapshot();
          }),
        );
        final session = CliSetupSession(connection);
        try {
          await connect(connection);
          final start = session.start('codex');
          await starting.future;
          if (leavePage) session.dispose();
          final logout = connection.disconnect();
          expect(connection.disconnecting, isTrue);
          expect(calls, isEmpty);
          expect(await session.start('codex'), isNull);
          reply.complete(http.Response('{"id":"late-setup"}', 200));
          expect(await start, isNull);
          await logout;
          expect(calls, ['close', 'logout']);
          expect(connection.state.connected, isFalse);
        } finally {
          if (!reply.isCompleted)
            reply.complete(http.Response('{"id":"late-setup"}', 200));
          await session.close();
          session.dispose();
          await connection.close();
        }
      },
    );
  }

  test(
    'a failed remote close does not prevent logout or duplicate the attempt',
    () async {
      var closes = 0, logouts = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/cli-setup/start')
            return http.Response('{"id":"setup"}', 200);
          if (request.url.path == '/api/cli-setup/close') {
            closes++;
            return http.Response('{"error":"Close unavailable"}', 503);
          }
          if (request.url.path == '/api/session/logout') logouts++;
          return snapshot();
        }),
      );
      final session = CliSetupSession(connection);
      try {
        await connect(connection);
        await session.start('codex');
        await connection.disconnect();
        await session.close();
        expect(closes, 1);
        expect(logouts, 1);
        expect(connection.state.connected, isFalse);
      } finally {
        session.dispose();
        await connection.close();
      }
    },
  );
}
