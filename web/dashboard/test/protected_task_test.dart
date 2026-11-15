import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/tasks_page.dart';
import 'helpers/dashboard_connection.dart';

void main() {
  for (final validLink in [true, false]) {
    testWidgets(
      'manual task handoff validates compare link and submits adoption: $validLink',
      (tester) async {
        tester.view.physicalSize = const Size(1280, 1800);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final head = 'b' * 40;
        final task = {
          'id': 'task',
          'title': 'Published protected task',
          'state': 'published',
          'repository': 'repo',
          'github_repository': 'SourceOwner/project',
          'head_sha': head,
          'branch': 'herdr/task-123456abcdef',
          'publish': {'head_sha': head},
          'manual_pr': {
            'state': 'awaiting_manual_pr',
            'message': 'Open the draft PR on GitHub, then check for adoption.',
            'compare_url': validLink
                ? 'https://github.com/SourceOwner/project/compare/main...ForkOwner:herdr%2Ftask-123456abcdef'
                : 'https://evil.example/compare/a',
          },
          'checks_unavailable': {
            'check_runs': {'github_status': 403},
          },
          'pull_preview': {
            'title': 'Copy to GitHub',
            'body': 'Operator PR text is preserved.',
            'head_sha': head,
            'repository': 'SourceOwner/project',
            'publishing_repository': 'ForkOwner/project',
            'head': 'ForkOwner:herdr/task-123456abcdef',
            'base': 'main',
          },
        };
        final posts = <Map<String, dynamic>>[];
        final connection = DashboardBloc(
          client: MockClient((request) async {
            if (request.url.path == '/api/tasks/pull')
              posts.add(Map<String, dynamic>.from(jsonDecode(request.body)));
            return http.Response(
              jsonEncode(
                request.url.path == '/api/tasks'
                    ? {
                        'tasks': [task],
                        'github': {
                          'configured': true,
                          'protected_publication': {
                            'sourceowner/project': {
                              'arrangement': 'manual_adoption',
                              'fork': 'ForkOwner/project',
                            },
                          },
                        },
                      }
                    : request.url.path == '/api/tasks/detail' ||
                          request.url.path == '/api/tasks/pull'
                    ? task
                    : {'workspaces': [], 'agents': []},
              ),
              200,
            );
          }),
        );
        await withDashboardConnection(tester, connection, () async {
          await tester.pumpWidget(
            const MaterialApp(
              home: Scaffold(body: TasksPage(taskId: 'task')),
            ),
          );
          await pumpUntil(
            tester,
            () => find.text('Published protected task').evaluate().isNotEmpty,
          );
          expect(
            find.text('Open GitHub comparison and create draft PR'),
            validLink ? findsOneWidget : findsNothing,
          );
          expect(
            find.textContaining('Delivery remains pending'),
            findsOneWidget,
          );
          await clickText(tester, 'Changes');
          await clickText(tester, 'Check for operator-created PR');
          await pumpUntil(
            tester,
            () => find.byType(AlertDialog).evaluate().isNotEmpty,
          );
          expect(
            find.textContaining('Checking for adoption preserves'),
            findsOneWidget,
          );
          expect(find.text('Create draft PR'), findsNothing);
          final confirm = find.descendant(
            of: find.byType(AlertDialog),
            matching: find.widgetWithText(
              FilledButton,
              'Check for operator-created PR',
            ),
          );
          await tester.ensureVisible(confirm);
          await tester.tap(confirm);
          await pumpUntil(tester, () => posts.isNotEmpty);
          expect(posts.single['head_sha'], head);
          expect(posts.single['reviewed'], isTrue);
          expect(tester.takeException(), isNull);
        });
      },
    );
  }
}
