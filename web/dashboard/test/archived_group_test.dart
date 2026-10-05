import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/group_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  testWidgets('archived groups expose artifacts and disable mutations', (
    tester,
  ) async {
    final data = {
      'group': {
        'id': 'old',
        'organization_id': 'org',
        'name': 'Archived review',
        'description': 'Saved purpose',
        'members': ['max', 'iris'],
        'removed_at': '2026-10-01',
      },
      'groups': [],
      'profiles': [],
      'member_states': {},
      'jobs': [
        {
          'id': 'discussion',
          'organization_id': 'org',
          'group_id': 'old',
          'kind': 'discussion',
          'state': 'artifact_ready',
          'result': 'Saved memo',
          'contributions': [],
        },
      ],
    };
    final connection = DashboardBloc(
      client: MockClient(
        (request) async => http.Response(
          request.url.path.endsWith('/history')
              ? jsonEncode(data)
              : '{"workspaces":[],"agents":[]}',
          200,
        ),
      ),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    connection.connect('token');
    await connection.stream.firstWhere(
      (_) => connection.state.refreshed != null,
    );
    final coordinator = AppCoordinator();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: GroupPage(id: 'old', coordinator: coordinator),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Start discussion'),
          )
          .onPressed,
      isNull,
    );
    expect(
      tester
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Edit group'),
          )
          .onPressed,
      isNull,
    );
    expect(find.text('Send to group'), findsNothing);
    await tester.tap(find.text('Artifacts'));
    await tester.pumpAndSettle();
    expect(find.text('Saved memo'), findsOneWidget);
    expect(find.text('Download discussion.json'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    BlocScope.endAll();
    coordinator.dispose();
  });
}
