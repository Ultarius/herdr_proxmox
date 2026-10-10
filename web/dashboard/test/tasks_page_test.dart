import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/tasks_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  test(
    'repository choices are relative, distinct, sorted and bounded to projects',
    () {
      expect(
        taskRepositories([
          {'project': '/home/herdr/projects/zeta'},
          {'project': '/home/herdr/projects/team/alpha'},
          {'project': '/home/herdr/projects/zeta'},
          {'project': '/outside/repo'},
          {'project': '../escape'},
          {'project': null},
        ], '/home/herdr/projects'),
        ['team/alpha', 'zeta'],
      );
    },
  );

  testWidgets(
    'GitHub authentication failure preserves the browser dashboard session',
    (tester) async {
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/session')) {
            return http.Response(
              '{"authenticated":true,"operator":"admin","role":"admin"}',
              200,
            );
          }
          if (request.url.path.endsWith('/tasks/refresh')) {
            return http.Response(
              '{"error":"GitHub credentials are invalid or expired.","source":"github","github_status":401}',
              502,
            );
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      await connection.restoreSession();
      await tester.pumpAndSettle();
      final generation = connection.generation;
      await expectLater(
        connection.request('tasks/refresh', {
          'task_id': 'task',
          'request_id': 'retry',
        }),
        throwsA(isA<Exception>()),
      );
      expect(connection.generation, generation);
      expect(connection.state.connected, isTrue);
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
  testWidgets('drafts are grouped below active work', (tester) async {
    tester.view.physicalSize = const Size(1280, 2000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final active = {
      'id': 'task-1',
      'title': 'Implement feature',
      'repository': 'repo',
      'state': 'implementing',
      'audit': [],
    };
    final draft = {
      'id': 'task-2',
      'title': 'Proposed follow-up',
      'repository': 'repo',
      'state': 'draft',
      'audit': [],
    };
    final connection = DashboardBloc(
      client: MockClient(
        (request) async => http.Response(
          jsonEncode({
            'tasks': [active, draft],
            'github': {'configured': false},
          }),
          200,
        ),
      ),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'name': 'admin', 'role': 'admin'};
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: TasksPage())),
    );
    await tester.pumpAndSettle();
    // Drafts are proposals awaiting an operator, so they sit under a header
    // rather than mixed into work that is actually in flight.
    expect(find.text('Drafts (1)'), findsOneWidget);
    expect(
      tester.getTopLeft(find.text('Proposed follow-up')).dy,
      greaterThan(tester.getTopLeft(find.text('Implement feature')).dy),
    );
    await tester.pumpWidget(const SizedBox());
    await connection.disconnect();
    await BlocScope.reset();
  });

  test('Tasks route is directly addressable', () {
    expect(
      AppCoordinator().parseRouteFromUri(Uri.parse('/tasks')),
      isA<TasksRoute>(),
    );
  });

  for (final brightness in Brightness.values) {
    for (final width in [390.0, 1280.0]) {
      testWidgets('task candidates adapt to $width and $brightness', (
        tester,
      ) async {
        tester.view.physicalSize = Size(width, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final published = <Map<String, dynamic>>[];
        final task = {
          'id': 'task-1',
          'title': 'Implement feature',
          'state': 'review_ready',
          'repository': 'repo',
          'github_repository': 'owner/repo',
          'run_id': 'run',
          'branch': 'herdr/task-abcdefabcdef',
          'base_ref': 'refs/remotes/origin/main',
          'base_sha': ''.padLeft(40, 'a'),
          'head_sha': ''.padLeft(40, 'b'),
          'diff': '+implementation',
          'audit': [],
        };
        final connection = DashboardBloc(
          client: MockClient((request) async {
            if (request.url.path.endsWith('/tasks/publish')) {
              published.add(
                Map<String, dynamic>.from(jsonDecode(request.body)),
              );
              return http.Response(jsonEncode(task), 200);
            }
            if (request.url.path.endsWith('/tasks/detail')) {
              return http.Response(jsonEncode(task), 200);
            }
            if (request.url.path.endsWith('/tasks')) {
              return http.Response(
                jsonEncode({
                  'tasks': [task],
                  'github': {
                    'configured': true,
                    'login': 'owner',
                    'repositories': ['owner/repo'],
                  },
                }),
                200,
              );
            }
            return http.Response('{"workspaces":[],"agents":[]}', 200);
          }),
        );
        BlocScope.register<DashboardBloc>(() => connection);
        connection.connect('token');
        connection.operator.value = {'name': 'admin', 'role': 'admin'};
        await tester.pumpWidget(
          MaterialApp(
            theme: ThemeData(brightness: brightness),
            home: const Scaffold(body: TasksPage(taskId: 'task-1')),
          ),
        );
        await tester.pumpAndSettle();
        expect(find.text('Implement feature'), findsOneWidget);
        await tester.ensureVisible(find.text('Review and publish'));
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Review and publish'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Review and publish'));
        await tester.pumpAndSettle();
        expect(find.text('Review and publish candidate'), findsOneWidget);
        expect(published, isEmpty);
        await tester.ensureVisible(find.text('Approve publication'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Approve publication'));
        await tester.pumpAndSettle();
        expect(published.single['head_sha'], ''.padLeft(40, 'b'));
        expect(published.single['request_id'], isNotEmpty);
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        await BlocScope.reset();
      });
    }
  }

  testWidgets('merged results, conflicts and unverified builds are stated', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1280, 2000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final head = ''.padLeft(40, 'b');
    final merge = ''.padLeft(40, 'c');
    final base = {
      'id': 'task-1',
      'title': 'Implement feature',
      'repository': 'repo',
      'github_repository': 'owner/repo',
      'run_id': 'run',
      'branch': 'herdr/task-abcdefabcdef',
      'base_ref': 'refs/remotes/origin/main',
      'base_sha': ''.padLeft(40, 'a'),
      'head_sha': head,
      'last_sync': '2026-01-01T00:00:00+00:00',
      'builds': {
        head: {
          'run_id': 'run-1',
          'state': 'complete',
          'target': head,
          'exit_code': 0,
          'required_checks_verified': false,
          'checks': [
            {'id': 'unit', 'status': 'failed'},
          ],
        },
      },
      'audit': [],
    };
    final conflicting = {
      ...base,
      'state': 'pr_open',
      'pull': {
        'number': 7,
        'url': 'https://github.com/owner/repo/pull/7',
        'state': 'open',
        'draft': false,
        'head_sha': head,
        'mergeable': false,
        'mergeable_state': 'dirty',
      },
    };
    final merged = {
      ...base,
      'id': 'task-2',
      'state': 'merged',
      'merge_sha': merge,
      'pull': {
        'number': 8,
        'url': 'https://github.com/owner/repo/pull/8',
        'state': 'closed',
        'draft': false,
        'head_sha': head,
        'mergeable': true,
        'mergeable_state': 'clean',
      },
    };
    final connection = DashboardBloc(
      client: MockClient(
        (request) async => http.Response(
          jsonEncode(
            request.url.path.endsWith('/tasks/detail')
                ? request.url.queryParameters['id'] == 'task-2'
                      ? merged
                      : conflicting
                : {
                    'tasks': [conflicting, merged],
                    'github': {'configured': true, 'login': 'owner'},
                  },
          ),
          200,
        ),
      ),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'name': 'admin', 'role': 'admin'};
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: TasksPage(taskId: 'task-1')),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Changes'));
    await tester.pumpAndSettle();
    // Conflicts are only reported for a pull request that can still merge.
    expect(find.textContaining('PR #7 · Open for review'), findsOneWidget);
    expect(find.textContaining('conflicts'), findsOneWidget);
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: TasksPage(key: ValueKey('merged'), taskId: 'task-2'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Changes'));
    await tester.pumpAndSettle();
    // A merged pull request is never presented as awaiting review.
    expect(find.textContaining('PR #8 · Merged'), findsOneWidget);
    expect(find.text('Merged · repo'), findsOneWidget);
    expect(find.textContaining('Merged result: $merge'), findsOneWidget);
    expect(find.text('Build merged result'), findsOneWidget);
    await tester.tap(find.text('Checks & builds'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.textContaining('Previous candidate build'));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Previous candidate build'));
    await tester.pumpAndSettle();
    expect(
      find.text('Does not validate the current candidate.'),
      findsOneWidget,
    );
    expect(find.textContaining('required checks not verified'), findsOneWidget);
    expect(find.textContaining('not evidence of a pass'), findsOneWidget);
    expect(find.text('unit: failed'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });

  testWidgets(
    'an unavailable credential file and missing credentials are stated',
    (tester) async {
      final head = ''.padLeft(40, 'b');
      final task = {
        'id': 'task-1',
        'title': 'Implement feature',
        'state': 'published',
        'repository': 'repo',
        'github_repository': 'owner/repo',
        'run_id': 'run',
        'branch': 'herdr/task-abcdefabcdef',
        'base_ref': 'refs/remotes/origin/main',
        'base_sha': ''.padLeft(40, 'a'),
        'head_sha': head,
        'publish': {'state': 'complete', 'head_sha': head},
        'audit': [],
      };
      final connection = DashboardBloc(
        client: MockClient(
          (request) async => http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks/detail')
                  ? task
                  : {
                      'tasks': [task],
                      'github': {
                        'configured': false,
                        'error': 'GitHub credential file must have mode 0600.',
                      },
                    },
            ),
            200,
          ),
        ),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      connection.operator.value = {'name': 'admin', 'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: TasksPage())),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('mode 0600'), findsOneWidget);
      await tester.tap(find.text('Open task'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Open draft PR'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      // Publishing actions stay disabled rather than failing after the click.
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Open draft PR'),
            )
            .onPressed,
        isNull,
      );
      // There is no pull request yet, so there is nothing to refresh.
      expect(find.text('Refresh PR status'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );

  testWidgets('operators cannot publish or configure credentials', (
    tester,
  ) async {
    final connection = DashboardBloc(
      client: MockClient(
        (request) async => http.Response(
          request.url.path.endsWith('/tasks')
              ? '{"tasks":[],"github":{"configured":false}}'
              : '{"workspaces":[],"agents":[]}',
          200,
        ),
      ),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'name': 'viewer', 'role': 'operator'};
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: TasksPage())),
    );
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Create task'),
          )
          .onPressed,
      isNull,
    );
    expect(
      tester
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'GitHub publishing'),
          )
          .onPressed,
      isNull,
    );
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });

  testWidgets('queued validation links to the builds page', (tester) async {
    tester.view.physicalSize = const Size(1280, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final head = ''.padLeft(40, 'b');
    final task = {
      'id': 'task-1',
      'title': 'Implement feature',
      'state': 'review_ready',
      'repository': 'repo',
      'github_repository': 'owner/repo',
      'run_id': 'run',
      'branch': 'herdr/task-abcdefabcdef',
      'base_ref': 'refs/remotes/origin/main',
      'base_sha': ''.padLeft(40, 'a'),
      'head_sha': head,
      'builds': {
        head: {'run_id': 'run-1', 'state': 'queued', 'target': head},
      },
      'audit': [],
    };
    final connection = DashboardBloc(
      client: MockClient(
        (request) async => http.Response(
          jsonEncode(
            request.url.path.endsWith('/tasks/detail')
                ? task
                : {
                    'tasks': [task],
                    'github': {'configured': false},
                    'executor': 'service',
                  },
          ),
          200,
        ),
      ),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'name': 'admin', 'role': 'admin'};
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: TasksPage(taskId: 'task-1', coordinator: AppCoordinator()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final chip = find.widgetWithText(
      OutlinedButton,
      'Validation in progress \u00b7 view build',
    );
    expect(chip, findsOneWidget);
    expect(find.text('Validate latest commit'), findsNothing);
    // The link is usable rather than a disabled "in progress" button.
    expect(tester.widget<OutlinedButton>(chip).onPressed, isNotNull);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });

  for (final exactTarget in [true, false]) {
    testWidgets('validate action respects exact target: $exactTarget', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(1280, 2000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final head = ''.padLeft(40, 'b');
      final task = {
        'id': 'task-1',
        'title': 'Implement feature',
        'state': 'review_ready',
        'repository': 'repo',
        'github_repository': 'owner/repo',
        'run_id': 'run',
        'branch': 'herdr/task-abcdefabcdef',
        'base_ref': 'refs/remotes/origin/main',
        'base_sha': ''.padLeft(40, 'a'),
        'head_sha': head,
        'builds': {
          head: {
            'run_id': 'run-1',
            'state': 'complete',
            'target': exactTarget ? head : ''.padLeft(40, 'a'),
            'exit_code': 0,
            'required_checks_verified': true,
            'checks': [
              {'id': 'unit', 'status': 'passed'},
            ],
          },
        },
        'audit': [],
      };
      final connection = DashboardBloc(
        client: MockClient(
          (request) async => http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks/detail')
                  ? task
                  : {
                      'tasks': [task],
                      'github': {'configured': false},
                      'executor': 'service',
                    },
            ),
            200,
          ),
        ),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      connection.operator.value = {'name': 'admin', 'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: TasksPage(taskId: 'task-1')),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Checks & builds'));
      await tester.pumpAndSettle();
      expect(
        find.text(
          exactTarget
              ? 'Current candidate validation: verified'
              : 'Current candidate validation: finished without verified required checks',
        ),
        findsOneWidget,
      );
      // Re-running the same identity is a no-op, so the action is hidden.
      expect(
        find.text('Validate candidate'),
        exactTarget ? findsNothing : findsOneWidget,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    });
  }

  testWidgets('the task overview states the group outcome notice', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1280, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final task = {
      'id': 'task-1',
      'title': 'Implement feature',
      'state': 'completed',
      'repository': 'repo',
      'source': {
        'group_id': 'group',
        'meeting_id': 'origin',
        'task_id': 'parent',
      },
      'outcome_notice': {
        'state': 'scheduled',
        'group_name': 'Project Review',
        'job_id': 'meeting-9',
        'outcome': 'completed',
      },
      'acceptance': {
        'state': 'not_satisfied',
        'reviewer_name': 'Iris',
        'reason': 'Missing boundary check.',
      },
      'audit': [],
    };
    final connection = DashboardBloc(
      client: MockClient(
        (request) async => http.Response(
          jsonEncode(
            request.url.path.endsWith('/tasks/detail')
                ? task
                : {
                    'tasks': [task],
                    'github': {'configured': false},
                  },
          ),
          200,
        ),
      ),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'name': 'admin', 'role': 'admin'};
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: TasksPage(taskId: 'task-1')),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.textContaining(
        'Outcome review scheduled for: Project Review \u00b7 outcome completed \u00b7 meeting meeting-9.',
      ),
      findsOneWidget,
    );
    expect(
      find.textContaining(
        'Acceptance: not satisfied by Iris. Missing boundary check.',
      ),
      findsOneWidget,
    );
    expect(
      find.textContaining(
        'Acceptance review needs attention: Missing boundary check.',
      ),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });
}
