import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:xterm/xterm.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  JuiceLoggerConfig.minLevel = Level.warning;
  testWidgets('configure CLI, render prompt, send code and close session', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final mutations = <String>[];
    final inputs = <String>[];
    final connection = DashboardBloc(
      client: MockClient((request) async {
        expect(request.headers['Authorization'], 'Bearer token');
        if (request.url.path == '/api/snapshot') {
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }
        if (request.url.path == '/api/ssh-access') {
          return http.Response('{"key_count":0}', 200);
        }
        if (request.url.path == '/api/dashboard-access' &&
            request.method == 'GET') {
          return http.Response('{"mode":"lan","pending_mode":null}', 200);
        }
        if (request.url.path == '/api/cli-setup') {
          return http.Response(
            jsonEncode({
              'clis': [
                {
                  'id': 'codex',
                  'name': 'Codex',
                  'installed': true,
                  'status': 'not_configured',
                  'detail': 'Sign in.',
                },
                {
                  'id': 'claude',
                  'name': 'Claude Code',
                  'installed': false,
                  'status': 'missing',
                  'detail': 'Install first.',
                },
              ],
            }),
            200,
          );
        }
        mutations.add(request.url.path);
        final body = jsonDecode(request.body);
        if (request.url.path == '/api/dashboard-access') {
          expect(body, {'mode': 'ssh', 'tunnel_ready': true});
          return http.Response(
            '{"mode":"lan","pending_mode":"ssh","apply_after_seconds":5}',
            200,
          );
        }
        if (request.url.path == '/api/ssh-access/add') {
          expect(body['public_key'], 'ssh-ed25519 example-public-key');
          return http.Response('{"key_count":1,"added":true}', 200);
        }
        if (request.url.path.endsWith('/start')) {
          expect(body['cli'], 'codex');
          return http.Response('{"id":"session1"}', 200);
        }
        if (request.url.path.endsWith('/poll')) {
          return http.Response(
            jsonEncode({
              'output': body['cursor'] == 0
                  ? 'Enter authorization code:\r\n'
                  : '',
              'cursor': 1,
              'truncated': false,
              'running': true,
              'exit_code': null,
            }),
            200,
          );
        }
        if (request.url.path.endsWith('/input'))
          inputs.add(body['data'] as String);
        return http.Response('{}', 200);
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
    expect(find.text('Codex'), findsOneWidget);
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, 'Connect account')).onPressed, isNotNull);
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, 'CLI not installed')).onPressed, isNull);
    await tester.tap(find.text('Connect account'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    final view = tester.widget<TerminalView>(find.byType(TerminalView));
    expect(
      view.terminal.buffer.getText(),
      contains('Enter authorization code:'),
    );
    await tester.tap(find.text('↓ Down'));
    await tester.pump();
    expect(inputs, contains('\x1b[B'));
    final authInput = find.widgetWithText(
      TextField,
      'Authorization code or API key',
    );
    await tester.ensureVisible(authInput);
    await tester.enterText(authInput, 'one-time-code');
    await tester.ensureVisible(find.text('Send to terminal'));
    await tester.tap(find.text('Send to terminal'));
    await tester.pump();
    expect(inputs, contains('one-time-code\r'));
    expect(tester.widget<TextField>(authInput).controller!.text, isEmpty);
    await tester.ensureVisible(find.text('Close terminal'));
    await tester.tap(find.text('Close terminal'));
    await tester.pump();
    expect(mutations, contains('/api/cli-setup/close'));
    final sshInput = find.widgetWithText(TextField, 'SSH public key (.pub)');
    await tester.ensureVisible(sshInput);
    await tester.enterText(sshInput, 'ssh-ed25519 example-public-key');
    await tester.ensureVisible(find.text('Add SSH public key'));
    await tester.tap(find.text('Add SSH public key'));
    await tester.pump();
    expect(mutations, contains('/api/ssh-access/add'));
    expect(
      find.text('Public key added. You can now connect as herdr over SSH.'),
      findsOneWidget,
    );
    expect(
      view.terminal.mainBuffer.getText(),
      isNot(contains('authorization')),
    );
    await tester.ensureVisible(find.text('Require SSH tunnel'));
    await tester.tap(find.text('Require SSH tunnel'));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Apply access mode'),
          )
          .onPressed,
      isNull,
    );
    await tester.tap(find.byType(CheckboxListTile));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Apply access mode'));
    await tester.pumpAndSettle();
    expect(mutations, contains('/api/dashboard-access'));
    expect(find.byType(TerminalView), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await connection.disconnect();
    coordinator.dispose();
    await BlocScope.reset();
  });
}
