import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:test/test.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/ventures_bloc.dart';

Map<String, dynamic> snapshot({bool selected = false}) => {
  'organizations': [
    {'id': 'org', 'name': 'Product organization'},
  ],
  'ventures': [
    {
      'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      'organization_id': 'org',
      'name': 'Settlement Desk',
      'state': 'draft',
      'capital': 100000,
      'currency': 'JPY',
      'last_tick': null,
    },
  ],
  'groups': [],
  'projects_root': '/projects',
  'limits': {'max_active_ventures': 3},
  if (selected)
    'selected': {
      'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      'organization_id': 'org',
      'name': 'Settlement Desk',
      'state': 'draft',
      'capital': 100000,
      'currency': 'JPY',
      'notebook': 'ventures/settlement',
      'configuration': {
        'sources': {'feeds': <String>[], 'tags': <String>[], 'keywords': <String>[]},
        'market': {'currency': 'JPY'},
        'simulation': {'architecture': 'api'},
        'outreach': {'language': 'ja'},
      },
    },
  'ticks': const <Map<String, dynamic>>[],
  'outreach': const <Map<String, dynamic>>[],
  'feeds': const <Map<String, dynamic>>[],
};

void main() {
  late DashboardBloc connection;
  late VenturesBloc bloc;
  late List<Map<String, dynamic>> requests;
  late List<String> paths;
  bool fail = false;

  setUp(() async {
    Logger.level = Level.off;
    JuiceLoggerConfig.minLevel = Level.off;
    requests = [];
    paths = [];
    fail = false;
    connection = DashboardBloc(
      client: MockClient((request) async {
        paths.add(request.url.toString());
        if (request.url.path == '/api/ventures') {
          if (request.method == 'POST') {
            final body = Map<String, dynamic>.from(jsonDecode(request.body));
            requests.add(body);
            if (fail) return http.Response('{"error":"Venture refused."}', 400);
            return http.Response(
              jsonEncode({'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'mode': body['mode']}),
              200,
            );
          }
          return http.Response(
            jsonEncode(snapshot(selected: request.url.queryParameters.containsKey('venture'))),
            200,
          );
        }
        return http.Response(jsonEncode({'workspaces': [], 'agents': []}), 200);
      }),
    );
    connection.connect('admin');
    await connection.stream.firstWhere((_) => connection.state.refreshed != null);
    bloc = VenturesBloc(connection);
    await bloc.refresh();
  });

  tearDown(() async {
    await bloc.close();
    await connection.disconnect();
  });

  test('snapshot loads and selecting a venture requests its history', () async {
    expect(bloc.ventures.single['name'], 'Settlement Desk');
    await bloc.select('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    expect(bloc.selected!['name'], 'Settlement Desk');
    expect(paths.any((path) => path.contains('venture=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')), isTrue);
  });

  test('run sends one admin action and refreshes the snapshot', () async {
    await bloc.submit('run', {'organization_id': 'org'}, ventureId: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    expect(requests.single['mode'], 'run');
    expect(requests.single['organization_id'], 'org');
    expect(requests.single['venture_id'], 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
    expect(requests.single['request_id'], isNotEmpty);
  });

  test('a refused action surfaces the server error without a stale pending state', () async {
    fail = true;
    await expectLater(
      bloc.submit('create', {'organization_id': 'org', 'name': 'Refused'}),
      throwsA(isA<DashboardRequestException>()),
    );
    expect(bloc.state.error, contains('Venture refused'));
  });
}
