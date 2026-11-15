import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/routes.dart';
import 'package:herdr_dashboard/ventures_page.dart';

import 'helpers/dashboard_connection.dart';

void main() {
  test('ventures route round-trips', () {
    final route = AppCoordinator().parseRouteFromUri(Uri.parse('/ventures'));
    expect(route, isA<VenturesRoute>());
    expect(route.toUri().path, '/ventures');
  });

  testWidgets('read-only ideas and studio sections render', (tester) async {
    tester.view.physicalSize = const Size(390, 2200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path != '/api/ventures') {
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }
        return http.Response(
          jsonEncode({
            'organizations': [
              {'id': 'org', 'name': 'Team'},
            ],
            'ventures': [
              {
                'id': 'a' * 32,
                'organization_id': 'org',
                'name': 'Settlement Desk',
                'state': 'running',
              },
            ],
            'reviews': [],
            'candidates': [
              {
                'id': 'b' * 32,
                'organization_id': 'org',
                'name': 'Carrier Desk',
                'state': 'proposed',
                'thesis': 'Invoice reconciliation for freight forwarders.',
              },
            ],
            'studios': [
              {
                'organization_id': 'org',
                'policy': {
                  'studio_portfolio_cadence': 'weekly',
                  'studio_scout_cadence': 'weekly',
                },
                'studio': {
                  'group_id': 'studio',
                  'notebook': 'ventures/studio-org',
                },
                'runs': [],
                'budget': {'daily_meetings': 0, 'projected_agent_sessions': 0},
              },
            ],
            'studio': {
              'organization_id': 'org',
              'policy': {
                'studio_portfolio_cadence': 'weekly',
                'studio_scout_cadence': 'weekly',
              },
              'studio': {
                'group_id': 'studio',
                'notebook': 'ventures/studio-org',
              },
              'runs': [],
              'budget': {'daily_meetings': 0, 'projected_agent_sessions': 0},
            },
            'groups': [],
            'limits': {'max_active_ventures': 3},
          }),
          200,
        );
      }),
    );
    await withDashboardConnection(tester, connection, () async {
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      connection.operator.value = {'role': 'admin', 'name': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: VenturesPage())),
      );
      await pumpUntil(
        tester,
        () => find.text('Venture ideas').evaluate().isNotEmpty,
      );
      expect(find.textContaining('Carrier Desk - proposed'), findsOneWidget);
      expect(find.text('Venture Studio'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
  testWidgets('portfolio renders and a tick posts one run action', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final posts = <Map<String, dynamic>>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/ventures') {
          if (request.method == 'POST') {
            posts.add(jsonDecode(request.body) as Map<String, dynamic>);
            return http.Response('{"id":"t"}', 200);
          }
          return http.Response(
            jsonEncode({
              'organizations': [
                {'id': 'org', 'name': 'Team'},
              ],
              'ventures': [
                {
                  'id': 'a' * 32,
                  'organization_id': 'org',
                  'name': 'Settlement Desk',
                  'state': 'running',
                  'score': 61,
                  'qualifying_days': 1,
                  'required_days': 3,
                  'survival_threshold': 60,
                },
              ],
              'reviews': [
                {
                  'simulated_date': '2026-11-15',
                  'kind': 'strategy',
                  'state': 'ready',
                },
              ],
              'candidates': [
                {
                  'id': 'b' * 32,
                  'organization_id': 'org',
                  'name': 'Carrier Desk',
                  'state': 'proposed',
                  'thesis': 'Invoice reconciliation for freight forwarders.',
                },
              ],
              'studios': [
                {
                  'organization_id': 'org',
                  'policy': {
                    'studio_portfolio_cadence': 'weekly',
                    'studio_scout_cadence': 'weekly',
                  },
                  'studio': {
                    'group_id': 'studio',
                    'notebook': 'ventures/studio-org',
                  },
                  'runs': [],
                  'budget': {
                    'daily_meetings': 0,
                    'projected_agent_sessions': 0,
                  },
                },
              ],
              'studio': {
                'organization_id': 'org',
                'policy': {
                  'studio_portfolio_cadence': 'weekly',
                  'studio_scout_cadence': 'weekly',
                },
                'studio': {
                  'group_id': 'studio',
                  'notebook': 'ventures/studio-org',
                },
                'runs': [],
                'budget': {'daily_meetings': 0, 'projected_agent_sessions': 0},
              },
              'groups': [],
              'limits': {'max_active_ventures': 3},
            }),
            200,
          );
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    await withDashboardConnection(tester, connection, () async {
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      connection.operator.value = {'role': 'admin', 'name': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: VenturesPage())),
      );
      await pumpUntil(
        tester,
        () => find.text('Settlement Desk').evaluate().isNotEmpty,
      );
      expect(find.textContaining('score 61'), findsOneWidget);
      expect(find.textContaining('1/3 days at ≥ 60'), findsOneWidget);
      await clickText(tester, 'Run tick now');
      await pumpUntil(tester, () => posts.isNotEmpty);
      expect(posts.single['mode'], 'run');
      expect(posts.single['venture_id'], 'a' * 32);
      expect(tester.takeException(), isNull);
    });
  });
}
