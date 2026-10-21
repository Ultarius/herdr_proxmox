import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/update_card.dart';

void main() {
  testWidgets('update recovery shows the backup check and recovery error', (
    tester,
  ) async {
    final bloc = DashboardBloc(
      client: MockClient(
        (request) async => http.Response(
          jsonEncode(
            request.url.path.endsWith('/updates')
                ? {
                    'installed': 'v0.0.55',
                    'latest': 'v0.0.55',
                    'state': 'failed',
                    'supported': true,
                    'available': true,
                    'error': 'Update failed.',
                    'recovery_error': 'Storage quota exceeded.',
                    'backup_verification': {
                      'method': 'manifest',
                      'file_count': 12,
                    },
                  }
                : {'workspaces': [], 'agents': []},
          ),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        ),
      ),
    );
    BlocScope.register<DashboardBloc>(() => bloc);
    var cleaned = false;
    Future<void> cleanup() async {
      if (cleaned) return;
      cleaned = true;
      await tester.pumpWidget(const SizedBox.shrink());
      await bloc.disconnect();
      await BlocScope.reset();
    }

    addTearDown(cleanup);
    try {
      bloc.connect('token');
      await tester.pump(const Duration(milliseconds: 110));
      await tester.pumpAndSettle();
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: SingleChildScrollView(child: UpdateCard())),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.text('Backup contents verified against its manifest.'),
        findsOneWidget,
      );
      expect(
        find.text('Recovery failed: Storage quota exceeded.'),
        findsOneWidget,
      );
    } finally {
      await cleanup();
    }
  });

  testWidgets(
    'failed lookup distinguishes previous install and shows recovery advice',
    (tester) async {
      final bloc = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/updates')) {
            return http.Response(
              jsonEncode({
                'installed': 'v0.0.46',
                'latest': null,
                'state': 'complete',
                'supported': true,
                'available': false,
                'check_error': 'DNS unavailable',
                'check_diagnostic': 'Check the gateway in Proxmox → Network.',
                'check_failures': 37,
                'check_failed_since': '2026-10-11T14:02:00+00:00',
              }),
              200,
              headers: {'content-type': 'application/json; charset=utf-8'},
            );
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => bloc);
      bloc.connect('token');
      await tester.pump(const Duration(milliseconds: 110));
      await tester.pumpAndSettle();
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: SingleChildScrollView(child: UpdateCard())),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('Latest: Unavailable'), findsOneWidget);
      expect(
        find.text('Check the gateway in Proxmox → Network.'),
        findsOneWidget,
      );
      // The diagnosis is the actionable message; the raw exception would only
      // compete with it. It stays the sole signal when there is no diagnosis.
      expect(find.textContaining('Release lookup unavailable'), findsNothing);
      expect(
        find.textContaining(
          'Release lookup failed 37 times since 2026-10-11T14:02:00+00:00',
        ),
        findsOneWidget,
      );
      expect(
        find.text(
          'The previous installation completed. The current release check failed.',
        ),
        findsOneWidget,
      );
      expect(
        find.text(
          'Update complete. Reload this page to use the new dashboard.',
        ),
        findsNothing,
      );
      await tester.pumpWidget(const SizedBox.shrink());
      await bloc.disconnect();
      await BlocScope.reset();
    },
  );

  testWidgets('alpha dropdown requires explicit selection and confirmation', (
    tester,
  ) async {
    const tag = 'alpha-login-123456abcdef-b42';
    final installs = <Map<String, dynamic>>[];
    final bloc = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path.endsWith('/updates/alphas')) {
          return http.Response(
            jsonEncode({
              'releases': [
                {'version': tag, 'notes': 'Feature alpha/login at abc'},
              ],
            }),
            200,
            headers: {'content-type': 'application/json; charset=utf-8'},
          );
        }
        if (request.url.path.endsWith('/updates/install')) {
          installs.add(jsonDecode(request.body) as Map<String, dynamic>);
          return http.Response('{"state":"queued"}', 200);
        }
        if (request.url.path.endsWith('/updates')) {
          return http.Response(
            '{"installed":"v1.0.0","latest":"v1.0.0","state":"idle","supported":true,"available":false}',
            200,
            headers: {'content-type': 'application/json; charset=utf-8'},
          );
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      }),
    );
    BlocScope.register<DashboardBloc>(() => bloc);
    bloc.connect('token');
    await tester.pump(const Duration(milliseconds: 110));
    await tester.pumpAndSettle();
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: SingleChildScrollView(child: UpdateCard())),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(DropdownButtonFormField<String>), findsNothing);
    await tester.tap(find.text('Browse alpha builds'));
    await tester.pumpAndSettle();
    final install = find.widgetWithText(FilledButton, 'Install selected alpha');
    expect(tester.widget<FilledButton>(install).onPressed, isNull);
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text(tag).last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(install);
    await tester.tap(install);
    await tester.pumpAndSettle();
    expect(find.textContaining('experimental feature-branch'), findsOneWidget);
    expect(installs, isEmpty);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(installs, isEmpty);
    await tester.tap(install);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Install update').last);
    await tester.pumpAndSettle();
    expect(installs.single['version'], tag);
    expect(installs.single['channel'], 'alpha');
    expect(installs.single['confirm_alpha'], isTrue);
    await tester.pumpWidget(const SizedBox.shrink());
    await bloc.disconnect();
    await BlocScope.reset();
  });
}
