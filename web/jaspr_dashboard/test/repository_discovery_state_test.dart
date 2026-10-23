import 'dart:async';
import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/repository_connections.dart';

void main() {
  late DashboardBloc connection;
  late RepositoryConnectionsController controller;
  late List<Map<String, dynamic>> requests;
  Completer<http.Response>? pending;
  bool fail = false;
  setUp(() async {
    requests = [];
    pending = null;
    fail = false;
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.method == 'POST') {
          final body = Map<String, dynamic>.from(jsonDecode(request.body));
          requests.add(body);
          if (pending != null) return pending!.future;
          if (fail)
            return http.Response(
              '{"error":"GitHub access was denied or rate limited."}',
              422,
            );
          return http.Response(
            jsonEncode({
              'repositories': [
                {'repository': 'owner/one'},
                if (body['page'] == 2) {'repository': 'owner/two'},
              ],
              'next_page': body['page'] == 1 ? 2 : null,
            }),
            200,
          );
        }
        return http.Response(
          jsonEncode(
            request.url.path.endsWith('/connections')
                ? {'connections': [], 'bindings': []}
                : {'workspaces': [], 'agents': []},
          ),
          200,
        );
      }),
    );
    connection.connect('admin');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    controller = RepositoryConnectionsController(
      connection,
      onUpdate: () {},
      onChanged: (_) {},
    );
    while (controller.refreshing) {
      await Future<void>.delayed(Duration.zero);
    }
  });
  tearDown(() async {
    controller.dispose();
    await connection.disconnect();
  });
  test(
    'pagination appends and deduplicates without creating connections',
    () async {
      await controller.discoverRepositories(' github_pat_fixture ');
      expect(requests.single, {
        'action': 'discover',
        'token': 'github_pat_fixture',
        'page': 1,
      });
      expect(controller.nextRepositoryPage, 2);
      expect(controller.message, contains('More repositories are available'));
      await controller.discoverRepositories('github_pat_fixture', more: true);
      expect(
        controller.discoveredRepositories.map((item) => item['repository']),
        ['owner/one', 'owner/two'],
      );
      expect(controller.nextRepositoryPage, isNull);
      expect(controller.connections, isEmpty);
    },
  );
  test('token edits invalidate an outstanding discovery response', () async {
    pending = Completer<http.Response>();
    final result = controller.discoverRepositories('github_pat_old');
    await Future<void>.delayed(Duration.zero);
    controller.resetRepositoryDiscovery();
    pending!.complete(
      http.Response(
        '{"repositories":[{"repository":"old/private"}],"next_page":2}',
        200,
      ),
    );
    await result;
    expect(controller.discoveredRepositories, isEmpty);
    expect(controller.nextRepositoryPage, isNull);
    expect(controller.busy, isFalse);
  });
  test('permission errors release busy state and allow a retry', () async {
    fail = true;
    await controller.discoverRepositories('github_pat_fixture');
    expect(controller.message, contains('denied'));
    expect(controller.busy, isFalse);
    fail = false;
    await controller.discoverRepositories('github_pat_fixture');
    expect(controller.discoveredRepositories, hasLength(1));
  });
  test('signing out discards late results and discovery state', () async {
    pending = Completer<http.Response>();
    final result = controller.discoverRepositories('github_pat_old');
    await Future<void>.delayed(Duration.zero);
    await connection.disconnect();
    pending!.complete(
      http.Response(
        '{"repositories":[{"repository":"old/private"}],"next_page":2}',
        200,
      ),
    );
    await result;
    expect(controller.discoveredRepositories, isEmpty);
    expect(controller.nextRepositoryPage, isNull);
    expect(controller.busy, isFalse);
  });
  test(
    'token reset clears stale errors and can preserve save-success feedback',
    () {
      controller.message = 'No repositories found';
      controller.resetRepositoryDiscovery();
      expect(controller.message, isNull);
      controller.message = 'Token connection saved';
      controller.resetRepositoryDiscovery(clearMessage: false);
      expect(controller.message, 'Token connection saved');
    },
  );
}
