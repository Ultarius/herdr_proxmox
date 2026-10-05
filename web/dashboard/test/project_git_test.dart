import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/project_git_panel.dart';

void main() {
  testWidgets(
    'checkout cards expose counts, changes, diff and file navigation',
    (tester) async {
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/git')) {
            final body = jsonDecode(request.body);
            return http.Response(
              jsonEncode(
                body['file'] != null
                    ? {'diff': '+updated', 'truncated': false}
                    : {
                        'repository': true,
                        'worktrees': [
                          {
                            'path': 'repo',
                            'branch': 'main',
                            'commit': 'abc123',
                            'upstream': 'origin/main',
                            'ahead': 0,
                            'behind': 1,
                            'counts': {'added': 0, 'deleted': 0, 'modified': 1},
                            'changes': [
                              {
                                'path': 'file.txt',
                                'kind': 'modified',
                                'status': ' M',
                              },
                            ],
                          },
                        ],
                      },
              ),
              200,
            );
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      final selected = <String>[];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ProjectGitPanel(path: 'repo', onOpen: selected.add),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('1 modified'), findsOneWidget);
      expect(find.text('origin/main · 0 ahead · 1 behind'), findsOneWidget);
      await tester.tap(find.text('1 changed files'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('file.txt'));
      await tester.pumpAndSettle();
      expect(find.text('+updated'), findsOneWidget);
      await tester.tap(find.text('Open file'));
      await tester.pumpAndSettle();
      expect(selected, ['repo/file.txt']);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
}
