import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/integration_board.dart';

void main() {
  testWidgets(
    'coordinator enablement is explicit, and deferred work can be reconsidered',
    (tester) async {
      var enabled = false;
      var paused = false;
      var reportState = 'uncertain';
      var validationState = 'validation_pending';
      final actions = <String>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          final endpoint = request.url.path;
          if (endpoint.endsWith('/integration/configure')) {
            actions.add('configure');
            final body = jsonDecode(request.body);
            expect(body['profile_id'], 'new');
            expect(body['repository'], 'repo');
            enabled = true;
            return http.Response('{}', 200);
          }
          if (endpoint.endsWith('/integration/retry')) {
            actions.add('retry');
            expect(jsonDecode(request.body)['id'], isIn(['event1', 'pending']));
            return http.Response('{}', 200);
          }
          if (endpoint.endsWith('/integration/repair')) {
            final body = jsonDecode(request.body);
            expect(body['job_id'], 'report1');
            expect(body['mode'], 'fresh');
            expect(body['inspected'], isTrue);
            actions.add('repair');
            return http.Response('{}', 200);
          }
          if (endpoint.endsWith('/integration'))
            return http.Response(
              jsonEncode({
                'coordination': {
                  'configurations': enabled
                      ? [
                          {
                            'repository': 'repo',
                            'enabled': !paused,
                            'profile_id': 'coord',
                            'coordinator_state': 'needs_attention',
                            'coordinator_error': 'Invalid root pane',
                            'report_state': reportState,
                            'report_error': 'Delivery interrupted',
                            'report_job_id': 'report1',
                          },
                        ]
                      : [],
                  'audit': [
                    {
                      'repository': 'repo',
                      'action': 'transition',
                      'state': 'ready',
                      'at': '2026-10-06',
                      'profile_id': 'worker',
                      'reason': 'Snapshot pinned',
                    },
                  ],
                  'events': enabled
                      ? [
                          {
                            'id': 'pending',
                            'repository': 'repo',
                            'name': 'Worker',
                            'state': validationState,
                            'path': 'repo',
                            'target': 'abc123',
                            'tests': {
                              'status': 'not_run',
                              'summary': 'Flutter missing; Python passed',
                            },
                            'verification': {
                              'target_incorporated': true,
                              'conflicts': 0,
                              'merging': false,
                            },
                          },
                          {
                            'id': 'event2',
                            'repository': 'repo',
                            'name': 'Maya',
                            'state': 'deciding',
                            'path': 'repo',
                            'target': 'abc123',
                          },
                          {
                            'id': 'event1',
                            'repository': 'repo',
                            'name': 'Max',
                            'state': 'deferred',
                            'path': 'repo',
                            'target': 'abc123',
                            'reason': 'Finish feature',
                            'checkpoint': 'After tests',
                            'recovery': {
                              'commit': 'saved123',
                              'ref': 'refs/herdr/recovery/saved123',
                            },
                          },
                        ]
                      : [],
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
      connection.organizationDirectory.value = {
        'organizations': [
          {'id': 'org1'},
        ],
      };
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: IntegrationBoard(repository: 'repo'),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(actions, isEmpty);
      await tester.tap(find.text('Integration coordinator'));
      await tester.pumpAndSettle();
      expect(find.text('Not configured'), findsOneWidget);
      await tester.tap(find.text('Enable coordinator'));
      await tester.pumpAndSettle();
      expect(actions, ['configure']);
      expect(find.text('Coordinator: needs_attention'), findsOneWidget);
      expect(find.text('Invalid root pane'), findsOneWidget);
      expect(
        find.textContaining('Coordinator report needs attention.'),
        findsOneWidget,
      );
      expect(find.byTooltip('Refresh coordination'), findsOneWidget);
      await tester.ensureVisible(find.text('Create fresh summary'));
      await tester.tap(find.text('Create fresh summary'));
      await tester.pumpAndSettle();
      expect(actions, ['configure']);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Create fresh summary'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Inspected · create summary'));
      await tester.pumpAndSettle();
      expect(actions, ['configure', 'repair']);
      await tester.ensureVisible(
        find.text('Worker · Merged · validation pending'),
      );
      expect(find.text('Worker · Merged · validation pending'), findsOneWidget);
      await tester.tap(find.text('Retry validation'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Maya · deciding'));
      await tester.tap(find.text('Maya · deciding'));
      await tester.pumpAndSettle();
      expect(
        find.byWidgetPredicate(
          (w) =>
              w is SelectableText &&
              (w.data ?? '').contains(
                'Recovery: Not created yet; required before merge delivery',
              ),
        ),
        findsOneWidget,
      );
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Recovery · Max'));
      await tester.tap(find.text('Recovery · Max'));
      await tester.pumpAndSettle();
      expect(
        find.byWidgetPredicate(
          (w) =>
              w is SelectableText &&
              (w.data ?? '').contains('git worktree add --detach'),
        ),
        findsOneWidget,
      );
      await tester.ensureVisible(find.text('Audit history'));
      await tester.tap(find.text('Audit history'));
      await tester.pumpAndSettle();
      expect(
        find.byWidgetPredicate(
          (w) =>
              w is SelectableText && (w.data ?? '').contains('Snapshot pinned'),
        ),
        findsOneWidget,
      );
      expect(find.text('Max · deferred'), findsOneWidget);
      expect(find.textContaining('After tests'), findsOneWidget);
      await tester.ensureVisible(find.text('Ask again'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Ask again'));
      await tester.pumpAndSettle();
      expect(actions, ['configure', 'repair', 'retry', 'retry']);
      // New report controls and validation labels must also fit phone widths.
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPhysicalSize);
      for (final width in [320.0, 390.0]) {
        tester.view.physicalSize = Size(width, 844);
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.ensureVisible(find.text('Create fresh summary'));
        await tester.tap(find.text('Create fresh summary'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.tap(find.text('Cancel'));
        await tester.pumpAndSettle();
      }
      tester.view.physicalSize = const Size(1200, 1200);
      paused = true;
      reportState = 'waiting';
      await tester.ensureVisible(find.byTooltip('Refresh coordination'));
      await tester.tap(find.byTooltip('Refresh coordination'));
      await tester.pumpAndSettle();
      expect(find.text('Fresh summary waiting'), findsOneWidget);
      expect(
        tester
            .widget<TextButton>(
              find.widgetWithText(TextButton, 'Retry validation'),
            )
            .onPressed,
        isNull,
      );
      for (final state in ['validation_ready', 'validating']) {
        validationState = state;
        await tester.ensureVisible(find.byTooltip('Refresh coordination'));
        await tester.tap(find.byTooltip('Refresh coordination'));
        await tester.pumpAndSettle();
        expect(
          find.text(
            state == 'validating'
                ? 'Worker · Merged · validating'
                : 'Worker · Merged · validation queued',
          ),
          findsOneWidget,
        );
      }
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
}
