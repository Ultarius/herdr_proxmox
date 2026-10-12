import 'dart:convert';
import 'package:flutter/gestures.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/agent_console_page.dart';
import 'package:herdr_dashboard/agent_attention.dart';
import 'package:herdr_dashboard/org_chart.dart';
import 'package:herdr_dashboard/routes.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';

void main() {
  final profile = <String, dynamic>{
    'id': 'backend',
    'organization_id': 'org',
    'name': 'Backend Developer',
    'role': 'Backend Developer',
    'runtime': 'opencode',
    'manager_id': '',
  };

  test('agent console routes round trip', () {
    final route = AppCoordinator().parseRouteFromUri(
      Uri.parse('/agents/backend'),
    );
    expect(route, isA<AgentConsoleRoute>());
    expect(route.toUri().path, '/agents/backend');
  });

  testWidgets('chart shows attention and separates left and right click', (
    tester,
  ) async {
    var opened = false;
    var configured = false;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: OrgChart(
            profiles: [profile],
            agents: [
              {'profile_id': 'backend', 'agent_status': 'blocked'},
            ],
            jobs: [],
            onSelect: (_) => opened = true,
            onConfigure: (_) => configured = true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('! requires attention !'), findsOneWidget);
    await tester.tap(find.text('opencode'));
    expect(opened, isTrue);
    expect(configured, isFalse);
    opened = false;
    await tester.tap(find.text('opencode'), buttons: kSecondaryMouseButton);
    expect(configured, isTrue);
    expect(opened, isFalse);
    expect(
      agentRequiresAttention('other', [
        {'profile_id': 'backend', 'agent_status': 'blocked'},
      ]),
      isFalse,
    );
  });

  testWidgets('menu attention label updates and opens the agent console', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1400, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var blocked = true;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/organizations/inspect') {
          return http.Response(
            jsonEncode({
              'output': 'Agent work',
              'status': blocked ? 'blocked' : 'working',
              'console_token': blocked ? 'token' : null,
            }),
            200,
          );
        }
        if (request.url.path.startsWith('/api/organizations')) {
          return http.Response(
            jsonEncode({
              'organizations': [
                {'id': 'org', 'name': 'Engineering'},
              ],
              'profiles': [profile],
              'jobs': [],
              'groups': [],
            }),
            200,
          );
        }
        return http.Response(
          jsonEncode({
            'workspaces': [],
            'agents': [
              {
                'profile_id': 'backend',
                'name': 'backend',
                'pane_id': 'w1:p1',
                'agent_status': blocked ? 'blocked' : 'working',
              },
            ],
          }),
          200,
        );
      }),
    );
    BlocScope.register<DashboardBloc>(
      () => connection,
      lifecycle: BlocLifecycle.permanent,
    );
    final connected = connection.stream.firstWhere(
      (_) => connection.state.connected,
    );
    connection.connect('token');
    await connected;
    final coordinator = AppCoordinator();
    await tester.pumpWidget(MaterialApp.router(routerConfig: coordinator));
    await tester.pumpAndSettle();
    expect(find.text('Backend Developer (requires attention)'), findsOneWidget);
    await tester.tap(find.text('Backend Developer (requires attention)'));
    await tester.pumpAndSettle();
    expect(find.byType(AgentConsolePage), findsOneWidget);
    blocked = false;
    await connection.send(DashboardCommand('refresh', null, true));
    await tester.pumpAndSettle();
    expect(find.text('Backend Developer (requires attention)'), findsNothing);
    expect(find.text('Backend Developer'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    await connection.disconnect();
    coordinator.dispose();
    await BlocScope.reset();
  });

  testWidgets(
    'console polls work, responds to reviewed prompts, and disables responses when working',
    (tester) async {
      tester.view.physicalSize = const Size(1200, 1200);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      var status = 'blocked';
      var output = 'Access requested: /project/private';
      Map<String, dynamic>? sent;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          final path = request.url.path;
          if (path == '/api/organizations/inspect') {
            expect(jsonDecode(request.body)['profile_id'], 'backend');
            return http.Response(
              jsonEncode({
                'status': status,
                'output': output,
                'console_token': status == 'blocked' ? 'reviewed-prompt' : null,
              }),
              200,
            );
          }
          if (path == '/api/organizations/input') {
            sent = Map<String, dynamic>.from(jsonDecode(request.body));
            return http.Response('{"id":"decision"}', 200);
          }
          if (path.startsWith('/api/organizations'))
            return http.Response(
              jsonEncode({
                'organizations': [
                  {'id': 'org', 'name': 'Engineering'},
                ],
                'profiles': [profile],
                'jobs': [
                  if (sent != null)
                    {
                      'id': 'decision',
                      'profile_id': 'backend',
                      'kind': 'input',
                      'state': 'input_sent',
                    },
                ],
              }),
              200,
            );
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(
        () => connection,
        lifecycle: BlocLifecycle.permanent,
      );
      final connected = connection.stream.firstWhere(
        (_) => connection.state.connected,
      );
      connection.connect('token');
      await connected;
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: AgentConsolePage(id: 'backend')),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Backend Developer console'), findsOneWidget);
      expect(find.text(output), findsOneWidget);
      expect(find.text('Requires attention'), findsOneWidget);
      await tester.tap(find.text('Confirm selected Allow / Reject'));
      await tester.pumpAndSettle();
      expect(sent?['key'], 'enter');
      expect(sent?['console_token'], 'reviewed-prompt');
      expect(find.text('Terminal response: input_sent'), findsOneWidget);
      output = List.generate(
        200,
        (index) => 'Build output line $index',
      ).join('\n');
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();
      final viewport = find.byKey(const ValueKey('agent-console-viewport'));
      final consoleScrollable = find
          .descendant(of: viewport, matching: find.byType(Scrollable))
          .first;
      expect(tester.getRect(viewport).bottom, lessThanOrEqualTo(1200));
      expect(
        tester
            .state<ScrollableState>(consoleScrollable)
            .position
            .maxScrollExtent,
        greaterThan(0),
      );
      await tester.drag(consoleScrollable, const Offset(0, -250));
      await tester.pumpAndSettle();
      final scrollOffset = tester
          .state<ScrollableState>(consoleScrollable)
          .position
          .pixels;
      expect(scrollOffset, greaterThan(0));
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();
      expect(
        tester.state<ScrollableState>(consoleScrollable).position.pixels,
        scrollOffset,
      );
      // A narrow, short viewport also contains the terminal and permission panel.
      tester.view.physicalSize = const Size(390, 600);
      await tester.pumpAndSettle();
      expect(tester.getRect(viewport).bottom, lessThanOrEqualTo(600));
      expect(tester.getSize(viewport).height, greaterThan(0));
      expect(tester.takeException(), isNull);
      status = 'working';
      output = 'Running build';
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();
      expect(find.text(output), findsOneWidget);
      expect(find.text('Requires attention'), findsNothing);
      await connection.disconnect();
      await tester.pumpAndSettle();
      expect(find.text('Running build'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
      await BlocScope.endAll();
    },
  );
}
