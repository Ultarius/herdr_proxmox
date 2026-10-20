import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/tasks_page.dart';
import 'package:herdr_dashboard/routes.dart';

Future<void> withDashboardConnection(
  WidgetTester tester,
  DashboardBloc connection,
  Future<void> Function() body,
) async {
  var cleaned = false;
  Future<void> cleanup() async {
    if (cleaned) return;
    cleaned = true;
    try {
      await tester.pumpWidget(const SizedBox.shrink());
    } finally {
      try {
        await connection.disconnect();
      } finally {
        await BlocScope.reset();
      }
    }
  }

  addTearDown(cleanup);
  try {
    await body();
  } finally {
    // Flutter checks pending timers before addTearDown runs. Cancel polling
    // here even when an assertion fails; teardown remains the safety net.
    await cleanup();
  }
}

void main() {
  testWidgets(
    'draft PR previews public text and sends only after confirmation',
    (tester) async {
      tester.view.physicalSize = const Size(1280, 1800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final head = 'b' * 40;
      final task = <String, dynamic>{
        'id': 'task',
        'title': 'Ready task',
        'state': 'published',
        'repository': 'repo',
        'github_repository': 'owner/repo',
        'head_sha': head,
        'branch': 'herdr/task-123456abcdef',
        'base_ref': 'refs/remotes/origin/main',
        'publish': {'head_sha': head},
        'pull_preview': {
          'title': 'Suggested title',
          'body': '## Summary\nSuggested evidence.',
          'head_sha': head,
          'repository': 'owner/repo',
          'branch': 'herdr/task-123456abcdef',
          'base': 'main',
        },
      };
      final posts = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/tasks/pull'))
            posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
          return http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks')
                  ? {
                      'tasks': [task],
                      'github': {'configured': true},
                    }
                  : task,
            ),
            200,
          );
        }),
      );
      await withDashboardConnection(tester, connection, () async {
        BlocScope.register<DashboardBloc>(() => connection);
        connection.connect('token');
        connection.operator.value = {'role': 'admin'};
        await tester.pumpWidget(
          const MaterialApp(
            home: Scaffold(body: TasksPage(taskId: 'task')),
          ),
        );
        await tester.pumpAndSettle();
        await tester.tap(find.text('Changes'));
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Open draft PR'));
        await tester.tap(find.text('Open draft PR'));
        await tester.pumpAndSettle();
        expect(find.text('Preview draft pull request'), findsOneWidget);
        expect(find.textContaining('Exact candidate: $head'), findsOneWidget);
        expect(posts, isEmpty);
        await tester.tap(find.text('Cancel'));
        await tester.pumpAndSettle();
        expect(posts, isEmpty);
        await tester.tap(find.text('Open draft PR'));
        await tester.pumpAndSettle();
        await tester.enterText(
          find.widgetWithText(TextFormField, 'PR title'),
          'Edited PR title',
        );
        await tester.enterText(
          find.widgetWithText(TextFormField, 'PR description (Markdown)'),
          'Edited public description.',
        );
        await tester.tap(find.text('Create draft PR'));
        await tester.pumpAndSettle();
        expect(posts.single['title'], 'Edited PR title');
        expect(posts.single['body'], 'Edited public description.');
        expect(posts.single['head_sha'], head);
        expect(posts.single['reviewed'], isTrue);
        expect(tester.takeException(), isNull);
      });
    },
  );

  for (final width in [390.0, 1280.0]) {
    testWidgets('draft PR preview and excerpt opt-in adapt to $width', (
      tester,
    ) async {
      tester.view.physicalSize = Size(width, 1400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
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
        'pull_preview': {
          'title': 'Suggested title',
          'body': '## Summary\nSuggested evidence.',
          'excerpt':
              'Finalized discussion excerpt (not an approval verdict):\n\nGroup findings.',
          'head_sha': head,
          'repository': 'owner/repo',
          'branch': 'herdr/task-abcdefabcdef',
          'base': 'main',
        },
        'audit': [],
      };
      final posts = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/tasks/pull')) {
            posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
          }
          return http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks/detail')
                  ? task
                  : {
                      'tasks': [task],
                      'github': {'configured': true, 'login': 'owner'},
                    },
            ),
            200,
          );
        }),
      );
      await withDashboardConnection(tester, connection, () async {
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
        await tester.scrollUntilVisible(
          find.text('Open draft PR'),
          200,
          scrollable: find.byType(Scrollable).first,
        );
        await tester.pumpAndSettle();
        await tester.tap(find.text('Open draft PR'));
        await tester.pumpAndSettle();
        expect(find.text('Preview draft pull request'), findsOneWidget);
        final description = find.widgetWithText(
          TextFormField,
          'PR description (Markdown)',
        );
        // The excerpt publishes internal deliberation, so it stays out of the
        // default body and is offered as an explicit choice.
        expect(
          tester.widget<TextFormField>(description).controller!.text,
          isNot(contains('Group findings')),
        );
        final optIn = find.text('Include the finalized discussion excerpt');
        await tester.ensureVisible(optIn);
        await tester.pumpAndSettle();
        await tester.tap(optIn);
        await tester.pumpAndSettle();
        expect(
          tester.widget<TextFormField>(description).controller!.text,
          contains('Group findings'),
        );
        await tester.tap(find.text('Create draft PR'));
        await tester.pumpAndSettle();
        expect(posts.single['body'], contains('Group findings'));
        expect(tester.takeException(), isNull);
      });
    });
  }

  testWidgets('fork publication states both destinations and the qualified head', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1280, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final head = 'b' * 40;
    final branch = 'herdr/task-123456abcdef';
    final task = <String, dynamic>{
      'id': 'task',
      'title': 'Ready task',
      'state': 'published',
      'repository': 'repo',
      'github_repository': 'owner/repo',
      'publishing_repository': 'contributor/repo',
      'head_sha': head,
      'branch': branch,
      'base_ref': 'refs/remotes/origin/main',
      'publish': {'head_sha': head, 'repository': 'contributor/repo'},
      'pull_preview': {
        'title': 'Suggested title',
        'body': '## Summary\nSuggested evidence.',
        'excerpt': '',
        'head_sha': head,
        'repository': 'owner/repo',
        'publishing_repository': 'contributor/repo',
        'fork': true,
        'head': 'contributor:$branch',
        'branch': branch,
        'base': 'main',
      },
    };
    final posts = <Map<String, dynamic>>[];
    var prCapability = 'needs_different_authentication';
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/tasks/pull'))
          posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
        return http.Response(
          jsonEncode(
            request.url.path.endsWith('/tasks')
                ? {
                    'tasks': [task],
                    'github': {
                      'configured': true,
                      'login': 'contributor',
                      'repositories': ['owner/repo'],
                      'publication': {
                        'owner/repo': {
                          'mode': 'fork',
                          'publishing_repository': 'contributor/repo',
                          'verification': {
                            'fork_access': 'verified',
                            'account_write_to_publishing': 'verified',
                            'token_write_capability': 'not_exercised',
                            'upstream_pr_creation': prCapability,
                          },
                        },
                      },
                    },
                  }
                : task,
          ),
          200,
        );
      }),
    );
    await withDashboardConnection(tester, connection, () async {
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      connection.operator.value = {'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: TasksPage())),
      );
      await tester.pumpAndSettle();
      // Setup reports capability, and never claims to have exercised a write.
      expect(
        find.textContaining('Token write access has not been tested'),
        findsOneWidget,
      );
      expect(
        find.textContaining('Different GitHub authentication is required'),
        findsOneWidget,
      );
      await tester.tap(find.text('Open task'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Changes'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Configure authentication for upstream PR'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(find.text('Open draft PR'), findsNothing);
      await tester.tap(find.text('Configure authentication for upstream PR'));
      await tester.pumpAndSettle();
      expect(find.text('GitHub publishing'), findsOneWidget);
      expect(posts, isEmpty);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      // Once compatible authentication is configured, the existing PR preview
      // still distinguishes the fork branch from the upstream target.
      prCapability = 'supported';
      await tester.pump(const Duration(seconds: 15));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Open draft PR'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      // The branch destination and the pull request target are separate.
      expect(
        find.textContaining(
          'Publish branch to: contributor/repo\nOpen draft PR into: owner/repo',
        ),
        findsOneWidget,
      );
      await tester.tap(find.text('Open draft PR'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining(
          'Publish branch to: contributor/repo\n\nOpen draft PR into: owner/repo',
        ),
        findsOneWidget,
      );
      expect(
        find.textContaining('Source branch: contributor:$branch'),
        findsOneWidget,
      );
      await tester.tap(find.text('Create draft PR'));
      await tester.pumpAndSettle();
      expect(posts.single['head_sha'], head);
      expect(posts.single['reviewed'], isTrue);
      expect(tester.takeException(), isNull);
    });
  });

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
      // An empty root would make the prefix '/' and match every absolute path.
      expect(
        taskRepositories([
          {'project': '/home/herdr/projects/zeta'},
          {'project': '/outside/repo'},
        ], ''),
        isEmpty,
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
      await withDashboardConnection(tester, connection, () async {
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
      });
    },
  );
  for (final brightness in Brightness.values) {
    for (final width in [390.0, 1280.0]) {
      testWidgets('drafts are readable at $width and $brightness', (
        tester,
      ) async {
        tester.view.physicalSize = Size(width, 2000);
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
        await withDashboardConnection(tester, connection, () async {
          BlocScope.register<DashboardBloc>(() => connection);
          connection.connect('token');
          connection.operator.value = {'name': 'admin', 'role': 'admin'};
          await tester.pumpWidget(
            MaterialApp(
              theme: ThemeData(brightness: brightness),
              home: const Scaffold(body: TasksPage()),
            ),
          );
          await tester.pumpAndSettle();
          // Drafts are proposals awaiting an operator, so they sit under a header
          // rather than mixed into work that is actually in flight.
          expect(find.text('Drafts (1)'), findsOneWidget);
          expect(
            tester.getTopLeft(find.text('Proposed follow-up')).dy,
            greaterThan(tester.getTopLeft(find.text('Implement feature')).dy),
          );
          final caption = find.text(
            'Work that has not started. Open a task for its next step.',
          );
          final text = tester.widget<Text>(caption);
          final theme = Theme.of(tester.element(caption));
          final foreground = text.style!.color!.computeLuminance();
          final background = theme.scaffoldBackgroundColor.computeLuminance();
          final ratio = foreground > background
              ? (foreground + 0.05) / (background + 0.05)
              : (background + 0.05) / (foreground + 0.05);
          expect(ratio, greaterThanOrEqualTo(4.5));
          expect(tester.takeException(), isNull);
        });
      });
    }
  }

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
        await withDashboardConnection(tester, connection, () async {
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
        });
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
    await withDashboardConnection(tester, connection, () async {
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
      expect(find.text('GitHub reports merge conflicts.'), findsOneWidget);
      expect(find.textContaining('PR has merge conflicts'), findsOneWidget);
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
      await tester.ensureVisible(
        find.textContaining('Previous candidate build'),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.textContaining('Previous candidate build'));
      await tester.pumpAndSettle();
      expect(
        find.text('Does not validate the current candidate.'),
        findsOneWidget,
      );
      expect(
        find.textContaining('required checks not verified'),
        findsOneWidget,
      );
      expect(find.textContaining('not evidence of a pass'), findsOneWidget);
      expect(find.text('unit: failed'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
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
      await withDashboardConnection(tester, connection, () async {
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
      });
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
    await withDashboardConnection(tester, connection, () async {
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
    });
  });

  testWidgets(
    'live work in the Flutter list belongs only to the matching task run',
    (tester) async {
      tester.view.physicalSize = const Size(1280, 1800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      var status = 'working', run = 'current-run';
      final tasks = [
        {
          'id': 'current',
          'title': 'Current task',
          'state': 'pr_open',
          'repository': 'repo',
          'profile_id': 'worker',
          'run_id': 'current-run',
          'pr_repair': {'state': 'running'},
        },
        {
          'id': 'other',
          'title': 'Other task',
          'state': 'pr_open',
          'repository': 'repo',
          'profile_id': 'worker',
          'run_id': 'old-run',
        },
      ];
      final connection = DashboardBloc(
        client: MockClient(
          (request) async => http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks')
                  ? {'tasks': tasks, 'github': {}}
                  : {
                      'workspaces': [],
                      'agents': [
                        {
                          'task_id': 'current',
                          'run_id': run,
                          'profile_id': 'worker',
                          'agent_status': status,
                        },
                      ],
                    },
            ),
            200,
          ),
        ),
      );
      await withDashboardConnection(tester, connection, () async {
        BlocScope.register<DashboardBloc>(() => connection);
        connection.connect('token');
        await tester.pumpWidget(
          const MaterialApp(home: Scaffold(body: TasksPage())),
        );
        await tester.pumpAndSettle();
        final current = find.ancestor(
          of: find.text('Current task'),
          matching: find.byType(Card),
        );
        final other = find.ancestor(
          of: find.text('Other task'),
          matching: find.byType(Card),
        );
        expect(
          find.descendant(
            of: current,
            matching: find.text('Resolving conflicts · running checks'),
          ),
          findsOneWidget,
        );
        expect(
          find.descendant(
            of: other,
            matching: find.byIcon(Icons.play_circle_outline),
          ),
          findsNothing,
        );
        status = 'blocked';
        connection.send(DashboardCommand('refresh'));
        await tester.pumpAndSettle();
        expect(
          find.descendant(of: current, matching: find.text('Needs attention')),
          findsOneWidget,
        );
        run = 'stale-run';
        connection.send(DashboardCommand('refresh'));
        await tester.pumpAndSettle();
        expect(find.text('Needs attention'), findsNothing);
        expect(find.byIcon(Icons.play_circle_outline), findsNothing);
      });
    },
  );

  for (final state in ['queued', 'running']) {
    testWidgets('$state validation links to the builds page', (tester) async {
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
          head: {'run_id': 'run-1', 'state': state, 'target': head},
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
      await withDashboardConnection(tester, connection, () async {
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
      });
    });
  }

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
        'merge_sha': 'c' * 40,
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
      await withDashboardConnection(tester, connection, () async {
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
          find.text('Validate latest commit'),
          exactTarget ? findsNothing : findsOneWidget,
        );
        expect(tester.takeException(), isNull);
      });
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
    await withDashboardConnection(tester, connection, () async {
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
    });
  });
}
