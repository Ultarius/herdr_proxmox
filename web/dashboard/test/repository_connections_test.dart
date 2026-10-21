import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/repository_connections.dart';

void main() {
  test(
    'disconnected emissions do not repeatedly clear the controller and refresh disables immediately',
    () async {
      final pending = Completer<http.Response>();
      var updates = 0, cleared = 0;
      final connection = DashboardBloc(
        client: MockClient(
          (request) async => request.url.path.endsWith('/connections')
              ? pending.future
              : http.Response('{"workspaces":[],"agents":[]}', 200),
        ),
      );
      final controller = RepositoryConnectionsController(
        connection,
        onUpdate: () => updates++,
        onChanged: (_) => cleared++,
      );
      addTearDown(() async {
        controller.dispose();
        if (!pending.isCompleted) pending.complete(http.Response('{}', 200));
        await connection.disconnect();
        connection.dispose();
      });
      controller.sessionChanged();
      controller.sessionChanged();
      expect(updates, 0);
      expect(cleared, 0);
      connection.connect('admin');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      expect(controller.refreshing, isTrue);
      expect(updates, greaterThan(0));
      pending.complete(http.Response('{}', 200));
      await Future<void>.delayed(Duration.zero);
      expect(controller.refreshing, isFalse);
    },
  );

  test(
    'pending authentication cannot fetch connections using the default administrator role',
    () async {
      final session = Completer<http.Response>();
      var requests = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/session') return session.future;
          if (request.url.path.endsWith('/connections')) requests++;
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      final controller = RepositoryConnectionsController(
        connection,
        onUpdate: () {},
        onChanged: (_) {},
      );
      addTearDown(() async {
        controller.dispose();
        await connection.disconnect();
        connection.dispose();
      });
      final signingIn = connection.signIn('operator-token');
      await Future<void>.delayed(Duration.zero);
      expect(requests, 0);
      expect(controller.canManage, isFalse);
      session.complete(
        http.Response(
          '{"authenticated":true,"operator":"reader","role":"operator"}',
          200,
        ),
      );
      await signingIn;
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      await Future<void>.delayed(Duration.zero);
      expect(requests, 0);
      expect(controller.canManage, isFalse);
    },
  );

  test(
    'failed connection loading waits for explicit refresh, not every snapshot',
    () async {
      var reads = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/connections')) {
            reads++;
            return http.Response('{"error":"Temporarily unavailable"}', 503);
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      connection.connect('admin');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      final controller = RepositoryConnectionsController(
        connection,
        onUpdate: () {},
        onChanged: (_) {},
      );
      addTearDown(() async {
        controller.dispose();
        await connection.disconnect();
        connection.dispose();
      });
      await Future<void>.delayed(Duration.zero);
      expect(reads, 1);
      expect(controller.message, contains('Temporarily unavailable'));
      for (var i = 0; i < 3; i++) {
        await connection.send(DashboardCommand('refresh'));
        await Future<void>.delayed(Duration.zero);
      }
      expect(reads, 1);
      await controller.refresh();
      expect(reads, 2);
    },
  );

  test(
    'role changes clear connections and reject delayed administrative results',
    () async {
      final pending = Completer<http.Response>();
      final loaded = <List<Map<String, dynamic>>>[];
      var requests = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/connections')) {
            requests++;
            return pending.future;
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      connection.connect('admin');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      final controller = RepositoryConnectionsController(
        connection,
        onUpdate: () {},
        onChanged: loaded.add,
      );
      addTearDown(() async {
        controller.dispose();
        await connection.disconnect();
        connection.dispose();
      });
      await Future<void>.delayed(Duration.zero);
      expect(requests, 1);
      connection.operator.value = {'role': 'operator'};
      pending.complete(
        http.Response(
          jsonEncode({
            'connections': [
              {'id': 'old-admin-key'},
            ],
          }),
          200,
        ),
      );
      await Future<void>.delayed(Duration.zero);
      expect(controller.canManage, isFalse);
      expect(controller.connections, isEmpty);
      expect(loaded.expand((items) => items), isEmpty);
      await controller.refresh();
      await controller.action('create', {});
      expect(requests, 1);
    },
  );
}
