import 'dart:convert';
import 'package:test/test.dart';
import 'package:juice/juice.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/shell_bloc.dart';
import 'package:herdr_dashboard_state/organization_bloc.dart';

http.Response json(Object value) => http.Response(jsonEncode(value), 200);
Future<void> connect(DashboardBloc connection) async {
  final ready = connection.stream.firstWhere(
    (_) => connection.state.refreshed != null,
  );
  connection.connect('admin');
  await ready;
}

Map<String, dynamic> notice(int behind) => {
  'profile_id': 'agent',
  'name': 'Agent',
  'path': 'repo',
  'branch': 'work',
  'base': 'origin/main',
  'behind': behind,
};

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  test('organization live observations stop when their bloc closes', () async {
    var reads = 0;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/organizations/state') {
          reads++;
          return json({'organizations': [], 'profiles': [], 'jobs': []});
        }
        return json({'workspaces': [], 'agents': []});
      }),
    );
    await connect(connection);
    final bloc = OrganizationBloc(connection);
    try {
      bloc.startPolling();
      bloc.startPolling();
      final observed = bloc.stream.firstWhere((_) => bloc.state.loaded);
      connection.notificationRevision.value++;
      await observed;
      expect(reads, 1);
      await bloc.close();
      connection.notificationRevision.value++;
      await Future<void>.delayed(Duration.zero);
      expect(reads, 1);
    } finally {
      await bloc.close();
      await connection.close();
    }
  });
  test(
    'old shell directory cannot replace a newer organization write',
    () async {
      final started = Completer<void>(), reply = Completer<http.Response>();
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/organizations/directory') {
            if (!started.isCompleted) started.complete();
            return reply.future;
          }
          if (request.url.path == '/api/organizations/state')
            return json({
              'organizations': [
                {'id': 'new'},
              ],
              'profiles': [],
            });
          return json({'workspaces': [], 'agents': []});
        }),
      );
      await connect(connection);
      final bloc = ShellBloc(connection);
      try {
        final hydration = bloc.hydrateDirectory();
        await started.future;
        await connection.request('organizations/save', {'name': 'New'});
        await connection.request('organizations/state');
        reply.complete(
          json({
            'organizations': [
              {'id': 'old'},
            ],
            'profiles': [],
          }),
        );
        await hydration;
        expect(
          (connection.organizationDirectory.value['organizations'] as List)
              .single['id'],
          'new',
        );
      } finally {
        if (!reply.isCompleted)
          reply.complete(json({'organizations': [], 'profiles': []}));
        await bloc.close();
        await connection.close();
      }
    },
  );
  test(
    'shell owns quiet initial notices, checked time and directory hydration',
    () async {
      var behind = 1;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/integration')
            return json({
              'alerts': [notice(behind)],
              'checked': 'checked-$behind',
            });
          if (request.url.path == '/api/organizations/directory')
            return json({
              'organizations': [
                {'id': 'org'},
              ],
              'profiles': [],
            });
          return json({'workspaces': [], 'agents': []});
        }),
      );
      await connect(connection);
      final bloc = ShellBloc(connection);
      try {
        await bloc.hydrateDirectory();
        await bloc.refreshNotices(announce: false);
        expect(connection.organizationDirectory.value['loaded'], isTrue);
        expect(bloc.notices.single.behind, 1);
        expect(bloc.checked, 'checked-1');
        expect(bloc.announce, isFalse);
        behind = 3;
        await bloc.refreshNotices();
        expect(bloc.notices.single.behind, 3);
        expect(bloc.checked, 'checked-3');
        expect(bloc.announce, isTrue);
        await connection.disconnect();
        expect(bloc.notices, isEmpty);
        expect(bloc.checked, isNull);
      } finally {
        await bloc.close();
        await connection.close();
      }
    },
  );

  test(
    'shell asks only an idle authorized agent and owns command admission',
    () async {
      final started = Completer<void>(), reply = Completer<http.Response>();
      final posts = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/integration')
            return json({
              'alerts': [notice(1)],
            });
          if (request.url.path == '/api/organizations/directory')
            return json({
              'organizations': [
                {'id': 'org'},
              ],
              'profiles': [
                {'id': 'agent', 'organization_id': 'org'},
              ],
            });
          if (request.url.path == '/api/organizations/chat') {
            posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
            started.complete();
            return reply.future;
          }
          return json({
            'workspaces': [],
            'agents': [
              {'profile_id': 'agent', 'agent_status': 'idle'},
            ],
          });
        }),
      );
      await connect(connection);
      final bloc = ShellBloc(connection);
      try {
        await bloc.hydrateDirectory();
        await bloc.refreshNotices(announce: false);
        final selected = bloc.notices.single;
        final ask = bloc.ask(selected);
        await started.future;
        expect(bloc.asking, {'agent'});
        expect(await bloc.ask(selected), isFalse);
        expect(posts, hasLength(1));
        expect(posts.single['organization_id'], 'org');
        expect(posts.single['wait_seconds'], 1800);
        expect(posts.single['prompt'], contains('Do not reset'));
        reply.complete(json({}));
        expect(await ask, isTrue);
        expect(bloc.asking, isEmpty);
        connection.operator.value = {'role': 'viewer'};
        await expectLater(bloc.ask(selected), throwsStateError);
      } finally {
        if (!reply.isCompleted) reply.complete(json({}));
        await bloc.close();
        await connection.close();
      }
    },
  );
}
