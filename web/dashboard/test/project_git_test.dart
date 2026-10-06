import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/project_git_panel.dart';

void main() {
  testWidgets('checkout cards expose counts, changes, diff and file navigation', (
    tester,
  ) async {
    var fetches = 0;
    Map<String, dynamic>? integrationRequest;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/organizations/chat')) {
          integrationRequest = Map<String, dynamic>.from(
            jsonDecode(request.body),
          );
          return http.Response('{}', 200);
        }
        if (request.url.path.endsWith('/git')) {
          final body = jsonDecode(request.body);
          if (body['action'] == 'fetch') fetches++;
          return http.Response(
            jsonEncode(
              body['file'] != null
                  ? {'diff': '+updated', 'truncated': false}
                  : {
                      'repository': true,
                      'worktrees': [
                        {
                          'path': 'repo',
                          'cwd': '/home/herdr/projects/repo',
                          'branch': 'main',
                          'commit': 'abc123',
                          'base': 'origin/main',
                          'base_behind': 2,
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
        return http.Response(
          '{"workspaces":[],"agents":[{"profile_id":"p1","display_name":"Max","cwd":"/home/herdr/projects/repo","agent_status":"idle"}]}',
          200,
        );
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    connection.organizationDirectory.value = {
      'profiles': [
        {'id': 'p1', 'organization_id': 'org1'},
      ],
    };
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
    await tester.tap(find.text('Fetch remote updates'));
    await tester.pumpAndSettle();
    expect(fetches, 1);
    expect(find.text('Copy merge instructions'), findsOneWidget);
    expect(find.text('origin/main · 0 ahead · 1 behind'), findsOneWidget);
    await tester.ensureVisible(find.text('Ask Max to integrate main'));
    await tester.tap(find.text('Ask Max to integrate main'));
    await tester.pumpAndSettle();
    expect(integrationRequest?['profile_id'], 'p1');
    expect(integrationRequest?['organization_id'], 'org1');
    ScaffoldMessenger.of(
      tester.element(find.byType(ProjectGitPanel)),
    ).hideCurrentSnackBar();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('1 changed files'));
    await tester.tap(find.text('1 changed files'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('file.txt'));
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
  });

  testWidgets('fetch failures and unreadable checkouts stay visible', (
    tester,
  ) async {
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/git')) {
          final body = jsonDecode(request.body);
          if (body['action'] == 'fetch') {
            return http.Response(
              jsonEncode({
                'repository': true,
                'last_fetch': null,
                'fetch_error':
                    'Remote fetch failed. Check container Git credentials and connectivity.',
                'worktrees': [
                  {
                    'path': 'agent',
                    'cwd': '/home/herdr/projects/agent',
                    'error':
                        'Git information is unavailable for this checkout.',
                  },
                ],
              }),
              200,
            );
          }
          return http.Response(
            jsonEncode({'repository': true, 'worktrees': []}),
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
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProjectGitPanel(path: 'repo', onOpen: (_) {}),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Fetch remote updates'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Fetch failed:'), findsOneWidget);
    expect(
      find.text('Git information is unavailable for this checkout.'),
      findsOneWidget,
    );
    expect(find.text('agent'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    await BlocScope.reset();
  });
}
