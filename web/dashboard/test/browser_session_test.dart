import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';

void main() {
  test(
    'browser sign in discards bearer token, restores and revokes session',
    () async {
      bool authenticated = false;
      final client = MockClient((request) async {
        if (request.url.path == '/api/session') {
          if (request.method == 'POST') {
            expect(request.headers['Authorization'], 'Bearer secret');
            authenticated = true;
          }
          return http.Response(
            jsonEncode({'authenticated': authenticated}),
            200,
          );
        }
        expect(request.headers.containsKey('Authorization'), isFalse);
        if (request.url.path == '/api/session/logout') {
          authenticated = false;
          return http.Response('{}', 200);
        }
        return http.Response('{"workspaces":[],"agents":[]}', 200);
      });
      final first = DashboardBloc(client: client);
      await first.signIn('secret');
      await Future<void>.delayed(const Duration(milliseconds: 30));
      expect(first.state.connected, isTrue);
      final restored = DashboardBloc(client: client);
      await restored.restoreSession();
      await Future<void>.delayed(const Duration(milliseconds: 30));
      expect(restored.state.connected, isTrue);
      await restored.disconnect();
      expect(authenticated, isFalse);
      expect(restored.state.connected, isFalse);
      await first.disconnect();
      first.dispose();
      restored.dispose();
    },
  );

  test(
    'invalid token remains disconnected and shows authentication error',
    () async {
      final bloc = DashboardBloc(
        client: MockClient(
          (_) async =>
              http.Response('{"error":"Invalid dashboard token."}', 401),
        ),
      );
      await bloc.signIn('wrong');
      expect(bloc.state.connected, isFalse);
      expect(bloc.state.error, contains('Invalid dashboard token'));
      bloc.dispose();
    },
  );
}
