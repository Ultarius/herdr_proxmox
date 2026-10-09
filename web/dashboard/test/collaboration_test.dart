import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/collaboration_panel.dart';

void main() {
  JuiceLoggerConfig.minLevel = Level.warning;
  testWidgets(
    'live output stays collapsed and action artifact fits a narrow screen',
    (tester) async {
      tester.view.physicalSize = const Size(360, 780);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/inspect'))
            return http.Response(
              jsonEncode({
                'status': 'blocked',
                'output': 'Allow this action? Selected option: Allow',
              }),
              200,
            );
          if ((request.url.path.endsWith('/organizations') ||
              request.url.path.endsWith('/state') ||
              request.url.path.endsWith('/activity')))
            return http.Response(
              jsonEncode({
                'groups': [
                  {
                    'id': 'group',
                    'organization_id': 'org',
                    'name': 'Deployment',
                    'description': 'Discuss deployment options',
                    'members': ['max', 'iris'],
                  },
                ],
                'jobs': [
                  {
                    'id': 'discussion',
                    'organization_id': 'org',
                    'kind': 'discussion',
                    'state': 'artifact_ready',
                    'group': {'name': 'Deployment'},
                    'result':
                        '# Action brief\nVerify backup and recovery before rollout.',
                    'contributions': [
                      {
                        'name': 'Max',
                        'round': 1,
                        'content': 'Test recovery first.',
                      },
                    ],
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
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: CollaborationPanel(
                organization: const {'id': 'org'},
                profiles: const [
                  {'id': 'max', 'name': 'Max', 'role': 'Developer'},
                  {'id': 'iris', 'name': 'Iris', 'role': 'Reviewer'},
                ],
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byType(DropdownButtonFormField<String>));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Max').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Live terminal output'));
      await tester.pumpAndSettle();
      expect(
        find.text('Allow this action? Selected option: Allow'),
        findsOneWidget,
      );
      final tile = tester.widget<ExpansionTile>(
        find.ancestor(
          of: find.text('Live terminal output'),
          matching: find.byType(ExpansionTile),
        ),
      );
      expect(tile.initiallyExpanded, isFalse);
      await tester.ensureVisible(find.text('View artifact'));
      await tester.tap(find.text('View artifact'));
      await tester.pumpAndSettle();
      expect(
        find.text('# Action brief\nVerify backup and recovery before rollout.'),
        findsOneWidget,
      );
      expect(find.text('Copy Markdown'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      connection.disconnect();
      BlocScope.endAll();
    },
  );
  testWidgets(
    'chat retry keeps its request and group saves members and brief',
    (tester) async {
      final mutations = <Map<String, dynamic>>[];
      final profiles = <Map<String, dynamic>>[
        {'id': 'max', 'name': 'Max', 'role': 'Developer'},
        {'id': 'iris', 'name': 'Iris', 'role': 'Reviewer'},
      ];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/inspect'))
            return http.Response('{"status":"idle","output":"Ready"}', 200);
          if (request.method == 'POST' &&
              !request.url.path.endsWith('/activity')) {
            mutations.add(jsonDecode(request.body) as Map<String, dynamic>);
            if (mutations.length == 1)
              return http.Response('{"error":"Connection interrupted"}', 502);
            return http.Response('{"id":"saved"}', 200);
          }
          if ((request.url.path.endsWith('/organizations') ||
              request.url.path.endsWith('/state') ||
              request.url.path.endsWith('/activity')))
            return http.Response('{"groups":[],"jobs":[]}', 200);
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
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: CollaborationPanel(
                organization: const {'id': 'org'},
                profiles: profiles,
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byType(DropdownButtonFormField<String>));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Max').last);
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byType(TextField).first,
        'Explain the architecture',
      );
      await tester.tap(find.text('Send prompt'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Retry same request'));
      await tester.tap(find.text('Retry same request'));
      await tester.pumpAndSettle();
      expect(mutations[0]['request_id'], mutations[1]['request_id']);
      expect(mutations[1]['profile_id'], 'max');
      expect(mutations[1]['prompt'], 'Explain the architecture');
      await tester.ensureVisible(find.text('Create group'));
      await tester.tap(find.text('Create group'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save group'));
      await tester.pumpAndSettle();
      expect(find.text('Enter a name'), findsOneWidget);
      final fields = find.descendant(
        of: find.byType(DiscussionGroupForm),
        matching: find.byType(TextFormField),
      );
      await tester.enterText(fields.at(0), 'Architecture review');
      await tester.enterText(
        fields.at(1),
        'Discuss deployment options and recommend next actions',
      );
      FocusManager.instance.primaryFocus?.unfocus();
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Max · Developer'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Max · Developer'));
      await tester.ensureVisible(find.text('Iris · Reviewer'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Iris · Reviewer'));
      await tester.tap(find.text('Save group'));
      await tester.pumpAndSettle();
      expect(mutations.last['members'], ['max', 'iris']);
      expect(mutations.last['read_only'], isTrue);
      expect(mutations.last['notify_outcomes'], isTrue);
      expect(mutations.last['create_tasks'], isFalse);
      expect(mutations.last['permission_mode'], 'default');
      expect(mutations.last['description'], contains('deployment options'));
      await tester.pumpWidget(const SizedBox.shrink());
      connection.disconnect();
      BlocScope.endAll();
    },
  );
  test(
    'delivery labels distinguish an acknowledged CLI return from a verified reply',
    () {
      expect(deliveryLabel('submitting'), 'Submitting the prompt');
      expect(
        deliveryLabel('command_returned'),
        'CLI returned (acknowledgment only)',
      );
      expect(deliveryLabel('session_idle_reply_pending'), contains('reply file'));
      expect(deliveryLabel('reply_verified'), 'Reply verified');
      expect(deliveryLabel(null), 'No delivery evidence');
      expect(deliveryLabel('future-stage'), 'future-stage');
    },
  );

  testWidgets('chat records show the delivery stage beside the error', (
    tester,
  ) async {
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/inspect'))
          return http.Response('{"status":"idle","output":"Ready"}', 200);
        if ((request.url.path.endsWith('/organizations') ||
            request.url.path.endsWith('/state') ||
            request.url.path.endsWith('/activity')))
          return http.Response(
            jsonEncode({
              'groups': [],
              'jobs': [
                {
                  'id': 'chat-1',
                  'organization_id': 'org',
                  'kind': 'chat',
                  'profile_id': 'max',
                  'state': 'needs_attention',
                  'prompt': 'Validate only',
                  'error':
                      'Agent did not produce a valid chat reply: file missing at /tmp/reply-x.md. Delivery stage: command_returned.',
                  'delivery': {
                    'stage': 'command_returned',
                    'reply_name': 'reply-abcdefabcdef.md',
                    'started_at': '2026-01-01T00:00:00+00:00',
                    'returned_at': '2026-01-01T00:01:00+00:00',
                  },
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
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: CollaborationPanel(
              organization: const {'id': 'org'},
              profiles: const [
                {'id': 'max', 'name': 'Max', 'role': 'Developer'},
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    // Chat records are shown for the selected agent.
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Max').last);
    await tester.pumpAndSettle();
    expect(
      find.text('Delivery: CLI returned (acknowledgment only)'),
      findsOneWidget,
    );
    expect(find.text('Reply file: reply-abcdefabcdef.md'), findsOneWidget);
    expect(find.text('Job chat-1'), findsOneWidget);
    expect(find.textContaining('file missing'), findsOneWidget);
    expect(find.textContaining('reply verified'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    connection.disconnect();
    BlocScope.endAll();
  });
}
