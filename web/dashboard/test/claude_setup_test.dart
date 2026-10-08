import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  JuiceLoggerConfig.minLevel = Level.warning;
  testWidgets(
    'Claude validates codes, submits once, and restarts with fresh state',
    (tester) async {
      tester.view.physicalSize = const Size(1200, 2000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final inputs = <String>[];
      var starts = 0;
      var closed = false;
      var running = true;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          final path = request.url.path;
          Object response = {};
          if (path == '/api/snapshot')
            response = {'workspaces': [], 'agents': []};
          if (path == '/api/ssh-access') response = {'key_count': 0};
          if (path == '/api/dashboard-access')
            response = {'mode': 'lan', 'pending_mode': null};
          if (path == '/api/cli-setup') {
            response = {
              'clis': [
                {
                  'id': 'claude',
                  'name': 'Claude Code',
                  'installed': true,
                  'status': 'not_configured',
                  'detail': 'Sign in.',
                },
              ],
            };
          }
          if (path.endsWith('/start')) {
            starts++;
            running = true;
            response = {'id': 'session$starts'};
          }
          if (path.endsWith('/poll')) {
            final body = jsonDecode(request.body);
            response = {
              'output': body['cursor'] == 0
                  ? 'https://claude.com/cai/oauth/authorize?state=state$starts\r\nPaste code here > '
                  : '',
              'cursor': 1,
              'truncated': false,
              'running': running,
              'exit_code': running ? null : 1,
            };
          }
          if (path.endsWith('/input'))
            inputs.add(jsonDecode(request.body)['data']);
          if (path.endsWith('/close')) closed = true;
          return http.Response(jsonEncode(response), 200);
        }),
      );
      BlocScope.register<DashboardBloc>(
        () => connection,
        lifecycle: BlocLifecycle.permanent,
      );
      final loaded = connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      connection.connect('token');
      await loaded;
      final coordinator = AppCoordinator();
      await tester.pumpWidget(MaterialApp.router(routerConfig: coordinator));
      coordinator.navigate(CliSetupRoute());
      await tester.pumpAndSettle();
      await tester.tap(find.text('Connect account'));
      await tester.pumpAndSettle();
      final field = find.widgetWithText(
        TextField,
        'Authorization code or API key',
      );
      Future<void> submit(String value) async {
        await tester.ensureVisible(field);
        await tester.enterText(field, value);
        await tester.ensureVisible(find.text('Send to terminal'));
        await tester.tap(find.text('Send to terminal'));
        await tester.pump();
      }

      await submit('partial-code');
      expect(inputs, isEmpty);
      expect(
        find.textContaining('Paste the full authorization code from Claude'),
        findsOneWidget,
      );
      await submit('code#old-state');
      expect(inputs, isEmpty);
      expect(find.textContaining('different sign-in attempt'), findsOneWidget);
      await submit(' code#state1 ');
      expect(inputs, ['code#state1\r']);
      await submit('');
      expect(inputs, hasLength(1));
      running = false;
      await tester.pump(const Duration(milliseconds: 400));
      await tester.pumpAndSettle();
      expect(tester.widget<TextField>(field).enabled, isFalse);
      await tester.ensureVisible(find.text('Restart sign-in'));
      await tester.tap(find.text('Restart sign-in'));
      await tester.pumpAndSettle();
      expect(closed, isTrue);
      expect(starts, 2);
      expect(
        find.text('https://claude.com/cai/oauth/authorize?state=state1'),
        findsNothing,
      );
      expect(
        find.text('https://claude.com/cai/oauth/authorize?state=state2'),
        findsOneWidget,
      );
      await submit('code#state1');
      expect(inputs, hasLength(1));
      await submit('fresh-code#state2');
      expect(inputs.last, 'fresh-code#state2\r');
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await connection.disconnect();
      coordinator.dispose();
      await BlocScope.reset();
    },
  );
}
