import 'package:herdr_dashboard/task_activity.dart';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/tasks_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  test('legacy JSON participant identity uses a typed lookup', () {
    final task =
        jsonDecode('{"participants":[{"name":"Maya at launch"}]}') as Map;
    expect(taskAgentName(task), 'Maya at launch');
    expect(
      taskAgentName({'participants': []}),
      'Assigned agent identity unavailable',
    );
    expect(
      taskAgentName({
        'participants': [
          null,
          {'name': ''},
          {'name': 'Olaf'},
        ],
      }),
      'Olaf',
    );
    expect(
      taskAgentName({'participants': 'invalid'}),
      'Assigned agent identity unavailable',
    );
    expect(
      taskAgentName({
        'assigned_agent': {'name': 'Assigned'},
        ...task,
      }),
      'Assigned',
    );
  });
  test('task detail route round trips and keeps Tasks selected', () {
    final route = TaskDetailRoute('a' * 32);
    final parsed = AppCoordinator().parseRouteFromUri(route.toUri());
    expect(parsed, isA<TaskDetailRoute>());
    expect((parsed as TaskDetailRoute).id, route.id);
    expect(parsed.toUri().path, '/tasks/${route.id}');
  });
  for (final brightness in Brightness.values) {
    for (final width in [390.0, 1280.0]) {
      testWidgets('task detail sections and evidence at $width $brightness', (
        tester,
      ) async {
        tester.view.physicalSize = Size(width, 1000);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final head = 'b' * 40, base = 'a' * 40;
        final writes = <String>[];
        final task = <String, dynamic>{
          'id': 'task-1',
          'title': 'Search agents',
          'description': 'Filter by name and preserve status selection.',
          'state': 'review_ready',
          'repository': 'repo',
          'head_sha': head,
          'base_sha': base,
          'branch': 'herdr/task-abcdefabcdef',
          'base_ref': 'refs/remotes/origin/main',
          'run_id': 'run',
          'participants': [
            {
              'run_id': 'run',
              'name': 'Maya at launch',
              'role': 'Frontend developer',
              'runtime': 'opencode',
              'provenance': 'task_launch',
            },
          ],
          'sessions': [
            {
              'id': 'run',
              'state': 'persona_sent',
              'history': [
                {'alias': 'old-session', 'session_closed_at': 'yesterday'},
              ],
            },
          ],
          'commit_graph': [
            {
              'sha': head,
              'parents': [base],
              'subject': 'Add agent search',
              'author': 'Herdr Agent',
            },
            {'sha': base, 'parents': [], 'subject': 'Initial base'},
          ],
          'builds': {
            base: {'state': 'complete', 'required_checks_verified': true},
          },
          'audit': [
            for (var i = 0; i < 2; i++)
              {
                'action': 'candidate',
                'actor': 'dashboard',
                'at': 'today',
                'head_sha': head,
              },
          ],
        };
        final connection = DashboardBloc(
          client: MockClient((request) async {
            if (request.method != 'GET') writes.add(request.url.path);
            if (request.url.path.endsWith('/tasks/detail'))
              return http.Response(jsonEncode(task), 200);
            if (request.url.path.endsWith('/tasks/commit'))
              return http.Response(
                jsonEncode({
                  'sha': head,
                  'diff': '+search feature',
                  'comparison': 'first parent',
                }),
                200,
              );
            if (request.url.path.endsWith('/tasks'))
              return http.Response(
                jsonEncode({
                  'tasks': [task],
                  'github': {'configured': false},
                  'executor': 'service',
                }),
                200,
              );
            return http.Response('{"workspaces":[],"agents":[]}', 200);
          }),
        );
        BlocScope.register<DashboardBloc>(() => connection);
        connection.connect('token');
        connection.operator.value = {'role': 'admin'};
        addTearDown(() async {
          await connection.disconnect();
          await BlocScope.reset();
        });
        await tester.pumpWidget(
          MaterialApp(
            theme: ThemeData(brightness: brightness),
            home: const Scaffold(body: TasksPage()),
          ),
        );
        await tester.pumpAndSettle();
        expect(find.text('Open task'), findsOneWidget);
        expect(find.text('Capture candidate'), findsNothing);
        expect(find.text('Candidate diff'), findsNothing);
        await tester.tap(find.text('Open task'));
        await tester.pumpAndSettle();
        expect(
          find.text('Filter by name and preserve status selection.'),
          findsOneWidget,
        );
        await tester.tap(find.text('Activity & agents'));
        await tester.pumpAndSettle();
        expect(find.text('Maya at launch'), findsOneWidget);
        expect(find.text('Candidate captured (2 records)'), findsOneWidget);
        await tester.tap(find.text('Maya at launch'));
        await tester.pumpAndSettle();
        expect(find.text('Session status: persona_sent'), findsOneWidget);
        expect(
          find.textContaining('old-session closed yesterday'),
          findsOneWidget,
        );
        await tester.tap(find.text('Changes'));
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Add agent search'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Add agent search'));
        await tester.pumpAndSettle();
        expect(find.text('+search feature'), findsOneWidget);
        await tester.tap(find.text('Close'));
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Checks & builds'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Checks & builds'));
        await tester.pumpAndSettle();
        expect(
          find.text('Current candidate validation: not run'),
          findsOneWidget,
        );
        expect(
          find.text('Does not validate the current candidate.'),
          findsOneWidget,
        );
        expect(
          writes,
          isEmpty,
        ); // Detail, graph and commit selection are read-only.
        expect(tester.takeException(), isNull);
        await tester.scrollUntilVisible(
          find.text('All tasks'),
          -300,
          scrollable: find.byType(Scrollable).first,
        );
        await tester.pumpAndSettle();
        await tester.tap(find.text('All tasks'));
        await tester.pumpAndSettle();
        expect(find.text('Open task'), findsOneWidget);
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        await BlocScope.reset();
      });
    }
  }
  testWidgets(
    'missing task presents a recoverable error without another task data',
    (tester) async {
      final connection = DashboardBloc(
        client: MockClient(
          (request) async => http.Response(
            request.url.path.endsWith('/tasks/detail')
                ? '{"error":"Task not found."}'
                : '{"tasks":[],"github":{}}',
            request.url.path.endsWith('/tasks/detail') ? 400 : 200,
          ),
        ),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      addTearDown(() async {
        await connection.disconnect();
        await BlocScope.reset();
      });
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: TasksPage(taskId: 'missing')),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('Task not found'), findsOneWidget);
      expect(
        find.text('Task unavailable. Refresh to try again.'),
        findsOneWidget,
      );
      expect(find.text('All tasks'), findsOneWidget);
      expect(find.text('Capture candidate'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
  testWidgets('history refresh reloads read-only without recapturing', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final writes = <String>[];
    var details = 0;
    final task = <String, dynamic>{
      'id': 'task-1',
      'title': 'Search agents',
      'state': 'review_ready',
      'repository': 'repo',
      'head_sha': 'b' * 40,
      'base_sha': 'a' * 40,
      'history_error': 'Git history is temporarily unavailable.',
      'commit_graph': [],
      'builds': {},
      'audit': [],
    };
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.method != 'GET') writes.add(request.url.path);
        if (request.url.path.endsWith('/tasks/detail')) {
          details += 1;
          return http.Response(jsonEncode(task), 200);
        }
        if (request.url.path.endsWith('/tasks'))
          return http.Response(
            jsonEncode({
              'tasks': [task],
              'github': {'configured': false},
              'executor': 'service',
            }),
            200,
          );
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'role': 'admin'};
    addTearDown(() async {
      await connection.disconnect();
      await BlocScope.reset();
    });
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: TasksPage(taskId: 'task-1')),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Changes'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Commit history unavailable'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    expect(find.text('Commit history unavailable'), findsOneWidget);
    expect(find.textContaining('temporarily unavailable'), findsOneWidget);
    final before = details;
    await tester.ensureVisible(find.text('Refresh history'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Refresh history'));
    await tester.pumpAndSettle();
    expect(details, greaterThan(before));
    expect(writes, isEmpty);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });
}
