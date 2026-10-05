import 'dart:ui' show Tristate;
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/group_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  testWidgets('sidebar groups open round posts and their matching artifacts', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final data = {
      'organizations': [
        {
          'id': 'org',
          'name': 'Engineering',
          'purpose': 'Build',
          'instructions': '',
        },
      ],
      'profiles': [
        {
          'id': 'max',
          'organization_id': 'org',
          'name': 'Max',
          'role': 'Developer',
        },
      ],
      'groups': [
        {
          'id': 'group1',
          'organization_id': 'org',
          'name': 'Deployment review',
          'description': 'Choose deployment options',
          'members': ['max', 'iris'],
        },
        {
          'id': 'group2',
          'organization_id': 'org',
          'name': 'Design review',
          'description': 'Review the design',
          'members': ['max', 'iris'],
        },
      ],
      'jobs': [
        {
          'id': 'run1',
          'group_id': 'group1',
          'organization_id': 'org',
          'kind': 'discussion',
          'state': 'artifact_ready',
          'created_at': '2026-10-04T10:00:00Z',
          'group': {'description': 'Original discussion topic'},
          'result': '# Action plan\nTest recovery before rollout.',
          'contributions': [
            {'round': 1, 'name': 'Max', 'content': 'Use a staged deployment.'},
            {'round': 2, 'name': 'Max', 'content': 'Add a recovery rehearsal.'},
          ],
        },
      ],
    };
    final sent = <Map<String, dynamic>>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/organizations/history')
          return http.Response(jsonEncode(data), 200);
        if (request.method == 'POST') {
          sent.add(jsonDecode(request.body) as Map<String, dynamic>);
          return http.Response('{"id":"message-job"}', 200);
        }
        return http.Response(
          [
                '/api/organizations',
                '/api/organizations/history',
                '/api/organizations/directory',
                '/api/organizations/state',
                '/api/organizations/activity',
              ].contains(request.url.path)
              ? jsonEncode(data)
              : '{"workspaces":[],"agents":[]}',
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
    expect(
      coordinator.parseRouteFromUri(Uri.parse('/groups/group1')),
      isA<GroupRoute>(),
    );
    await tester.pumpWidget(MaterialApp.router(routerConfig: coordinator));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Deployment review'));
    await tester.pumpAndSettle();
    expect(find.byType(GroupPage), findsOneWidget);
    expect(find.text('# Deployment review'), findsOneWidget);
    await tester.binding.setSurfaceSize(const Size(600, 800));
    await tester.pumpAndSettle();
    final shell = Scaffold.of(
      tester.element(find.byTooltip('Open navigation')),
    );
    await tester.tap(find.byTooltip('Open navigation'));
    await tester.pumpAndSettle();
    expect(shell.isDrawerOpen, isTrue);
    await tester.tap(find.text('Deployment review'));
    await tester.pumpAndSettle();
    expect(shell.isDrawerOpen, isFalse);
    await tester.binding.setSurfaceSize(null);
    await tester.pumpAndSettle();
    final semantics = tester.ensureSemantics();
    await tester.pumpAndSettle();
    expect(
      tester
              .getSemantics(find.byKey(const ValueKey('group-tab-posts')))
              .getSemanticsData()
              .flagsCollection
              .isSelected ==
          Tristate.isTrue,
      isTrue,
    );
    await tester.tap(find.text('About'));
    await tester.pumpAndSettle();
    expect(
      tester
              .getSemantics(find.byKey(const ValueKey('group-tab-about')))
              .getSemanticsData()
              .flagsCollection
              .isSelected ==
          Tristate.isTrue,
      isTrue,
    );
    expect(
      tester
              .getSemantics(find.byKey(const ValueKey('group-tab-posts')))
              .getSemanticsData()
              .flagsCollection
              .isSelected ==
          Tristate.isTrue,
      isFalse,
    );
    await tester.tap(find.text('Posts'));
    await tester.pumpAndSettle();
    semantics.dispose();
    await tester.enterText(
      find.byType(TextField),
      'Ask Iris to review recovery',
    );
    await tester.tap(find.text('Send to group'));
    await tester.pumpAndSettle();
    expect(sent.single['group_id'], 'group1');
    expect(sent.single['prompt'], 'Ask Iris to review recovery');
    expect(find.text('Round 1 · Proposals'), findsOneWidget);
    expect(find.text('Round 2 · Review and refinement'), findsOneWidget);
    await tester.ensureVisible(find.text('Discussion context'));
    await tester.tap(find.text('Discussion context'));
    await tester.pumpAndSettle();
    expect(find.text('Original discussion topic'), findsOneWidget);
    await tester.ensureVisible(find.text('View resulting artifact').first);
    await tester.tap(find.text('View resulting artifact').first);
    await tester.pumpAndSettle();
    expect(
      find.text('# Action plan\nTest recovery before rollout.'),
      findsOneWidget,
    );
    expect(find.text('Show all artifacts'), findsOneWidget);
    await tester.tap(find.text('Members'));
    await tester.pumpAndSettle();
    expect(find.text('Developer · unknown'), findsOneWidget);
    await tester.tap(find.text('Design review'));
    await tester.pumpAndSettle();
    expect(tester.widget<GroupPage>(find.byType(GroupPage)).id, 'group2');
    expect(find.text('# Design review'), findsOneWidget);
    expect(
      find.text(
        'No discussion posts yet. Start a discussion to hear from the group.',
      ),
      findsOneWidget,
    );
    await tester.pumpWidget(const SizedBox.shrink());
    connection.disconnect();
    BlocScope.endAll();
  });
}
