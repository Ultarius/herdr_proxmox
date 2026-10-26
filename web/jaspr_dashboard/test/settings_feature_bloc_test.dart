import 'dart:convert';
import 'package:test/test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/settings_bloc.dart';
import 'package:herdr_dashboard_state/organization_tools_bloc.dart';

http.Response json(Object value) => http.Response(jsonEncode(value), 200);
http.Response snapshot() => json({'workspaces': [], 'agents': []});
Future<void> connect(DashboardBloc connection) async {
  final ready = connection.stream.firstWhere((_) => connection.state.connected);
  connection.connect('admin');
  await ready;
}

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  test('SSH key mutation owns state over an older status read', () async {
    final started = Completer<void>();
    final reply = Completer<http.Response>();
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/ssh-access') {
          if (!started.isCompleted) started.complete();
          return reply.future;
        }
        if (request.url.path == '/api/ssh-access/add') {
          expect(jsonDecode(request.body), {'public_key': 'ssh-ed25519 key'});
          return json({'key_count': 2, 'added': true});
        }
        return snapshot();
      }),
    );
    await connect(connection);
    final bloc = SshAccessBloc(connection);
    try {
      final read = bloc.refresh();
      await started.future;
      expect(await bloc.add('ssh-ed25519 key'), isTrue);
      reply.complete(json({'key_count': 1}));
      await read;
      expect(bloc.count, 2);
      expect(bloc.state.message, contains('Public key added'));
      connection.operator.value = {'role': 'viewer'};
      expect(await bloc.add('forbidden key'), isFalse);
    } finally {
      if (!reply.isCompleted) reply.complete(json({'key_count': 1}));
      await bloc.close();
      await connection.close();
    }
  });
  test('non-polling settings reload after a privilege reset', () async {
    var count = 0;
    final connection = DashboardBloc(
      client: MockClient(
        (request) async => request.url.path == '/api/ssh-access'
            ? json({'key_count': ++count})
            : snapshot(),
      ),
    );
    await connect(connection);
    final bloc = SshAccessBloc(connection);
    try {
      await bloc.refresh();
      expect(bloc.count, 1);
      final reloaded = bloc.stream.firstWhere((_) => bloc.count == 2);
      connection.operator.value = {'role': 'viewer'};
      await reloaded;
      expect(bloc.count, 2);
    } finally {
      await bloc.close();
      await connection.close();
    }
  });
  test('SDK installation publishes its refreshed server status', () async {
    var queued = false;
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/sdk/install') {
          expect(jsonDecode(request.body), {'toolchain': 'rust'});
          queued = true;
          return json({'state': 'queued'});
        }
        if (request.url.path == '/api/sdk')
          return json({
            'state': queued ? 'queued' : 'idle',
            'toolchains': {
              'rust': {'required': '1.0', 'installed': false},
            },
          });
        return snapshot();
      }),
    );
    await connect(connection);
    final bloc = ToolchainBloc(connection);
    try {
      await bloc.refresh();
      expect(await bloc.install('rust'), isTrue);
      expect(bloc.queued, isTrue);
      expect(bloc.state.message, contains('rust installation queued'));
    } finally {
      await bloc.close();
      await connection.close();
    }
  });
  test('changing model context rejects a delayed previous catalog', () async {
    final started = Completer<void>();
    final reply = Completer<http.Response>();
    final connection = DashboardBloc(
      client: MockClient((request) async {
        if (request.url.path == '/api/models') {
          started.complete();
          return reply.future;
        }
        return snapshot();
      }),
    );
    await connect(connection);
    final bloc = ModelDiscoveryBloc(connection);
    try {
      final read = bloc.discover(
        runtime: 'opencode',
        project: 'old',
        provider: '',
        model: '',
      );
      await started.future;
      await bloc.reset();
      reply.complete(
        json({
          'models': [
            {'id': 'old', 'name': 'Old'},
          ],
        }),
      );
      await read;
      expect(bloc.catalog, isNull);
      expect(bloc.state.error, isNull);
    } finally {
      if (!reply.isCompleted) reply.complete(json({'models': []}));
      await bloc.close();
      await connection.close();
    }
  });
}
