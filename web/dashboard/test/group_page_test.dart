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
  testWidgets(
    'blocked member response requires review and carries the inspected prompt token',
    (tester) async {
      tester.view.physicalSize = const Size(1400, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final data = {
        'organizations': [
          {'id': 'org'},
        ],
        'profiles': [],
        'groups': [
          {
            'id': 'group',
            'organization_id': 'org',
            'name': 'Review',
            'description': 'Review',
            'members': ['nora'],
          },
        ],
        'jobs': [
          {
            'id': 'meeting',
            'kind': 'discussion',
            'organization_id': 'org',
            'group_id': 'group',
            'state': 'running',
            'group': {'description': 'Review'},
            'prompt': 'Review repository',
          },
        ],
      };
      final posted = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/input')) {
            posted.add(jsonDecode(request.body) as Map<String, dynamic>);
            return http.Response('{"id":"input"}', 200);
          }
          if (request.url.path.endsWith('/inspect')) {
            return http.Response(
              jsonEncode({
                'streams': [
                  {
                    'profile_id': 'nora',
                    'name': 'Nora',
                    'status': 'blocked',
                    'output':
                        'Permission required: discussion-artifacts. Selected: Allow once',
                    'console_token': 'reviewed-prompt-token',
                  },
                ],
              }),
              200,
            );
          }
          return http.Response(
            jsonEncode(
              request.url.path.contains('/organizations')
                  ? data
                  : {'authenticated': true, 'workspaces': [], 'agents': []},
            ),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      final connected = connection.stream.firstWhere(
        (_) => connection.state.connected,
      );
      connection.connect('token');
      await connected;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: GroupPage(id: 'group', coordinator: AppCoordinator()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Live discussion · 0 replies'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Nora · blocked'));
      await tester.pumpAndSettle();
      final respond = find.text('Respond to permission or question');
      await tester.ensureVisible(respond);
      await tester.tap(respond);
      await tester.pumpAndSettle();
      expect(posted, isEmpty);
      expect(find.text('Respond to Nora'), findsOneWidget);
      await tester.tap(find.text('Confirm selected option'));
      await tester.pumpAndSettle();
      expect(posted.single['profile_id'], 'nora');
      expect(posted.single['key'], 'enter');
      expect(posted.single['console_token'], 'reviewed-prompt-token');
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      BlocScope.endAll();
    },
  );
  for (final force in [false, true]) {
    testWidgets(
      'project mismatch realignment uses explicit confirmation (force=$force)',
      (tester) async {
        tester.view.physicalSize = const Size(1400, 1400);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final job = <String, dynamic>{
          'id': 'meeting',
          'kind': 'discussion',
          'group_id': 'group',
          'organization_id': 'org',
          'state': 'needs_attention',
          'created_at': '2026-10-13',
          'preparation_version': 1,
          'prompt': 'Review',
          'error': 'Project mismatch',
          'group': {'description': 'Review'},
          'project_mismatches': [
            {
              'run_id': 'old-felix',
              'alias': 'felix-alias',
              'name': 'Felix',
              'session_project': '/projects',
              'configured_project': '/projects/repo',
              'expected_project': '/projects/repo',
              'repairable': true,
            },
          ],
        };
        final data = {
          'organizations': [
            {'id': 'org'},
          ],
          'profiles': [],
          'jobs': [job],
          'groups': [
            {
              'id': 'group',
              'organization_id': 'org',
              'name': 'Review',
              'description': 'Review',
              'members': ['one'],
            },
          ],
        };
        final posted = <String, Map<String, dynamic>>{};
        final connection = DashboardBloc(
          client: MockClient((request) async {
            if (request.url.path.endsWith('/realign_discussion_session')) {
              posted['realign'] =
                  jsonDecode(request.body) as Map<String, dynamic>;
              job['project_mismatches'] = [];
              job['state'] = 'waiting_for_members';
              job['error'] = '';
              job['progress'] =
                  'Sessions realigned; discussion will resume automatically when members are ready.';
              return http.Response(
                '{"id":"meeting","new_run_id":"new-felix"}',
                200,
              );
            }
            if (request.url.path.endsWith('/retry_discussion')) {
              posted['retry'] =
                  jsonDecode(request.body) as Map<String, dynamic>;
              job['state'] = 'queued';
              return http.Response('{"id":"meeting"}', 200);
            }
            return http.Response(
              jsonEncode(
                request.url.path.contains('/organizations')
                    ? data
                    : {'authenticated': true, 'workspaces': [], 'agents': []},
              ),
              200,
            );
          }),
        );
        BlocScope.register<DashboardBloc>(() => connection);
        final connected = connection.stream.firstWhere(
          (_) => connection.state.connected,
        );
        connection.connect('token');
        await connected;
        connection.operator.value = {'role': 'admin'};
        await tester.pumpWidget(
          MaterialApp(
            home: Scaffold(
              body: GroupPage(id: 'group', coordinator: AppCoordinator()),
            ),
          ),
        );
        await tester.pumpAndSettle();
        expect(find.text('Felix: session project mismatch'), findsOneWidget);
        final action = find.text(
          force
              ? 'Force realign session'
              : 'Archive and relaunch in current project',
        );
        await tester.ensureVisible(action);
        await tester.tap(action);
        await tester.pumpAndSettle();
        expect(posted, isEmpty);
        await tester.tap(
          find.text(force ? 'Force stop and realign' : 'Archive and relaunch'),
        );
        await tester.pumpAndSettle();
        expect(posted['realign']?['force'], force);
        expect(posted['realign']?['run_id'], 'old-felix');
        expect(posted['realign']?['expected_project'], '/projects/repo');
        expect(posted['retry'], isNull);
        expect(
          find.text(
            'Sessions realigned; discussion will resume automatically when members are ready.',
          ),
          findsOneWidget,
        );
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        BlocScope.endAll();
      },
    );
  }
  testWidgets(
    'failed preparation retries only after explicit inspection confirmation',
    (tester) async {
      tester.view.physicalSize = const Size(1400, 1200);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final data = {
        'organizations': [
          {'id': 'org', 'name': 'Org'},
        ],
        'profiles': [],
        'groups': [
          {
            'id': 'group',
            'organization_id': 'org',
            'name': 'Product',
            'description': 'Review',
            'members': ['one', 'two'],
          },
        ],
        'jobs': [
          {
            'id': 'meeting',
            'kind': 'discussion',
            'group_id': 'group',
            'organization_id': 'org',
            'state': 'needs_attention',
            'created_at': '2026-10-09',
            'preparation_version': 1,
            'prompt': 'Review',
            'error': 'Inspect member',
            'group': {'description': 'Review'},
            'contributions': [],
          },
        ],
      };
      final posted = <Map<String, dynamic>>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/retry_discussion')) {
            posted.add(jsonDecode(request.body) as Map<String, dynamic>);
            return http.Response('{"id":"meeting"}', 200);
          }
          return http.Response(
            jsonEncode(
              request.url.path.contains('/organizations')
                  ? data
                  : {'authenticated': true, 'workspaces': [], 'agents': []},
            ),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      final connected = connection.stream.firstWhere(
        (_) => connection.state.connected,
      );
      connection.connect('token');
      await connected;
      connection.operator.value = {'role': 'admin'};
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: GroupPage(id: 'group', coordinator: AppCoordinator()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Retry inspected preparation'));
      await tester.tap(find.text('Retry inspected preparation'));
      await tester.pumpAndSettle();
      expect(posted, isEmpty);
      await tester.tap(find.text('Confirm'));
      await tester.pumpAndSettle();
      expect(posted.single['inspected'], true);
      expect(posted.single['job_id'], 'meeting');
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
  testWidgets('a blocked participant can be answered from the group page', (
    tester,
  ) async {
    // The discussion state was wired into the label but not into the polling
    // lists, so a waiting discussion fetched no streams and offered no way to
    // answer the very block it was waiting on.
    tester.view.physicalSize = const Size(1400, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final data = {
      'organizations': [
        {'id': 'org', 'name': 'Org'},
      ],
      'profiles': [],
      'groups': [
        {
          'id': 'group',
          'organization_id': 'org',
          'name': 'Product',
          'description': 'Review',
          'members': ['one', 'two'],
        },
      ],
      'jobs': [
        {
          'id': 'meeting',
          'kind': 'discussion',
          'group_id': 'group',
          'organization_id': 'org',
          'state': 'waiting_for_input',
          'created_at': '2026-10-09',
          'preparation_version': 1,
          'discussion_submitted_at': '2026-10-09',
          'prompt': 'Review',
          'group': {'description': 'Review'},
          'contributions': [],
        },
      ],
    };
    final posted = <Map<String, dynamic>>[];
    var inspected = 0;
    final paths = <String>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        paths.add(request.url.path);
        if (request.url.path.endsWith('/input')) {
          posted.add(jsonDecode(request.body) as Map<String, dynamic>);
          return http.Response('{"id":"input"}', 200);
        }
        if (request.url.path.endsWith('/inspect')) {
          inspected++;
          return http.Response(
            jsonEncode({
              'streams': [
                {
                  'profile_id': 'fac',
                  'name': 'Product Council',
                  'status': 'blocked',
                  'output': 'Permission required',
                },
              ],
              'artifact_draft': '',
              'contributions': [],
            }),
            200,
          );
        }
        return http.Response(
          jsonEncode(
            request.url.path.contains('/organizations')
                ? data
                : {'authenticated': true, 'workspaces': [], 'agents': []},
          ),
          200,
        );
      }),
    );
    BlocScope.register<DashboardBloc>(() => connection);
    final connected = connection.stream.firstWhere(
      (_) => connection.state.connected,
    );
    connection.connect('token');
    await connected;
    connection.operator.value = {'role': 'admin'};
    try {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: GroupPage(id: 'group', coordinator: AppCoordinator()),
          ),
        ),
      );
      await tester.pumpAndSettle();
      // The page refreshes on its own interval; advance it so the inspection
      // poll runs rather than depending on the first connect timing.
      await tester.pump(const Duration(seconds: 1));
      await tester.pumpAndSettle();
      expect(find.text('Waiting for your decision'), findsOneWidget);
      // The state must be polled, or the block is invisible and cannot be
      // answered from the page that shows it.
      expect(
        inspected,
        greaterThan(0),
        reason: 'a waiting discussion must still be inspected; saw $paths',
      );
      expect(find.textContaining('Product Council: blocked'), findsOneWidget);
      expect(tester.takeException(), isNull);
    } finally {
      // The page owns a periodic timer; dispose it before the test ends.
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    }
  });

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
    await tester.ensureVisible(find.text('Deployment review'));
    await tester.pumpAndSettle();
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
