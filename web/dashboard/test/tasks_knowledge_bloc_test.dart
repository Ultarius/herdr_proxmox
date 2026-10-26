import 'package:flutter_test/flutter_test.dart';
import 'package:juice/juice.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/tasks_bloc.dart';
import 'package:herdr_dashboard_state/knowledge_bloc.dart';
import 'dart:convert';

http.Response json(Object value, [int code = 200]) =>
    http.Response(jsonEncode(value), code);
Map<String, dynamic> tasks(String title) => {
  'tasks': [
    {'id': 'task', 'title': title},
  ],
  'releases': [],
  'github': {'configured': true},
};
Future<void> connect(DashboardBloc connection) async {
  final ready = connection.stream.firstWhere(
    (_) => connection.state.refreshed != null,
  );
  connection.connect('admin');
  await ready;
}

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  test(
    'task feature admits a write during polling and fences older observations',
    () async {
      final started = Completer<void>(), oldReply = Completer<http.Response>();
      var reads = 0, writes = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/tasks') {
            if (++reads == 2) {
              started.complete();
              return oldReply.future;
            }
            return json(tasks(writes == 0 ? 'before' : 'after'));
          }
          if (request.url.path == '/api/tasks/candidate') {
            writes++;
            return json({});
          }
          return json({'workspaces': [], 'agents': []});
        }),
      );
      final bloc = TasksBloc(connection);
      try {
        await connect(connection);
        await bloc.refresh(force: true);
        final old = bloc.refresh(force: true);
        await started.future;
        await bloc.mutate('candidate', {'id': 'task'});
        expect(writes, 1);
        expect(bloc.state.tasks.single['title'], 'after');
        oldReply.complete(json(tasks('stale')));
        await old;
        expect(bloc.state.tasks.single['title'], 'after');
        expect(bloc.state.github['configured'], isTrue);
      } finally {
        if (!oldReply.isCompleted) oldReply.complete(json(tasks('stale')));
        await bloc.close();
        await connection.close();
      }
    },
  );

  test(
    'old-role task observations cannot replace current viewer data',
    () async {
      final started = Completer<void>(), reply = Completer<http.Response>();
      var reads = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/tasks') {
            if (++reads == 1) {
              started.complete();
              return reply.future;
            }
            return json(tasks('viewer data'));
          }
          return json({'workspaces': [], 'agents': []});
        }),
      );
      final bloc = TasksBloc(connection);
      try {
        await connect(connection);
        final old = bloc.refresh(force: true);
        await started.future;
        final ready = bloc.stream.firstWhere(
          (_) => bloc.state.tasks.firstOrNull?['title'] == 'viewer data',
        );
        connection.operator.value = {'role': 'viewer'};
        await ready;
        reply.complete(json(tasks('privileged old data')));
        await old;
        expect(bloc.state.tasks.single['title'], 'viewer data');
        expect(bloc.state.busy, isFalse);
      } finally {
        if (!reply.isCompleted) reply.complete(json(tasks('old')));
        await bloc.close();
        await connection.close();
      }
    },
  );

  test('task retry identity is retained after a failed mutation', () async {
    final ids = <String>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/tasks/candidate') {
          ids.add(jsonDecode(request.body)['request_id'] as String);
          return ids.length == 1
              ? json({'error': 'Retry this candidate'}, 503)
              : json({});
        }
        if (request.url.path == '/api/tasks') return json(tasks('candidate'));
        return json({'workspaces': [], 'agents': []});
      }),
    );
    final bloc = TasksBloc(connection);
    try {
      await connect(connection);
      await expectLater(
        bloc.mutate('candidate', {'id': 'task'}),
        throwsA(isA<DashboardRequestException>()),
      );
      expect(bloc.state.error, contains('Retry this candidate'));
      await bloc.mutate('candidate', {'id': 'task'});
      expect(ids, hasLength(2));
      expect(ids[1], ids[0]);
      expect(bloc.state.error, isNull);
    } finally {
      await bloc.close();
      await connection.close();
    }
  });

  test(
    'task feature close releases bindings and refuses late poll results',
    () async {
      final started = Completer<void>(), reply = Completer<http.Response>();
      var reads = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/tasks') {
            reads++;
            started.complete();
            return reply.future;
          }
          return json({'workspaces': [], 'agents': []});
        }),
      );
      final bloc = TasksBloc(connection);
      try {
        await connect(connection);
        final read = bloc.refresh(force: true);
        await started.future;
        await bloc.close();
        final closedState = bloc.state;
        connection.notificationRevision.value++;
        reply.complete(json(tasks('late')));
        await read;
        await bloc.refresh(force: true);
        expect(identical(bloc.state, closedState), isTrue);
        expect(bloc.activeBindingCount, 0);
        expect(reads, 1);
      } finally {
        if (!reply.isCompleted) reply.complete(json(tasks('late')));
        await bloc.close();
        await connection.close();
      }
    },
  );

  test(
    'knowledge feature owns read/write admission independently of filters',
    () async {
      final started = Completer<void>(), reply = Completer<http.Response>();
      var writes = 0;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/organizations/directory')
            return json({
              'organizations': [
                {'id': 'org', 'name': 'Organization'},
              ],
              'profiles': [],
            });
          if (request.url.path == '/api/knowledge') {
            final body = jsonDecode(request.body);
            if (body['mode'] != 'list') {
              writes++;
              started.complete();
              return reply.future;
            }
            return json({
              'records': [
                {'id': 'record', 'title': body['kind']},
              ],
              'repositories': ['repo'],
              'next_before': null,
            });
          }
          return json({'workspaces': [], 'agents': []});
        }),
      );
      final bloc = KnowledgeBloc(connection);
      try {
        await connect(connection);
        await bloc.initialize('org');
        expect(bloc.state.organizations.single['id'], 'org');
        final write = bloc.mutate({'mode': 'create', 'title': 'Finding'});
        await started.future;
        await bloc.load(
          const KnowledgeQuery(organization: 'org', kind: 'decision'),
        );
        expect(bloc.state.mutating, isTrue);
        expect(bloc.state.busy, isTrue);
        await bloc.mutate({'mode': 'create', 'title': 'Duplicate'});
        expect(writes, 1);
        reply.complete(json({}));
        await write;
        expect(bloc.state.records.single['title'], 'decision');
        expect(bloc.state.busy, isFalse);
        await connection.disconnect();
        expect(bloc.state.records, isEmpty);
        expect(bloc.state.organizations, isEmpty);
      } finally {
        if (!reply.isCompleted) reply.complete(json({}));
        await bloc.close();
        await connection.close();
      }
    },
  );
}
