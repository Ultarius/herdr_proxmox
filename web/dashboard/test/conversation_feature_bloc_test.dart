import 'dart:async';
import 'dart:convert';
import 'package:juice/juice.dart' show JuiceLoggerConfig, Logger, Level;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/agent_console_bloc.dart';
import 'package:herdr_dashboard_state/cli_setup_bloc.dart';
import 'package:herdr_dashboard_state/group_bloc.dart';

Future<void> connected(DashboardBloc connection) async {
  final ready = connection.stream.firstWhere(
    (_) => connection.state.refreshed != null,
  );
  connection.connect('admin');
  await ready;
}

http.Response json(Object value) => http.Response(jsonEncode(value), 200);
Map<String, dynamic> get snapshot => {'workspaces': [], 'agents': []};

class TrackedConsoleConnection extends DashboardBloc {
  TrackedConsoleConnection({required super.client});
  final firstReadSettled = Completer<void>();
  int _reads = 0;
  @override
  Future<dynamic> requestRead(
    String path, {
    required bool Function() isCurrent,
  }) async {
    final first = ++_reads == 1;
    try {
      return await super.requestRead(path, isCurrent: isCurrent);
    } finally {
      if (first) firstReadSettled.complete();
    }
  }
}

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  test(
    'console owns replay identity and clears it only after input acceptance',
    () async {
      var inputAttempts = 0;
      final ids = <String>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          final body = request.body.isEmpty
              ? <String, dynamic>{}
              : jsonDecode(request.body);
          if (request.url.path == '/api/organizations/state')
            return json({
              'profiles': [
                {'id': 'agent', 'organization_id': 'org'},
              ],
              'jobs': [],
            });
          if (request.url.path == '/api/organizations/inspect')
            return json({'status': 'blocked', 'console_token': 'reviewed'});
          if (request.url.path == '/api/organizations/input') {
            ids.add(body['request_id'] as String);
            if (++inputAttempts == 1)
              return http.Response('{"error":"response lost"}', 503);
            return json({'id': 'input-job'});
          }
          return json(snapshot);
        }),
      );
      await connected(connection);
      final bloc = AgentConsoleBloc(connection, 'agent');
      try {
        await bloc.stream.firstWhere((_) => bloc.state.inspection != null);
        await bloc.sendKey('enter');
        expect(bloc.state.pendingInput, isNotNull);
        expect(bloc.state.responseError, contains('response lost'));
        await bloc.sendKey('esc');
        expect(ids, hasLength(1));
        await bloc.retryResponse();
        expect(ids, [ids.first, ids.first]);
        expect(bloc.state.pendingInput, isNull);
        expect(bloc.state.inputJob, 'input-job');
        await bloc.sendKey('enter');
        expect(
          ids,
          hasLength(2),
          reason:
              'A queued response must settle before another key is admitted.',
        );
      } finally {
        await bloc.close();
        await connection.close();
      }
    },
  );

  test(
    'group owns history recovery without losing a failed command retry',
    () async {
      var failHistory = false, failCommand = true;
      final ids = <String>[];
      final history = {
        'group': {'id': 'group', 'organization_id': 'org'},
        'profiles': [],
        'jobs': [],
        'member_states': {},
      };
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/organizations/history')
            return failHistory
                ? http.Response('{"error":"history unavailable"}', 503)
                : json(history);
          if (request.url.path == '/api/organizations/discuss') {
            ids.add(jsonDecode(request.body)['request_id'] as String);
            return failCommand
                ? http.Response('{"error":"command uncertain"}', 503)
                : json({});
          }
          return json(snapshot);
        }),
      );
      await connected(connection);
      final bloc = GroupBloc(connection, 'group');
      try {
        await bloc.stream.firstWhere((_) => bloc.state.loaded);
        await bloc.submit('discuss', {'message': 'Reviewed prompt'});
        expect(bloc.state.pending, isNotNull);
        failHistory = true;
        await bloc.refresh();
        expect(bloc.state.readError, contains('history unavailable'));
        failHistory = false;
        await bloc.refresh();
        expect(bloc.state.readError, isNull);
        expect(bloc.state.error, contains('command uncertain'));
        failCommand = false;
        final result = await bloc.submit('discuss', const {}, retry: true);
        expect(result.succeeded, isTrue);
        expect(ids, [ids.first, ids.first]);
        expect(bloc.state.pending, isNull);
      } finally {
        await bloc.close();
        await connection.close();
      }
    },
  );

  test(
    'CLI owns serialized input and invalidates queued keys when stopped',
    () async {
      final firstStarted = Completer<void>(),
          firstReply = Completer<http.Response>();
      final inputs = <String>[];
      var closes = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          switch (request.url.path) {
            case '/api/cli-setup':
              return json({'clis': []});
            case '/api/cli-setup/start':
              return json({'id': 'setup'});
            case '/api/cli-setup/poll':
              return json({'output': 'prompt', 'cursor': 1, 'running': true});
            case '/api/cli-setup/close':
              closes++;
              return json({});
            case '/api/cli-setup/input':
              inputs.add(jsonDecode(request.body)['data'] as String);
              firstStarted.complete();
              return firstReply.future;
            default:
              return json(snapshot);
          }
        }),
      );
      await connected(connection);
      final bloc = CliSetupBloc(connection);
      try {
        await bloc.openSession('codex');
        expect(bloc.state.output, 'prompt');
        final first = bloc.input('first');
        await firstStarted.future;
        final queued = bloc.input('queued');
        await bloc.stop();
        firstReply.complete(json({}));
        await Future.wait([first, queued]);
        expect(inputs, ['first']);
        expect(closes, 1);
        expect(bloc.state.session, isNull);
        expect(bloc.state.output, isEmpty);
      } finally {
        if (!firstReply.isCompleted) firstReply.complete(json({}));
        await bloc.close();
        await connection.close();
      }
    },
  );

  test('console ignores an admin inspection after a role change', () async {
    final started = Completer<void>(), reply = Completer<http.Response>();
    var reads = 0;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/organizations/state') {
          return json({
            'profiles': [
              {'id': 'agent', 'organization_id': 'org'},
            ],
            'jobs': [],
          });
        }
        if (request.url.path == '/api/organizations/inspect') {
          if (++reads == 1) {
            started.complete();
            return reply.future;
          }
          return json({'status': 'idle', 'output': 'viewer output'});
        }
        return json(snapshot);
      }),
    );
    await connected(connection);
    final bloc = AgentConsoleBloc(connection, 'agent');
    try {
      await started.future;
      final refreshed = bloc.stream.firstWhere(
        (_) => bloc.state.inspection?['output'] == 'viewer output',
      );
      connection.operator.value = {'name': 'viewer', 'role': 'viewer'};
      await refreshed;
      reply.complete(
        json({
          'status': 'blocked',
          'output': 'admin output',
          'console_token': 'secret',
        }),
      );
      await bloc.refresh();
      expect(bloc.state.inspection?['output'], 'viewer output');
      expect(bloc.state.inspection?['console_token'], isNull);
    } finally {
      if (!reply.isCompleted) reply.complete(json({}));
      await bloc.close();
      await connection.close();
    }
  });

  test('group reloads for a new role without adopting old history', () async {
    final started = Completer<void>(), reply = Completer<http.Response>();
    var reads = 0;
    Map<String, dynamic> history(String role) => {
      'group': {'id': 'group', 'organization_id': 'org', 'name': role},
      'profiles': [],
      'jobs': [],
      'member_states': {},
    };
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/organizations/history') {
          if (++reads == 1) {
            started.complete();
            return reply.future;
          }
          return json(history('viewer'));
        }
        return json(snapshot);
      }),
    );
    await connected(connection);
    final bloc = GroupBloc(connection, 'group');
    try {
      await started.future;
      final refreshed = bloc.stream.firstWhere(
        (_) => bloc.state.loaded && bloc.state.group?['name'] == 'viewer',
      );
      connection.operator.value = {'name': 'viewer', 'role': 'viewer'};
      await refreshed;
      reply.complete(json(history('admin')));
      await bloc.refresh();
      expect(bloc.state.group?['name'], 'viewer');
    } finally {
      if (!reply.isCompleted) reply.complete(json(history('admin')));
      await bloc.close();
      await connection.close();
    }
  });

  test('CLI role change clears terminal ownership and queued input', () async {
    final started = Completer<void>(), reply = Completer<http.Response>();
    final inputs = <String>[];
    var closes = 0;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        switch (request.url.path) {
          case '/api/cli-setup':
            return json({'clis': []});
          case '/api/cli-setup/start':
            return json({'id': 'setup'});
          case '/api/cli-setup/poll':
            return json({
              'output': 'admin prompt',
              'cursor': 1,
              'running': true,
            });
          case '/api/cli-setup/close':
            closes++;
            return json({});
          case '/api/cli-setup/input':
            inputs.add(jsonDecode(request.body)['data'] as String);
            started.complete();
            return reply.future;
          default:
            return json(snapshot);
        }
      }),
    );
    await connected(connection);
    final bloc = CliSetupBloc(connection);
    try {
      await bloc.openSession('codex');
      final first = bloc.input('first');
      await started.future;
      final queued = bloc.input('queued');
      final reset = bloc.stream.firstWhere(
        (_) => bloc.state.session == null && bloc.state.output.isEmpty,
      );
      connection.operator.value = {'name': 'viewer', 'role': 'viewer'};
      await reset;
      reply.complete(http.Response('{"error":"old input failed"}', 503));
      await Future.wait([first, queued]);
      expect(inputs, ['first']);
      expect(closes, 1);
      expect(bloc.state.error, isNull);
      expect(bloc.state.output, isEmpty);
    } finally {
      if (!reply.isCompleted) reply.complete(json({}));
      await bloc.close();
      await connection.close();
    }
  });
  test(
    'console fences shared directory updates from an old-role read',
    () async {
      final started = Completer<void>(), reply = Completer<http.Response>();
      var reads = 0;
      Map<String, dynamic> directory(String role) => {
        'organizations': [
          {'id': role},
        ],
        'profiles': [
          {'id': 'agent', 'organization_id': role},
        ],
        'jobs': [],
      };
      final connection = TrackedConsoleConnection(
        client: MockClient((request) async {
          if (request.url.path == '/api/organizations/state') {
            if (++reads == 1) {
              started.complete();
              return reply.future;
            }
            return json(directory('viewer'));
          }
          if (request.url.path == '/api/organizations/inspect')
            return json({'status': 'idle'});
          return json(snapshot);
        }),
      );
      await connected(connection);
      final bloc = AgentConsoleBloc(connection, 'agent');
      try {
        await started.future;
        final refreshed = bloc.stream.firstWhere(
          (_) => bloc.state.inspection != null,
        );
        connection.operator.value = {'name': 'viewer', 'role': 'viewer'};
        await refreshed;
        reply.complete(json(directory('admin')));
        await connection.firstReadSettled.future;
        expect(
          (connection.organizationDirectory.value['organizations'] as List)
              .single['id'],
          'viewer',
        );
        expect(connection.selectedOrganization.value, 'viewer');
      } finally {
        if (!reply.isCompleted) reply.complete(json(directory('admin')));
        await bloc.close();
        await connection.close();
      }
    },
  );
}
