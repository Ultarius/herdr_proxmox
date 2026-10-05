import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/clone_project_card.dart';

void main() {
  testWidgets('clone status is recovered after navigating away', (
    tester,
  ) async {
    final jobs = <Map<String, dynamic>>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/clone')) {
          jobs.add({
            'id': 'clone',
            'folder': 'repo',
            'cwd': '/home/herdr/projects/repo',
            'state': 'queued',
            'error': '',
          });
          return http.Response(jsonEncode(jobs.first), 200);
        }
        if (request.url.path.endsWith('/jobs'))
          return http.Response(jsonEncode({'jobs': jobs}), 200);
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    final selected = <String>[];
    Widget card() => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: CloneProjectCard(onCloned: selected.add),
        ),
      ),
    );
    await tester.pumpWidget(card());
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byType(TextField).first,
      'https://example.com/repo.git',
    );
    await tester.enterText(find.byType(TextField).last, 'repo');
    await tester.ensureVisible(find.text('Clone repository'));
    await tester.tap(find.text('Clone repository'));
    await tester.pumpAndSettle();
    expect(find.text('repo · queued'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    jobs.first['state'] = 'completed';
    await tester.pumpWidget(card());
    await tester.pumpAndSettle();
    expect(find.text('repo · completed'), findsOneWidget);
    expect(selected, ['/home/herdr/projects/repo']);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    BlocScope.endAll();
  });
}
