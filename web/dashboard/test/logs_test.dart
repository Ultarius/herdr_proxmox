import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/logs_page.dart';
import 'package:herdr_dashboard/routes.dart';

void main() {
  test('sizes use decimal MB and retain precision for small files', () {
    expect(logSize(1000000), '1.000 MB');
    expect(logSize(1048576), '1.049 MB');
    expect(logSize(8), '0.000008 MB');
  });
  testWidgets(
    'log list stays metadata-only until preview is requested and fits narrow screen',
    (tester) async {
      tester.view.physicalSize = const Size(390, 1200);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final paths = <String>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          paths.add(request.url.path);
          if (request.url.path == '/api/snapshot')
            return http.Response('{"workspaces":[],"agents":[]}', 200);
          if (request.url.path.endsWith('/preview'))
            return http.Response(
              '{"text":"saved output","truncated":true}',
              200,
            );
          return http.Response(
            jsonEncode({
              'total_bytes': 1000000,
              'logs': [
                {
                  'id': 'a' * 32,
                  'label': 'Worker run',
                  'pane': 'w1:p1',
                  'source': 'visible',
                  'size_bytes': 1000000,
                  'created_at': '2026-10-04',
                  'download_allowed': true,
                },
              ],
            }),
            200,
          );
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
      coordinator.navigate(LogsRoute());
      await tester.pumpAndSettle();
      expect(find.text('Worker run — 1.000 MB'), findsOneWidget);
      expect(paths.where((path) => path.startsWith('/api/logs')), [
        '/api/logs',
      ]);
      await tester.ensureVisible(find.text('Preview last 8 KB'));
      await tester.tap(find.text('Preview last 8 KB'));
      await tester.pumpAndSettle();
      expect(
        find.text('[Last 8 KB of saved text]\nsaved output'),
        findsOneWidget,
      );
      expect(paths, contains('/api/logs/preview'));
      expect(paths, isNot(contains('/log-data')));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await connection.disconnect();
      coordinator.dispose();
      await BlocScope.reset();
    },
  );
}
