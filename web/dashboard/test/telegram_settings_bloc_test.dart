import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:juice/juice.dart';
import 'package:herdr_dashboard_state/dashboard_bloc.dart';
import 'package:herdr_dashboard_state/settings_bloc.dart';

Map<String, dynamic> status({bool enabled = true, bool configured = true}) => {
  'enabled': enabled, 'token_configured': configured,
  'chat_id': configured ? '123456789' : '', 'last_error': '', 'last_sent_at': 0,
};
http.Response json(Object value) => http.Response(jsonEncode(value), 200);
Future<void> connect(DashboardBloc connection) async {
  final ready = connection.stream.firstWhere((_) => connection.state.connected);
  connection.connect('admin');
  await ready;
}

void main() {
  Logger.level = Level.off;
  JuiceLoggerConfig.minLevel = Level.off;
  test('Telegram save owns status and retains no token in state', () async {
    final started = Completer<void>(), delayed = Completer<http.Response>();
    final writes = <Map<String, dynamic>>[];
    final connection = DashboardBloc(client: MockClient((request) async {
      if (request.url.path == '/api/telegram') {
        if (!started.isCompleted) started.complete();
        return delayed.future;
      }
      if (request.url.path == '/api/telegram/configure') {
        writes.add(Map<String, dynamic>.from(jsonDecode(request.body)));
        return json({...status(), 'token': 'must-never-reach-state'});
      }
      return json({'workspaces': [], 'agents': []});
    }));
    await connect(connection);
    final bloc = TelegramNotificationsBloc(connection);
    try {
      final read = bloc.refresh();
      await started.future;
      expect(await bloc.configure(enabled: true, chatId: ' 123456789 ', token: ' secret-token '), isTrue);
      delayed.complete(json(status(enabled: false)));
      await read;
      expect(bloc.status!['enabled'], isTrue);
      expect(jsonEncode(bloc.state.values), isNot(contains('secret-token')));
      expect(jsonEncode(bloc.state.values), isNot(contains('must-never')));
      expect(writes.single, {'enabled': true, 'chat_id': '123456789', 'token': 'secret-token'});
      expect(await bloc.configure(enabled: false, chatId: '123456789', token: ' '), isTrue);
      expect(writes.last.containsKey('token'), isFalse);
    } finally {
      if (!delayed.isCompleted) delayed.complete(json(status()));
      await bloc.close();
      await connection.close();
    }
  });
  test('Telegram tests and removal obey administrator privileges', () async {
    final calls = <String>[];
    final connection = DashboardBloc(client: MockClient((request) async {
      final path = request.url.path;
      if (path.startsWith('/api/telegram')) {
        calls.add(path);
        if (request.method == 'POST') expect(jsonDecode(request.body), <String, dynamic>{});
        return json(status(configured: !path.endsWith('/disconnect')));
      }
      return json({'workspaces': [], 'agents': []});
    }));
    await connect(connection);
    final bloc = TelegramNotificationsBloc(connection);
    try {
      await bloc.refresh();
      expect(await bloc.test(), isTrue);
      expect(bloc.state.message, contains('Test notification sent'));
      expect(await bloc.disconnect(), isTrue);
      expect(bloc.status!['token_configured'], isFalse);
      connection.operator.value = {'role': 'viewer'};
      await bloc.refresh();
      expect(await bloc.test(), isFalse);
      expect(await bloc.disconnect(), isFalse);
      expect(await bloc.configure(enabled: true, chatId: '123', token: 'forbidden'), isFalse);
      expect(calls, ['/api/telegram', '/api/telegram/test', '/api/telegram/disconnect']);
    } finally {
      await bloc.close();
      await connection.close();
    }
  });
}
