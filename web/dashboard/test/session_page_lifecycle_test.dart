import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/app_shell.dart';
import 'package:herdr_dashboard/cli_setup_page.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/knowledge_page.dart';
import 'package:herdr_dashboard/logs_page.dart';
import 'package:herdr_dashboard/project_explorer_page.dart';
import 'package:herdr_dashboard/routes.dart';
import 'helpers/dashboard_connection.dart' show pumpUntil;

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  for (final route in ['explorer', 'knowledge', 'logs', 'configuration']) {
    testWidgets('$route waits for restoration and resets after sign out', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(1400, 1800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final session = Completer<http.Response>();
      final reads = <String>[];
      var account = 'first';
      final connection = DashboardBloc(
        client: MockClient((request) async {
          final path = request.url.path;
          reads.add(path);
          if (path == '/api/session') return session.future;
          final payload = switch (path) {
            '/api/snapshot' => {'workspaces': [], 'agents': []},
            '/api/organizations/directory' => {
              'organizations': [
                {'id': 'org', 'name': 'Organization'},
              ],
              'profiles': [],
            },
            '/api/projects/browse' => {
              'kind': 'folder',
              'root': '/home/herdr/projects',
              'path': '',
              'entries': [
                {'kind': 'folder', 'name': '$account folder', 'path': 'repo'},
              ],
            },
            '/api/knowledge' => {
              'records': [
                {
                  'id': 'record',
                  'title': '$account knowledge',
                  'body': 'Saved evidence',
                  'kind': 'finding',
                  'state': 'reported',
                  'repository': '',
                  'source': {},
                  'audit': [],
                },
              ],
              'repositories': [],
              'next_before': null,
            },
            '/api/logs' => {
              'logs': [
                {
                  'id': 'a' * 32,
                  'label': '$account log',
                  'pane': 'w1:p1',
                  'source': 'visible',
                  'size_bytes': 8,
                  'created_at': '2026-10-25',
                },
              ],
              'total_bytes': 8,
            },
            '/api/cli-setup' => {
              'clis': [
                {
                  'id': 'codex',
                  'name': '$account CLI',
                  'installed': true,
                  'status': 'not_configured',
                  'detail': 'Sign in.',
                },
              ],
            },
            '/api/sdk' => {
              'state': 'idle',
              'supported': true,
              'toolchains': {},
            },
            '/api/ssh-access' => {'key_count': 0},
            '/api/integration' => {'alerts': []},
            _ => <String, dynamic>{},
          };
          return http.Response(jsonEncode(payload), 200);
        }),
      );
      final coordinator = AppCoordinator();
      final child = switch (route) {
        'explorer' => const ProjectExplorerPage(),
        'knowledge' => KnowledgePage(coordinator: coordinator),
        'logs' => LogsPage(coordinator: coordinator),
        _ => CliSetupPage(coordinator: coordinator),
      };
      String label(String value) => switch (route) {
        'explorer' => '$value folder',
        'knowledge' => '$value knowledge',
        'logs' => '$value log — 0.000008 MB',
        _ => '$value CLI',
      };
      final endpoint = switch (route) {
        'explorer' => '/api/projects/browse',
        'knowledge' => '/api/knowledge',
        'logs' => '/api/logs',
        _ => '/api/cli-setup',
      };
      BlocScope.register<DashboardBloc>(() => connection);
      final restoring = connection.restoreSession();
      try {
        await tester.pumpWidget(
          MaterialApp(
            home: AppShell(
              coordinator: coordinator,
              section: route,
              child: child,
            ),
          ),
        );
        expect(find.text('Dashboard access token'), findsOneWidget);
        expect(reads, isNot(contains(endpoint)));
        session.complete(http.Response('{"authenticated":true}', 200));
        await pumpUntil(
          tester,
          () => find.text(label('first')).evaluate().isNotEmpty,
          reason: '$route must load after session restoration',
        );
        await restoring;
        expect(reads, contains(endpoint));
        if (route == 'knowledge') {
          await tester.ensureVisible(find.text('Add knowledge'));
          await tester.tap(find.text('Add knowledge'));
          await pumpUntil(
            tester,
            () => find.byType(AlertDialog).evaluate().isNotEmpty,
          );
          expect(find.text('Add project knowledge'), findsOneWidget);
        }
        await connection.disconnect();
        await pumpUntil(
          tester,
          () => find.byType(AlertDialog).evaluate().isEmpty,
          reason: 'Session dialogs must be removed on sign out',
        );
        expect(find.text(label('first')), findsNothing);
        expect(find.text('Dashboard access token'), findsOneWidget);

        account = 'second';
        connection.connect('new-account');
        await pumpUntil(
          tester,
          () => find.text(label('second')).evaluate().isNotEmpty,
          reason: '$route must load fresh data for the next account',
        );
        expect(find.text(label('first')), findsNothing);
        expect(reads.where((path) => path == endpoint), hasLength(2));
        expect(tester.takeException(), isNull);
      } finally {
        if (!session.isCompleted)
          session.complete(http.Response('{"authenticated":false}', 200));
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        coordinator.dispose();
        await BlocScope.reset();
      }
    });
  }
}
