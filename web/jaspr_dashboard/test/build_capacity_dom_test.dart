@TestOn('browser')
library;

import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:jaspr/client.dart';
import 'package:web/web.dart' as web;
import 'package:herdr_jaspr_dashboard/overview.dart';
import 'package:herdr_jaspr_dashboard/services.dart';

void main() {
  test(
    'health refresh distinguishes queued capacity from idle builds',
    () async {
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
      final mount = web.HTMLDivElement()..id = 'capacity-test';
      web.document.body!.appendChild(mount);
      final binding = ClientAppBinding();
      binding.attachRootComponent(
        const SystemHealth(),
        attachTo: '#capacity-test',
      );
      addTearDown(() async {
        binding.detachRootComponent();
        mount.remove();
        await connection.disconnect();
        BlocScope.endAll();
      });
      await Future<void>.delayed(const Duration(milliseconds: 80));
      expect(mount.textContent, contains('running · 2 waiting'));
      expect(mount.textContent, contains('0 MiB available'));
      expect(
        mount.textContent,
        contains('Storage needs attention: Low disk space.'),
      );
      expect(
        mount.textContent,
        contains('Waiting for available memory. New builds will wait.'),
      );
      capacity.addAll({'active': null, 'waiting': 0, 'waiting_reason': ''});
      disk.addAll({'blocked': false, 'free_bytes': 8 * 1024 * 1024 * 1024});
      (mount.querySelector('button') as web.HTMLButtonElement).click();
      await Future<void>.delayed(const Duration(milliseconds: 80));
      expect(mount.textContent, contains('idle · 0 waiting'));
      expect(mount.textContent, isNot(contains('New builds will wait.')));
      expect(mount.textContent, isNot(contains('Storage needs attention:')));
      expect(mount.textContent, contains('8,192 MiB available'));
    },
  );
}
