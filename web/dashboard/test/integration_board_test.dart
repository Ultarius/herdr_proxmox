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
            expect(jsonDecode(request.body)['id'], 'event1');
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
                            'enabled': true,
                            'profile_id': 'coord',
                            'coordinator_state': 'needs_attention',
                            'coordinator_error': 'Invalid root pane',
                            'report_state': 'uncertain',
                            'report_error': 'Delivery interrupted',
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
      expect(actions, ['configure', 'retry']);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );
}
