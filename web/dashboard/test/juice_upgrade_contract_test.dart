import 'dart:async';
import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/organization_bloc.dart';

http.Response snapshot([String name = 'initial']) => http.Response(
  jsonEncode({
    'workspaces': [
      {'name': name},
    ],
    'agents': [],
    'herdr_server': 'running',
  }),
  200,
);

Future<void> connect(DashboardBloc bloc, String token) async {
  final ready = bloc.stream.firstWhere((_) => bloc.state.refreshed != null);
  bloc.connect(token);
  await ready;
}

class CountingClient extends http.BaseClient {
  CountingClient(this.delegate);
  final http.Client delegate;
  int closes = 0;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      delegate.send(request);

  @override
  void close() {
    closes++;
    delegate.close();
  }
}

// Capture owned polling timers without waiting for wall-clock polling intervals.
class RecordedTimer implements Timer {
  RecordedTimer(this.duration, {this.periodic = false});
  final Duration duration;
  final bool periodic;
  bool _active = true;

  @override
  bool get isActive => _active;
  @override
  int get tick => 0;
  @override
  void cancel() => _active = false;
}

void main() {
  test(
    'workspace writes remain droppable while a request is in flight',
    () async {
      final started = Completer<void>();
      final response = Completer<http.Response>();
      final writes = <Map<String, dynamic>>[];
      final bloc = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/workspaces/create') {
            writes.add(jsonDecode(request.body) as Map<String, dynamic>);
            started.complete();
            return response.future;
          }
          return snapshot();
        }),
      );
      try {
        await connect(bloc, 'account-a');
        final first = bloc.send(DashboardCommand('create', {'name': 'first'}));
        await started.future;
        await bloc.send(DashboardCommand('create', {'name': 'duplicate'}));
        expect(writes, [
          {'name': 'first'},
        ]);
        response.complete(http.Response('{}', 200));
        await first;
        expect(bloc.state.busy, isFalse);
        expect(bloc.state.error, isNull);
      } finally {
        if (!response.isCompleted) response.complete(http.Response('{}', 200));
        await bloc.close();
      }
    },
  );

  test(
    'disconnect resets immediately during an in-flight workspace write',
    () async {
      final started = Completer<void>();
      final response = Completer<http.Response>();
      var snapshots = 0;
      final bloc = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/workspaces/create') {
            started.complete();
            return response.future;
          }
          if (request.url.path == '/api/snapshot') snapshots++;
          return snapshot();
        }),
      );
      try {
        await connect(bloc, 'account-a');
        final write = bloc.send(DashboardCommand('create', {'name': 'old'}));
        await started.future;
        final generation = bloc.generation;
        await bloc.disconnect();
        expect(bloc.state.connected, isFalse);
        expect(bloc.state.busy, isFalse);
        expect(bloc.generation, generation + 1);
        response.complete(http.Response('{}', 200));
        await write;
        expect(
          snapshots,
          1,
          reason: 'A stale write must not fetch another snapshot.',
        );
        expect(bloc.state.connected, isFalse);
        expect(bloc.state.workspaces, isEmpty);
      } finally {
        if (!response.isCompleted) response.complete(http.Response('{}', 200));
        await bloc.close();
      }
    },
  );

  test(
    'organization writes remain droppable and do not repeat API side effects',
    () async {
      final started = Completer<void>();
      final response = Completer<http.Response>();
      final names = <String>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/organizations/save') {
            names.add(jsonDecode(request.body)['name'] as String);
            started.complete();
            return response.future;
          }
          if (request.url.path == '/api/organizations/state') {
            return http.Response(
              '{"organizations":[{"name":"saved"}],"profiles":[],"jobs":[]}',
              200,
            );
          }
          return snapshot();
        }),
      );
      final org = OrganizationBloc(connection);
      try {
        await connect(connection, 'account-a');
        final first = org.send(OrganizationCommand('save', {'name': 'first'}));
        await started.future;
        await org.send(OrganizationCommand('save', {'name': 'duplicate'}));
        expect(names, ['first']);
        response.complete(http.Response('{}', 200));
        await first;
        expect(org.pending, isNull);
        expect(org.state.organizations.single['name'], 'saved');
        expect(org.state.error, isNull);
      } finally {
        if (!response.isCompleted) response.complete(http.Response('{}', 200));
        await org.close();
        await connection.close();
      }
    },
  );

  test(
    'late dashboard and organization reads cannot cross account generations',
    () async {
      final dashboardStarted = Completer<void>();
      final organizationStarted = Completer<void>();
      final dashboardResponse = Completer<http.Response>();
      final organizationResponse = Completer<http.Response>();
      var delayOldReads = false;
      final headers = <String?>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/notifications') {
            return http.Response('{}', 200);
          }
          final credential = request.headers['Authorization'];
          headers.add(credential);
          if (request.url.path == '/api/organizations/state') {
            if (delayOldReads && credential == 'Bearer account-a') {
              organizationStarted.complete();
              return organizationResponse.future;
            }
            return http.Response(
              '{"organizations":[{"name":"account-b"}],"profiles":[],"jobs":[]}',
              200,
            );
          }
          if (delayOldReads && credential == 'Bearer account-a') {
            dashboardStarted.complete();
            return dashboardResponse.future;
          }
          return snapshot(
            credential == 'Bearer account-b' ? 'account-b' : 'account-a',
          );
        }),
      );
      final org = OrganizationBloc(connection);
      try {
        await connect(connection, 'account-a');
        delayOldReads = true;
        final oldDashboard = connection.send(DashboardCommand('refresh'));
        final oldOrganization = org.send(OrganizationCommand('refresh'));
        await Future.wait([
          dashboardStarted.future,
          organizationStarted.future,
        ]);
        await connection.disconnect();
        connection.connect('account-b');
        dashboardResponse.complete(snapshot('stale-account-a'));
        organizationResponse.complete(
          http.Response(
            '{"organizations":[{"name":"stale-account-a"}],"profiles":[],"jobs":[]}',
            200,
          ),
        );
        await Future.wait([oldDashboard, oldOrganization]);
        expect(connection.state.workspaces, isEmpty);
        expect(org.state.organizations, isEmpty);
        expect(org.state.error, isNull);
        await connection.send(DashboardCommand('refresh'));
        await org.send(OrganizationCommand('refresh'));
        expect(connection.state.workspaces.single['name'], 'account-b');
        expect(org.state.organizations.single['name'], 'account-b');
        expect(headers, containsAll(['Bearer account-a', 'Bearer account-b']));
      } finally {
        if (!dashboardResponse.isCompleted)
          dashboardResponse.complete(snapshot());
        if (!organizationResponse.isCompleted) {
          organizationResponse.complete(http.Response('{}', 200));
        }
        await org.close();
        await connection.close();
      }
    },
  );

  test(
    'close and dispose share teardown and reject late request results',
    () async {
      final started = Completer<void>();
      final response = Completer<http.Response>();
      var blocked = false;
      var snapshots = 0;
      final client = CountingClient(
        MockClient((request) async {
          if (request.url.path == '/api/notifications')
            return http.Response('{}', 200);
          snapshots++;
          if (blocked) {
            started.complete();
            return response.future;
          }
          return snapshot();
        }),
      );
      final bloc = DashboardBloc(client: client);
      try {
        await connect(bloc, 'account-a');
        blocked = true;
        final refresh = bloc.send(DashboardCommand('refresh'));
        await started.future;
        final before = bloc.state;
        final generation = bloc.generation;
        final closing = bloc.close();
        bloc.dispose();
        expect(identical(closing, bloc.close()), isTrue);
        await closing;
        expect(client.closes, 1);
        expect(bloc.generation, generation + 1);
        expect(bloc.isClosed, isTrue);
        response.complete(snapshot('late'));
        await refresh;
        expect(identical(bloc.state, before), isTrue);
        bloc.connect('account-b');
        await bloc.send(DashboardCommand('refresh'));
        expect(
          snapshots,
          2,
          reason: 'Closing must prevent reconnection and new HTTP work.',
        );
        expect(bloc.generation, generation + 1);
      } finally {
        if (!response.isCompleted) response.complete(snapshot());
        await bloc.close();
      }
    },
  );

  test(
    'awaitable close cancels notification and periodic polling timers',
    () async {
      final timers = <RecordedTimer>[];
      final client = CountingClient(MockClient((_) async => snapshot()));
      await runZoned(
        () async {
          final bloc = DashboardBloc(client: client);
          try {
            await connect(bloc, 'account-a');
            final polling = timers.where((timer) => timer.isActive).toList();
            expect(polling, hasLength(2));
            expect(
              polling.any(
                (timer) =>
                    timer.periodic &&
                    timer.duration == const Duration(seconds: 5),
              ),
              isTrue,
            );
            expect(
              polling.any(
                (timer) =>
                    !timer.periodic &&
                    timer.duration == const Duration(milliseconds: 100),
              ),
              isTrue,
            );
            await bloc.close();
            expect(polling.every((timer) => !timer.isActive), isTrue);
            expect(client.closes, 1);
            bloc.connect('account-b');
            expect(timers.where((timer) => timer.isActive), isEmpty);
          } finally {
            await bloc.close();
          }
        },
        zoneSpecification: ZoneSpecification(
          createTimer: (self, parent, zone, duration, callback) {
            final timer = RecordedTimer(duration);
            timers.add(timer);
            return timer;
          },
          createPeriodicTimer: (self, parent, zone, duration, callback) {
            final timer = RecordedTimer(duration, periodic: true);
            timers.add(timer);
            return timer;
          },
        ),
      );
    },
  );
}
