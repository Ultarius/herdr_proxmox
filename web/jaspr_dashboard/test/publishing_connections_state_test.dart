import 'dart:convert';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/publishing_connections.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:test/test.dart';

// Isolate this helper's generation check from DashboardBloc.request's own
// transport guard, so deleting either guard cannot be masked by the other.
class MetadataConnection extends DashboardBloc {
  MetadataConnection(this.metadata)
    : super(
        client: MockClient(
          (_) async => http.Response('{"workspaces":[],"agents":[]}', 200),
        ),
      );
  final Future<dynamic> metadata;
  @override
  Future<dynamic> request(String path, [Map<String, dynamic>? body]) =>
      path == 'projects/connections' ? metadata : super.request(path, body);
}

void main() {
  late DashboardBloc connection;
  late Completer<http.Response> sources;
  var reads = 0;
  setUp(() async {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    sources = Completer<http.Response>();
    reads = 0;
    connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/projects/connections') {
          reads++;
          return sources.future;
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    connection.connect('token');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
  });
  tearDown(() async {
    if (!sources.isCompleted) sources.complete(http.Response('{}', 503));
    await connection.disconnect();
    connection.dispose();
  });

  test(
    'only usable PAT metadata is retained, never credential fields',
    () async {
      final future = publishingTokenConnections(connection);
      sources.complete(
        http.Response(
          jsonEncode({
            'connections': [
              {
                'id': 'a' * 32,
                'kind': 'token',
                'repository': 'owner/repo',
                'label': 'Saved PAT',
                'token': 'secret',
              },
              {
                'id': 'b' * 32,
                'kind': 'ssh',
                'repository': 'owner/repo',
                'label': 'SSH',
              },
              {'id': 'c' * 32, 'kind': 'github', 'repository': 'owner/repo'},
              {
                'id': '../untrusted',
                'kind': 'token',
                'repository': 'owner/repo',
              },
            ],
          }),
          200,
        ),
      );
      expect(await future, [
        {'id': 'a' * 32, 'repository': 'owner/repo', 'label': 'Saved PAT'},
      ]);
      expect(reads, 1);
    },
  );
  test('operators cannot load saved publishing credentials', () async {
    connection.operator.value = {'role': 'operator'};
    await expectLater(publishingTokenConnections(connection), throwsStateError);
    expect(reads, 0);
  });
  test('an account change invalidates metadata already in flight', () async {
    final metadata = Completer<dynamic>();
    final isolated = MetadataConnection(metadata.future);
    try {
      isolated.connect('first-account');
      await isolated.stream.firstWhere((_) => isolated.state.refreshed != null);
      final future = publishingTokenConnections(isolated);
      final assertion = expectLater(
        future,
        throwsA(
          isA<StateError>().having(
            (error) => error.message,
            'message',
            'Connection changed. Reopen GitHub publishing.',
          ),
        ),
      );
      await isolated.disconnect();
      isolated.connect('second-account');
      await isolated.stream.firstWhere((_) => isolated.state.refreshed != null);
      expect(isolated.state.connected, isTrue);
      expect(isolated.operator.value['role'], 'admin');
      metadata.complete({'connections': []});
      await assertion;
    } finally {
      if (!metadata.isCompleted) metadata.complete({'connections': []});
      await isolated.disconnect();
      isolated.dispose();
    }
  });
  test(
    'an unreadable listing is an error rather than a claimed empty list',
    () async {
      final future = publishingTokenConnections(connection);
      final assertion = expectLater(future, throwsStateError);
      sources.complete(http.Response('{}', 200));
      await assertion;
    },
  );
  test('shared source selection only replaces suggested allowlists', () {
    final selection = PublishingTokenSelection([
      {'id': 'a' * 32, 'repository': 'owner/repo', 'label': 'First'},
      {'id': 'b' * 32, 'repository': 'owner/other', 'label': 'Second'},
    ]);
    expect(selection.select('a' * 32, ''), 'owner/repo');
    expect(selection.select('b' * 32, 'owner/repo'), 'owner/other');
    expect(
      selection.select('a' * 32, 'custom/repository'),
      'custom/repository',
    );
    expect(selection.select('', 'owner/repo'), 'owner/repo');
    expect(selection.manual, isTrue);
    expect(selection.options.keys, ['', 'a' * 32, 'b' * 32]);
  });
}
