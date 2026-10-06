import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/integration_board.dart';
import 'package:herdr_dashboard/project_git_panel.dart';

void main() {
  testWidgets(
    'phone recovery requires inspection and submits validation only',
    (tester) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final calls = <Map>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/integration/recover')) {
            calls.add(jsonDecode(request.body) as Map);
            return http.Response('{}', 200);
          }
          if (request.url.path.endsWith('/integration')) {
            return http.Response(
              jsonEncode({
                'coordination': {
                  'configurations': [],
                  'events': [
                    {
                      'id': 'event',
                      'repository': 'repo',
                      'name': 'Max',
                      'state': 'needs_attention',
                      'path': 'repo/worker',
                      'target': 'abc',
                      'job_id': 'interrupted-job',
                    },
                  ],
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
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: IntegrationBoard(repository: 'repo'),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Integration coordinator'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Max · needs_attention'));
      await tester.tap(find.text('Max · needs_attention'));
      await tester.pumpAndSettle();
      expect(find.text('Recover saved result'), findsOneWidget);
      await tester.tap(find.text('Continue validation only'));
      await tester.pumpAndSettle();
      expect(calls, isEmpty);
      await tester.tap(find.text('I inspected the conversation'));
      await tester.pumpAndSettle();
      expect(calls.single, {
        'id': 'event',
        'job_id': 'interrupted-job',
        'mode': 'validate',
        'inspected': true,
      });
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );

  testWidgets(
    'base update requires exact target review and exposes audit recovery',
    (tester) async {
      final head = List.filled(40, 'a').join(),
          target = List.filled(40, 'b').join();
      final updates = <Map>[];
      final plan = {
        'path': 'repo',
        'branch': 'trunk',
        'base': 'refs/remotes/origin/trunk',
        'head': head,
        'target': target,
      };
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/projects/git')) {
            final body = jsonDecode(request.body) as Map;
            if (body['action'] == 'update_base') {
              updates.add(body);
              return http.Response('{"state":"complete"}', 200);
            }
            return http.Response(
              jsonEncode({
                'repository': true,
                'base_update': plan,
                'base_updates': updates.isEmpty
                    ? []
                    : [
                        {
                          'selection': {'branch': 'trunk'},
                          'state': 'complete',
                          'actor': 'damien',
                          'recovery_ref': 'refs/herdr/base-updates/test',
                        },
                      ],
                'worktrees': [
                  {
                    'path': 'repo',
                    'cwd': '/projects/repo',
                    'branch': 'trunk',
                    'commit': 'aaaaaaa',
                    'base': 'origin/trunk',
                    'counts': {'added': 0, 'deleted': 0, 'modified': 0},
                    'changes': [],
                  },
                ],
              }),
              200,
            );
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ProjectGitPanel(path: 'repo', onOpen: (_) {}),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Update base branch'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Review exact target'));
      await tester.pumpAndSettle();
      expect(find.text('Advance trunk?'), findsOneWidget);
      expect(find.textContaining(target), findsOneWidget);
      expect(updates, isEmpty);
      await tester.tap(find.widgetWithText(FilledButton, 'Update base branch'));
      await tester.pumpAndSettle();
      expect(updates.single['branch'], 'trunk');
      expect(updates.single['target'], target);
      expect(updates.single['head'], head);
      expect(updates.single['request_id'], isNotEmpty);
      await tester.ensureVisible(find.text('Base update audit & recovery'));
      await tester.tap(find.text('Base update audit & recovery'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('refs/herdr/base-updates/test'),
        findsOneWidget,
      );
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
}
