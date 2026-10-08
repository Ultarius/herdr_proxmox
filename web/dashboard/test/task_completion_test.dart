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
}
