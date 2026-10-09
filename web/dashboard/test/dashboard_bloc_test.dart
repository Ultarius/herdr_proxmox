import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:herdr_dashboard/dashboard_bloc.dart';
import 'package:herdr_dashboard/routes.dart';
import 'package:herdr_dashboard/main.dart';
import 'package:juice/juice.dart';

void main() {
  JuiceLoggerConfig.minLevel = Level.warning;
  testWidgets(
    'notification cursor refreshes records and stops after disconnect',
    (tester) async {
      var feeds = 0;
      final feedRequests = <http.Request>[];
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/notifications') {
            feeds++;
            feedRequests.add(request);
            if (feeds == 1) {
              return http.Response(
                '{"epoch":"gateway","revision":4,"reset":true,"changes":[]}',
                200,
              );
            }

            return http.Response(
              '{}',
              200,
            ); // Older gateway disables push; periodic refresh remains.
          }
          return http.Response('{"workspaces":[],"agents":[]}', 200);
        }),
      );
      BlocScope.register<DashboardBloc>(() => connection);
      await tester.pumpWidget(const MaterialApp(home: SizedBox()));
      BlocScope.get<DashboardBloc>();
      connection.connect('token');
      await tester.pump(const Duration(milliseconds: 110));
      await tester.pumpAndSettle();
      expect(feeds, greaterThanOrEqualTo(1));
      expect(connection.notificationRevision.value, 1);
      await tester.pump(const Duration(milliseconds: 110));
      await tester.pumpAndSettle();
      expect(feeds, 2);
      expect(feedRequests.first.headers['Authorization'], 'Bearer token');
      expect(feedRequests.last.url.queryParameters['after'], '4');
      expect(feedRequests.last.url.queryParameters['epoch'], 'gateway');
      await connection.disconnect();
      await tester.pump(const Duration(seconds: 1));
      expect(feeds, 2);
      await tester.pumpWidget(const SizedBox());
      await BlocScope.reset();
    },
  );

  testWidgets(
    'dashboard starts a stopped Herdr server and refreshes its state',
    (tester) async {
      bool running = false;
      final connection = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/session' ||
              request.url.path == '/api/session/logout')
            return http.Response('{"authenticated":true}', 200);
          if (request.url.path == '/api/session')
            return http.Response('{"authenticated":true}', 200);
          if (request.url.path == '/api/herdr-server/start') {
            expect(request.method, 'POST');
            expect(jsonDecode(request.body), {});
            running = true;
            return http.Response('{"state":"running"}', 200);
          }
          if ([
            '/api/organizations',
            '/api/organizations/history',
            '/api/organizations/directory',
            '/api/organizations/state',
            '/api/organizations/activity',
          ].contains(request.url.path))
            return http.Response(
              '{"organizations":[],"profiles":[],"jobs":[]}',
              200,
            );
          return http.Response(
            jsonEncode({
              'herdr_server': running ? 'running' : 'stopped',
              'workspaces': [],
              'agents': [],
            }),
            200,
          );
        }),
      );
      BlocScope.register<DashboardBloc>(
        () => connection,
        lifecycle: BlocLifecycle.permanent,
      );
      final coordinator = AppCoordinator();
      await tester.pumpWidget(MaterialApp.router(routerConfig: coordinator));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).first, 'token');
      await tester.tap(find.text('Connect'));
      await tester.pumpAndSettle();
      expect(find.text('Unavailable'), findsOneWidget);
      await tester.tap(find.text('Start Herdr'));
      await tester.pumpAndSettle();
      expect(find.text('Connected'), findsOneWidget);
      expect(find.text('Start Herdr'), findsNothing);
      expect(tester.takeException(), isNull);
      await connection.disconnect();
      await tester.pumpWidget(const SizedBox());
      coordinator.dispose();
      await BlocScope.reset();
    },
  );
  test(
    'connection loads real snapshot and a workspace action refreshes it',
    () async {
      final paths = <String>[];
      final bloc = DashboardBloc(
        client: MockClient((request) async {
          if (request.url.path == '/api/session' ||
              request.url.path == '/api/session/logout')
            return http.Response('{"authenticated":true}', 200);
          paths.add(request.url.path);
          expect(request.headers['Authorization'], 'Bearer test-token');
          if (request.method == 'POST' &&
              !request.url.path.endsWith('/activity')) {
            expect(jsonDecode(request.body)['id'], 'w1');
            return http.Response('{}', 200);
          }
          return http.Response(
            '{"workspaces":[{"workspace_id":"w1"}],"agents":[{"state":"working"}]}',
            200,
          );
        }),
      );
      final loaded = bloc.stream.firstWhere(
        (_) => bloc.state.refreshed != null,
      );
      bloc.connect('test-token');
      await loaded;
      expect(bloc.state.agents.single['state'], 'working');
      await bloc.send(DashboardCommand('focus', {'id': 'w1'}));
      expect(paths, [
        '/api/snapshot',
        '/api/workspaces/focus',
        '/api/snapshot',
      ]);
      await bloc.disconnect();
      expect(bloc.state.connected, false);
      expect(bloc.state.workspaces, isEmpty);
      bloc.dispose();
    },
  );

  test(
    'disconnect prevents an old response from restoring credentials and data',
    () async {
      final response = Completer<http.Response>();
      final started = Completer<void>();
      final bloc = DashboardBloc(
        client: MockClient((_) {
          started.complete();
          return response.future;
        }),
      );
      bloc.connect('test-token');
      await started.future;
      await bloc.disconnect();
      response.complete(http.Response('{"workspaces":[],"agents":[]}', 200));
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(bloc.state.connected, false);
      expect(bloc.state.refreshed, null);
      bloc.dispose();
    },
  );

  test('authentication failures are shown and leave controls usable', () async {
    final bloc = DashboardBloc(
      client: MockClient(
        (_) async => http.Response('{"error":"Invalid token"}', 401),
      ),
    );
    final failed = bloc.stream.firstWhere((_) => bloc.state.error != null);
    bloc.connect('wrong-token');
    await failed;
    expect(bloc.state.error, contains('Invalid token'));
    expect(bloc.state.busy, false);
    bloc.dispose();
  });

  test('ZenRouter resolves both dashboard routes and unknown locations', () {
    final coordinator = AppCoordinator();
    expect(
      coordinator.parseRouteFromUri(Uri.parse('/')),
      isA<DashboardRoute>(),
    );
    expect(
      coordinator.parseRouteFromUri(Uri.parse('/agents')),
      isA<AgentsRoute>(),
    );
    expect(
      coordinator.parseRouteFromUri(Uri.parse('/missing')),
      isA<NotFoundRoute>(),
    );
    coordinator.dispose();
  });

  testWidgets(
    'Juice connection survives ZenRouter navigation and disconnect rebuilds the UI',
    (tester) async {
      final bloc = DashboardBloc(
        client: MockClient(
          (_) async => http.Response(
            '{"authenticated":true,"workspaces":[],"agents":[]}',
            200,
          ),
        ),
      );
      BlocScope.register<DashboardBloc>(
        () => bloc,
        lifecycle: BlocLifecycle.permanent,
      );
      final coordinator = AppCoordinator();
      await tester.pumpWidget(MaterialApp.router(routerConfig: coordinator));
      await tester.pumpAndSettle();
      // Route widgets are the real dashboard, with the registered Juice bloc.
      expect(find.byType(Dashboard), findsOneWidget);
      await tester.enterText(find.byType(TextField).first, 'test-token');
      await tester.tap(find.text('Connect'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Agents').first);
      await tester.pumpAndSettle();
      expect(bloc.state.connected, true);
      expect(find.text('Create workspace'), findsNothing);
      await tester.tap(find.byTooltip('Open navigation'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Sign out'),
        150,
        scrollable: find
            .descendant(
              of: find.byType(Drawer),
              matching: find.byType(Scrollable),
            )
            .first,
      );
      await tester.ensureVisible(find.text('Sign out'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sign out'));
      await tester.pumpAndSettle();
      expect(find.text('Connect'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      coordinator.dispose();
      await BlocScope.reset();
    },
  );
}
