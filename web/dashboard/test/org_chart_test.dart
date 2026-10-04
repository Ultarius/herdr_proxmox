import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/org_chart.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  testWidgets('group topics are distinct chart cards and open their page', (
    tester,
  ) async {
    Map<String, dynamic>? opened;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: OrgChart(
            profiles: [
              {
                'id': 'facilitator',
                'group_id': 'standup',
                'name': 'Standup',
                'manager_id': '',
                'role': 'Group facilitator',
                'runtime': 'opencode',
              },
            ],
            groups: [
              {
                'id': 'standup',
                'name': 'Standup',
                'description': 'Share work',
                'members': ['max', 'olaf'],
              },
            ],
            agents: [],
            jobs: [],
            onSelect: (_) => fail('Group opened agent editing'),
            onSelectGroup: (g) => opened = g,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Group facilitator'), findsNothing);
    expect(find.text('GROUPS / TOPICS'), findsOneWidget);
    expect(find.text('Group / topic · 2 members'), findsOneWidget);
    await tester.tap(find.text('# Standup'));
    expect(opened?['id'], 'standup');
  });

  test(
    'chart places managers above reports and handles disconnected cycles',
    () {
      final layout = ChartLayout([
        {'id': 'ceo', 'manager_id': ''},
        {'id': 'a', 'manager_id': 'ceo'},
        {'id': 'b', 'manager_id': 'ceo'},
        {'id': 'cycle1', 'manager_id': 'cycle2'},
        {'id': 'cycle2', 'manager_id': 'cycle1'},
      ]);
      expect(layout.positions.length, 5);
      expect(layout.positions['ceo']!.dy, lessThan(layout.positions['a']!.dy));
      expect(
        layout.positions['ceo']!.dx,
        (layout.positions['a']!.dx + layout.positions['b']!.dx) / 2,
      );
      expect(layout.positions.values.toSet().length, 5);
    },
  );

  testWidgets(
    'organization rail changes the chart and preserves selection across pages',
    (tester) async {
      tester.view.physicalSize = const Size(1280, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/organizations') {
            return http.Response(
              jsonEncode({
                'organizations': [
                  {
                    'id': 'one',
                    'name': 'Engineering',
                    'purpose': 'Build',
                    'instructions': '',
                  },
                  {
                    'id': 'two',
                    'name': 'Design',
                    'purpose': 'Design',
                    'instructions': '',
                  },
                ],
                'profiles': [
                  {
                    'id': 'lead1',
                    'organization_id': 'one',
                    'name': 'Maya',
                    'role': 'Lead',
                    'runtime': 'codex',
                    'manager_id': '',
                  },
                  {
                    'id': 'lead2',
                    'organization_id': 'two',
                    'name': 'Sam',
                    'role': 'Designer',
                    'runtime': 'claude',
                    'manager_id': '',
                  },
                ],
                'jobs': [],
              }),
              200,
            );
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(
        () => connection,
        lifecycle: BlocLifecycle.permanent,
      );
      final coordinator = AppCoordinator();
      await tester.pumpWidget(MaterialApp.router(routerConfig: coordinator));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'token');
      await tester.tap(find.text('Connect'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Design'));
      await tester.pumpAndSettle();
      expect(connection.selectedOrganization.value, 'two');
      expect(
        tester.widget<OrgChart>(find.byType(OrgChart)).profiles.single['id'],
        'lead2',
      );
      await tester.tap(find.text('Dashboard'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Org chart'));
      await tester.pumpAndSettle();
      expect(
        tester.widget<OrgChart>(find.byType(OrgChart)).profiles.single['id'],
        'lead2',
      );
      await tester.tap(find.byTooltip('Engineering'));
      await tester.pumpAndSettle();
      expect(
        tester.widget<OrgChart>(find.byType(OrgChart)).profiles.single['id'],
        'lead1',
      );
      expect(tester.takeException(), isNull);
      await connection.disconnect();
      await tester.pumpWidget(const SizedBox());
      coordinator.dispose();
      await BlocScope.reset();
      await tester.pump(const Duration(seconds: 1));
    },
  );
}
