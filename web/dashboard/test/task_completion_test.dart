import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/tasks_page.dart';

void main() {
  testWidgets(
    'reviewed completion records done without claiming a validation pass',
    (tester) async {
      final task = <String, dynamic>{
        'id': 'task',
        'title': 'Already implemented',
        'state': 'review_ready',
        'head_sha': 'b' * 40,
        'current_upstream_sha': 'a' * 40,
        'repository': 'repo',
        'run_id': 'run',
        'organization_id': 'org',
      };
      Map<String, dynamic>? submitted;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/tasks/complete')) {
            submitted = Map<String, dynamic>.from(jsonDecode(request.body));
            task['state'] = 'completed';
            task['completion'] = {
              'reason': submitted!['reason'],
              'actor': 'admin',
              'at': 'today',
              'validation': 'not_verified',
              'outcome': 'incorporated_elsewhere',
              'candidate': 'b' * 40,
              'upstream': 'a' * 40,
            };
          }
          return http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks')
                  ? {
                      'tasks': [task],
                      'github': {},
                    }
                  : task,
            ),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      connection.operator.value = {'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: TasksPage(taskId: 'task')),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Mark task done'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Confirm completion'),
            )
            .onPressed,
        isNull,
      );
      await tester.enterText(
        find.byType(TextField),
        'Reviewed the implementation already included upstream.',
      );
      await tester.tap(find.byType(CheckboxListTile));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm completion'));
      await tester.pumpAndSettle();
      expect(submitted!['inspected'], isTrue);
      expect(submitted!['head_sha'], 'b' * 40);
      expect(submitted!['upstream_sha'], 'a' * 40);
      expect(submitted!['outcome'], 'incorporated_elsewhere');
      expect(
        submitted!['reason'],
        'Reviewed the implementation already included upstream.',
      );
      expect(find.text('Done \u00b7 repo'), findsOneWidget);
      expect(find.text('Done: incorporated elsewhere'), findsOneWidget);
      expect(
        find.text(
          'No verified required checks for this candidate at completion. Done is not validation.',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('Candidate: ${'b' * 40}'), findsOneWidget);
      expect(find.text('Mark task done'), findsNothing);
      expect(find.text('Validate latest commit'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
  testWidgets(
    'group review creates proposals and accepts a draft without launching it',
    (tester) async {
      tester.view.physicalSize = const Size(390, 1400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final task = <String, dynamic>{
        'id': 'task',
        'title': 'Open work',
        'state': 'review_ready',
        'head_sha': 'b' * 40,
        'repository': 'repo',
        'run_id': 'run',
        'organization_id': 'org',
      };
      final posts = <String>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.method == 'POST') posts.add(request.url.path);
          if (request.url.path.endsWith('/organizations/directory'))
            return http.Response(
              '{"groups":[{"id":"review","organization_id":"org","name":"Project Review"}]}',
              200,
            );
          if (request.url.path.endsWith('/tasks/discuss'))
            task['meeting_results'] = [
              {
                'job_id': 'meeting',
                'group_id': 'review',
                'group_name': 'Project Review',
                'state': 'artifact_ready',
                'result': 'Review found one follow-up.',
                'proposals': [
                  {
                    'key': 'proposal',
                    'title': 'Add test coverage',
                    'description': 'Cover the remaining filter case.',
                    'profile_id': 'worker',
                    'assignee': 'Maya',
                  },
                ],
              },
            ];
          if (request.url.path.endsWith('/tasks/proposal'))
            task['follow_up_tasks'] = {'proposal': 'new-draft'};
          return http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks')
                  ? {
                      'tasks': [task],
                      'github': {},
                    }
                  : task,
            ),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      connection.operator.value = {'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: TasksPage(taskId: 'task')),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Discuss with group'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Start group review'));
      await tester.pumpAndSettle();
      expect(posts, contains('/api/tasks/review_policy'));
      expect(posts, contains('/api/tasks/discuss'));
      await tester.tap(find.text('Activity & agents'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Project Review review'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Create draft'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Create draft'));
      await tester.pumpAndSettle();
      expect(posts, contains('/api/tasks/proposal'));
      expect(posts.any((p) => p.endsWith('/launch')), isFalse);
      expect(find.textContaining('Assigned: Maya'), findsOneWidget);
      expect(find.text('Draft created'), findsOneWidget);
      expect(find.text('Open follow-up draft'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
  testWidgets(
    'queued assignment shows its position and cancels through the queue action',
    (tester) async {
      final task = <String, dynamic>{
        'id': 'task',
        'title': 'Next task',
        'state': 'draft',
        'repository': 'repo',
        'organization_id': 'org',
        'assigned_agent': {'name': 'Nora'},
        'assignment': {
          'state': 'queued',
          'position': 2,
          'profile_id': 'worker',
        },
        'blocking_execution': {
          'run_id': 'run',
          'state': 'persona_sent',
          'created_at': 'today',
          'task_id': null,
          'task_title': '',
          'handoff_ready': false,
        },
      };
      final posts = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.method == 'POST') {
            posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
          }
          return http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks')
                  ? {
                      'tasks': [task],
                      'github': {},
                    }
                  : task,
            ),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      connection.operator.value = {'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: TasksPage(taskId: 'task')),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Queued \u00b7 repo'), findsOneWidget);
      expect(find.textContaining('Queued for this agent'), findsOneWidget);
      expect(find.textContaining('order 2'), findsOneWidget);
      expect(find.text("Waiting on: Nora's active session"), findsOneWidget);
      expect(
        find.textContaining('Session run \u00b7 persona_sent'),
        findsOneWidget,
      );
      expect(find.textContaining('inspect it in Org chart'), findsOneWidget);
      await tester.tap(find.text('Change queue order'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Later'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save queue order'));
      await tester.pumpAndSettle();
      expect(posts.single['mode'], 'move');
      expect(posts.single['position'], 3);
      await tester.tap(find.text('Cancel queued assignment'));
      await tester.pumpAndSettle();
      expect(posts.last['mode'], 'cancel');
      expect(posts.last['task_id'], 'task');
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
  testWidgets(
    'a ready startup session offers Start implementation with rotation context',
    (tester) async {
      final task = <String, dynamic>{
        'id': 'task',
        'title': 'Next task',
        'state': 'draft',
        'repository': 'repo',
        'organization_id': 'org',
        'assigned_agent': {'name': 'Nora'},
        'blocking_execution': {
          'run_id': 'run',
          'state': 'persona_sent',
          'created_at': 'today',
          'task_id': null,
          'task_title': '',
          'handoff_ready': false,
        },
        'availability': {
          'state': 'ready',
          'action': 'start',
          'run_id': 'run',
          'detail': 'The agent is ready.',
        },
      };
      final posts = <String>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.method == 'POST') posts.add(request.url.path);
          return http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks')
                  ? {
                      'tasks': [task],
                      'github': {},
                    }
                  : task,
            ),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      connection.operator.value = {'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: TasksPage(taskId: 'task')),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Start implementation'), findsOneWidget);
      expect(
        find.text(
          'Nora is ready. Starting this task archives the startup conversation and opens a dedicated task execution.',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('Blocked by'), findsNothing);
      await tester.tap(find.text('Start implementation'));
      await tester.pumpAndSettle();
      expect(posts.single, endsWith('/tasks/launch'));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
  testWidgets(
    'general session archival requires confirmation and pins the inspected run',
    (tester) async {
      final task = <String, dynamic>{
        'id': 'task',
        'title': 'Task',
        'state': 'draft',
        'repository': 'repo',
        'organization_id': 'org',
        'blocking_execution': {
          'run_id': 'general',
          'state': 'persona_sent',
          'task_id': null,
        },
        'availability': {
          'state': 'attention',
          'action': 'archive_general',
          'run_id': 'general',
          'detail': 'Inspect general session',
        },
      };
      final posts = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.method == 'POST')
            posts.add(jsonDecode(request.body) as Map<String, dynamic>);
          return http.Response(
            jsonEncode(
              request.url.path.endsWith('/tasks')
                  ? {
                      'tasks': [task],
                      'github': {},
                    }
                  : task,
            ),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      connection.operator.value = {'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: TasksPage(taskId: 'task')),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Archive inspected session and start'));
      await tester.pumpAndSettle();
      expect(posts, isEmpty);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(posts, isEmpty);
      await tester.tap(find.text('Archive inspected session and start'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('I inspected it; archive and start'));
      await tester.pumpAndSettle();
      expect(posts.single['inspected_general_session'], 'general');
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
  testWidgets('a busy agent offers a precise blocker and the queue action', (
    tester,
  ) async {
    final task = <String, dynamic>{
      'id': 'task',
      'title': 'Next task',
      'state': 'draft',
      'repository': 'repo',
      'organization_id': 'org',
      'blocking_execution': {
        'run_id': 'run',
        'task_id': 'other',
        'task_title': 'Open work',
        'handoff_ready': false,
      },
    };
    final posts = <Map<String, dynamic>>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.method == 'POST') {
          posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
        }
        return http.Response(
          jsonEncode(
            request.url.path.endsWith('/tasks')
                ? {
                    'tasks': [task],
                    'github': {},
                  }
                : task,
          ),
          200,
        );
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'role': 'admin'};
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: TasksPage(taskId: 'task')),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Blocked by: Open work'), findsOneWidget);
    expect(find.text('Start implementation'), findsNothing);
    await tester.tap(find.text('Queue for this agent'));
    await tester.pumpAndSettle();
    expect(posts.single['mode'], 'queue');
    expect(posts.single['task_id'], 'task');
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });
  testWidgets('a verified finished blocker offers hand off and start', (
    tester,
  ) async {
    final task = <String, dynamic>{
      'id': 'task',
      'title': 'Next task',
      'state': 'draft',
      'repository': 'repo',
      'organization_id': 'org',
      'blocking_execution': {
        'run_id': 'run',
        'task_id': 'other',
        'task_title': 'Open work',
        'handoff_ready': true,
      },
    };
    final posts = <String>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.method == 'POST') posts.add(request.url.path);
        return http.Response(
          jsonEncode(
            request.url.path.endsWith('/tasks')
                ? {
                    'tasks': [task],
                    'github': {},
                  }
                : task,
          ),
          200,
        );
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'role': 'admin'};
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: TasksPage(taskId: 'task')),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Verify handoff and start'), findsOneWidget);
    expect(find.text('Inspect previous task'), findsOneWidget);
    expect(find.text('Launch task agent'), findsNothing);
    expect(find.text('Queue instead'), findsOneWidget);
    await tester.tap(find.text('Verify handoff and start'));
    await tester.pumpAndSettle();
    expect(posts.single, endsWith('/tasks/launch'));
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });
  testWidgets('the organization follow-up policy is editable per task', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(500, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final task = <String, dynamic>{
      'id': 'task',
      'title': 'Next task',
      'state': 'draft',
      'repository': 'repo',
      'organization_id': 'org',
      'source': {
        'task_id': 'parent',
        'auto_queued': {'at': 'today'},
      },
    };
    final posts = <Map<String, dynamic>>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.method == 'POST') {
          posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
        }
        return http.Response(
          jsonEncode(
            request.url.path.endsWith('/tasks')
                ? {
                    'tasks': [task],
                    'github': {},
                    'automation': {
                      'org': {
                        'auto_queue_proposals': true,
                        'paused': false,
                        'max_per_meeting': 1,
                        'max_open_per_agent': 2,
                        'max_follow_up_depth': 1,
                        'daily_cap': 5,
                      },
                    },
                  }
                : task,
          ),
          200,
        );
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    connection.operator.value = {'role': 'admin'};
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: TasksPage(taskId: 'task')),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.text('Auto-queued from group review of task parent.'),
      findsOneWidget,
    );
    await tester.ensureVisible(find.text('Automation settings'));
    await tester.tap(find.text('Automation settings'));
    await tester.pumpAndSettle();
    expect(find.textContaining('On: 1/meeting'), findsOneWidget);
    await tester.tap(find.text('Edit policy'));
    await tester.pumpAndSettle();
    expect(find.text('Follow-up automation policy'), findsOneWidget);
    await tester.tap(find.text('Save policy'));
    await tester.pumpAndSettle();
    expect(posts.single['organization_id'], 'org');
    expect(posts.single['auto_queue_proposals'], true);
    expect(posts.single['daily_cap'], 5);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });
}
