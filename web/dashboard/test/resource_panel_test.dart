import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/resource_panel.dart';

void main() {
  testWidgets(
    'shows container pressure and process usage; stops polling on errors',
    (tester) async {
      tester.view.physicalSize = const Size(320, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      var requests = 0;
      var fail = false;
      final bloc = DashboardBloc(
        client: MockClient((request) async {
          if (!request.url.path.endsWith('/resources')) {
            return http.Response('{"workspaces":[],"agents":[]}', 200);
          }
          requests++;
          if (fail) return http.Response('{"error":"unavailable"}', 503);
          return http.Response(
            jsonEncode({
              'available': true,
              'sampled_at': 1,
              'cpu_percent': 75,
              'cpu_cores': 2,
              'memory_used': 950,
              'memory_total': 1000,
              'swap_used': 0,
              'swap_total': 0,
              'processes': [
                {
                  'name': 'opencode',
                  'pid': 42,
                  'memory_bytes': 1048576,
                  'cpu_percent': 25,
                },
              ],
            }),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => bloc);
      bloc.connect('token');
      await bloc.stream.firstWhere((_) => bloc.state.refreshed != null);
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: SingleChildScrollView(child: ResourcePanel())),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('CPU 75.0%'), findsOneWidget);
      expect(find.textContaining('High memory usage'), findsOneWidget);
      await tester.tap(find.text('Processes by memory usage'));
      await tester.pumpAndSettle();
      expect(find.text('opencode · PID 42'), findsOneWidget);
      fail = true;
      await tester.pump(const Duration(seconds: 10));
      await tester.pumpAndSettle();
      expect(find.textContaining('may be stale'), findsOneWidget);
      final count = requests;
      await tester.pump(const Duration(seconds: 30));
      expect(requests, count);
      await tester.pumpWidget(const SizedBox.shrink());
      await bloc.disconnect();
      await BlocScope.reset();
    },
  );
}
