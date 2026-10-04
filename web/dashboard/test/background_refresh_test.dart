import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/organization_bloc.dart';

void main() {
  test(
    'background snapshot polling keeps controls enabled and skips unchanged data',
    () async {
      Completer<http.Response>? waiting;
      final bloc = DashboardBloc(
        client: MockClient(
          (_) async => waiting == null
              ? http.Response(
                  '{"workspaces":[],"agents":[],"herdr_server":"running"}',
                  200,
                )
              : waiting.future,
        ),
      );
      bloc.connect('token');
      await bloc.stream.firstWhere((_) => bloc.state.refreshed != null);
      final states = <DashboardState>[];
      final sub = bloc.stream.listen((_) => states.add(bloc.state));
      waiting = Completer<http.Response>();
      final refresh = bloc.send(DashboardCommand('refresh', null, true));
      await Future<void>.delayed(Duration.zero);
      expect(bloc.state.busy, isFalse);
      waiting.complete(
        http.Response(
          '{"workspaces":[],"agents":[],"herdr_server":"running"}',
          200,
        ),
      );
      await refresh;
      expect(states, isEmpty);
      waiting = Completer<http.Response>();
      final changed = bloc.send(DashboardCommand('refresh', null, true));
      waiting.complete(
        http.Response(
          '{"workspaces":[],"agents":[{"name":"Max"}],"herdr_server":"running"}',
          200,
        ),
      );
      await changed;
      expect(bloc.state.agents.single['name'], 'Max');
      expect(states.every((s) => !s.busy), isTrue);
      await sub.cancel();
      bloc.dispose();
    },
  );

  test(
    'organization refresh does not replace loaded content with busy state',
    () async {
      final connection = DashboardBloc(
        client: MockClient(
          (r) async => http.Response(
            r.url.path.endsWith('organizations')
                ? '{"organizations":[],"profiles":[],"jobs":[]}'
                : '{"workspaces":[],"agents":[]}',
            200,
          ),
        ),
      );
      connection.connect('token');
      await connection.stream.firstWhere(
        (_) => connection.state.refreshed != null,
      );
      final org = OrganizationBloc(connection);
      await org.send(OrganizationCommand('refresh'));
      final states = <OrganizationState>[];
      final sub = org.stream.listen((_) => states.add(org.state));
      await org.send(OrganizationCommand('refresh'));
      expect(org.state.busy, isFalse);
      expect(states, isEmpty);
      await sub.cancel();
      org.dispose();
      connection.dispose();
    },
  );
}
