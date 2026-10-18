import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/builds_page.dart';

void main() {
  testWidgets(
    'administrator can queue the build service without installer options',
    (tester) async {
      var state = 'idle';
      final submitted = <String>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/build-service/install')) {
            submitted.add(request.body);
            state = 'queued';
            return http.Response(jsonEncode({'state': state}), 200);
          }
          if (request.url.path.endsWith('/build-service'))
            return http.Response(
              jsonEncode({'state': state, 'supported': true}),
              200,
            );
          if (request.url.path.endsWith('/validation'))
            return http.Response('{"runs":[],"executor":"gateway"}', 200);
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      connection.operator.value = {'role': 'admin'};
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: BuildsPage())),
      );
      await tester.pumpAndSettle();
      final install = find.text('Install build service');
      await tester.ensureVisible(install);
      await tester.tap(install);
      await tester.pumpAndSettle();
      expect(submitted, ['{}']);
      expect(
        find.text('Build-service installation: Waiting to start'),
        findsOneWidget,
      );
      expect(find.text('Installing build service…'), findsOneWidget);
      final button = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Installing build service…'),
      );
      expect(button.onPressed, isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    },
  );

  for (final brightness in [Brightness.light, Brightness.dark]) {
    testWidgets('phone build history and retention error in $brightness', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path.endsWith('/validation')) {
            return http.Response(
              jsonEncode({
                'runs': [
                  {
                    'id': 'build-1',
                    'repository': 'repo',
                    'state': 'complete',
                    'target': 'exact-sha',
                    'command': 'scripts/build-web.sh',
                    'artifact_error': 'Retention limit',
                    'timings': {
                      'workspace': {'duration_ms': 123, 'status': 'complete'},
                      'checks': {'duration_ms': 456, 'status': 'complete'},
                    },
                  },
                ],
              }),
              200,
            );
          }
          if (request.url.path.endsWith('/updates'))
            return http.Response('{"error":"Updater unavailable"}', 400);
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData(brightness: brightness),
          home: const Scaffold(body: BuildsPage()),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Builds & deployments'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.textContaining('Retention limit'),
        150,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.textContaining('Retention limit'), findsOneWidget);
      expect(find.text('Download log'), findsOneWidget);
      expect(find.text('Download static artifact'), findsNothing);
      final timings = find.text('Recorded phase timings');
      await tester.ensureVisible(timings);
      await tester.pumpAndSettle();
      await tester.tap(timings);
      await tester.pumpAndSettle();
      expect(find.text('workspace: 123 ms · complete'), findsOneWidget);
      expect(find.text('checks: 456 ms · complete'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    });
  }

  for (final refused in [false, true]) {
    testWidgets('administrator deployment and persistent refusal: $refused', (
      tester,
    ) async {
      final requests = <Map<String, dynamic>>[];
      var deploymentState = 'idle';
      final connection = DashboardBloc(
        client: MockClient((request) async {
          final endpoint = request.url.path;
          if (request.method == 'POST' &&
              endpoint.endsWith('/updates/promote')) {
            requests.add({'endpoint': 'promote', ...jsonDecode(request.body)});
            if (refused)
              return http.Response(
                '{"error":"Package checksum mismatch"}',
                409,
              );
            deploymentState = 'complete';
            return http.Response('{"state":"queued"}', 200);
          }
          if (request.method == 'POST' &&
              endpoint.endsWith('/updates/rollback')) {
            requests.add({'endpoint': 'rollback'});
            deploymentState = 'complete';
            return http.Response('{"state":"queued"}', 200);
          }
          if (endpoint.endsWith('/validation')) {
            return http.Response(
              jsonEncode({
                'runs': [
                  {
                    'id': 'build-1',
                    'repository': 'repo',
                    'state': 'complete',
                    'target': 'exact-sha',
                    'command': 'scripts/build-web.sh',
                    'deployment_package': {
                      'build_id': 'build-1',
                      'sha256': 'c' * 64,
                      'deployment_mode': 'local',
                    },
                  },
                ],
              }),
              200,
            );
          }
          if (endpoint.endsWith('/updates')) {
            return http.Response(
              jsonEncode({
                'state': deploymentState,
                'deployment_mode': 'local',
                'rollback_available': true,
                'build_id': 'build-1',
              }),
              200,
            );
          }
          if (endpoint.endsWith('/build')) {
            return http.Response(
              jsonEncode({
                'build_id': 'running-build',
                'deployment_mode': 'local',
              }),
              200,
            );
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.operator.value = {'name': 'damien', 'role': 'admin'};
      connection.connect('token');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      await tester.pumpWidget(
        MaterialApp(home: const Scaffold(body: BuildsPage())),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.textContaining('Deployment: idle'),
        150,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.textContaining('Deployment: idle'), findsOneWidget);
      // Scroll to the action rather than assuming it sits in the same built
      // range: the page grew, and a lazily built list drops off-screen rows.
      await tester.scrollUntilVisible(
        find.text('Deploy this build'),
        150,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('Deploy this build'), findsOneWidget);
      await tester.tap(find.text('Deploy this build'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Approve and deploy'));
      await tester.pumpAndSettle();
      expect(requests, [
        {'endpoint': 'promote', 'run_id': 'build-1'},
      ]);
      if (refused) {
        await tester.drag(find.byType(ListView).first, const Offset(0, 1200));
        await tester.pumpAndSettle();
        expect(
          find.textContaining('Package checksum mismatch'),
          findsOneWidget,
        );
        await tester.pump(const Duration(seconds: 6));
        await tester.pumpAndSettle();
        expect(
          find.textContaining('Package checksum mismatch'),
          findsOneWidget,
        );
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        await BlocScope.reset();
        return;
      }
      await tester.ensureVisible(find.text('Roll back last deployment'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Roll back last deployment'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Restore previous deployment'));
      await tester.pumpAndSettle();
      expect(requests.last, {'endpoint': 'rollback'});
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      await BlocScope.reset();
    });
  }
}
