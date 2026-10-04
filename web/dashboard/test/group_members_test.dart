import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/group_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  testWidgets('Members shows offline agents and launches from its switch', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    bool launched = false;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/launch')) {
          expect(jsonDecode(request.body)['profile_id'], 'iris');
          launched = true;
          return http.Response('{"id":"launch"}', 200);
        }
        if (request.url.path.endsWith('/organizations'))
          return http.Response(
            jsonEncode({
              'organizations': [],
              'profiles': [
                for (final name in ['max', 'iris'])
                  {
                    'id': name,
                    'organization_id': 'org',
                    'name': name,
                    'role': 'Developer',
                  },
              ],
              'groups': [
                {
                  'id': 'group',
                  'organization_id': 'org',
                  'name': 'Planning',
                  'description': 'Review',
                  'members': ['max', 'iris'],
                },
              ],
              'jobs': [],
              'member_states': {
                'max': {'active': true, 'status': 'idle'},
                'iris': {
                  'active': launched,
                  'status': launched ? 'idle' : 'off',
                },
              },
            }),
            200,
          );
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
          body: GroupPage(id: 'group', coordinator: AppCoordinator()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Members not active: iris'), findsOneWidget);
    await tester.tap(find.text('Members'));
    await tester.pumpAndSettle();
    final toggles = tester.widgetList<Switch>(find.byType(Switch)).toList();
    expect(toggles.map((s) => s.value), [true, false]);
    await tester.ensureVisible(find.byType(Switch).last);
    await tester.tap(find.byType(Switch).last);
    await tester.pumpAndSettle();
    expect(launched, isTrue);
    expect(
      tester.widgetList<Switch>(find.byType(Switch)).every((s) => s.value),
      isTrue,
    );
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    BlocScope.endAll();
    await tester.pump();
  });
}
