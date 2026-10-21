import 'dart:convert';
import 'package:juice/juice.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/resource_panel.dart';

void main() {
  testWidgets(
    'health refresh updates build capacity without a stale waiting warning',
    (tester) async {
      final capacity = <String, dynamic>{
        'active': {'kind': 'cargo'},
        'waiting': 2,
        'compiler_jobs': 1,
        'waiting_reason': 'Waiting for available memory.',
      };
      final disk = <String, dynamic>{
        'available': true,
        'total_bytes': 32 * 1024 * 1024 * 1024,
        'used_bytes': 32 * 1024 * 1024 * 1024,
        'free_bytes': 0,
        'blocked': true,
        'reason': 'Low disk space. Free space or expand storage.',
      };
      final connection = DashboardBloc(
        client: MockClient((request) async {
          return http.Response(
            jsonEncode(
              request.url.path == '/api/resources'
                  ? {
                      'available': true,
                      'sampled_at': 1700000000,
                      'cpu_percent': 10,
                      'cpu_cores': 2,
                      'memory_total': 8000000000,
                      'memory_used': 2000000000,
                      'swap_total': 4000000000,
                      'swap_used': 3900000000,
                      'processes': [],
                      'build_capacity': capacity,
                      'storage': disk,
                    }
                  : {'workspaces': [], 'agents': []},
            ),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      connection.connect('token');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      addTearDown(() async {
        await tester.pumpWidget(const SizedBox.shrink());
        await connection.disconnect();
        BlocScope.endAll();
      });
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: SingleChildScrollView(child: ResourcePanel())),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('running · 2 waiting'), findsOneWidget);
      expect(find.textContaining('Disk 0 MiB available'), findsOneWidget);
      expect(find.textContaining('Storage needs attention:'), findsOneWidget);
      expect(find.textContaining('New builds will wait.'), findsOneWidget);
      capacity.addAll({'active': null, 'waiting': 0, 'waiting_reason': ''});
      disk.addAll({'blocked': false, 'free_bytes': 8 * 1024 * 1024 * 1024});
      await tester.tap(find.byTooltip('Refresh resources'));
      await tester.pumpAndSettle();
      expect(find.textContaining('idle · 0 waiting'), findsOneWidget);
      expect(find.textContaining('New builds will wait.'), findsNothing);
      expect(find.textContaining('Storage needs attention:'), findsNothing);
      expect(find.textContaining('Disk 8192 MiB available'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
      await connection.disconnect();
      BlocScope.endAll();
    },
  );
}
